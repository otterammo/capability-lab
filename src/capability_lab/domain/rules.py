import re

from capability_lab.domain.models import FailureClassification, HarnessResult, ScoreResult


def ollama_hostname(base_url: str) -> str:
    match = re.fullmatch(r"http://([^/:?#]+):11434", base_url)
    if match is None:
        raise ValueError("must be plain http://HOST:11434")
    host = match.group(1)
    if not all(
        re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
        for label in host.split(".")
    ):
        raise ValueError("host must contain only valid DNS labels")
    normalized = host.lower()
    if normalized not in {"127.0.0.1", "localhost", "desktop"}:
        raise ValueError("host must be 127.0.0.1, localhost, or desktop")
    return normalized


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
    if harness.failure_kind == "model_runtime":
        return FailureClassification.MODEL_RUNTIME
    if harness.failure_kind == "tool_budget":
        return FailureClassification.TOOL_EXECUTION
    if any(not score.passed and score.category == "editing" for score in scores):
        return FailureClassification.EDITING
    if any(not score.passed and score.category == "verification" for score in scores):
        return FailureClassification.VERIFICATION
    if harness.exit_code == 0 and scores and all(score.passed for score in scores):
        return FailureClassification.SUCCESS
    return FailureClassification.UNKNOWN
