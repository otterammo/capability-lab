from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from alembic.util.exc import CommandError
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship

from capability_lab.domain.models import BenchmarkRelease, Experiment, Run, RunResult


class PersistenceError(RuntimeError):
    pass


class Base(DeclarativeBase):
    pass


class BenchmarkRow(Base):
    __tablename__ = "benchmarks"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    channel: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class TaskRow(Base):
    __tablename__ = "tasks"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    id: Mapped[str] = mapped_column(String, nullable=False)
    version: Mapped[str] = mapped_column(String, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)


class BenchmarkTaskRow(Base):
    __tablename__ = "benchmark_tasks"

    benchmark_key: Mapped[str] = mapped_column(ForeignKey("benchmarks.key"), primary_key=True)
    task_key: Mapped[str] = mapped_column(ForeignKey("tasks.key"), primary_key=True)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)


class ExperimentRow(Base):
    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    benchmark_id: Mapped[str] = mapped_column(String, nullable=False)
    config_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    runs: Mapped[list[RunRow]] = relationship(back_populates="experiment")


class RunRow(Base):
    __tablename__ = "runs"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), nullable=False)
    task_id: Mapped[str] = mapped_column(String, nullable=False)
    started_at: Mapped[Any] = mapped_column(DateTime(timezone=True), nullable=False)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    timeout_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_tool_calls: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False)
    termination_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, nullable=False)
    intervention_count: Mapped[int] = mapped_column(Integer, nullable=False)
    classification: Mapped[str | None] = mapped_column(String, nullable=True)
    cleanup_succeeded: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    artifact_dir: Mapped[str | None] = mapped_column(Text, nullable=True)
    failure: Mapped[str | None] = mapped_column(Text, nullable=True)
    changed_paths: Mapped[str | None] = mapped_column(Text, nullable=True)
    experiment: Mapped[ExperimentRow] = relationship(back_populates="runs")
    scores: Mapped[list[ScoreRow]] = relationship(
        back_populates="run", cascade="all, delete-orphan"
    )


