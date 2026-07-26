# Docker Sandbox and Local-Model Delivery Plan

**Goal:** Add the Docker execution boundary before any live model is allowed to
generate commands, then introduce Ollama, Pi, one live smoke run, and a controlled
raw-versus-tools comparison as separate reviewable pull requests.

**Architecture:** Keep `LabService` as the orchestrator and keep the existing
`Harness.execute(request, context)` contract. Add one narrow `Sandbox` port backed by
Docker; the later `PiHarness` composes that sandbox. `bootstrap.py` selects `FakeHarness`
or `PiHarness` directly from the resolved harness profile. No registry, factory, plugin
system, or benchmark schema change is part of this sequence.

## Global constraints

- Preserve `cli -> application -> domain` and `application -> ports <- adapters`.
- Domain code remains standard-library-only and performs no I/O.
- Released benchmark manifests, task manifests, protected evaluator files, and fixture
  revisions remain byte-for-byte unchanged.
- The fake harness and `lab smoke` remain deterministic, offline, and Docker-independent.
- Protected evaluator content is never mounted into or returned from the sandbox.
- Every subprocess uses an argument array. No host shell interpolation is permitted.
- Runtime containers receive no host secrets, Docker socket, home directory, SSH agent,
  package-manager cache, or repository path other than the prepared worktree.
- Normal `make check` remains usable without Docker, Ollama, Pi, a GPU, or network.
- Do not commit or push during implementation without separate authorization.

## Shared decisions

1. **The sandbox is not a harness.** `DockerSandbox` executes argv in a prepared
   worktree and returns bounded output plus execution provenance. `PiHarness` will use
   it later; `FakeHarness` will not.
2. **Network enforcement uses an internal bridge plus a fixed relay.** An internal
   Docker network has no external route. The untrusted container sees only a trusted
   relay named `ollama.local`; that relay forwards TCP only to
   `host.docker.internal:11434`. Merely adding `host-gateway` to the untrusted
   container is insufficient because it would expose every reachable host port.
3. **The image is local and identified by content.** Build a small project-owned image
   from a digest-pinned Alpine base. Record the resulting image ID, Docker server
   version, effective UID:GID, and Dockerfile SHA-256. Do not pull images during a run.
4. **Limits are explicit resolved configuration.** Start with `1.0` CPU, `512 MiB`
   memory and swap combined, `128` PIDs, `256` open files, and `1 MiB` combined output.
   The task's existing `timeout_seconds` remains the wall-clock deadline.
5. **Docker is optional for the existing workflow.** `lab doctor` reports CLI, daemon,
   and sandbox-image status, but missing optional Docker does not make the current fake
   smoke workflow unhealthy.

---

## PR 1: Docker sandbox boundary

### Task 1: Define the sandbox data and port

**Goal:** Establish the smallest stable contract that Pi will need without changing the
existing harness contract.

**Relevant files**

- Modify: `src/capability_lab/domain/models.py`
- Modify: `src/capability_lab/ports/interfaces.py`
- Modify: `src/capability_lab/schemas/config.py`
- Modify: `src/capability_lab/adapters/config.py`
- Modify: `configs/defaults.yaml`
- Test: `tests/unit/test_config.py`

**Proposed interface**

```python
@dataclass(frozen=True, slots=True)
class SandboxSettings:
    image: str
    cpus: float
    memory_mb: int
    pids: int
    nofile: int
    max_output_bytes: int


@dataclass(frozen=True, slots=True)
class SandboxIdentity:
    image_id: str
    docker_version: str
    user: str
    dockerfile_sha256: str


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    name: str
    endpoint: str
    external_access: bool


@dataclass(frozen=True, slots=True)
class SandboxResult:
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    output_limited: bool
    identity: SandboxIdentity
    network_policy: NetworkPolicy


class Sandbox(Protocol):
    def execute(
        self,
        argv: tuple[str, ...],
        context: ExecutionContext,
    ) -> SandboxResult: ...
```

Add `sandbox: SandboxSettings` to `RunSettings`. Validate positive, bounded values in
Pydantic; keep policy name and Ollama alias constant in the Docker adapter so authored
configuration cannot widen network access.

