# Capability Lab

Capability Lab is a local-first experiment loop for measuring software-engineering capabilities. Its default smoke path uses a deterministic fake harness and needs no model, Docker, GPU, or network. Optional Docker-backed paths run raw Ollama or Pi against the same protected benchmark and preserve comparable evidence.

## Quick start

The core path requires Python 3.13.7, `uv`, and Git. Docker and local Ollama are optional.

```bash
make setup
uv run lab doctor
uv run lab config resolve
uv run lab benchmark validate
uv run lab smoke
make check
```

Inspect a completed run with `uv run lab inspect <run-id>`. Runtime state is kept under `.lab/`: SQLite metadata in `state.sqlite3`, content-addressed evidence in `artifacts/`, generated source fixtures in `fixtures/`, and temporary worktrees in `worktrees/`.

Configuration files use JSON syntax, a portable subset of YAML 1.2. Layers resolve in this order: defaults, optional model profile, harness profile, experiment, CLI override. The resolved value is canonicalized and SHA-256 hashed.

Every run modifies only an isolated checkout. Protected test and reference data remain outside the harness worktree. Live-model execution is explicit and local-only.

## Optional Docker and Ollama paths

`DockerSandbox` runs a pinned image as a non-root user with resource limits. The untrusted container has Docker networking disabled and reaches only the authorized local Ollama endpoint through a separate relay container and Unix socket. Invocation-owned containers and volumes carry a unique ownership label and are verified before cleanup; no custom Docker network is created.

```bash
make sandbox-image
make test-docker
OLLAMA_HOST=http://desktop:11434 uv run lab run configs/experiments/pi-smoke.yaml
OLLAMA_HOST=http://desktop:11434 uv run lab compare configs/experiments/raw-vs-pi.yaml
```

`lab compare` accepts only a raw-Ollama baseline followed by a Pi candidate, resolves each side once, and records equality evidence plus both normal runs. These explicit commands never pull a model or enable external network access. Promotion and distributed execution remain deferred.
