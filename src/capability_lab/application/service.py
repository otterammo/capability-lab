from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from capability_lab.domain.models import (
    ArtifactPayload,
    BenchmarkRelease,
    ComparisonOutcome,
    ComparisonResult,
    DoctorCheck,
    EqualityEvidence,
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
        *,
        model_profile: Path | None = None,
    ) -> ResolvedConfiguration:
        return self.config_loader(
            defaults,
            harness_profile,
            experiment_path,
            overrides,
            model_profile=model_profile,
        )

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
        *,
        model_profile: Path | None = None,
    ) -> RunResult:
        config = self.resolve_configuration(
            defaults,
            harness_profile,
            experiment_path,
            overrides,
            model_profile=model_profile,
        )
        return self._run_resolved(config)

    def _run_resolved(self, config: ResolvedConfiguration) -> RunResult:
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
        if config.value.harness.kind != "fake":
            provenance = replace(provenance, reproducible=False)
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
                ExecutionContext(
                    workspace.path,
                    task.budget.timeout_seconds,
                    task.budget.max_tool_calls,
                ),
            )
            if harness_result.sandbox_provenance is not None:
                provenance = replace(
                    provenance,
                    sandbox_provenance=harness_result.sandbox_provenance,
                    reproducible=provenance.model_identity is not None,
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

    def compare(
        self,
        defaults: Path,
        baseline_harness_profile: Path,
        candidate_harness_profile: Path,
        experiment_path: Path,
        overrides: Mapping[str, Any] | None = None,
        *,
        model_profile: Path | None = None,
    ) -> ComparisonResult:
        baseline_config = self.resolve_configuration(
            defaults,
            baseline_harness_profile,
            experiment_path,
            overrides,
            model_profile=model_profile,
        )
        candidate_config = self.resolve_configuration(
            defaults,
            candidate_harness_profile,
            experiment_path,
            overrides,
            model_profile=model_profile,
        )
        if any(
            config.value.model is None or config.value.model.base_url != "http://desktop:11434"
            for config in (baseline_config, candidate_config)
        ):
            raise ValueError("comparison requires OLLAMA_HOST=http://desktop:11434")
        if (
            baseline_config.value.harness.kind,
            candidate_config.value.harness.kind,
        ) != ("raw-ollama", "pi"):
            raise ValueError("comparison requires baseline raw-ollama and candidate pi")
        baseline = self._run_resolved(baseline_config)
        candidate = self._run_resolved(candidate_config)
        equality = comparison_equality(baseline_config, candidate_config, baseline, candidate)
        baseline_outcome = _comparison_outcome(baseline, baseline_config.hash)
        candidate_outcome = _comparison_outcome(candidate, candidate_config.hash)
        comparison = ComparisonResult(
            id=f"cmp-{uuid4()}",
            baseline=baseline_outcome,
            candidate=candidate_outcome,
            duration_delta_ms=candidate.duration_ms - baseline.duration_ms,
            timeout_delta=int(candidate_outcome.timed_out) - int(baseline_outcome.timed_out),
            intervention_delta=(candidate.intervention_count - baseline.intervention_count),
            repetition_count=baseline_config.value.repetition_count,
            equality=equality,
            comparable=all(item.equal for item in equality),
        )
        ref = self.runtime_builder(baseline_config).artifacts.put(
            ArtifactPayload(
                comparison.id,
                "comparison.json",
                _json(asdict(comparison)),
            )
        )
        return replace(comparison, artifact=ref)

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


def comparison_equality(
    baseline_config: ResolvedConfiguration,
    candidate_config: ResolvedConfiguration,
    baseline: RunResult,
    candidate: RunResult,
) -> tuple[EqualityEvidence, ...]:
    baseline_model = baseline_config.value.model
    candidate_model = candidate_config.value.model
    baseline_provenance = baseline.provenance
    candidate_provenance = candidate.provenance
    dimensions = (
        (
            "model_name",
            None if baseline_model is None else baseline_model.name,
            None if candidate_model is None else candidate_model.name,
        ),
        (
            "model_digest",
            None
            if baseline_provenance is None or baseline_provenance.model_identity is None
            else baseline_provenance.model_identity.digest,
            None
            if candidate_provenance is None or candidate_provenance.model_identity is None
            else candidate_provenance.model_identity.digest,
        ),
        (
            "ollama_endpoint",
            None if baseline_model is None else baseline_model.base_url,
            None if candidate_model is None else candidate_model.base_url,
        ),
        (
            "benchmark_hash",
            None if baseline_provenance is None else baseline_provenance.benchmark_hash,
            None if candidate_provenance is None else candidate_provenance.benchmark_hash,
        ),
        (
            "task_hash",
            None if baseline_provenance is None else baseline_provenance.task_hash,
            None if candidate_provenance is None else candidate_provenance.task_hash,
        ),
        ("task_timeout", baseline.budget.timeout_seconds, candidate.budget.timeout_seconds),
        ("tool_budget", baseline.budget.max_tool_calls, candidate.budget.max_tool_calls),
        ("seed", baseline_config.value.seed, candidate_config.value.seed),
        (
            "temperature",
            None if baseline_model is None else baseline_model.temperature,
            None if candidate_model is None else candidate_model.temperature,
        ),
        (
            "context_window",
            None if baseline_model is None else baseline_model.context_window,
            None if candidate_model is None else candidate_model.context_window,
        ),
        (
            "max_output_tokens",
            None if baseline_model is None else baseline_model.max_output_tokens,
            None if candidate_model is None else candidate_model.max_output_tokens,
        ),
        (
            "sandbox_constraints",
            asdict(baseline_config.value.sandbox),
            asdict(candidate_config.value.sandbox),
        ),
        (
            "sandbox_identity",
            None
            if baseline_provenance is None or baseline_provenance.sandbox_provenance is None
            else asdict(baseline_provenance.sandbox_provenance),
            None
            if candidate_provenance is None or candidate_provenance.sandbox_provenance is None
            else asdict(candidate_provenance.sandbox_provenance),
        ),
        (
            "repetition_count",
            baseline_config.value.repetition_count,
            candidate_config.value.repetition_count,
        ),
    )
    return tuple(
        EqualityEvidence(
            name,
            baseline_value,
            candidate_value,
            baseline_value is not None
            and candidate_value is not None
            and baseline_value == candidate_value,
        )
        for name, baseline_value, candidate_value in dimensions
    )


def _comparison_outcome(result: RunResult, config_hash: str) -> ComparisonOutcome:
    return ComparisonOutcome(
        run_id=result.run_id,
        config_hash=config_hash,
        classification=result.classification,
        scores=tuple((score.scorer_id, score.passed) for score in result.scores),
        duration_ms=result.duration_ms,
        timed_out=result.termination_reason == "timeout",
        intervention_count=result.intervention_count,
    )
