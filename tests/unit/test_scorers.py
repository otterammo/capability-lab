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
