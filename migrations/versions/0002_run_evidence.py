"""Add benchmark, artifact, environment, and run evidence metadata."""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "benchmarks",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("channel", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
    )
    op.create_table(
        "tasks",
        sa.Column("key", sa.String(), primary_key=True),
        sa.Column("id", sa.String(), nullable=False),
        sa.Column("version", sa.String(), nullable=False),
        sa.Column("content_hash", sa.String(length=64), nullable=False),
    )
    op.create_table(
        "benchmark_tasks",
        sa.Column("benchmark_key", sa.String(), sa.ForeignKey("benchmarks.key"), primary_key=True),
        sa.Column("task_key", sa.String(), sa.ForeignKey("tasks.key"), primary_key=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
    )
    with op.batch_alter_table("experiments") as batch:
        batch.alter_column("id", existing_type=sa.String(), nullable=False)
    with op.batch_alter_table("runs") as batch:
        batch.alter_column("id", existing_type=sa.String(), nullable=False)
        batch.add_column(sa.Column("duration_ms", sa.Integer(), nullable=True))
        batch.add_column(
            sa.Column("timeout_seconds", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(
            sa.Column("max_tool_calls", sa.Integer(), nullable=False, server_default="0")
        )
        batch.add_column(sa.Column("status", sa.String(), nullable=False, server_default="legacy"))
        batch.add_column(sa.Column("termination_reason", sa.Text(), nullable=True))
        batch.add_column(sa.Column("attempt", sa.Integer(), nullable=False, server_default="1"))
        batch.add_column(
            sa.Column("intervention_count", sa.Integer(), nullable=False, server_default="0")
        )
    with op.batch_alter_table("scores") as batch:
        batch.alter_column("id", existing_type=sa.Integer(), nullable=False)
    with op.batch_alter_table("runs") as batch:
        batch.alter_column("timeout_seconds", existing_type=sa.Integer(), server_default=None)
        batch.alter_column("max_tool_calls", existing_type=sa.Integer(), server_default=None)
        batch.alter_column("status", existing_type=sa.String(), server_default=None)
        batch.alter_column("attempt", existing_type=sa.Integer(), server_default=None)
        batch.alter_column("intervention_count", existing_type=sa.Integer(), server_default=None)
    op.create_table(
        "artifacts",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("size", sa.Integer(), nullable=False),
        sa.Column("blob_path", sa.Text(), nullable=False),
        sa.Column("run_path", sa.Text(), nullable=False),
        sa.UniqueConstraint("run_id", "name"),
    )
    op.create_table(
        "environment_snapshots",
        sa.Column("run_id", sa.String(), sa.ForeignKey("runs.id"), primary_key=True),
        sa.Column("provenance", sa.Text(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("environment_snapshots")
    op.drop_table("artifacts")
    with op.batch_alter_table("scores") as batch:
        batch.alter_column("id", existing_type=sa.Integer(), nullable=True)
    with op.batch_alter_table("runs") as batch:
        batch.drop_column("intervention_count")
        batch.drop_column("attempt")
        batch.drop_column("termination_reason")
        batch.drop_column("status")
        batch.drop_column("max_tool_calls")
        batch.drop_column("timeout_seconds")
        batch.drop_column("duration_ms")
        batch.alter_column("id", existing_type=sa.String(), nullable=True)
    with op.batch_alter_table("experiments") as batch:
        batch.alter_column("id", existing_type=sa.String(), nullable=True)
    op.drop_table("benchmark_tasks")
    op.drop_table("tasks")
    op.drop_table("benchmarks")
