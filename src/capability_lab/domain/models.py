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
    MODEL_RUNTIME = "model_runtime"
    TOOL_EXECUTION = "tool_execution"
    EVALUATOR = "evaluator"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class HarnessSettings:
    mode: str
    kind: str = "fake"


@dataclass(frozen=True, slots=True)
class RuntimePaths:
    state: str
    artifacts: str
    worktrees: str
    fixtures: str


@dataclass(frozen=True, slots=True)
class SandboxSettings:
    image: str
    cpus: float
    memory_mb: int
    pids: int
    nofile: int
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class ModelSettings:
    provider: str
    name: str
    base_url: str
    timeout_seconds: float
    temperature: float
    context_window: int
    max_output_tokens: int
    expected_digest: str | None = None


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    provider: str
    name: str
    digest: str
    format: str
    family: str
    parameter_size: str
    quantization_level: str
    capabilities: tuple[str, ...]
    server_version: str | None


@dataclass(frozen=True, slots=True)
class RunSettings:
    name: str
    benchmark: str
    harness: HarnessSettings
    paths: RuntimePaths
    sandbox: SandboxSettings
    seed: int
    repetition_count: int
    model: ModelSettings | None = None


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
    required: bool = True


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
    max_tool_calls: int | None = None


@dataclass(frozen=True, slots=True)
class SandboxIdentity:
    image_id: str
    docker_version: str
    user: str
    dockerfile_sha256: str


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    name: str
    endpoint: str | None
    external_access: bool


@dataclass(frozen=True, slots=True)
class SandboxProvenance:
    identity: SandboxIdentity
    network_policy: NetworkPolicy


@dataclass(frozen=True, slots=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    output_limited: bool
    provenance: SandboxProvenance


@dataclass(frozen=True, slots=True)
class HarnessResult:
    exit_code: int
    events: tuple[dict[str, Any], ...] = ()
    final_response: str = ""
    failure_kind: str | None = None
    timed_out: bool = False
    output_limited: bool = False
    sandbox_provenance: SandboxProvenance | None = None


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
    sandbox_provenance: SandboxProvenance | None = None
    model_identity: ModelIdentity | None = None
    reproducible: bool = True


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


@dataclass(frozen=True, slots=True)
class EqualityEvidence:
    dimension: str
    baseline: Any
    candidate: Any
    equal: bool


@dataclass(frozen=True, slots=True)
class ComparisonOutcome:
    run_id: str
    config_hash: str
    classification: FailureClassification
    scores: tuple[tuple[str, bool], ...]
    duration_ms: int
    timed_out: bool
    intervention_count: int


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    id: str
    baseline: ComparisonOutcome
    candidate: ComparisonOutcome
    duration_delta_ms: int
    timeout_delta: int
    intervention_delta: int
    repetition_count: int
    equality: tuple[EqualityEvidence, ...]
    comparable: bool
    artifact: ArtifactRef | None = None
