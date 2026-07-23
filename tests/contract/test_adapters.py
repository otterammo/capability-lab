from pathlib import Path

from capability_lab.adapters.artifacts import FilesystemArtifactStore
from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.adapters.harnesses import FakeHarness
from capability_lab.adapters.persistence import SqliteMetadataRepository
from capability_lab.adapters.scorers import ExactContentScorer
from capability_lab.adapters.workspaces import GitWorktreeManager
from capability_lab.domain.models import (
    ArtifactPayload,
    EvaluationEvidence,
    ExecutionBudget,
    ExecutionContext,
    HarnessRequest,
    RepositorySpec,
    Task,
    WorkspaceDiff,
)
from capability_lab.ports.interfaces import MetadataRepository, Scorer, WorkspaceManager


def test_fake_harness_contract_success_and_controlled_failure(tmp_path: Path) -> None:
    source = tmp_path / "src/example.py"
    source.parent.mkdir()
    source.write_text("wrong\n")
    harness = FakeHarness()

    success = harness.execute(
        HarnessRequest("task", "fix", "success"), ExecutionContext(tmp_path, 10)
    )
    failure = harness.execute(
        HarnessRequest("task", "fix", "failure"), ExecutionContext(tmp_path, 10)
    )

    assert success.exit_code == 0
    assert "return a + b" in source.read_text()
    assert failure.exit_code != 0
    assert failure.failure_kind == "environment"


def test_artifact_contract_round_trip(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    ref = store.put(ArtifactPayload("run", "result.json", b"{}"))

    with store.open(ref) as stream:
        assert stream.read() == b"{}"


def test_workspace_manager_contract_lifecycle(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "example.py").write_text("wrong\n")
    revision = ensure_fixture_repository(template, tmp_path / "fixtures/fixture")
    manager: WorkspaceManager = GitWorktreeManager(tmp_path / "worktrees", tmp_path / "fixtures")

    workspace = manager.prepare(RepositorySpec("fixture", revision), revision, "run")
    (workspace.path / "example.py").write_text("right\n")
    assert manager.collect_diff(workspace).changed_paths == ("example.py",)
    manager.destroy(workspace)
    assert not workspace.path.exists()


def test_scorer_contract_returns_structured_result(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("right\n")
    scorer: Scorer = ExactContentScorer("exact", "example.py", "right\n")
    task = Task(
        "task",
        "1.0.0",
        (),
        RepositorySpec("fixture", "revision"),
        "",
        ExecutionBudget(30, 1),
        (),
        "hash",
    )

    result = scorer.score(task, EvaluationEvidence(tmp_path, WorkspaceDiff("", ())))

    assert result.scorer_id == "exact"
    assert result.passed


def test_metadata_repository_contract_migrates(tmp_path: Path) -> None:
    repository: MetadataRepository = SqliteMetadataRepository(tmp_path / "state.sqlite3")

    repository.migrate()

    assert SqliteMetadataRepository(tmp_path / "state.sqlite3").schema_revision() == "0002"
