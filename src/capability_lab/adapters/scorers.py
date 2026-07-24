from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path

from capability_lab.domain.models import EvaluationEvidence, ScoreResult, ScorerSpec, Task


def _within(path: str, roots: tuple[str, ...]) -> bool:
    return any(path == root or path.startswith(f"{root.rstrip('/')}/") for root in roots)


@dataclass(frozen=True, slots=True)
class ExactContentScorer:
    id: str
    path: str
    expected: str

    def score(self, task: Task, evidence: EvaluationEvidence) -> ScoreResult:
        try:
            actual = (evidence.workspace / self.path).read_text()
        except OSError as exc:
            return ScoreResult(self.id, False, str(exc), "editing")
        return ScoreResult(self.id, actual == self.expected, "exact content match", "editing")


@dataclass(frozen=True, slots=True)
class AllowedDiffScorer:
    id: str
    allowed_paths: tuple[str, ...]

    def score(self, task: Task, evidence: EvaluationEvidence) -> ScoreResult:
        unexpected = tuple(
            path for path in evidence.diff.changed_paths if not _within(path, self.allowed_paths)
        )
        return ScoreResult(self.id, not unexpected, f"unexpected paths: {unexpected}", "editing")


@dataclass(frozen=True, slots=True)
class ForbiddenPathScorer:
    id: str
    paths: tuple[str, ...]

    def score(self, task: Task, evidence: EvaluationEvidence) -> ScoreResult:
        forbidden = tuple(path for path in evidence.diff.changed_paths if _within(path, self.paths))
        return ScoreResult(self.id, not forbidden, f"forbidden paths: {forbidden}", "editing")


@dataclass(frozen=True, slots=True)
class CommandScorer:
    id: str
    command: tuple[str, ...]

    def score(self, task: Task, evidence: EvaluationEvidence) -> ScoreResult:
        with tempfile.TemporaryDirectory(prefix="capability-lab-scorer-") as tmpdir:
            scoring_workspace = Path(tmpdir) / "workspace"
            shutil.copytree(evidence.workspace, scoring_workspace)
            command = tuple(
                part.replace("{workspace}", str(scoring_workspace)) for part in self.command
            )
            if command and command[0] == "python":
                command = (sys.executable, *command[1:])
            try:
                result = subprocess.run(
                    command,
                    cwd=scoring_workspace,
                    capture_output=True,
                    text=True,
                    timeout=task.budget.timeout_seconds if task is not None else 30,
                )
            except (OSError, subprocess.TimeoutExpired) as exc:
                return ScoreResult(self.id, False, error=str(exc), category="verification")
        details = (
            f"exit_code={result.returncode}; "
            f"stdout_bytes={len(result.stdout.encode(errors='surrogateescape'))}; "
            f"stderr_bytes={len(result.stderr.encode(errors='surrogateescape'))}"
        )
        return ScoreResult(self.id, result.returncode == 0, details, "verification")


def build_scorer(
    spec: ScorerSpec,
) -> ExactContentScorer | AllowedDiffScorer | ForbiddenPathScorer | CommandScorer:
    if spec.type == "command":
        return CommandScorer(spec.id, ("python", str(spec.options["script"]), "{workspace}"))
    if spec.type == "exact-content":
        expected = spec.options.get("expected")
        if expected is None:
            expected = Path(str(spec.options["expected_path"])).read_text()
        return ExactContentScorer(spec.id, str(spec.options["path"]), str(expected))
    if spec.type == "allowed-diff":
        return AllowedDiffScorer(spec.id, tuple(spec.options["paths"]))
    if spec.type == "forbidden-path":
        return ForbiddenPathScorer(spec.id, tuple(spec.options["paths"]))
    raise ValueError(f"unknown scorer type: {spec.type}")