class ScoreRow(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    scorer_id: Mapped[str] = mapped_column(String, nullable=False)
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    details: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    run: Mapped[RunRow] = relationship(back_populates="scores")


class ArtifactRow(Base):
    __tablename__ = "artifacts"
    __table_args__ = (UniqueConstraint("run_id", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), nullable=False)
    name: Mapped[str] = mapped_column(String, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    size: Mapped[int] = mapped_column(Integer, nullable=False)
    blob_path: Mapped[str] = mapped_column(Text, nullable=False)
    run_path: Mapped[str] = mapped_column(Text, nullable=False)


class EnvironmentSnapshotRow(Base):
    __tablename__ = "environment_snapshots"

    run_id: Mapped[str] = mapped_column(ForeignKey("runs.id"), primary_key=True)
    provenance: Mapped[str] = mapped_column(Text, nullable=False)


class SqliteMetadataRepository:
    def __init__(self, path: Path, migrations: Path | None = None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path.resolve()
        self.migrations = (
            migrations or Path(__file__).resolve().parents[3] / "migrations"
        ).resolve()
        self.engine = create_engine(f"sqlite:///{self.path}")

    def migrate(self) -> None:
        config = Config()
        config.set_main_option("script_location", str(self.migrations))
        config.set_main_option("sqlalchemy.url", f"sqlite:///{self.path}")
        try:
            command.upgrade(config, "head")
        except (CommandError, SQLAlchemyError) as exc:
            raise PersistenceError(f"database migration failed: {exc}") from exc

    def record_benchmark(self, benchmark: BenchmarkRelease) -> None:
        benchmark_key = f"{benchmark.id}@{benchmark.version}"
        with Session(self.engine) as session:
            existing = session.get(BenchmarkRow, benchmark_key)
            values = (
                benchmark.id,
                benchmark.version,
                benchmark.channel,
                benchmark.content_hash,
            )
            if existing is None:
                session.add(
                    BenchmarkRow(
                        key=benchmark_key,
                        id=benchmark.id,
                        version=benchmark.version,
                        channel=benchmark.channel,
                        content_hash=benchmark.content_hash,
                    )
                )
            elif (existing.id, existing.version, existing.channel, existing.content_hash) != values:
                raise PersistenceError(f"benchmark record changed: {benchmark_key}")

            for ordinal, task in enumerate(benchmark.tasks):
                task_key = f"{task.id}@{task.version}"
                stored_task = session.get(TaskRow, task_key)
                task_values = (task.id, task.version, task.content_hash)
                if stored_task is None:
                    session.add(
                        TaskRow(
                            key=task_key,
                            id=task.id,
                            version=task.version,
                            content_hash=task.content_hash,
                        )
                    )
                elif (stored_task.id, stored_task.version, stored_task.content_hash) != task_values:
                    raise PersistenceError(f"task record changed: {task_key}")
                relation = session.get(BenchmarkTaskRow, (benchmark_key, task_key))
                if relation is None:
                    session.add(
                        BenchmarkTaskRow(
                            benchmark_key=benchmark_key,
                            task_key=task_key,
                            ordinal=ordinal,
                        )
                    )
                elif relation.ordinal != ordinal:
                    raise PersistenceError(
                        f"benchmark task ordering changed: {benchmark_key} / {task_key}"
                    )
            session.commit()

    def create_experiment(self, experiment: Experiment) -> None:
        with Session(self.engine) as session:
            session.add(
                ExperimentRow(
                    id=experiment.id,
                    name=experiment.name,
                    benchmark_id=experiment.benchmark_id,
                    config_hash=experiment.config_hash,
                    created_at=experiment.created_at,
                )
            )
            session.commit()

    def create_run(self, run: Run) -> None:
        with Session(self.engine) as session:
            session.add(
                RunRow(
                    id=run.id,
                    experiment_id=run.experiment_id,
                    task_id=run.task_id,
                    started_at=run.started_at,
                    timeout_seconds=run.budget.timeout_seconds,
                    max_tool_calls=run.budget.max_tool_calls,
                    status="running",
                    attempt=run.attempt,
                    intervention_count=run.intervention_count,
                )
            )
            session.commit()

    def complete_run(self, result: RunResult) -> None:
        with Session(self.engine) as session:
            row = session.get(RunRow, result.run_id)
            if row is None:
                raise PersistenceError(f"unknown run: {result.run_id}")
            if row.status != "running":
                raise PersistenceError(f"run already completed: {result.run_id}")
            row.classification = result.classification.value
            row.duration_ms = result.duration_ms
            row.timeout_seconds = result.budget.timeout_seconds
            row.max_tool_calls = result.budget.max_tool_calls
            row.status = result.status
            row.termination_reason = result.termination_reason
            row.attempt = result.attempt
            row.intervention_count = result.intervention_count
            row.cleanup_succeeded = result.cleanup_succeeded
            row.artifact_dir = result.artifact_dir
            row.failure = result.failure
            row.changed_paths = json.dumps(result.diff.changed_paths)
            row.scores.extend(
                ScoreRow(
                    scorer_id=score.scorer_id,
                    passed=score.passed,
                    details=score.details,
                    category=score.category,
                    error=score.error,
                )
                for score in result.scores
            )
            row_artifacts = [
                ArtifactRow(
                    run_id=result.run_id,
                    name=artifact.name,
                    sha256=artifact.ref.sha256,
                    size=artifact.ref.size,
                    blob_path=artifact.ref.blob_path,
                    run_path=artifact.ref.run_path,
                )
                for artifact in result.artifacts
            ]
            session.add_all(row_artifacts)
            if result.provenance is not None:
                session.add(
                    EnvironmentSnapshotRow(
                        run_id=result.run_id,
                        provenance=json.dumps(asdict(result.provenance), sort_keys=True),
                    )
                )
            session.commit()

    def get_run(self, run_id: str) -> dict[str, Any]:
        with Session(self.engine) as session:
            row = session.get(RunRow, run_id)
            if row is None:
                raise PersistenceError(f"unknown run: {run_id}")
            return {
                "id": row.id,
                "experiment_id": row.experiment_id,
                "task_id": row.task_id,
                "duration_ms": row.duration_ms,
                "timeout_seconds": row.timeout_seconds,
                "max_tool_calls": row.max_tool_calls,
                "status": row.status,
                "termination_reason": row.termination_reason,
                "attempt": row.attempt,
                "intervention_count": row.intervention_count,
                "classification": row.classification,
                "cleanup_succeeded": row.cleanup_succeeded,
                "artifact_dir": row.artifact_dir,
                "failure": row.failure,
                "changed_paths": json.loads(row.changed_paths or "[]"),
                "scores": [
                    {
                        "id": score.scorer_id,
                        "passed": score.passed,
                        "details": score.details,
                        "category": score.category,
                        "error": score.error,
                    }
                    for score in row.scores
                ],
            }

    def schema_revision(self) -> str | None:
        with self.engine.connect() as connection:
            try:
                row = connection.exec_driver_sql("SELECT version_num FROM alembic_version").first()
            except SQLAlchemyError:
                return None
        return None if row is None else str(row[0])
