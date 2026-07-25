import json
import os
import socket
import subprocess
from dataclasses import asdict
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capability_lab.adapters.models import OllamaError, OllamaModelAdapter
from capability_lab.bootstrap import build_service
from capability_lab.cli.main import app
from capability_lab.domain.models import FailureClassification, RunResult, WorkspaceDiff

PROJECT_ROOT = Path(__file__).parents[2]
EXPECTED_SCORERS = (
    "protected-test",
    "exact-content",
    "allowed-scope",
    "evaluator-protection",
)


def _assert_pi_lifecycle(events: list[dict[str, object]]) -> None:
    event_types = [event.get("type") for event in events]
    assert "session" in event_types
    assert "agent_start" in event_types
    assert "agent_end" in event_types
    attempt = next(
        (
            index
            for index, event_type in enumerate(event_types)
            if event_type
            in {"message_end", "turn_end", "tool_execution_start", "tool_execution_end"}
        ),
        None,
    )
    assert attempt is not None
    assert (
        event_types.index("session")
        < event_types.index("agent_start")
        < attempt
        < event_types.index("agent_end")
    )


def test_live_acceptance_rejects_harness_failure_without_pi_lifecycle() -> None:
    with pytest.raises(AssertionError):
        _assert_pi_lifecycle([{"type": "harness_failure", "kind": "environment"}])


def test_live_acceptance_allows_real_pi_session_ordering() -> None:
    _assert_pi_lifecycle(
        [
            {"type": "session"},
            {"type": "agent_start"},
            {"type": "message_end"},
            {"type": "agent_end"},
        ]
    )


def test_run_command_uses_pi_and_ollama_profiles(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[tuple[tuple[Path, ...], dict[str, object] | None, Path | None]] = []

    class Service:
        def run(
            self,
            *paths: Path,
            overrides: dict[str, object] | None = None,
            model_profile: Path | None = None,
        ) -> RunResult:
            calls.append((paths, overrides, model_profile))
            return RunResult(
                "exp",
                "run",
                "task",
                FailureClassification.SUCCESS,
                (),
                WorkspaceDiff("", ()),
                "artifacts",
                True,
            )

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("OLLAMA_HOST", "http://desktop:11434")
    monkeypatch.setattr("capability_lab.cli.main.build_service", lambda root: Service())

    runner = CliRunner()
    result = runner.invoke(app, ["run", "configs/experiments/pi-smoke.yaml"])
    smoke = runner.invoke(app, ["smoke"])

    assert result.exit_code == 0
    assert smoke.exit_code == 0
    assert calls == [
        (
            (
                tmp_path / "configs/defaults.yaml",
                tmp_path / "configs/harnesses/pi.yaml",
                tmp_path / "configs/experiments/pi-smoke.yaml",
            ),
            {"model": {"base_url": "http://desktop:11434"}},
            tmp_path / "configs/models/ollama.yaml",
        ),
        (
            (
                tmp_path / "configs/defaults.yaml",
                tmp_path / "configs/harnesses/fake.yaml",
                tmp_path / "configs/experiments/smoke.yaml",
            ),
            None,
            None,
        ),
    ]


def _docker(command: list[str]) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=5, check=False)
    except (FileNotFoundError, subprocess.TimeoutExpired) as error:
        pytest.fail(f"live prerequisite unavailable: {error}")
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _sandbox_inventory() -> tuple[str, str, str]:
    return (
        _docker(
            [
                "docker",
                "ps",
                "-a",
                "--filter",
                "label=capability-lab.sandbox",
                "--format",
                "{{.ID}}",
            ]
        ),
        _docker(
            [
                "docker",
                "volume",
                "ls",
                "--filter",
                "label=capability-lab.sandbox",
                "--format",
                "{{.Name}}",
            ]
        ),
        _docker(
            [
                "docker",
                "network",
                "ls",
                "--filter",
                "label=capability-lab.sandbox",
                "--format",
                "{{.ID}}",
            ]
        ),
    )


