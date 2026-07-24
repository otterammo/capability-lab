from pathlib import Path

from capability_lab.adapters.scorers import (
    AllowedDiffScorer,
    CommandScorer,
    ExactContentScorer,
    ForbiddenPathScorer,
)
from capability_lab.domain.models import (
    EvaluationEvidence,
    ExecutionBudget,
    RepositorySpec,
    Task,
    WorkspaceDiff,
)

SCORING_TASK = Task(
    "test-task",
    "1.0.0",
    (),
    RepositorySpec("fixture", "revision"),
    "",
    ExecutionBudget(30, 1),
    (),
    "",
)


def evidence(tmp_path: Path, changed: tuple[str, ...] = ("src/example.py",)) -> EvaluationEvidence:
    (tmp_path / "src").mkdir()
    (tmp_path / "src/example.py").write_text("def add(a, b):\n    return a + b\n")
    task = tmp_path / ".evaluation"
    task.mkdir()
    (task / "check.py").write_text("raise SystemExit(0)\n")
    return EvaluationEvidence(tmp_path, WorkspaceDiff("patch", changed))


def test_deterministic_scorers(tmp_path: Path) -> None:
    observed = evidence(tmp_path)

    assert (
        ExactContentScorer("exact", "src/example.py", "def add(a, b):\n    return a + b\n")
        .score(
            SCORING_TASK,
            observed,
        )
        .passed
    )
    assert (
        AllowedDiffScorer("scope", ("src/example.py",))
        .score(
            SCORING_TASK,
            observed,
        )
        .passed
    )
    assert (
        ForbiddenPathScorer("forbidden", (".evaluation",))
        .score(
            SCORING_TASK,
            observed,
        )
        .passed
    )
    assert (
        CommandScorer("command", ("python", ".evaluation/check.py"))
        .score(
            SCORING_TASK,
            observed,
        )
        .passed
    )


def test_forbidden_path_scorer_fails_on_hidden_change(tmp_path: Path) -> None:
    observed = evidence(tmp_path, (".evaluation/check.py",))

    result = ForbiddenPathScorer("forbidden", (".evaluation",)).score(
        SCORING_TASK,
        observed,
    )

    assert not result.passed
    assert result.category == "editing"


def test_command_scorer_redacts_protected_output(tmp_path: Path) -> None:
    observed = evidence(tmp_path)
    (tmp_path / ".evaluation/check.py").write_text(
        "import sys\n"
        "print('hidden expected stdout')\n"
        "print('hidden expected stderr', file=sys.stderr)\n"
        "raise SystemExit(1)\n"
    )

    result = CommandScorer("command", ("python", ".evaluation/check.py")).score(
        SCORING_TASK,
        observed,
    )

    assert not result.passed
    assert "hidden expected" not in result.details
    assert result.details.startswith("exit_code=1; stdout_bytes=")
    assert "stderr_bytes=" in result.details


def test_command_scorer_does_not_mutate_evidence_workspace(tmp_path: Path) -> None:
    observed = evidence(tmp_path)
    (tmp_path / ".evaluation/check.py").write_text(
        "from pathlib import Path\n"
        "Path('src/example.py').write_text('mutated by scorer')\n"
        "raise SystemExit(0)\n"
    )

    result = CommandScorer("command", ("python", ".evaluation/check.py")).score(
        SCORING_TASK,
        observed,
    )

    assert result.passed
    assert (tmp_path / "src/example.py").read_text() == "def add(a, b):\n    return a + b\n"


def test_command_scorer_copy_does_not_expose_git_metadata(tmp_path: Path) -> None:
    observed = evidence(tmp_path)
    (tmp_path / ".git").write_text("gitdir: /original/worktree/gitdir\n")
    (tmp_path / ".evaluation/check.py").write_text(
        "from pathlib import Path\nraise SystemExit(0 if not Path('.git').exists() else 1)\n"
    )

    result = CommandScorer("command", ("python", ".evaluation/check.py")).score(
        SCORING_TASK,
        observed,
    )

    assert result.passed
