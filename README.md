# Capability Lab

Capability Lab is a local-first experiment loop for measuring software-engineering capabilities. This first slice uses a deterministic fake harness so isolation, protected scoring, persistence, reporting, and cleanup can be verified without a model, Docker, GPU, or network.

## Quick start

Requires Python 3.13.7, `uv`, and Git.

```bash
make setup
uv run lab doctor
uv run lab config resolve
uv run lab benchmark validate
uv run lab smoke
make check
```

Inspect a completed run with `uv run lab inspect <run-id>`. Runtime state is kept under `.lab/`: SQLite metadata in `state.sqlite3`, content-addressed evidence in `artifacts/`, generated source fixtures in `fixtures/`, and temporary worktrees in `worktrees/`.

Configuration files use JSON syntax, which is a portable subset of YAML 1.2. Layers resolve in this order: defaults, harness profile, experiment, CLI override. The resolved value is canonicalized and SHA-256 hashed.

The smoke run modifies only an isolated checkout. Protected test and reference data remain outside the harness worktree. Ollama, Pi, Docker, live models, networking, promotion, and distributed execution are intentionally deferred.
