from __future__ import annotations

import json
import shutil
import sqlite3
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capability_lab.adapters.config import resolve_config
from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.adapters.persistence import SqliteMetadataRepository
from capability_lab.adapters.pi_harness import PiHarness
from capability_lab.adapters.raw_ollama_harness import RawOllamaHarness
from capability_lab.adapters.sandboxes import SandboxError
from capability_lab.application.service import comparison_equality
from capability_lab.bootstrap import build_service
from capability_lab.cli.main import app
from capability_lab.domain.models import (
    ArtifactRef,
    ComparisonOutcome,
    ComparisonResult,
    EqualityEvidence,
    ExecutionBudget,
    ExecutionContext,
    FailureClassification,
    HarnessRequest,
    HarnessResult,
    HarnessSettings,
    ModelIdentity,
    ModelSettings,
    NetworkPolicy,
    ResolvedConfiguration,
    RunProvenance,
    RunResult,
    RunSettings,
    RuntimePaths,
    SandboxIdentity,
    SandboxProvenance,
    SandboxResult,
    SandboxSettings,
    ScoreResult,
    WorkspaceDiff,
)


def _provenance() -> SandboxProvenance:
    return SandboxProvenance(
        SandboxIdentity("sha256:image", "27.0.0", "501:20", "dockerfile"),
        NetworkPolicy("ollama-only", "http://host.docker.internal:11434", False),
    )