**Acceptance criteria**

- Resolved configuration and its canonical hash include image and limit values.
- Invalid zero/negative limits are rejected at the configuration boundary.
- `Harness`, `HarnessRequest`, and `Harness.execute(...)` are unchanged.
- No registry or harness-selection abstraction is introduced.

**Verify**

```bash
uv run pytest tests/unit/test_config.py
uv run pyright
uv run lint-imports
```

### Task 2: Build the minimal sandbox image

**Goal:** Provide a non-root image capable of a shell probe and a fixed TCP relay, but
not Pi or Ollama integration.

**Relevant files**

- Create: `docker/sandbox/Dockerfile`
- Create: `.dockerignore`
- Modify: `Makefile`
- Modify: `docs/development.md`

**Proposed approach**

- Pin the Alpine base by multi-platform index digest.
- Install only `socat`; use Alpine's existing BusyBox shell and tools.
- Create user/group ID `65532`, set `USER 65532:65532`, and set
  `WORKDIR /workspace`.
- Add `make sandbox-image` to build the local tag configured in
  `configs/defaults.yaml`.
- Do not install Pi, Node, Python, Git, curl, compilers, or package managers beyond
  what the base/build step requires. Pi gets its own image revision in PR 3.

**Acceptance criteria**

- `docker image inspect` reports a non-empty immutable image ID.
- `docker run --rm capability-lab-sandbox:0.1.0 id -u` prints a non-zero UID.
- The image contains `socat` and no project source, benchmark, evaluator, or secret.
- Runtime execution never performs an implicit pull.

**Verify**

```bash
make sandbox-image
docker image inspect capability-lab-sandbox:0.1.0 --format '{{.Id}}'
docker run --rm capability-lab-sandbox:0.1.0 id -u
```

Expected: an image ID is printed and the UID is `65532`.

### Task 3: Implement bounded Docker execution and cleanup

**Goal:** Run argv inside the prepared worktree with the requested isolation, limits,
and failure behavior.

**Relevant files**

- Create: `src/capability_lab/adapters/sandboxes.py`
- Test: `tests/unit/test_sandboxes.py`
- Test: `tests/integration/test_docker_sandbox.py`

**Proposed approach**

`DockerSandbox.execute(...)` performs this fixed lifecycle:

1. Resolve and validate the worktree path.
2. Inspect the configured local image and capture its image ID.
3. Create a unique `--internal` bridge network.
4. Start a fixed-command `socat` relay on that network, attach only the relay to the
   normal bridge, and map its upstream to `host.docker.internal:11434`.
5. Create the untrusted container with:
   `--read-only`, `--cap-drop=ALL`, `--security-opt=no-new-privileges`,
   `--memory=512m`, `--memory-swap=512m`, `--cpus=1.0`, `--pids-limit=128`,
   `--ulimit=nofile=256:256`, `--user` set to the numeric UID:GID returned by
   `Path.stat()` for the worktree, the internal
   network, and exactly one read-write bind mount at `/workspace`.
6. Start attached, read stdout and stderr concurrently in bounded chunks, kill the
   named container when the deadline or combined `1 MiB` cap is reached, and return a
   structured termination result.
7. In `finally`, attempt container removal, relay removal, and network removal
   independently so one cleanup failure does not prevent the others.

Use deterministic names derived from the run ID and pass every Docker invocation as a
list. Reject newline/NUL-containing argv, non-absolute workspaces, missing worktrees,
and image-inspection failures before container creation.

**Acceptance criteria**

- A trivial command can write under `/workspace` and reports a non-root UID.
- The image filesystem is read-only and no host path other than the worktree is
  mounted writable.
- Memory, CPU, PID, file-descriptor, timeout, and combined-output limits are present
  and enforced.
- Public network access fails; the fixed relay endpoint succeeds against a temporary
  host test server on port `11434` without requiring Ollama.
- Timeout and output-limit results are distinguishable.
- Containers, relay, network, and worktree are removed on success, command failure,
  timeout, output overflow, and setup failure.
