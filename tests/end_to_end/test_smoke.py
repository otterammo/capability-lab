import hashlib
import json
import sqlite3
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import BinaryIO

import pytest

from capability_lab.bootstrap import build_service
from capability_lab.domain.models import (
    ArtifactPayload,
    ArtifactRef,
    ExecutionContext,
    HarnessRequest,
    HarnessResult,
    ModelIdentity,
)

NON_UTF8_CONTENT = b"\xff\n"


class BinaryFileHarness:
    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult:
        (context.workspace / "raw.bin").write_bytes(NON_UTF8_CONTENT)
        return HarnessResult(exit_code=0)


class BudgetCapturingHarness:
    context: ExecutionContext | None = None

    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult:
        self.context = context
        (context.workspace / "src/example.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
        return HarnessResult(exit_code=0)


class FailingArtifactStore:
    def put(self, artifact: ArtifactPayload) -> ArtifactRef:
        raise OSError("artifact disk unavailable")

    def open(self, ref: ArtifactRef) -> BinaryIO:
        raise AssertionError("artifact reads are not expected")


def test_smoke_run_persists_evidence_and_cleans_worktree(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project)
    service = build_service(project)

    result = service.run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )

    assert result.classification.value == "success"
    assert all(score.passed for score in result.scores)
    assert result.cleanup_succeeded
    assert not (project / ".lab/worktrees" / result.run_id).exists()
    artifact_dir = project / ".lab/artifacts/runs" / result.run_id
    assert {
        "harness-execution.json",
        "manifest.json",
        "patch.diff",
        "provenance.json",
        "resolved-config.json",
        "scorer-results.json",
    } <= {path.name for path in artifact_dir.iterdir()}
    assert (project / ".lab/state.sqlite3").exists()
    with sqlite3.connect(project / ".lab/state.sqlite3") as connection:
        assert connection.execute("SELECT id, version FROM benchmarks").fetchone() == (
            "smoke",
            "1.0.1",
        )
        assert connection.execute("SELECT id, version FROM tasks").fetchone() == (
            "python.fix-incorrect-return",
            "1.0.1",
        )
        run_evidence = connection.execute(
            """SELECT duration_ms, timeout_seconds, max_tool_calls, status,
                      termination_reason, attempt, intervention_count
               FROM runs WHERE id = ?""",
            (result.run_id,),
        ).fetchone()
        assert run_evidence is not None
        assert run_evidence[0] >= 0
        assert run_evidence[1:] == (30, 1, "succeeded", "completed", 1, 0)
        artifacts = connection.execute(
            "SELECT sha256, size, blob_path, run_path FROM artifacts WHERE run_id = ?",
            (result.run_id,),
        ).fetchall()
        assert len(artifacts) >= 6
        assert all(len(row[0]) == 64 and row[1] >= 0 for row in artifacts)
        assert all(row[2] and row[3] for row in artifacts)
        snapshot = json.loads(
            connection.execute(
                "SELECT provenance FROM environment_snapshots WHERE run_id = ?",
                (result.run_id,),
            ).fetchone()[0]
        )
        assert snapshot["benchmark_hash"]
        assert snapshot["task_hash"]
        assert snapshot["config_hash"]
        assert snapshot["sandbox_provenance"] is None
    source = project / ".lab/fixtures/incorrect-function"
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=source, capture_output=True, text=True, check=True
        ).stdout
        == ""
    )


def test_lab_service_passes_the_task_tool_budget_to_the_harness(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project)
    service = build_service(project)
    runtime_builder = service.runtime_builder
    harness = BudgetCapturingHarness()
    service.runtime_builder = lambda config: replace(runtime_builder(config), harness=harness)

    service.run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )

    assert harness.context is not None
    assert harness.context.timeout_seconds == 30
    assert harness.context.max_tool_calls == 1


def test_controlled_failure_is_persisted_and_cleaned_up(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project)
    (project / "configs/harnesses/fake.yaml").write_text('{"harness":{"mode":"failure"}}')

    result = build_service(project).run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )

    assert result.classification.value == "environment"
    assert result.cleanup_succeeded
    assert (project / ".lab/artifacts/runs" / result.run_id / "failure.json").exists()
    assert not (project / ".lab/worktrees" / result.run_id).exists()


def test_artifact_failure_is_terminal_and_preserves_cleanup_evidence(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project)
    service = build_service(project)
    runtime_builder = service.runtime_builder
    service.runtime_builder = lambda config: replace(
        runtime_builder(config), artifacts=FailingArtifactStore()
    )

    result = service.run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )

    persisted = service.inspect(result.run_id)
    assert result.status == "failed"
    assert result.classification.value == "environment"
    assert result.cleanup_succeeded
    assert "artifact persistence: OSError: artifact disk unavailable" in (result.failure or "")
    assert persisted["status"] == "failed"
    assert persisted["classification"] == "environment"
    assert persisted["cleanup_succeeded"] is True
    assert persisted["failure"] == result.failure
    assert not (project / ".lab/worktrees" / result.run_id).exists()


