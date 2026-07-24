import pytest

from capability_lab.domain.models import FailureClassification, HarnessResult, ScoreResult
from capability_lab.domain.rules import classify


@pytest.mark.parametrize(
    ("harness", "scores", "expected"),
    [
        (HarnessResult(exit_code=0), (ScoreResult("exact", True),), "success"),
        (HarnessResult(exit_code=1, failure_kind="environment"), (), "environment"),
        (HarnessResult(exit_code=124, timed_out=True), (), "timeout"),
        (HarnessResult(exit_code=0), (ScoreResult("exact", False, category="editing"),), "editing"),
        (
            HarnessResult(exit_code=0),
            (ScoreResult("command", False, category="verification"),),
            "verification",
        ),
        (HarnessResult(exit_code=0), (ScoreResult("broken", False, error="boom"),), "evaluator"),
        (
            HarnessResult(exit_code=1, failure_kind="environment"),
            (ScoreResult("broken", False, error="boom"),),
            "evaluator",
        ),
        (HarnessResult(exit_code=1), (), "unknown"),
    ],
)
def test_classification_is_deterministic(
    harness: HarnessResult,
    scores: tuple[ScoreResult, ...],
    expected: str,
) -> None:
    assert classify(harness, scores) == FailureClassification(expected)


def test_score_errors_can_be_used_as_failure_messages() -> None:
    from capability_lab.application.service import _score_error

    assert (
        _score_error((ScoreResult("command", False, error="scorer timed out"),))
        == "scorer timed out"
    )
