import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext

from capability_lab.adapters.persistence import Base, PersistenceError, SqliteMetadataRepository
from capability_lab.domain.models import (
    ArtifactRef,
    BenchmarkRelease,
    ExecutionBudget,
    Experiment,
    FailureClassification,
    RepositorySpec,
    Run,
    RunArtifact,
    RunProvenance,
    RunResult,
    ScorerSpec,
    Task,
    WorkspaceDiff,
)

MIGRATIONS = Path(__file__).parents[2] / "migrations"


def _upgrade(database: Path, revision: str) -> None:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.set_main_option("sqlalchemy.url", f"sqlite:///{database}")
    command.upgrade(config, revision)


def _database_columns(database: Path) -> dict[str, set[str]]:
    with sqlite3.connect(database) as connection:
        tables = connection.execute(
            """SELECT name FROM sqlite_master
               WHERE type = 'table'
                 AND name NOT LIKE 'sqlite_%'
                 AND name != 'alembic_version'"""
        ).fetchall()
        return {
            table[0]: {
                column[1] for column in connection.execute(f'PRAGMA table_info("{table[0]}")')
            }
            for table in tables
        }


def _schema_diffs(repository: SqliteMetadataRepository) -> list[object]:
    with repository.engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_server_default": True})
        return compare_metadata(context, Base.metadata)


def test_sqlite_migration_and_run_round_trip(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    repository = SqliteMetadataRepository(database)
    repository.migrate()
    task = Task(
        "task",
        "1.0.1",
        ("patch.generation",),
        RepositorySpec("fixture", "revision"),
        "Fix it.",
        ExecutionBudget(30, 2),
        (ScorerSpec("test", "command"),),
        "task-hash",
    )
    benchmark = BenchmarkRelease("smoke", "1.0.1", "development", (task,), "bench-hash")
    provenance = RunProvenance(
        "bench-hash",
        "task-hash",
        "config-hash",
        "revision",
        "3.13.13",
        "test-platform",
        0,
        "platform-commit",
        False,
    )
    artifact = RunArtifact(
        "patch.diff",
        ArtifactRef("a" * 64, 5, "blobs/aa/hash", "runs/run/patch.diff"),
    )
    experiment = Experiment("exp", "smoke", "smoke@1.0.1", "config-hash")
    run = Run("run", "exp", "task@1.0.1", attempt=1, budget=task.budget)
    result = RunResult(
        "exp",
        "run",
        "task@1.0.1",
        FailureClassification.SUCCESS,
        (),
        WorkspaceDiff("", ()),
        "artifacts/runs/run",
        True,
        duration_ms=125,
        status="succeeded",
        termination_reason="completed",
        attempt=1,
        intervention_count=0,
        budget=task.budget,
        provenance=provenance,
        artifacts=(artifact,),
    )

    repository.record_benchmark(benchmark)
    repository.create_experiment(experiment)
    repository.create_run(run)
    repository.complete_run(result)

    stored = repository.get_run("run")
    assert stored["classification"] == "success"
    assert stored["cleanup_succeeded"] is True

    with pytest.raises(PersistenceError, match="run already completed: run"):
        repository.complete_run(
            replace(
                result,
                classification=FailureClassification.ENVIRONMENT,
                status="failed",
                failure="replacement evidence",
                artifacts=(),
                provenance=None,
            )
        )

    assert repository.get_run("run") == stored
    assert repository.schema_revision() == "0002"
    assert _database_columns(database) == {
        table.name: set(table.columns.keys()) for table in Base.metadata.sorted_tables
    }
    assert _schema_diffs(repository) == []

    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT id, version, channel, content_hash FROM benchmarks"
        ).fetchone() == ("smoke", "1.0.1", "development", "bench-hash")
        assert connection.execute("SELECT id, version, content_hash FROM tasks").fetchone() == (
            "task",
            "1.0.1",
            "task-hash",
        )
        assert connection.execute(
            "SELECT benchmark_key, task_key, ordinal FROM benchmark_tasks"
        ).fetchone() == ("smoke@1.0.1", "task@1.0.1", 0)
        assert connection.execute(
            """SELECT duration_ms, timeout_seconds, max_tool_calls, status,
                      termination_reason, attempt, intervention_count
               FROM runs WHERE id = 'run'"""
        ).fetchone() == (125, 30, 2, "succeeded", "completed", 1, 0)
        stored_artifact = connection.execute(
            "SELECT name, sha256, size, blob_path, run_path FROM artifacts"
        ).fetchone()
        assert stored_artifact == (
            "patch.diff",
            "a" * 64,
            5,
            "blobs/aa/hash",
            "runs/run/patch.diff",
        )
        snapshot = json.loads(
            connection.execute(
                "SELECT provenance FROM environment_snapshots WHERE run_id = 'run'"
            ).fetchone()[0]
        )
        assert snapshot["benchmark_hash"] == "bench-hash"
        assert snapshot["platform_commit"] == "platform-commit"


def test_upgrade_from_populated_0001_preserves_rows_and_adds_evidence_schema(
    tmp_path: Path,
) -> None:
    database = tmp_path / "state.sqlite3"
    _upgrade(database, "0001")
    with sqlite3.connect(database) as connection:
        connection.execute(
            "INSERT INTO experiments VALUES (?, ?, ?, ?, ?)",
            ("legacy-exp", "legacy", "smoke@1.0.0", "config-hash", "2026-07-23"),
        )
        connection.execute(
            "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "legacy-run",
                "legacy-exp",
                "task@1.0.0",
                "2026-07-23",
                "success",
                1,
                "artifacts/runs/legacy-run",
                None,
                "[]",
            ),
        )
        connection.execute(
            "INSERT INTO scores (run_id, scorer_id, passed, details) VALUES (?, ?, ?, ?)",
            ("legacy-run", "legacy-score", 1, "passed"),
        )

    SqliteMetadataRepository(database).migrate()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == ("0002",)
        assert connection.execute("SELECT id, name, benchmark_id FROM experiments").fetchone() == (
            "legacy-exp",
            "legacy",
            "smoke@1.0.0",
        )
        assert connection.execute(
            """SELECT id, experiment_id, task_id, classification,
                      timeout_seconds, max_tool_calls, status, attempt,
                      intervention_count
               FROM runs"""
        ).fetchone() == (
            "legacy-run",
            "legacy-exp",
            "task@1.0.0",
            "success",
            0,
            0,
            "legacy",
            1,
            0,
        )
        assert connection.execute(
            "SELECT run_id, scorer_id, passed, details FROM scores"
        ).fetchone() == ("legacy-run", "legacy-score", 1, "passed")
    assert _database_columns(database) == {
        table.name: set(table.columns.keys()) for table in Base.metadata.sorted_tables
    }
    assert _schema_diffs(SqliteMetadataRepository(database)) == []


def test_migrate_does_not_relabel_unknown_newer_revision(tmp_path: Path) -> None:
    database = tmp_path / "state.sqlite3"
    repository = SqliteMetadataRepository(database)
    repository.migrate()
    with sqlite3.connect(database) as connection:
        connection.execute("UPDATE alembic_version SET version_num = 'future-revision'")

    with pytest.raises(PersistenceError, match="future-revision"):
        repository.migrate()

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT version_num FROM alembic_version").fetchone() == (
            "future-revision",
        )
