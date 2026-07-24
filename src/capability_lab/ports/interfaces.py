from __future__ import annotations

from typing import BinaryIO, Protocol

from capability_lab.domain.models import (
    ArtifactPayload,
    ArtifactRef,
    BenchmarkRelease,
    EvaluationEvidence,
    ExecutionContext,
    Experiment,
    HarnessRequest,
    HarnessResult,
    PreparedWorkspace,
    RepositorySpec,
    Run,
    RunResult,
    ScoreResult,
    ScorerSpec,
    Task,
    WorkspaceDiff,
)


class Harness(Protocol):
    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult: ...


class WorkspaceManager(Protocol):
    def prepare(
        self, repository: RepositorySpec, revision: str, run_id: str
    ) -> PreparedWorkspace: ...

    def collect_diff(self, workspace: PreparedWorkspace) -> WorkspaceDiff: ...

    def destroy(self, workspace: PreparedWorkspace) -> None: ...


class Scorer(Protocol):
    def score(self, task: Task, evidence: EvaluationEvidence) -> ScoreResult: ...


class MetadataRepository(Protocol):
    def migrate(self) -> None: ...

    def record_benchmark(self, benchmark: BenchmarkRelease) -> None: ...

    def create_experiment(self, experiment: Experiment) -> None: ...

    def create_run(self, run: Run) -> None: ...

    def complete_run(self, result: RunResult) -> None: ...


class ArtifactStore(Protocol):
    def put(self, artifact: ArtifactPayload) -> ArtifactRef: ...

    def open(self, ref: ArtifactRef) -> BinaryIO: ...


class ScorerBuilder(Protocol):
    def __call__(self, spec: ScorerSpec) -> Scorer: ...