- Protected evaluator paths never appear in mounts, argv, stdout, stderr, or returned
  metadata.

**Test cases**

- Unit-test the exact Docker argv for security flags, one writable mount, internal
  network, fixed relay target, and absence of `--privileged`/Docker socket/home mounts.
- Integration-test `id -u`, a write to `/workspace`, and a failed write to `/etc`.
- Integration-test a sleeping process with a one-second deadline.
- Integration-test output exceeding a small configured cap.
- Integration-test successful access to `http://ollama.local:11434` and failed access
  to an external IP and DNS name.
- Parameterize cleanup assertions over normal exit, non-zero exit, timeout, and output
  overflow.

**Verify**

```bash
uv run pytest tests/unit/test_sandboxes.py
make test-docker
docker ps -a --filter label=capability-lab.sandbox --format '{{.ID}}'
docker network ls --filter label=capability-lab.sandbox --format '{{.ID}}'
```

Expected: both final Docker inventory commands print nothing.

### Task 4: Carry sandbox identity into run provenance

**Goal:** Persist the actual execution boundary used by a harness without falsely
marking fake-harness runs as sandboxed.

**Relevant files**

- Modify: `src/capability_lab/domain/models.py`
- Modify: `src/capability_lab/application/service.py`
- Modify: `src/capability_lab/bootstrap.py`
- Modify: `tests/end_to_end/test_smoke.py`
- Test: `tests/integration/test_docker_sandbox.py`

**Proposed approach**

- Add optional `sandbox_identity` and a structured `network_policy` to
  `HarnessResult`/`RunProvenance`.
- Build base provenance before execution as today, then use `dataclasses.replace` after
  harness execution when the returned result contains sandbox provenance.
- A small test-only harness composes `DockerSandbox`, runs the trivial command, and
  returns its sandbox provenance through the existing `HarnessResult`. Run it through
  `LabService` and assert the persisted `provenance.json` and SQLite environment
  snapshot contain the exact image ID and `ollama-only` policy.
- Leave `FakeHarness` unchanged. Its current provenance remains explicitly
  `sandbox_identity=null` and `network_policy=not_enforced`.

**Acceptance criteria**

- Persisted Docker provenance contains image ID, Docker server version, Dockerfile
  hash, effective UID:GID, policy name, allowed endpoint, and
  `external_access=false`.
- Failed and timed-out sandbox executions retain the same provenance.
- Fake smoke still passes without Docker and does not claim Docker enforcement.
- No database migration is required because environment snapshots already store
  provenance as JSON.

**Verify**

```bash
uv run pytest tests/end_to_end/test_smoke.py
make test-docker
```

### Task 5: Report Docker status in `lab doctor`

**Goal:** Make Docker readiness visible without turning it into a prerequisite for the
current deterministic slice.

**Relevant files**

- Modify: `src/capability_lab/domain/models.py`
- Modify: `src/capability_lab/bootstrap.py`
- Modify: `src/capability_lab/cli/main.py`
- Modify: `tests/integration/test_cli_defaults.py`
- Modify: `docs/development.md`

**Proposed approach**

- Add `required: bool = True` to `DoctorCheck`.
- Report one optional Docker check containing CLI version, daemon reachability, server
  version, and configured image presence.
- Render unavailable optional checks as `optional-missing`; only failed required checks
  affect the command's exit status.
- Invoke Docker with a short timeout and turn missing CLI, stopped daemon, permission
  failure, and missing image into concise details rather than tracebacks.

**Acceptance criteria**

- `lab doctor` shows Docker's actual state.
- A stopped/missing Docker daemon does not break `make setup` or fake `lab smoke`.
- A malformed required fixture or migration still makes `lab doctor` exit non-zero.
- No Ollama health check is added in this PR.

**Verify**

```bash
uv run pytest tests/integration/test_cli_defaults.py
uv run lab doctor
```

### PR 1 final verification

```bash
make check
make smoke
make test-docker
make diff-check
git status --short
```

`make check` and `make smoke` must pass with Docker stopped. `make test-docker` must pass
in the configured Docker environment and may skip only when its explicit opt-in flag is
absent; it must not silently skip after the target has opted in.