def test_non_utf8_untracked_patch_is_persisted_and_applyable(tmp_path: Path) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project)
    service = build_service(project)
    runtime_builder = service.runtime_builder
    service.runtime_builder = lambda config: replace(
        runtime_builder(config), harness=BinaryFileHarness()
    )

    result = service.run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )

    patch = project / ".lab/artifacts/runs" / result.run_id / "patch.diff"
    patch_bytes = patch.read_bytes()
    target = project / "apply-target"
    subprocess.run(
        ["git", "clone", "--quiet", str(project / ".lab/fixtures/incorrect-function"), str(target)],
        check=True,
    )
    subprocess.run(["git", "-C", str(target), "apply", str(patch)], check=True)

    assert b"+\xff\n" in patch_bytes
    assert (target / "raw.bin").read_bytes() == NON_UTF8_CONTENT
    assert result.cleanup_succeeded


@pytest.mark.parametrize(
    ("scorer", "error_type", "harness_mode"),
    [
        ({"id": "invalid", "type": "invalid"}, "ValueError", "success"),
        (
            {
                "id": "invalid-path",
                "type": "allowed-diff",
                "paths": [None],
            },
            "AttributeError",
            "success",
        ),
        ({"id": "invalid", "type": "invalid"}, "ValueError", "failure"),
    ],
)
def test_scorer_errors_are_persisted_as_evaluator_failures(
    tmp_path: Path, scorer: dict[str, object], error_type: str, harness_mode: str
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project, scorers=[scorer])
    (project / "configs/harnesses/fake.yaml").write_text(
        json.dumps({"harness": {"mode": harness_mode}})
    )

    service = build_service(project)
    result = service.run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )

    artifact_dir = project / ".lab/artifacts/runs" / result.run_id
    failure = json.loads((artifact_dir / "failure.json").read_text())
    persisted = service.inspect(result.run_id)
    assert result.classification.value == "evaluator"
    assert result.cleanup_succeeded
    assert error_type in (result.failure or "")
    assert failure == {"classification": "evaluator", "message": result.failure}
    assert persisted["classification"] == "evaluator"
    assert persisted["failure"] == result.failure
    assert not (project / ".lab/worktrees" / result.run_id).exists()


def test_model_configured_run_persists_resolved_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project)
    model_profile = _write_model_profile(project)
    identity = ModelIdentity(
        "ollama",
        "qwen2.5-coder:1.5b",
        "a" * 64,
        "gguf",
        "qwen2",
        "1.5B",
        "Q4_K_M",
        ("completion", "tools", "insert"),
        "0.11.4",
    )
    from capability_lab import bootstrap

    class FakeOllama:
        def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
            pass

        def identity(self, expected_digest: str | None = None) -> ModelIdentity:
            assert expected_digest == "a" * 64
            return identity

    monkeypatch.setattr(bootstrap, "OllamaModelAdapter", FakeOllama)

    result = build_service(project).run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
        model_profile=model_profile,
    )

    artifact = json.loads(
        (project / ".lab/artifacts/runs" / result.run_id / "provenance.json").read_text()
    )
    with sqlite3.connect(project / ".lab/state.sqlite3") as connection:
        snapshot = json.loads(
            connection.execute(
                "SELECT provenance FROM environment_snapshots WHERE run_id = ?",
                (result.run_id,),
            ).fetchone()[0]
        )
    assert artifact["model_identity"] == snapshot["model_identity"]
    assert artifact["model_identity"]["digest"] == "a" * 64


def test_pi_run_without_sandbox_identity_is_marked_non_reproducible(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project)
    model_profile = _write_model_profile(project)
    pi_profile = project / "configs/harnesses/pi.yaml"
    pi_profile.write_text('{"harness":{"kind":"pi"}}')
    from capability_lab import bootstrap

    class FakeOllama:
        def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
            pass

        def identity(self, expected_digest: str | None = None) -> ModelIdentity:
            return ModelIdentity(
                "ollama",
                "qwen2.5-coder:1.5b",
                "a" * 64,
                "gguf",
                "qwen2",
                "1.5B",
                "Q4_K_M",
                ("completion", "tools", "insert"),
                "0.11.4",
            )

    monkeypatch.setattr(bootstrap, "OllamaModelAdapter", FakeOllama)
    service = build_service(project)
    runtime_builder = service.runtime_builder
    service.runtime_builder = lambda config: replace(
        runtime_builder(config), harness=BudgetCapturingHarness()
    )

    result = service.run(
        project / "configs/defaults.yaml",
        pi_profile,
        project / "configs/experiments/smoke.yaml",
        model_profile=model_profile,
    )

    artifact = json.loads(
        (project / ".lab/artifacts/runs" / result.run_id / "provenance.json").read_text()
    )
    assert artifact["reproducible"] is False


