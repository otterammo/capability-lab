from capability_lab.domain.models import FailureClassification, HarnessResult, ScoreResult


def classify(
    harness: HarnessResult,
    scores: tuple[ScoreResult, ...],
) -> FailureClassification:
    if any(score.error for score in scores):
        return FailureClassification.EVALUATOR
    if harness.timed_out:
        return FailureClassification.TIMEOUT
    if harness.failure_kind == "environment":
        return FailureClassification.ENVIRONMENT
    if any(not score.passed and score.category == "editing" for score in scores):
        return FailureClassification.EDITING
    if any(not score.passed and score.category == "verification" for score in scores):
        return FailureClassification.VERIFICATION
    if harness.exit_code == 0 and scores and all(score.passed for score in scores):
        return FailureClassification.SUCCESS
    return FailureClassification.UNKNOWN