class StubSandbox:
    def __init__(self, result: SandboxResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.calls = 0
        self.argv: tuple[str, ...] = ()
        self.request: dict[str, object] | None = None
        self.runtime_name = ""

    def execute(self, argv: tuple[str, ...], context: ExecutionContext) -> SandboxResult:
        self.calls += 1
        self.argv = argv
        runtime_dirs = list(context.workspace.glob(".capability-lab-raw-*"))
        assert len(runtime_dirs) == 1
        runtime_dir = runtime_dirs[0]
        self.runtime_name = runtime_dir.name
        self.request = json.loads((runtime_dir / "request.json").read_text())
        assert (runtime_dir / "request.mjs").is_file()
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _stdout(status: int, body: object) -> str:
    raw_body = body if isinstance(body, str) else json.dumps(body)
    return json.dumps({"status": status, "body": raw_body})


def _execute(tmp_path: Path, sandbox: StubSandbox):
    return RawOllamaHarness(
        sandbox,
        "qwen2.5-coder:1.5b",
        seed=7,
        temperature=0.25,
        max_output_tokens=256,
    ).execute(
        HarnessRequest("task", "fix only src/example.py", "success"),
        ExecutionContext(tmp_path, 30, 1),
    )


def test_raw_ollama_makes_one_fixed_non_streaming_request_without_tools_or_output_execution(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("host instructions")
    (tmp_path / ".evaluation").mkdir()
    (tmp_path / ".evaluation/hidden.py").write_text("protected")
    model_text = "open('/workspace/model-output-was-executed', 'w').write('bad')"
    response = {
        "model": "qwen2.5-coder:1.5b",
        "response": model_text,
        "done": True,
        "done_reason": "stop",
    }
    sandbox = StubSandbox(SandboxResult(0, _stdout(200, response), "", False, False, _provenance()))

    result = _execute(tmp_path, sandbox)

    root = f"/workspace/{sandbox.runtime_name}"
    assert sandbox.calls == 1
    assert sandbox.argv == ("node", f"{root}/request.mjs", f"{root}/request.json")
    assert sandbox.request == {
        "model": "qwen2.5-coder:1.5b",
        "prompt": "fix only src/example.py",
        "stream": False,
        "options": {
            "seed": 7,
            "temperature": 0.25,
            "num_predict": 256,
        },
    }
    assert sandbox.request is not None
    assert "tools" not in sandbox.request
    assert "host instructions" not in json.dumps(sandbox.request)
    assert "protected" not in json.dumps(sandbox.request)
    assert result.exit_code == 0
    assert result.final_response == model_text
    assert result.events == ({"type": "model_response", "status": 200, "body": response},)
    assert result.sandbox_provenance == _provenance()
    assert not (tmp_path / "model-output-was-executed").exists()
    assert list(tmp_path.glob(".capability-lab-raw-*")) == []


@pytest.mark.parametrize(
    ("sandbox_result", "expected_kind", "timed_out", "output_limited"),
    [
        (SandboxResult(7, "", "node failed", False, False, _provenance()), "process", False, False),
        (SandboxResult(137, "", "", True, False, _provenance()), "timeout", True, False),
        (
            SandboxResult(137, "", "", False, True, _provenance()),
            "output_overflow",
            False,
            True,
        ),
    ],
)
def test_raw_ollama_process_failures_are_structured_cleaned_and_keep_provenance(
    tmp_path: Path,
    sandbox_result: SandboxResult,
    expected_kind: str,
    timed_out: bool,
    output_limited: bool,
) -> None:
    result = _execute(tmp_path, StubSandbox(sandbox_result))

    assert result.exit_code != 0
    assert result.events[-1]["kind"] == expected_kind
    assert result.failure_kind == "environment"
    assert result.timed_out is timed_out
    assert result.output_limited is output_limited
    assert result.sandbox_provenance == _provenance()
    assert list(tmp_path.glob(".capability-lab-raw-*")) == []


@pytest.mark.parametrize(
    ("stdout", "detail"),
    [
        ("not-json", "sandbox output"),
        (_stdout(200, "not-json"), "Ollama response"),
        (_stdout(200, {"done": True}), "response text"),
        (_stdout(200, {"response": "partial", "done": False}), "completed response"),
    ],
)
def test_raw_ollama_rejects_malformed_responses_with_evidence(
    tmp_path: Path, stdout: str, detail: str
) -> None:
    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert result.exit_code == 1
    assert result.failure_kind == "environment"
    assert result.events[-1]["kind"] == "malformed_response"
    assert detail in result.events[-1]["detail"]
    assert result.sandbox_provenance == _provenance()


def test_raw_ollama_preserves_http_error_as_model_failure(tmp_path: Path) -> None:
    error = {"error": "model runner crashed"}

    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, _stdout(500, error), "", False, False, _provenance())),
    )

    assert result.exit_code == 1
    assert result.failure_kind == "model_runtime"
    assert result.events == (
        {"type": "model_response", "status": 500, "body": error},
        {"type": "harness_failure", "kind": "model_runtime", "detail": "model runner crashed"},
    )
    assert result.sandbox_provenance == _provenance()


def test_raw_ollama_contains_a_tracked_runtime_symlink(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / ".capability-lab-raw").symlink_to(outside, target_is_directory=True)

    result = _execute(
        tmp_path,
        StubSandbox(
            SandboxResult(
                0,
                _stdout(200, {"response": "done", "done": True}),
                "",
                False,
                False,
                _provenance(),
            )
        ),
    )

    assert result.exit_code == 0
    assert list(outside.iterdir()) == []
    assert (tmp_path / ".capability-lab-raw").is_symlink()
    assert list(tmp_path.glob(".capability-lab-raw-*")) == []


def test_raw_ollama_translates_sandbox_error_and_cleans_runtime(tmp_path: Path) -> None:
    error = SandboxError("cleanup failed", returncode=9, provenance=_provenance())

    result = _execute(tmp_path, StubSandbox(error=error))

    assert result.exit_code == 9
    assert result.failure_kind == "environment"
    assert result.events == (
        {"type": "harness_failure", "kind": "sandbox", "detail": "cleanup failed"},
    )
    assert result.sandbox_provenance == _provenance()
    assert list(tmp_path.glob(".capability-lab-raw-*")) == []


