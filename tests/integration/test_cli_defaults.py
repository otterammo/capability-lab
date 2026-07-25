import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.adapters.persistence import SqliteMetadataRepository
from capability_lab.adapters.pi_harness import PiHarness
from capability_lab.adapters.sandboxes import DockerSandbox
from capability_lab.bootstrap import build_service
from capability_lab.cli.main import app
from capability_lab.domain.models import DoctorCheck, ModelIdentity
from capability_lab.schemas.config import LabConfig

PROJECT_ROOT = Path(__file__).parents[2]


def _copy_smoke_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
    shutil.copytree(PROJECT_ROOT / "configs", project / "configs")
    shutil.copytree(
        PROJECT_ROOT / "benchmarks",
        project / "benchmarks",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )
    ensure_fixture_repository(
        project / "benchmarks/fixtures/incorrect-function",
        project / ".lab/fixtures/incorrect-function",
    )
    SqliteMetadataRepository(project / ".lab/state.sqlite3").migrate()
    return project


def test_default_diagnostics_validate_active_protected_benchmark(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_smoke_project(tmp_path)
    monkeypatch.chdir(project)
    runner = CliRunner()

    doctor = runner.invoke(app, ["doctor"])
    validate = runner.invoke(app, ["benchmark", "validate"])

    assert doctor.exit_code == 0
    assert validate.exit_code == 0
    assert "smoke@1.0.1" in validate.output
    assert LabConfig().benchmark == "benchmarks/releases/smoke@1.0.1.yaml"

    (project / "benchmarks/protected/expected_example.py").write_text("tampered\n")

    assert runner.invoke(app, ["doctor"]).exit_code != 0
    assert runner.invoke(app, ["benchmark", "validate"]).exit_code != 0


def test_doctor_reports_unavailable_docker_as_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_smoke_project(tmp_path)
    from capability_lab import bootstrap

    def unavailable_docker(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "docker":
            raise FileNotFoundError
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", unavailable_docker)

    docker = next(check for check in build_service(project).doctor() if check.name == "Docker")

    assert docker == DoctorCheck("Docker", False, "CLI unavailable", required=False)


@pytest.mark.parametrize(
    ("stage", "outcome", "expected"),
    [
        (
            "daemon",
            subprocess.TimeoutExpired(["docker", "version"], 2),
            "Docker version test; daemon timeout",
        ),
        (
            "daemon",
            subprocess.CompletedProcess([], 1, stderr="Cannot connect to the Docker daemon"),
            "Docker version test; daemon unavailable",
        ),
        (
            "daemon",
            subprocess.CompletedProcess([], 1, stderr="permission denied"),
            "Docker version test; daemon permission denied",
        ),
        (
            "image",
            subprocess.TimeoutExpired(["docker", "image", "inspect"], 2),
            "Docker version test; server 27.0.0; image check timeout",
        ),
        (
            "image",
            subprocess.CompletedProcess([], 1, stderr="No such image"),
            "Docker version test; server 27.0.0; image missing: capability-lab-sandbox:0.1.0",
        ),
    ],
)
def test_doctor_retains_known_docker_status_after_optional_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    outcome: subprocess.CompletedProcess[str] | subprocess.TimeoutExpired,
    expected: str,
) -> None:
    project = _copy_smoke_project(tmp_path)
    from capability_lab import bootstrap

    def docker_status(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[:2] == ["docker", "--version"]:
            return subprocess.CompletedProcess(argv, 0, stdout="Docker version test")
        if argv[0] != "docker":
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")
        if (stage == "daemon" and argv[1] == "version") or (
            stage == "image" and argv[1:3] == ["image", "inspect"]
        ):
            if isinstance(outcome, subprocess.TimeoutExpired):
                raise outcome
            return outcome
        return subprocess.CompletedProcess(argv, 0, stdout="27.0.0", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", docker_status)

    docker = next(check for check in build_service(project).doctor() if check.name == "Docker")

    assert docker == DoctorCheck("Docker", False, expected, required=False)


def test_doctor_inspects_image_from_effective_repository_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_smoke_project(tmp_path)
    defaults = project / "configs/defaults.yaml"
    defaults.write_text(
        defaults.read_text().replace("capability-lab-sandbox:0.1.0", "test-image:1")
    )
    commands: list[list[str]] = []
    from capability_lab import bootstrap

    def docker_status(argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] == "docker":
            commands.append(argv)
            if argv[:2] == ["docker", "--version"]:
                return subprocess.CompletedProcess(argv, 0, stdout="Docker version test")
            return subprocess.CompletedProcess(argv, 0, stdout="27.0.0", stderr="")
        return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

    monkeypatch.setattr(bootstrap.subprocess, "run", docker_status)

    build_service(project).doctor()

    assert ["docker", "image", "inspect", "--format", "{{.Id}}", "test-image:1"] in commands


def test_doctor_reports_invalid_config_as_optional_docker_status(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_smoke_project(tmp_path)
    (project / "configs/defaults.yaml").write_text("not JSON-compatible YAML")
    from capability_lab import bootstrap

    monkeypatch.setattr(
        bootstrap.subprocess,
        "run",
        lambda argv, **kwargs: subprocess.CompletedProcess(argv, 0, stdout="", stderr=""),
    )

    docker = next(check for check in build_service(project).doctor() if check.name == "Docker")

    assert docker == DoctorCheck("Docker", False, "configuration unavailable", required=False)


def test_doctor_renders_missing_optional_check_without_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "capability_lab.cli.main.build_service",
        lambda root: type(
            "Service",
            (),
            {"doctor": lambda self: (DoctorCheck("Docker", False, "CLI unavailable", False),)},
        )(),
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 0
    assert "optional-missing" in result.output


def test_doctor_fails_for_missing_required_check(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "capability_lab.cli.main.build_service",
        lambda root: type(
            "Service",
            (),
            {"doctor": lambda self: (DoctorCheck("Fixture", False, "tampered"),)},
        )(),
    )

    result = CliRunner().invoke(app, ["doctor"])

    assert result.exit_code == 1
    assert "fail" in result.output


def test_config_resolve_accepts_optional_model_profile(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_smoke_project(tmp_path)
    monkeypatch.chdir(project)

    result = CliRunner().invoke(
        app,
        ["config", "resolve", "--model-profile", "configs/models/ollama.yaml"],
        terminal_width=240,
    )

    assert result.exit_code == 0
    output = result.output.replace("\n", "")
    assert '"base_url":"http://127.0.0.1:11434"' in output
    assert '"name":"qwen2.5-coder:1.5b"' in output


def test_bootstrap_selects_pi_directly_and_keeps_fake_default(tmp_path: Path) -> None:
    project = _copy_smoke_project(tmp_path)
    pi_profile = project / "configs/harnesses/pi.yaml"
    service = build_service(project)

    fake = service.resolve_configuration(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )
    pi = service.resolve_configuration(
        project / "configs/defaults.yaml",
        pi_profile,
        project / "configs/experiments/smoke.yaml",
        model_profile=project / "configs/models/ollama.yaml",
    )

    assert type(service.runtime_builder(fake).harness).__name__ == "FakeHarness"
    pi_harness = service.runtime_builder(pi).harness
    assert isinstance(pi_harness, PiHarness)
    assert isinstance(pi_harness.sandbox, DockerSandbox)
    assert pi_harness.sandbox.ollama_base_url == "http://127.0.0.1:11434"

    pi_without_model = service.resolve_configuration(
        project / "configs/defaults.yaml",
        pi_profile,
        project / "configs/experiments/smoke.yaml",
    )
    with pytest.raises(ValueError, match="requires a model profile"):
        service.runtime_builder(pi_without_model)

    benchmark = service.validate_benchmark(project / "benchmarks/releases/smoke@1.0.1.yaml")
    assert service.provenance_builder(fake, benchmark, benchmark.tasks[0]).harness_version == (
        "fake@1.0.0"
    )
    assert (
        service.provenance_builder(pi_without_model, benchmark, benchmark.tasks[0]).harness_version
        == "pi@0.79.3"
    )


def test_doctor_reports_configured_ollama_identity_as_optional(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = _copy_smoke_project(tmp_path)
    calls: list[tuple[str, str, float, str | None]] = []
    from capability_lab import bootstrap

    class FakeOllama:
        def __init__(self, base_url: str, model: str, timeout_seconds: float) -> None:
            self.base_url = base_url
            self.model = model
            self.timeout_seconds = timeout_seconds

        def identity(self, expected_digest: str | None = None) -> ModelIdentity:
            calls.append((self.base_url, self.model, self.timeout_seconds, expected_digest))
            return ModelIdentity(
                "ollama",
                self.model,
                "a" * 64,
                "gguf",
                "qwen2",
                "1.5B",
                "Q4_K_M",
                ("completion", "tools", "insert"),
                "0.11.4",
            )

    monkeypatch.setattr(bootstrap, "OllamaModelAdapter", FakeOllama)

    ollama = next(check for check in build_service(project).doctor() if check.name == "Ollama")

    assert ollama.required is False
    assert ollama.passed is True
    assert "qwen2.5-coder:1.5b" in ollama.details
    assert "0.11.4" in ollama.details
    assert calls == [("http://127.0.0.1:11434", "qwen2.5-coder:1.5b", 2.0, None)]