def test_live_pi_smoke_retains_evidence_and_cleans_every_boundary(
    pytestconfig: pytest.Config,
) -> None:
    if not pytestconfig.getoption("--run-live"):
        pytest.skip("run with --run-live")

    model_config = json.loads((PROJECT_ROOT / "configs/models/ollama.yaml").read_text())["model"]
    assert model_config["base_url"] == "http://127.0.0.1:11434"
    ollama_host = "http://desktop:11434"
    assert os.environ.get("OLLAMA_HOST") == ollama_host
    desktop_addresses = sorted(
        {
            str(record[4][0])
            for record in socket.getaddrinfo("desktop", 11434, type=socket.SOCK_STREAM)
            if record[0] == socket.AF_INET
        }
    )
    assert len(desktop_addresses) == 1
    try:
        model_identity = OllamaModelAdapter(
            ollama_host,
            model_config["name"],
            model_config["timeout_seconds"],
        ).identity(model_config.get("expected_digest"))
    except OllamaError as error:
        pytest.fail(f"local Ollama prerequisite unavailable: {error}")
    image = json.loads((PROJECT_ROOT / "configs/defaults.yaml").read_text())["sandbox"]["image"]
    assert _docker(["docker", "image", "inspect", "--format", "{{.Id}}", image])
    assert _sandbox_inventory() == ("", "", "")

    result = build_service(PROJECT_ROOT).run(
        PROJECT_ROOT / "configs/defaults.yaml",
        PROJECT_ROOT / "configs/harnesses/pi.yaml",
        PROJECT_ROOT / "configs/experiments/pi-smoke.yaml",
        {"model": {"base_url": ollama_host}},
        model_profile=PROJECT_ROOT / "configs/models/ollama.yaml",
    )

    artifact_dir = PROJECT_ROOT / ".lab/artifacts/runs" / result.run_id
    harness = json.loads((artifact_dir / "harness-execution.json").read_text())
    provenance = json.loads((artifact_dir / "provenance.json").read_text())
    scores = json.loads((artifact_dir / "scorer-results.json").read_text())
    resolved = json.loads((artifact_dir / "resolved-config.json").read_text())
    manifest = json.loads((artifact_dir / "manifest.json").read_text())
    persisted = build_service(PROJECT_ROOT).inspect(result.run_id)

    _assert_pi_lifecycle(harness["events"])
    assert (artifact_dir / "patch.diff").read_bytes() == result.diff.patch.encode(
        errors="surrogateescape"
    )
    assert tuple(score["scorer_id"] for score in scores) == EXPECTED_SCORERS
    assert tuple(score.scorer_id for score in result.scores) == EXPECTED_SCORERS
    assert scores == [asdict(score) for score in result.scores]
    assert provenance["model_identity"] == json.loads(json.dumps(asdict(model_identity)))
    assert resolved["value"]["model"]["base_url"] == ollama_host
    assert provenance["sandbox_provenance"]["identity"]["image_id"]
    assert provenance["sandbox_provenance"]["network_policy"] == {
        "name": "ollama-only",
        "endpoint": f"http://{desktop_addresses[0]}:11434",
        "external_access": False,
    }
    assert provenance["reproducible"] is True
    assert persisted["termination_reason"] == result.termination_reason
    assert persisted["failure"] == result.failure
    assert persisted["cleanup_succeeded"] is True
    assert persisted["scores"] == [
        {
            "id": score.scorer_id,
            "passed": score.passed,
            "details": score.details,
            "category": score.category,
            "error": score.error,
        }
        for score in result.scores
    ]
    assert manifest["cleanup_succeeded"] is True
    if result.classification is not FailureClassification.SUCCESS:
        failure = json.loads((artifact_dir / "failure.json").read_text())
        assert failure["classification"] == result.classification.value

    source = PROJECT_ROOT / ".lab/fixtures/incorrect-function"
    assert _docker(["docker", "ps", "--filter", "label=capability-lab.sandbox", "-q"]) == ""
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=source,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        == ""
    )
    assert not (PROJECT_ROOT / ".lab/worktrees" / result.run_id).exists()
    assert list((PROJECT_ROOT / ".lab/worktrees").glob("**/.capability-lab-pi-*")) == []
    assert _sandbox_inventory() == ("", "", "")