class PiStubSandbox:
    def __init__(self) -> None:
        self.models: dict[str, object] | None = None
        self.extension = ""

    def execute(self, argv: tuple[str, ...], context: ExecutionContext) -> SandboxResult:
        runtime_dirs = list(context.workspace.glob(".capability-lab-pi-*"))
        assert len(runtime_dirs) == 1
        runtime_dir = runtime_dirs[0]
        self.models = json.loads((runtime_dir / "agent/models.json").read_text())
        self.extension = (runtime_dir / "tool-budget.js").read_text()
        stdout = json.dumps(
            {
                "type": "message_end",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "done"}],
                    "stopReason": "stop",
                },
            }
        )
        return SandboxResult(0, stdout, "", False, False, _provenance())


def test_pi_declares_and_injects_the_same_model_request_settings(tmp_path: Path) -> None:
    sandbox = PiStubSandbox()

    result = PiHarness(
        sandbox,
        "qwen2.5-coder:1.5b",
        seed=7,
        temperature=0.25,
        context_window=4096,
        max_output_tokens=256,
    ).execute(
        HarnessRequest("task", "fix", "success"),
        ExecutionContext(tmp_path, 30, 1),
    )

    assert result.exit_code == 0
    assert sandbox.models is not None
    model = sandbox.models["providers"]["ollama"]["models"][0]  # type: ignore[index]
    assert model["id"] == "qwen2.5-coder:1.5b"
    assert model["contextWindow"] == 4096
    assert model["maxTokens"] == 256
    assert 'pi.on("before_provider_request"' in sandbox.extension
    assert "seed: 7" in sandbox.extension
    assert "temperature: 0.25" in sandbox.extension
    assert "max_tokens: 256" in sandbox.extension
    assert "CAPABILITY_LAB_TOOL_BUDGET_EXCEEDED max=1" in sandbox.extension