**PR 1 out of scope**

- Ollama API calls, model pulls, or generation
- Pi installation or invocation
- Live benchmark execution
- Raw-model generation
- Harness registries, factories, entry points, or plugins
- Host-only live-agent execution

---

## PR 2: Ollama health and model identity adapter

**Goal:** Verify one configured local model and capture reproducible identity without
generating text or invoking Pi.

**Relevant files**

- Create: `src/capability_lab/adapters/models.py`
- Modify: `src/capability_lab/domain/models.py`
- Modify: `src/capability_lab/ports/interfaces.py`
- Modify: `src/capability_lab/schemas/config.py`
- Modify: `src/capability_lab/adapters/config.py`
- Modify: `src/capability_lab/application/service.py`
- Modify: `src/capability_lab/bootstrap.py`
- Modify: `src/capability_lab/cli/main.py`
- Create: `configs/models/ollama.yaml`
- Test: `tests/contract/test_adapters.py`
- Test: `tests/integration/test_ollama.py`

**Approach:** Insert the optional model profile into configuration resolution between
defaults and the harness profile, matching the TDD's declared layer order; existing fake
callers pass no model profile and retain their behavior. Use `urllib.request` with
explicit timeouts. `GET /api/tags` is the health
and installed-model lookup; `POST /api/show` supplies capabilities and model details.
Require an exact configured model name and record digest, format, family, parameter
size, quantization level, capabilities, and Ollama server version when available. Unit
and contract tests use an in-process fake HTTP server; one opt-in integration test uses
the real endpoint through the sandbox relay. Do not pull a missing model.

**Acceptance criteria**

- Unreachable Ollama, malformed JSON, missing model, ambiguous model, and digest change
  have distinct errors.
- Model identity is serializable into existing run provenance.
- `lab doctor` adds optional Ollama endpoint/model status.
- No generation request occurs.

**Verify**

```bash
uv run pytest tests/contract/test_adapters.py tests/integration/test_ollama.py
make check
make diff-check
```

---

## PR 3: Pi harness adapter

**Goal:** Implement the existing `Harness.execute(...)` contract using a pinned Pi CLI
inside `DockerSandbox` and capture its JSON event trajectory.

**Relevant files**

- Modify: `docker/sandbox/Dockerfile`
- Create: `src/capability_lab/adapters/pi_harness.py`
- Modify: `src/capability_lab/domain/models.py`
- Modify: `src/capability_lab/schemas/config.py`
- Modify: `src/capability_lab/adapters/config.py`
- Modify: `src/capability_lab/bootstrap.py`
- Create: `configs/harnesses/pi.yaml`
- Test: `tests/contract/test_adapters.py`
- Test: `tests/integration/test_pi_harness.py`

**Approach:** Pin the Pi package/version in the sandbox image. Generate Pi's
`models.json` in a sandbox-only writable temporary location with base URL
`http://ollama.local:11434/v1`; mount no host Pi state. Invoke Pi non-interactively in
JSON event mode, pass the task prompt, workspace, model, seed/settings, and remaining
budget, then translate its event stream, final response, exit status, timeout, and
sandbox provenance into `HarnessResult`. Select `FakeHarness` or `PiHarness` directly
in `bootstrap.py` from a validated `harness.kind` field in the harness profile.

**Acceptance criteria**

- The shared harness contract suite passes for Pi with a stub Ollama endpoint.
- Malformed JSON events, non-zero exit, timeout, output overflow, and model-runtime
  errors produce structured failure evidence and still clean up.
- No protected scorer data or host credentials enter Pi's prompt, files, or mounts.
- No registry or plugin system is added.

**Verify**

```bash
uv run pytest tests/contract/test_adapters.py tests/integration/test_pi_harness.py
make check
make test-docker
make diff-check
```

---

## PR 4: Run the existing one-task smoke benchmark through Pi

**Goal:** Produce the first live, sandboxed Pi run using the current immutable smoke
benchmark.

**Relevant files**

- Create: `configs/experiments/pi-smoke.yaml`
- Modify: `src/capability_lab/cli/main.py`
- Modify: `docs/development.md`
- Test: `tests/end_to_end/test_pi_smoke.py`

