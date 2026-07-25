# Testing

- Unit tests cover configuration, benchmark integrity, classification, scorers, artifact deduplication, sandbox policy, ownership, limits, and cleanup failures.
- Contract tests exercise fake-harness and artifact-store behavior through their public interfaces.
- Integration tests cover fixtures, Git worktrees, SQLite persistence, raw/Pi request contracts, comparison evidence, and Docker resource isolation.
- End-to-end tests cover the model-free smoke lifecycle. The Pi live acceptance is separately opt-in and retains full evidence for honest success or failure.

`make check` is deterministic and requires no model, Docker, GPU, external service, or network. `make test-docker` explicitly builds the pinned image and runs Docker isolation, stub-provider, ownership-collision, limit, and cleanup tests without live model generation. The live Pi acceptance requires an already-installed local model and must be requested explicitly:

```bash
OLLAMA_HOST=http://desktop:11434 uv run pytest tests/end_to_end/test_pi_smoke.py --run-live
```

Normal verification skips that live test. No test pulls a model or enables cloud/external inference.