def test_request_settings_and_one_repetition_are_validated_and_hashed(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    model = tmp_path / "model.yaml"
    harness = tmp_path / "harness.yaml"
    experiment = tmp_path / "experiment.yaml"
    defaults.write_text("{}")
    model.write_text(
        json.dumps(
            {
                "model": {
                    "provider": "ollama",
                    "name": "qwen2.5-coder:1.5b",
                    "base_url": "http://127.0.0.1:11434",
                    "temperature": 0.25,
                    "context_window": 4096,
                    "max_output_tokens": 256,
                }
            }
        )
    )
    harness.write_text('{"harness":{"kind":"raw-ollama"}}')
    experiment.write_text('{"repetition_count":1}')

    first = resolve_config(defaults, harness, experiment, model_profile=model)
    model.write_text(model.read_text().replace('"temperature": 0.25', '"temperature": 0.5'))
    second = resolve_config(defaults, harness, experiment, model_profile=model)

    assert first.value.harness.kind == "raw-ollama"
    assert first.value.model is not None
    assert first.value.model.temperature == 0.25
    assert first.value.model.context_window == 4096
    assert first.value.model.max_output_tokens == 256
    assert first.value.repetition_count == 1
    assert first.hash != second.hash

    experiment.write_text('{"repetition_count":2}')
    with pytest.raises(ValueError, match="repetition_count"):
        resolve_config(defaults, harness, experiment, model_profile=model)


def test_bootstrap_selects_raw_ollama_directly() -> None:
    root = Path(__file__).parents[2]
    service = build_service(root)
    config = service.resolve_configuration(
        root / "configs/defaults.yaml",
        root / "configs/harnesses/raw-ollama.yaml",
        root / "configs/experiments/raw-vs-pi.yaml",
        model_profile=root / "configs/models/ollama.yaml",
    )

    harness = service.runtime_builder(config).harness

    assert isinstance(harness, RawOllamaHarness)


def _comparison_inputs() -> tuple[
    ResolvedConfiguration,
    ResolvedConfiguration,
    RunResult,
    RunResult,
]:
    sandbox = SandboxSettings("image:1", 1.0, 512, 128, 256, 1_048_576)
    model = ModelSettings(
        "ollama",
        "qwen2.5-coder:1.5b",
        "http://desktop:11434",
        2.0,
        0.0,
        4096,
        512,
        "a" * 64,
    )
    paths = RuntimePaths("state", "artifacts", "worktrees", "fixtures")
    baseline = ResolvedConfiguration(
        RunSettings(
            "raw-vs-pi",
            "benchmarks/releases/smoke@1.0.1.yaml",
            HarnessSettings("success", "raw-ollama"),
            paths,
            sandbox,
            1,
            1,
            model,
        ),
        "b" * 64,
        (),
    )
    candidate = replace(
        baseline,
        value=replace(baseline.value, harness=HarnessSettings("success", "pi")),
        hash="c" * 64,
    )
    identity = ModelIdentity(
        "ollama",
        model.name,
        "a" * 64,
        "gguf",
        "qwen2",
        "1.5B",
        "Q4_K_M",
        ("completion", "tools"),
        "0.24.0",
    )
    provenance = RunProvenance(
        "benchmark-hash",
        "task-hash",
        baseline.hash,
        "revision",
        "3.13.7",
        "test",
        1,
        "commit",
        False,
        sandbox_provenance=_provenance(),
        model_identity=identity,
    )
    baseline_result = RunResult(
        "baseline-exp",
        "baseline-run",
        "task@1.0.1",
        FailureClassification.EDITING,
        (ScoreResult("protected-test", False),),
        WorkspaceDiff("", ()),
        "baseline-artifacts",
        True,
        duration_ms=10,
        termination_reason="completed",
        budget=ExecutionBudget(30, 1),
        provenance=provenance,
    )
    candidate_result = replace(
        baseline_result,
        experiment_id="candidate-exp",
        run_id="candidate-run",
        duration_ms=12,
        provenance=replace(provenance, config_hash=candidate.hash),
    )
    return baseline, candidate, baseline_result, candidate_result


@pytest.mark.parametrize(
    "dimension",
    [
        "model_name",
        "model_digest",
        "ollama_endpoint",
        "benchmark_hash",
        "task_hash",
        "task_timeout",
        "tool_budget",
        "seed",
        "temperature",
        "context_window",
        "max_output_tokens",
        "sandbox_constraints",
        "sandbox_identity",
        "repetition_count",
    ],
)
def test_comparison_rejects_each_mismatched_equality_dimension(dimension: str) -> None:
    baseline, candidate, baseline_result, candidate_result = _comparison_inputs()
    assert candidate.value.model is not None
    assert candidate_result.provenance is not None
    if dimension == "model_name":
        candidate = replace(
            candidate,
            value=replace(candidate.value, model=replace(candidate.value.model, name="other")),
        )
    elif dimension == "model_digest":
        assert candidate_result.provenance.model_identity is not None
        candidate_result = replace(
            candidate_result,
            provenance=replace(
                candidate_result.provenance,
                model_identity=replace(candidate_result.provenance.model_identity, digest="d" * 64),
            ),
        )
    elif dimension == "ollama_endpoint":
        candidate = replace(
            candidate,
            value=replace(
                candidate.value,
                model=replace(candidate.value.model, base_url="http://127.0.0.1:11434"),
            ),
        )
    elif dimension in {"benchmark_hash", "task_hash"}:
        candidate_result = replace(
            candidate_result,
            provenance=replace(candidate_result.provenance, **{dimension: "different"}),
        )
    elif dimension == "task_timeout":
        candidate_result = replace(candidate_result, budget=ExecutionBudget(31, 1))
    elif dimension == "tool_budget":
        candidate_result = replace(candidate_result, budget=ExecutionBudget(30, 2))
    elif dimension == "seed":
        candidate = replace(candidate, value=replace(candidate.value, seed=2))
    elif dimension in {"temperature", "context_window", "max_output_tokens"}:
        value = (
            0.5 if dimension == "temperature" else 8192 if dimension == "context_window" else 256
        )
        candidate = replace(
            candidate,
            value=replace(
                candidate.value,
                model=replace(candidate.value.model, **{dimension: value}),
            ),
        )
    elif dimension == "sandbox_constraints":
        candidate = replace(
            candidate,
            value=replace(
                candidate.value, sandbox=replace(candidate.value.sandbox, memory_mb=1024)
            ),
        )
    elif dimension == "sandbox_identity":
        assert candidate_result.provenance.sandbox_provenance is not None
        candidate_result = replace(
            candidate_result,
            provenance=replace(
                candidate_result.provenance,
                sandbox_provenance=replace(
                    candidate_result.provenance.sandbox_provenance,
                    identity=replace(
                        candidate_result.provenance.sandbox_provenance.identity,
                        image_id="sha256:different",
                    ),
                ),
            ),
        )
    else:
        candidate = replace(candidate, value=replace(candidate.value, repetition_count=2))

    evidence = comparison_equality(baseline, candidate, baseline_result, candidate_result)

    mismatch = next(item for item in evidence if item.dimension == dimension)
    assert mismatch.equal is False
    assert not all(item.equal for item in evidence)


def test_comparison_rejects_missing_model_or_sandbox_identity_on_both_runs() -> None:
    baseline, candidate, baseline_result, candidate_result = _comparison_inputs()
    assert baseline_result.provenance is not None
    assert candidate_result.provenance is not None
    baseline_result = replace(
        baseline_result,
        provenance=replace(
            baseline_result.provenance,
            model_identity=None,
            sandbox_provenance=None,
        ),
    )
    candidate_result = replace(
        candidate_result,
        provenance=replace(
            candidate_result.provenance,
            model_identity=None,
            sandbox_provenance=None,
        ),
    )

    evidence = comparison_equality(baseline, candidate, baseline_result, candidate_result)

    assert next(item for item in evidence if item.dimension == "model_digest").equal is False
    assert next(item for item in evidence if item.dimension == "sandbox_identity").equal is False


def _copy_project(tmp_path: Path) -> Path:
    root = Path(__file__).parents[2]
    project = tmp_path / "project"
    shutil.copytree(root / "configs", project / "configs")
    shutil.copytree(root / "benchmarks", project / "benchmarks")
    ensure_fixture_repository(
        project / "benchmarks/fixtures/incorrect-function",
        project / ".lab/fixtures/incorrect-function",
    )
    SqliteMetadataRepository(project / ".lab/state.sqlite3").migrate()
    return project


def _assert_no_comparison_state(project: Path) -> None:
    with sqlite3.connect(project / ".lab/state.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM experiments").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (0,)
    artifacts = project / ".lab/artifacts/runs"
    assert not artifacts.exists() or list(artifacts.iterdir()) == []


class ControlledComparisonHarness:
    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult:
        return HarnessResult(
            0,
            events=({"type": "controlled_model_response"},),
            final_response="honest no-edit result",
            sandbox_provenance=_provenance(),
        )


class MissingSandboxIdentityHarness:
    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult:
        return HarnessResult(0, final_response="no sandbox identity")


def test_raw_run_without_sandbox_identity_is_non_reproducible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_project(tmp_path)
    from capability_lab import bootstrap

    class FakeOllama:
        def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
            self.model = model

        def identity(self, expected_digest: str | None = None) -> ModelIdentity:
            return ModelIdentity(
                "ollama",
                self.model,
                "a" * 64,
                "gguf",
                "qwen2",
                "1.5B",
                "Q4_K_M",
                ("completion",),
                "0.24.0",
            )

    monkeypatch.setattr(bootstrap, "OllamaModelAdapter", FakeOllama)
    service = build_service(project)
    runtime_builder = service.runtime_builder
    service.runtime_builder = lambda config: replace(
        runtime_builder(config), harness=MissingSandboxIdentityHarness()
    )

    result = service.run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/raw-ollama.yaml",
        project / "configs/experiments/raw-vs-pi.yaml",
        model_profile=project / "configs/models/ollama.yaml",
    )

    assert result.provenance is not None
    assert result.provenance.reproducible is False


