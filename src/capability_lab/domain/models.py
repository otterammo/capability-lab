from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any


class FailureClassification(StrEnum):
    SUCCESS = "success"
    EDITING = "editing"
    VERIFICATION = "verification"
    ENVIRONMENT = "environment"
    TIMEOUT = "timeout"
    EVALUATOR = "evaluator"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    mode: str


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    state: str
    artifacts: str
    worktrees: str
    fixtures: str


@dataclass(frozen=True, slots=True)
class RunSettings:
    name: str
    benchmark: str
    harness: HarnessSettings
    paths: RuntimePaths
    seed: int


@dataclass(frozen=True, slots=True)
class ResolvedConfiguration:
    value: RunSettings
    hash: str
    provenance: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class DoctorCheck:
    name: str
    passed: bool
    details: str


@dataclass(frozen=True, slots=True)
class ExecutionBudget:
    timeout_seconds: int
    max_tool_calls: int


@dataclass(frozen=True, slots=True)
class RepositorySpec:
    fixture: str
    revision: str


@dataclass(frozen=True, slots=True)
class ScorerSpec:
    id: str
    type: str
    options: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class Task:
    id: str
    version: str
    capabilities: tuple[str, ...]
    repository: RepositorySpec
    prompt: str
    budget: ExecutionBudget
    scorers: tuple[ScorerSpec, ...]
    content_hash: str


@dataclass(frozen=True, slots=True)
class BenchmarkRelease:
    id: str
    version: str
    channel: str
    tasks: tuple[Task, ...]
    content_hash: str


@dataclass(frozen=True, slots=True)
class Experiment:
    id: str
    name: str
    benchmark_id: str
    config_hash: str
    created_at: datetime = field(default_factory=lambda: datetime.now(UTC))


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    experiment_id: str
    task_id: str
    started_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    attempt: int = 1
    budget: ExecutionBudget = field(default_factory=lambda: ExecutionBudget(0, 0))
    intervention_count: int = 0


@dataclass(frozen=True, slots=True)
class PreparedWorkspace:
    path: Path
    repository: Path
    revision: str


@dataclass(frozen=True, slots=True)
class WorkspaceDiff:
    patch: str
    changed_paths: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class HarnessRequest:
    task_id: str
    prompt: str
    mode: str


@dataclass(frozen=True, slots=True)
class ExecutionContext:
    workspace: Path
    timeout_seconds: int


@dataclass(frozen=True, slots=True)
class HarnessResult:
    exit_code: int
    events: tuple[dict[str, Any], ...] = ()
    final_response: str = ""
    failure_kind: str | None = None
    timed_out: bool = False


@dataclass(frozen=True, slots=True)
class EvaluationEvidence:
    workspace: Path
    diff: WorkspaceDiff


@dataclass(frozen=True, slots=True)
class ScoreResult:
    scorer_id: str
    passed: bool
    details: str = ""
    category: str | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ArtifactPayload:
    run_id: str
    name: str
    content: bytes


@dataclass(frozen=True, slots=True)
class ArtifactRef:
    sha256: str
    size: int
    blob_path: str
    run_path: str


@dataclass(frozen=True, slots=True)
class RunArtifact:
    name: str
    ref: ArtifactRef


@dataclass(frozen=True, slots=True)
class RunProvenance:
    benchmark_hash: str
    task_hash: str
    config_hash: str
    fixture_revision: str
    python_version: str
    platform: str
    seed: int
    platform_commit: str
    platform_dirty: bool
    harness_version: str = "fake@1.0.0"
    scorer_version: str = "deterministic@1.0.0"
    network: str = "not_enforced"


@dataclass(frozen=True, slots=True)
class RunResult:
    experiment_id: str
    run_id: str
    task_id: str
    classification: FailureClassification
    scores: tuple[ScoreResult, ...]
    diff: WorkspaceDiff
    artifact_dir: str
    cleanup_succeeded: bool
    failure: str | None = None
    duration_ms: int = 0
    status: str = "completed"
    termination_reason: str = "completed"
    attempt: int = 1
    intervention_count: int = 0
    budget: ExecutionBudget = field(default_factory=lambda: ExecutionBudget(0, 0))
    provenance: RunProvenance | None = None
    artifacts: tuple[RunArtifact, ...] = ()
