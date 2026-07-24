from pathlib import Path

import pytest

from capability_lab.adapters.benchmark import BenchmarkIntegrityError, validate_fixture_revisions
from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.domain.models import BenchmarkRelease, ExecutionBudget, RepositorySpec, Task


def test_fixture_revision_validation_rejects_mismatch(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "file.py").write_text("value = 1\n")
    fixtures = tmp_path / "fixtures"
    ensure_fixture_repository(template, fixtures / "fixture")
    task = Task(
        "task",
        "1.0.0",
        (),
        RepositorySpec("fixture", "0" * 40),
        "prompt",
        ExecutionBudget(1, 1),
        (),
        "hash",
    )
    benchmark = BenchmarkRelease("smoke", "1.0.0", "development", (task,), "hash")

    with pytest.raises(BenchmarkIntegrityError, match="fixture revision mismatch"):
        validate_fixture_revisions(benchmark, fixtures)