def test_lab_service_records_a_compact_controlled_comparison_and_both_normal_runs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_project(tmp_path)
    from capability_lab import bootstrap

    class FakeOllama:
        def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
            self.model = model

        def identity(self, expected_digest: str | None = None) -> ModelIdentity:
            return ModelIdentity(
                "ollama",
                self.model,
                "a" * 64,
                "gguf",
                "qwen2",
                "1.5B",
                "Q4_K_M",
                ("completion", "tools"),
                "0.24.0",
            )

    monkeypatch.setattr(bootstrap, "OllamaModelAdapter", FakeOllama)
    service = build_service(project)
    runtime_builder = service.runtime_builder
    service.runtime_builder = lambda config: replace(
        runtime_builder(config), harness=ControlledComparisonHarness()
    )

    comparison = service.compare(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/raw-ollama.yaml",
        project / "configs/harnesses/pi.yaml",
        project / "configs/experiments/raw-vs-pi.yaml",
        overrides={"model": {"base_url": "http://desktop:11434"}},
        model_profile=project / "configs/models/ollama.yaml",
    )

    assert comparison.comparable is True
    assert comparison.id.startswith("cmp-")
    assert comparison.baseline.run_id != comparison.candidate.run_id
    assert comparison.baseline.config_hash != comparison.candidate.config_hash
    assert comparison.baseline.classification is FailureClassification.EDITING
    assert comparison.candidate.classification is FailureClassification.EDITING
    assert (
        comparison.baseline.scores
        == comparison.candidate.scores
        == (
            ("protected-test", False),
            ("exact-content", False),
            ("allowed-scope", True),
            ("evaluator-protection", True),
        )
    )
    assert comparison.duration_delta_ms == (
        comparison.candidate.duration_ms - comparison.baseline.duration_ms
    )
    assert comparison.timeout_delta == 0
    assert comparison.intervention_delta == 0
    assert comparison.repetition_count == 1
    assert all(item.equal for item in comparison.equality)
    assert comparison.artifact is not None

    report = json.loads(Path(comparison.artifact.run_path).read_text())
    assert report == json.loads(json.dumps(asdict(comparison), default=str)) | {"artifact": None}
    serialized = json.dumps(report)
    assert "events" not in serialized
    assert "patch" not in serialized
    assert "promotion" not in serialized
    for outcome in (comparison.baseline, comparison.candidate):
        persisted = service.inspect(outcome.run_id)
        assert persisted["classification"] == "editing"
        assert Path(persisted["artifact_dir"]).is_dir()
        assert (Path(persisted["artifact_dir"]) / "harness-execution.json").is_file()
        assert (Path(persisted["artifact_dir"]) / "patch.diff").is_file()
    with sqlite3.connect(project / ".lab/state.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (2,)


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://127.0.0.1:11434",
        "http://example.com:11434",
        "http://127.0.0.1.nip.io:11434",
        "http://169.254.169.254:11434",
    ],
)
def test_lab_service_compare_requires_exact_desktop_endpoint_before_any_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    endpoint: str,
) -> None:
    project = _copy_project(tmp_path)
    from capability_lab import bootstrap

    class FakeOllama:
        def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
            self.model = model

        def identity(self, expected_digest: str | None = None) -> ModelIdentity:
            return ModelIdentity(
                "ollama",
                self.model,
                "a" * 64,
                "gguf",
                "qwen2",
                "1.5B",
                "Q4_K_M",
                ("completion", "tools"),
                "0.24.0",
            )

    monkeypatch.setattr(bootstrap, "OllamaModelAdapter", FakeOllama)
    service = build_service(project)
    config_loader = service.config_loader

    def load_with_endpoint(*args: object, **kwargs: object) -> ResolvedConfiguration:
        config = config_loader(*args, **kwargs)
        assert config.value.model is not None
        return replace(
            config,
            value=replace(config.value, model=replace(config.value.model, base_url=endpoint)),
        )

    service.config_loader = load_with_endpoint
    runtime_builder = service.runtime_builder
    service.runtime_builder = lambda config: replace(
        runtime_builder(config), harness=ControlledComparisonHarness()
    )

    with pytest.raises(ValueError, match="comparison requires OLLAMA_HOST=http://desktop:11434"):
        service.compare(
            project / "configs/defaults.yaml",
            project / "configs/harnesses/raw-ollama.yaml",
            project / "configs/harnesses/pi.yaml",
            project / "configs/experiments/raw-vs-pi.yaml",
            overrides={"model": {"base_url": "http://desktop:11434"}},
            model_profile=project / "configs/models/ollama.yaml",
        )

    _assert_no_comparison_state(project)


