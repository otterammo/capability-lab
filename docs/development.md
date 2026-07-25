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
make sandbox-image
make test-docker
```

`make setup` syncs the editable project and dependencies with Python 3.13.7, applies the Alembic migration, creates the deterministic fixture repository, and runs `lab doctor`. Runtime state is disposable except for evidence the user wants to retain; remove specific run state deliberately, never with a broad recursive command.

`lab doctor` reports Docker CLI, daemon, server, and sandbox-image readiness as optional. Missing Docker does not block the deterministic fake smoke path; build the image and use `make test-docker` only for the Docker-backed suite.

For non-trivial behavior, write one focused failing test, observe the expected failure, implement the minimum behavior, and rerun it before wider checks.

Build the local sandbox image before using a Docker-backed sandbox. Its tag matches `configs/defaults.yaml`; it runs as UID `65532` with `socat` and the pinned Pi CLI.

`make test-docker` is the opt-in live Docker suite. It builds the image, exercises the network-disabled sandbox, fixed Unix-socket relay, and runtime limits, and verifies container, volume, and network cleanup without changing the host service on port `11434`.

Run the Pi smoke profile only after the configured model is installed on the explicitly
authorized Ollama endpoint. `lab run` accepts only `127.0.0.1`, `localhost`, or `desktop`
on the fixed local Ollama port. The value is included in resolved configuration and its
hash, then pinned to one numeric relay target. `lab compare` additionally requires
`OLLAMA_HOST` to be set exactly to `http://desktop:11434` before service construction,
and the service checks the same endpoint before either run. No command pulls a missing
model:

```bash
OLLAMA_HOST=http://desktop:11434 uv run lab run configs/experiments/pi-smoke.yaml
OLLAMA_HOST=http://desktop:11434 uv run pytest tests/end_to_end/test_pi_smoke.py --run-live
OLLAMA_HOST=http://desktop:11434 uv run lab compare configs/experiments/raw-vs-pi.yaml
```

`lab smoke` remains the Docker-independent fake run. The live test requires Docker,
the pinned sandbox image, the explicit Ollama endpoint, and `qwen2.5-coder:1.5b`; an
opted-in run fails with the missing prerequisite instead of substituting another
endpoint. The default model profile remains `http://127.0.0.1:11434`.

The comparison command is the only authorized live raw-versus-Pi path. It rejects any
other harness order or pairing before runtime construction, runs raw first and Pi second
exactly once, accepts honest task failures, and exits nonzero only when the pair is
incomparable or execution cannot produce the report. It never pulls a model, reruns a
side, or promotes a result.

```bash
make sandbox-image
docker image inspect capability-lab-sandbox:0.1.0 --format '{{.Id}}'
docker run --rm --pull=never capability-lab-sandbox:0.1.0 id -u
docker run --rm --pull=never capability-lab-sandbox:0.1.0 socat -V
```
