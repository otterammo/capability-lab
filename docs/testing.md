# Testing

- Unit tests cover configuration precedence/hashing, benchmark integrity, classification, scorers, and artifact deduplication.
- Contract tests exercise fake-harness and artifact-store behavior through their public interfaces.
- Integration tests cover deterministic fixture creation, Git worktree isolation/cleanup, and SQLAlchemy/SQLite migration persistence.
- The end-to-end test runs the full smoke lifecycle and proves the source fixture stays clean, the worktree disappears, SQLite retains the run, and artifacts exist.

All tests are local and deterministic. They require no model, Docker, GPU, external service, or network.