@pytest.mark.parametrize(
    ("baseline_profile", "candidate_profile"),
    [
        ("pi.yaml", "raw-ollama.yaml"),
        ("raw-ollama.yaml", "raw-ollama.yaml"),
        ("pi.yaml", "pi.yaml"),
        ("raw-ollama.yaml", "fake.yaml"),
    ],
)
def test_lab_service_compare_requires_the_exact_raw_then_pi_pair_before_any_run(
    tmp_path: Path,
    baseline_profile: str,
    candidate_profile: str,
) -> None:
    project = _copy_project(tmp_path)
    service = build_service(project)
    runtime_calls = 0

    def unexpected_runtime(config: ResolvedConfiguration) -> object:
        nonlocal runtime_calls
        runtime_calls += 1
        raise AssertionError("runtime must not be built")

    service.runtime_builder = unexpected_runtime  # type: ignore[assignment]

    with pytest.raises(
        ValueError,
        match="comparison requires baseline raw-ollama and candidate pi",
    ):
        service.compare(
            project / "configs/defaults.yaml",
            project / f"configs/harnesses/{baseline_profile}",
            project / f"configs/harnesses/{candidate_profile}",
            project / "configs/experiments/raw-vs-pi.yaml",
            overrides={"model": {"base_url": "http://desktop:11434"}},
            model_profile=project / "configs/models/ollama.yaml",
        )

    assert runtime_calls == 0
    _assert_no_comparison_state(project)


