# Development

```bash
make setup
make format
make lint
make typecheck
make test-unit
make test-contract
make test-integration
make test-end-to-end
make architecture
make check
make smoke
make diff-check
```

`make setup` syncs the editable project and dependencies with Python 3.13.7, applies the Alembic migration, creates the deterministic fixture repository, and runs `lab doctor`. Runtime state is disposable except for evidence the user wants to retain; remove specific run state deliberately, never with a broad recursive command.

For non-trivial behavior, write one focused failing test, observe the expected failure, implement the minimum behavior, and rerun it before wider checks.
