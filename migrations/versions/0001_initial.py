"""Initial experiment metadata schema."""

from __future__ import annotations

from typing import Any

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade(connection: Any | None = None) -> None:
    if connection is None:
        from alembic import op

        connection = op.get_bind()
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS experiments (
            id VARCHAR PRIMARY KEY,
            name VARCHAR NOT NULL,
            benchmark_id VARCHAR NOT NULL,
            config_hash VARCHAR(64) NOT NULL,
            created_at DATETIME NOT NULL
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id VARCHAR PRIMARY KEY,
            experiment_id VARCHAR NOT NULL REFERENCES experiments(id),
            task_id VARCHAR NOT NULL,
            started_at DATETIME NOT NULL,
            classification VARCHAR,
            cleanup_succeeded BOOLEAN,
            artifact_dir TEXT,
            failure TEXT,
            changed_paths TEXT
        )
        """
    )
    connection.exec_driver_sql(
        """
        CREATE TABLE IF NOT EXISTS scores (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id VARCHAR NOT NULL REFERENCES runs(id),
            scorer_id VARCHAR NOT NULL,
            passed BOOLEAN NOT NULL,
            details TEXT NOT NULL,
            category VARCHAR,
            error TEXT
        )
        """
    )


def downgrade() -> None:
    from alembic import op

    op.drop_table("scores")
    op.drop_table("runs")
    op.drop_table("experiments")
