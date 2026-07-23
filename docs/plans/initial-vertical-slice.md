# Initial Vertical Slice Implementation Plan

**Goal:** Prove one deterministic, offline experiment run from layered configuration through protected scoring, persisted evidence, reporting, and worktree cleanup.

**Architecture:** Keep the standard-library domain and protocols at the center. One application service orchestrates concrete local adapters for Git, a fake harness, deterministic scorers, SQLite/Alembic metadata, and content-addressed filesystem artifacts; Typer only validates command input and wires the service.

**Constraints:** Python 3.13.7, `uv`, no network or live model, exact subprocess argument arrays, no speculative packages, no commits, and no hidden evaluator content in harness inputs or reports.

## Tasks

- [x] Add the Python project, CLI entry point, Make targets, import rules, and offline CI workflow.
- [x] Test first, then implement typed domain records, boundary protocols, layered Pydantic configuration, canonical JSON, and stable hashes.
- [x] Test first, then implement immutable benchmark validation, deterministic fixture creation, Git worktree lifecycle, fake harness, scorers, and failure classification.
- [x] Test first, then implement content-addressed artifacts, SQLAlchemy metadata, and the initial Alembic migration.
- [x] Implement the lifecycle service and CLI commands; prove the complete smoke path with integration and end-to-end tests.
- [x] Add concise operating, architecture, testing, methodology, benchmark, decision, and ADR documentation matching the shipped behavior.
- [x] Run setup, formatting, all checks, smoke, direct end-to-end Pytest, and final Git diff checks; record exact evidence.
