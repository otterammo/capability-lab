import shutil
from pathlib import Path

import pytest
from typer.testing import CliRunner

from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.adapters.persistence import SqliteMetadataRepository
from capability_lab.cli.main import app
from capability_lab.schemas.config import LabConfig

PROJECT_ROOT = Path(__file__).parents[2]


def _copy_smoke_project(tmp_path: Path) -> Path:
    project = tmp_path / "project"
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
