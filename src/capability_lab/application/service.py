from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from capability_lab.domain.models import (
    ArtifactPayload,
    BenchmarkRelease,
    DoctorCheck,
    EvaluationEvidence,
    ExecutionContext,
    Experiment,
    FailureClassification,
    HarnessRequest,
    HarnessResult,
    PreparedWorkspace,
    ResolvedConfiguration,
    Run,
    RunArtifact,
    RunProvenance,
    RunResult,
    ScoreResult,
    ScorerSpec,
    Task,
    WorkspaceDiff,
)
from capability_lab.domain.rules import classify
from capability_lab.ports.interfaces import (
    ArtifactStore,
    Harness,
    MetadataRepository,
    Scorer,
    WorkspaceManager,
)


@dataclass(frozen=True, slots=True)
class Runtime:
    metadata: MetadataRepository
    artifacts: ArtifactStore
    workspaces: WorkspaceManager
    harness: Harness


class LabService:
    def __init__(
        self,
        project_root: Path,
        config_loader: Callable[..., ResolvedConfiguration],
        benchmark_loader: Callable[[Path], BenchmarkRelease],
        fixture_initializer: Callable[[], str],
        runtime_builder: Callable[[ResolvedConfiguration], Runtime],
        scorer_builder: Callable[[ScorerSpec], Scorer],
        provenance_builder: Callable[
            [ResolvedConfiguration, BenchmarkRelease, Task], RunProvenance
        ],
        fixture_validator: Callable[[BenchmarkRelease], None],
        health_checker: Callable[[], tuple[DoctorCheck, ...]],
        run_reader: Callable[[str], Mapping[str, Any]],
    ) -> None:
        self.project_root = project_root
        self.config_loader = config_loader
        self.benchmark_loader = benchmark_loader
        self.fixture_initializer = fixture_initializer
        self.runtime_builder = runtime_builder
        self.scorer_builder = scorer_builder
        self.provenance_builder = provenance_builder
        self.fixture_validator = fixture_validator
        self.health_checker = health_checker
        self.run_reader = run_reader

    def resolve_configuration(
        self,
        defaults: Path,
        harness_profile: Path,
        experiment_path: Path,
        overrides: Mapping[str, Any] | None = None,
    ) -> ResolvedConfiguration:
        return self.config_loader(defaults, harness_profile, experiment_path, overrides)

    def validate_benchmark(self, release_path: Path) -> BenchmarkRelease:
        self.fixture_initializer()
        benchmark = self.benchmark_loader(release_path)
        self.fixture_validator(benchmark)
        return benchmark

    def doctor(self) -> tuple[DoctorCheck, ...]:
        return self.health_checker()

    def inspect(self, run_id: str) -> Mapping[str, Any]:
        return self.run_reader(run_id)

    def run(
        self,
        defaults: Path,
        harness_profile: Path,
        experiment_path: Path,
        overrides: Mapping[str, Any] | None = None,
    ) -> RunResult:
        config = self.resolve_configuration(defaults, harness_profile, experiment_path, overrides)
        benchmark = self.validate_benchmark(self.project_root / config.value.benchmark)
        if len(benchmark.tasks) != 1:
            raise ValueError("the deterministic slice requires exactly one smoke task")
        task = benchmark.tasks[0]
        runtime = self.runtime_builder(config)
        runtime.metadata.migrate()
        runtime.metadata.record_benchmark(benchmark)
        experiment_id = f"exp-{uuid4()}"
        run_id = f"run-{uuid4()}"
        provenance = self.provenance_builder(config, benchmark, task)
        runtime.metadata.create_experiment(
            Experiment(
                experiment_id,
                config.value.name,
                f"{benchmark.id}@{benchmark.version}",
                config.hash,
            )
        )
        runtime.metadata.create_run(
            Run(
                run_id,
                experiment_id,
                f"{task.id}@{task.version}",
                attempt=1,
                budget=task.budget,
                intervention_count=0,
            )
        )

        workspace: PreparedWorkspace | None = None
        diff = WorkspaceDiff("", ())
        scores: tuple[ScoreResult, ...] = ()
        harness_result = HarnessResult(1, failure_kind="environment")
        failure: str | None = None
        cleanup_succeeded = False
        started = perf_counter()
        try:
            workspace = runtime.workspaces.prepare(
                task.repository, task.repository.revision, run_id
            )
            harness_result = runtime.harness.execute(
                HarnessRequest(task.id, task.prompt, config.value.harness.mode),
                ExecutionContext(workspace.path, task.budget.timeout_seconds),
            )
            diff = runtime.workspaces.collect_diff(workspace)
            evidence = EvaluationEvidence(workspace.path, diff)
            for spec in task.scorers:
                try:
                    scores += (self.scorer_builder(spec).score(task, evidence),)
                except Exception as exc:
                    failure = f"scorer {spec.id}: {type(exc).__name__}: {exc}"
                    scores += (ScoreResult(spec.id, False, failure, error=failure),)
                    break
        except Exception as exc:
            failure = f"{type(exc).__name__}: {exc}"
        finally:
            if workspace is not None:
                try:
                    runtime.workspaces.destroy(workspace)
                    cleanup_succeeded = True
                except Exception as exc:
                    cleanup_succeeded = False
                    failure = (
                        f"{failure + '; ' if failure else ''}cleanup: {type(exc).__name__}: {exc}"
                    )
        duration_ms = round((perf_counter() - started) * 1000)

        classification = classify(harness_result, scores)
        if not cleanup_succeeded:
            classification = FailureClassification.ENVIRONMENT
        status = "succeeded" if classification is FailureClassification.SUCCESS else "failed"
        if failure is None:
            failure = _score_error(scores)
        termination_reason = (
            "timeout"
            if harness_result.timed_out
            else failure or harness_result.failure_kind or "completed"
        )
        try:
            artifact_dir, artifacts = self._persist_artifacts(
                runtime.artifacts,
                run_id,
                config,
                provenance,
                harness_result,
                diff,
                scores,
                classification,
                cleanup_succeeded,
                failure,
            )
        except Exception as exc:
            artifact_failure = f"artifact persistence: {type(exc).__name__}: {exc}"
            failure = f"{failure}; {artifact_failure}" if failure else artifact_failure
            classification = FailureClassification.ENVIRONMENT
            status = "failed"
            termination_reason = artifact_failure
            artifact_dir = ""
            artifacts = ()
        result = RunResult(
            experiment_id,
            run_id,
            f"{task.id}@{task.version}",
            classification,
            scores,
            diff,
            artifact_dir,
            cleanup_succeeded,
            failure,
            duration_ms,
            status,
            termination_reason,
            1,
            0,
            task.budget,
            provenance,
            artifacts,
        )
        runtime.metadata.complete_run(result)
        return result

    @staticmethod
    def _persist_artifacts(
        store: ArtifactStore,
        run_id: str,
        config: ResolvedConfiguration,
        provenance: RunProvenance,
        harness: HarnessResult,
        diff: WorkspaceDiff,
        scores: tuple[ScoreResult, ...],
        classification: FailureClassification,
        cleanup_succeeded: bool,
        failure: str | None,
    ) -> tuple[str, tuple[RunArtifact, ...]]:
        payloads: dict[str, Any] = {
            "resolved-config.json": asdict(config),
            "provenance.json": asdict(provenance),
            "harness-execution.json": asdict(harness),
            "patch.diff": diff.patch.encode(errors="surrogateescape"),
            "scorer-results.json": [asdict(score) for score in scores],
        }
        if failure or classification is not FailureClassification.SUCCESS:
            payloads["failure.json"] = {
                "classification": classification.value,
                "message": failure,
            }
        references: dict[str, Any] = {}
        artifacts: list[RunArtifact] = []
        artifact_dir = ""
        for name, value in payloads.items():
            if isinstance(value, bytes):
                content = value
            else:
                content = value.encode() if isinstance(value, str) else _json(value)
            ref = store.put(ArtifactPayload(run_id, name, content))
            references[name] = asdict(ref)
            artifacts.append(RunArtifact(name, ref))
            artifact_dir = str(Path(ref.run_path).parent)
        manifest = {
            "run_id": run_id,
            "classification": classification.value,
            "cleanup_succeeded": cleanup_succeeded,
            "artifacts": references,
        }
        manifest_ref = store.put(ArtifactPayload(run_id, "manifest.json", _json(manifest)))
        artifacts.append(RunArtifact("manifest.json", manifest_ref))
        return artifact_dir, tuple(artifacts)


def _json(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()


def _score_error(scores: tuple[ScoreResult, ...]) -> str | None:
    return next((score.error for score in scores if score.error), None)