def test_comparison_resolves_each_side_once_and_persists_the_same_config_hash(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _copy_project(tmp_path)
    from capability_lab import bootstrap

    class FakeOllama:
        def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
            self.model = model

        def identity(self, expected_digest: str | None = None) -> ModelIdentity:
            return ModelIdentity(
                "ollama",
                self.model,
                "a" * 64,
                "gguf",
                "qwen2",
                "1.5B",
                "Q4_K_M",
                ("completion", "tools"),
                "0.24.0",
            )

    monkeypatch.setattr(bootstrap, "OllamaModelAdapter", FakeOllama)
    service = build_service(project)
    config_loader = service.config_loader
    resolution_counts: dict[str, int] = {}

    def mutating_loader(*args: object, **kwargs: object) -> ResolvedConfiguration:
        profile_path = args[1]
        assert isinstance(profile_path, Path)
        profile = profile_path.name
        resolution_counts[profile] = resolution_counts.get(profile, 0) + 1
        config = config_loader(*args, **kwargs)
        if resolution_counts[profile] > 1:
            stale_hash = "d" * 64 if profile == "raw-ollama.yaml" else "e" * 64
            return replace(config, hash=stale_hash)
        return config

    service.config_loader = mutating_loader
    runtime_builder = service.runtime_builder
    service.runtime_builder = lambda config: replace(
        runtime_builder(config), harness=ControlledComparisonHarness()
    )

    comparison = service.compare(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/raw-ollama.yaml",
        project / "configs/harnesses/pi.yaml",
        project / "configs/experiments/raw-vs-pi.yaml",
        overrides={"model": {"base_url": "http://desktop:11434"}},
        model_profile=project / "configs/models/ollama.yaml",
    )

    assert resolution_counts == {"raw-ollama.yaml": 1, "pi.yaml": 1}
    assert comparison.artifact is not None
    report = json.loads(Path(comparison.artifact.run_path).read_text())
    for label, outcome in (
        ("baseline", comparison.baseline),
        ("candidate", comparison.candidate),
    ):
        artifact_dir = Path(service.inspect(outcome.run_id)["artifact_dir"])
        resolved = json.loads((artifact_dir / "resolved-config.json").read_text())
        provenance = json.loads((artifact_dir / "provenance.json").read_text())
        assert outcome.config_hash == report[label]["config_hash"]
        assert outcome.config_hash == resolved["hash"]
        assert outcome.config_hash == provenance["config_hash"]


def test_compare_command_uses_direct_raw_and_pi_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[Path, ...], dict[str, object] | None, Path | None]] = []
    outcome = ComparisonOutcome(
        "raw-run",
        "a" * 64,
        FailureClassification.EDITING,
        (("protected-test", False),),
        10,
        False,
        0,
    )
    comparison = ComparisonResult(
        "cmp-test",
        outcome,
        replace(outcome, run_id="pi-run", config_hash="b" * 64),
        0,
        0,
        0,
        1,
        (EqualityEvidence("model_digest", "c" * 64, "c" * 64, True),),
        True,
        ArtifactRef("d" * 64, 2, "blob", "comparison.json"),
    )

    class Service:
        def compare(
            self,
            *paths: Path,
            overrides: dict[str, object] | None = None,
            model_profile: Path | None = None,
        ) -> ComparisonResult:
            calls.append((paths, overrides, model_profile))
            return comparison

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", "http://desktop:11434")
    monkeypatch.setattr("capability_lab.cli.main.build_service", lambda root: Service())

    result = CliRunner().invoke(app, ["compare", "configs/experiments/raw-vs-pi.yaml"])

    assert result.exit_code == 0
    assert calls == [
        (
            (
                tmp_path / "configs/defaults.yaml",
                tmp_path / "configs/harnesses/raw-ollama.yaml",
                tmp_path / "configs/harnesses/pi.yaml",
                tmp_path / "configs/experiments/raw-vs-pi.yaml",
            ),
            {"model": {"base_url": "http://desktop:11434"}},
            tmp_path / "configs/models/ollama.yaml",
        )
    ]
    assert "Comparison ID: cmp-test" in result.output
    assert "Baseline run: raw-run" in result.output
    assert "Candidate run: pi-run" in result.output
    assert "Comparable: true" in result.output
    assert "comparison.json" in result.output


@pytest.mark.parametrize(
    "ollama_host",
    [None, "http://127.0.0.1:11434", "http://example.com:11434"],
)
def test_compare_command_requires_exact_desktop_endpoint_before_service_creation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    ollama_host: str | None,
) -> None:
    monkeypatch.chdir(tmp_path)
    if ollama_host is None:
        monkeypatch.delenv("OLLAMA_HOST", raising=False)
    else:
        monkeypatch.setenv("OLLAMA_HOST", ollama_host)
    calls = 0

    def unexpected_build(root: Path) -> object:
        nonlocal calls
        calls += 1
        raise AssertionError("service must not be created")

    monkeypatch.setattr("capability_lab.cli.main.build_service", unexpected_build)

    result = CliRunner().invoke(app, ["compare", "configs/experiments/raw-vs-pi.yaml"])

    assert result.exit_code == 2
    assert "OLLAMA_HOST must be exactly http://desktop:11434" in result.output
    assert calls == 0
    assert list(tmp_path.iterdir()) == []