**Approach:** Add an explicit `lab run` path that accepts the Pi experiment profile and
continues to use `LabService.run`. Keep `lab smoke` wired to the fake profile. Mark the
live test opt-in and require Docker, the pinned image, Ollama, and the configured model.
Persist the full Pi trajectory, patch, scorer results, model identity, sandbox identity,
network policy, termination reason, and cleanup outcome.

**Acceptance criteria**

- The unchanged `smoke@1.0.1` task executes in its prepared worktree through Pi.
- Protected scorers remain outside the sandbox and run after Pi exits.
- The source fixture stays clean and the run worktree/container/network are removed.
- The run is marked non-reproducible if any required model or sandbox identity is
  missing.
- A failed live attempt is retained honestly; tests do not weaken scoring to force a
  pass.

**Verify**

```bash
uv run lab run configs/experiments/pi-smoke.yaml
uv run pytest tests/end_to_end/test_pi_smoke.py --run-live
make check
make diff-check
```

---

## PR 5: Raw-model versus Pi tool-enabled baseline

**Goal:** Compare one declared variable—tool-enabled Pi versus the same raw Ollama
model—under equal tasks and budgets.

**Relevant files**

- Create: `src/capability_lab/adapters/raw_ollama_harness.py`
- Modify: `src/capability_lab/domain/models.py`
- Modify: `src/capability_lab/schemas/config.py`
- Modify: `src/capability_lab/adapters/config.py`
- Modify: `src/capability_lab/bootstrap.py`
- Create: `configs/harnesses/raw-ollama.yaml`
- Create: `configs/experiments/raw-vs-pi.yaml`
- Modify: `src/capability_lab/application/service.py`
- Modify: `src/capability_lab/cli/main.py`
- Test: `tests/integration/test_comparison.py`
- Modify: `docs/experiment-methodology.md`

**Approach:** Implement a minimal raw harness that makes one non-streaming Ollama
generation request with the task prompt and cannot use tools or edit the worktree.
Compare it with Pi using the exact same model digest, task release, timeout, seed,
context/output limits, and repetition policy. Reuse existing scores and run artifacts;
add only the comparison orchestration/report fields required to show paired outcomes,
runtime, timeout, and intervention deltas.

**Acceptance criteria**

- Baseline and candidate differ only in harness/tool access.
- The report records both configuration hashes and rejects mismatched model digests,
  benchmark hashes, budgets, or repetition counts.
- Raw output, Pi trajectory, patches, scores, failures, and provenance remain inspectable.
- No automatic promotion occurs.
- Direct selection in `bootstrap.py` remains; this is the third concrete harness and the
  point to reassess selection only if the direct branch is measurably awkward.

**Verify**

```bash
uv run pytest tests/integration/test_comparison.py
make check
make diff-check
```

## Source references

- `PRD.md`: Phase 1/2 roadmap, protected evaluation, reproducible evidence, and no
  live-model/cloud integration in the deterministic slice.
- `TDD.md`: dependency direction, execution lifecycle, provenance, security controls,
  optional doctor checks, testing policy, and delivery sequence.
- `docs/adr/0003-fake-harness-first.md`
- `docs/adr/0004-git-worktrees.md`
- `docs/architecture.md`
- `docs/testing.md`
- Merged vertical slice: commit `fa70bf2fb5098bbd2298b24ec82682e70b7c5990`.
- Docker internal-network behavior:
  <https://docs.docker.com/reference/cli/docker/network/create/#network-internal-mode---internal>
- Docker resource constraints:
  <https://docs.docker.com/engine/containers/resource_constraints/>
- Docker run security and host-gateway options:
  <https://docs.docker.com/reference/cli/docker/container/run/>
- Ollama model list and identity endpoints:
  <https://docs.ollama.com/api/tags> and
  <https://docs.ollama.com/api-reference/show-model-details>
- Pi custom Ollama models and JSON mode:
  <https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/models.md>
  and
  <https://github.com/badlogic/pi-mono/blob/main/packages/coding-agent/docs/json.md>