def test_model_identity_failure_prevents_run_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    project.mkdir()
    _write_smoke_project(project)
    model_profile = _write_model_profile(project)
    from capability_lab import bootstrap
    from capability_lab.adapters.models import OllamaModelMissingError

    class MissingOllama:
        def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
            pass

        def identity(self, expected_digest: str | None = None) -> ModelIdentity:
            raise OllamaModelMissingError("model not installed: qwen2.5-coder:1.5b")

    monkeypatch.setattr(bootstrap, "OllamaModelAdapter", MissingOllama)

    with pytest.raises(OllamaModelMissingError, match="model not installed"):
        build_service(project).run(
            project / "configs/defaults.yaml",
            project / "configs/harnesses/fake.yaml",
            project / "configs/experiments/smoke.yaml",
            model_profile=model_profile,
        )

    with sqlite3.connect(project / ".lab/state.sqlite3") as connection:
        assert connection.execute("SELECT COUNT(*) FROM experiments").fetchone() == (0,)
        assert connection.execute("SELECT COUNT(*) FROM runs").fetchone() == (0,)


def _write_model_profile(project: Path) -> Path:
    profile = project / "configs/models/ollama.yaml"
    profile.parent.mkdir(parents=True)
    profile.write_text(
        json.dumps(
            {
                "model": {
                    "provider": "ollama",
                    "name": "qwen2.5-coder:1.5b",
                    "base_url": "http://127.0.0.1:11434",
                    "timeout_seconds": 1,
                    "expected_digest": "a" * 64,
                }
            }
        )
    )
    return profile


def _write_smoke_project(project: Path, scorers: list[dict[str, object]] | None = None) -> None:
    for directory in (
        "configs/harnesses",
        "configs/experiments",
        "benchmarks/tasks",
        "benchmarks/releases",
        "benchmarks/fixtures/incorrect-function/src",
        "benchmarks/protected",
    ):
        (project / directory).mkdir(parents=True)
    (project / "configs/defaults.yaml").write_text(
        json.dumps(
            {
                "paths": {
                    "state": ".lab/state.sqlite3",
                    "artifacts": ".lab/artifacts",
                    "worktrees": ".lab/worktrees",
                    "fixtures": ".lab/fixtures",
                }
            }
        )
    )
    (project / "configs/harnesses/fake.yaml").write_text('{"harness":{"mode":"success"}}')
    (project / "configs/experiments/smoke.yaml").write_text(
        '{"name":"smoke","benchmark":"benchmarks/releases/smoke@1.0.1.yaml"}'
    )
    (project / "benchmarks/fixtures/incorrect-function/src/example.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a - b\n"
    )
    (project / "benchmarks/protected/check.py").write_text(
        "import runpy, sys\nns = runpy.run_path(sys.argv[1] + '/src/example.py')\n"
        "raise SystemExit(0 if ns['add'](2, 3) == 5 else 1)\n"
    )
    (project / "benchmarks/protected/expected.py").write_text(
        "def add(a: int, b: int) -> int:\n    return a + b\n"
    )
    from capability_lab.adapters.fixtures import ensure_fixture_repository

    revision = ensure_fixture_repository(
        project / "benchmarks/fixtures/incorrect-function",
        project / ".lab/fixtures/incorrect-function",
    )
    task_scorers = (
        scorers
        if scorers is not None
        else [
            {"id": "protected-test", "type": "command", "script": "../protected/check.py"},
            {
                "id": "exact-content",
                "type": "exact-content",
                "path": "src/example.py",
                "expected_path": "../protected/expected.py",
            },
            {
                "id": "allowed-scope",
                "type": "allowed-diff",
                "paths": ["src/example.py"],
            },
            {"id": "forbidden", "type": "forbidden-path", "paths": [".evaluation"]},
        ]
    )
    task = project / "benchmarks/tasks/python.fix-incorrect-return@1.0.1.yaml"
    protected_dependencies = []
    for scorer in task_scorers:
        for key in ("script", "expected_path"):
            if key in scorer:
                dependency = str(scorer[key])
                protected_dependencies.append(
                    {
                        "path": dependency,
                        "sha256": hashlib.sha256(
                            (task.parent / dependency).read_bytes()
                        ).hexdigest(),
                    }
                )
    task.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "python.fix-incorrect-return",
                "version": "1.0.1",
                "capabilities": ["patch.generation", "change.verification"],
                "repository": {"fixture": "incorrect-function", "revision": revision},
                "prompt": "Fix the incorrect add function.",
                "budget": {"timeout_seconds": 30, "max_tool_calls": 1},
                "scorers": task_scorers,
                "protected_dependencies": protected_dependencies,
            }
        )
    )
    release = project / "benchmarks/releases/smoke@1.0.1.yaml"
    release.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/python.fix-incorrect-return@1.0.1.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
