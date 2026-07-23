# Capability Optimization Platform: Technical Design

**Version:** 0.1  
**Status:** Proposed  
**Deployment:** Single-user, local-first

## Technical Objective

Build the smallest trustworthy experiment loop for local coding models:

```text
Task -> isolated workspace -> harness -> patch -> protected scoring -> evidence -> comparison
```

The initial implementation is a Python modular monolith. External systems sit behind narrow ports only after a second implementation or a real testing boundary makes the port useful.

## Decisions

| Concern | Decision | Deferred alternative |
|---|---|---|
| Language | Python 3.13 with `uv` | TypeScript, Rust |
| Architecture | Modular monolith | Microservices |
| CLI | Typer command named `lab` | Web UI |
| Validation | Pydantic at configuration boundaries | Hand-written validation |
| Metadata | SQLite through SQLAlchemy | PostgreSQL |
| Artifacts | Content-addressed local filesystem | S3 or MinIO |
| Migrations | Alembic | Manual schema management |
| Workspace | Git worktrees | Repository copies |
| Runtime isolation | Docker after the deterministic slice | Host-only production runs |
| Initial model provider | Ollama | llama.cpp, vLLM, cloud APIs |
| Initial live harness | Pi | Inspect, Aider, OpenHands |
| Initial retrieval | Ripgrep, then Tree-sitter if measured | Embeddings first |
| Scoring | Deterministic scorers | LLM judge |
| Statistics | Paired bootstrap and McNemar | Bayesian hierarchy |
| Parallelism | Local scheduler | Distributed queue |

MLflow may mirror completed experiments later, but SQLite and the artifact store remain the source of truth.

## Dependency Direction

```text
cli -> application -> domain
                   -> ports <- adapters
```

- `domain` uses only the Python standard library.
- `ports` may import domain types.
- `application` orchestrates domain behavior through ports.
- `adapters` implement external boundaries.
- `cli` parses input and invokes application services; it contains no experiment logic.
- Benchmark task definitions contain no Pi-, Ollama-, or runner-specific fields.
- Scorers cannot write to workspaces or persistence directly.

These rules are enforced with import-linter and tests.

## Repository Layout

```text
capability-lab/
├── PRD.md
├── TDD.md
├── AGENT_OPTIMIZATION.md
├── KICKOFF_PROMPT.md
├── pyproject.toml
├── uv.lock
├── Makefile
├── configs/
├── benchmarks/
├── src/capability_lab/
│   ├── cli/
│   ├── domain/
│   ├── application/
│   ├── ports/
│   ├── adapters/
│   │   ├── models/
│   │   ├── harnesses/
│   │   ├── workspaces/
│   │   ├── sandboxes/
│   │   ├── scorers/
│   │   ├── persistence/
│   │   └── artifacts/
│   └── schemas/
├── migrations/
├── tests/
│   ├── unit/
│   ├── contract/
│   ├── integration/
│   ├── end_to_end/
│   └── fixtures/
├── docs/
│   ├── adr/
│   └── plans/
└── artifacts/
```

Create packages only when the first vertical slice needs them.

## Core Domain

The initial domain contains immutable, typed representations for:

- `Capability`
- `Task`
- `BenchmarkRelease`
- `Experiment`
- `Run`
- `RunResult`
- `ScoreResult`
- `FailureClassification`
- `ExecutionBudget`
- `WorkspaceDiff`
- `RunProvenance`
- `ArtifactRef`

A task is one scored unit of work. A benchmark release is an immutable list of versioned task references and content hashes. An experiment compares a baseline and candidate on the same benchmark. A run is one task attempt using one resolved configuration.

## Required Boundaries

The first slice needs these protocols:

```python
class Harness(Protocol):
    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult: ...

class WorkspaceManager(Protocol):
    def prepare(self, repository: RepositorySpec, revision: str, run_id: RunId) -> PreparedWorkspace: ...
    def collect_diff(self, workspace: PreparedWorkspace) -> WorkspaceDiff: ...
    def destroy(self, workspace: PreparedWorkspace) -> None: ...

class Scorer(Protocol):
    def score(self, task: Task, evidence: EvaluationEvidence) -> ScoreResult: ...

class MetadataRepository(Protocol):
    def create_experiment(self, experiment: ExperimentRecord) -> None: ...
    def create_run(self, run: RunRecord) -> None: ...
    def complete_run(self, result: PersistedRunResult) -> None: ...

class ArtifactStore(Protocol):
    def put(self, artifact: ArtifactPayload) -> ArtifactRef: ...
    def open(self, ref: ArtifactRef) -> BinaryIO: ...
```

Do not add factories, registries, or plugin frameworks until more than one concrete implementation needs selection.

## Configuration

Authored configuration uses YAML and is validated by Pydantic. Layers resolve in this order:

```text
defaults -> model profile -> harness profile -> tool profile -> experiment -> CLI overrides
```

The resolved value is serialized as canonical JSON with sorted keys and hashed with SHA-256. Formatting changes to YAML must not alter experiment identity.

Every run stores the complete resolved configuration and hash.

## Benchmark Definition

Tasks are declarative. A task includes:

- Stable ID and semantic version
- Capability tags
- Repository fixture and exact revision
- User-facing prompt
- Execution budget
- Tool-policy reference
- Deterministic scorer definitions
- Metadata such as difficulty, language, scope, ambiguity, and expected-change status

A benchmark release command validates tasks, verifies fixture revisions, computes hashes, reports coverage, and creates an immutable manifest. Corrections require a new task or benchmark version.

## Execution Lifecycle

1. Load and validate the experiment.
2. Resolve and hash configuration.
3. Verify benchmark integrity.
4. Create experiment and run records.
5. Create a clean worktree at the exact fixture revision.
6. Prepare cached repository information when configured.
7. Invoke the harness within the run budget.
8. Capture its transcript and structured events.
9. Collect the repository diff.
10. Run protected deterministic scorers outside the harness.
11. Classify failure from structured evidence.
12. Store immutable artifacts and complete metadata.
13. Destroy the worktree in a failure-safe cleanup path.

The initial slice uses a deterministic fake harness and does not require Docker, Ollama, or network access.

## Evaluation Integrity

The harness cannot modify or read hidden tests, scorer code, expected patches, benchmark manifests, reference outputs, promotion policies, or run metadata.

Protected scoring runs from the orchestration process against the completed worktree. Scorers are deterministic, versioned, non-mutating, and return structured details.

Initial scorers:

- Command exit status
- Exact file content
- Allowed diff scope
- Forbidden path
- No-change expectation

## Persistence

SQLite stores capabilities, benchmarks, tasks, experiments, runs, scores, baselines, artifact references, and environment snapshots. SQLAlchemy types remain inside the persistence adapter. Alembic owns schema migrations.

Large data is stored under:

```text
artifacts/
├── blobs/sha256/<prefix>/<hash>
└── runs/<run-id>/
    ├── manifest.json
    ├── resolved-config.json
    ├── provenance.json
    ├── transcript.jsonl.gz
    ├── patch.diff
    ├── scorer-results.json
    └── failure.json
```

Blob content is immutable and deduplicated by SHA-256. Temporary workspaces are deleted after collection.

## Provenance

Each run captures:

- Platform commit and dirty state
- Benchmark, task, and content hashes
- Resolved configuration and hash
- Model identity and digest when available
- Harness, toolset, and scorer versions
- Source repository identity and revision
- Execution image digest when containers are used
- OS and relevant hardware
- Time, seed, resource budgets, and network policy

A run with incomplete provenance is marked non-reproducible and cannot become a baseline.

## Failure Classification

Version 1 uses deterministic rules over structured events. It distinguishes success, localization, reasoning, retrieval, tool selection, tool execution, editing, verification, repair, environment, timeout, model runtime, evaluator, and unknown failures.

The unknown rate is tracked. A diagnostic model may suggest a secondary label later, but promotion decisions do not depend on it.

## CLI

The canonical command is `lab`:

```text
lab doctor
lab config resolve <path>
lab benchmark validate <path-or-id>
lab smoke
lab run <experiment>
lab compare <experiment-id>
lab inspect <run-id>
lab report <experiment-id>
lab baseline list|show|promote|rollback
```

All report commands support terminal and JSON output; comparison reports additionally support CSV and Markdown.

`lab doctor` checks Python, Git, database migrations, artifact storage, fixtures, disk space, and optional dependencies such as Docker and Ollama.

## Security and Resource Controls

Live agents run as untrusted code. Docker execution will use a non-root user, disabled network by default, read-only mounts where possible, no host secrets, process and resource limits, bounded output, and command timeouts. Internal subprocesses use argument arrays rather than interpolated shell strings.

Host-only agent execution requires an explicit unsafe option and cannot produce a promotable baseline.

## Testing

- Unit tests cover configuration, hashing, release integrity, scoring, classification, artifacts, statistics, and reporting.
- Contract tests apply the same behavioral suite to every harness, workspace, scorer, artifact, and metadata implementation.
- Integration tests cover SQLite, Alembic, filesystem artifacts, and Git worktrees.
- One end-to-end test executes the deterministic smoke benchmark.

Normal CI requires no GPU, live model, Ollama, Docker, or network access.

Required checks:

```text
ruff format --check
ruff check
pyright
pytest
import-linter
pip-audit
migration validation
benchmark integrity
smoke test
```

## Delivery Sequence

1. Deterministic vertical slice: fake harness, fixture, worktree, scorers, persistence, reports.
2. Docker sandbox boundary.
3. Ollama model identity and health adapter.
4. Pi harness adapter and trajectory capture.
5. Raw versus Pi baseline on a small development set.
6. Controlled retrieval and verification experiments.

## MVP Acceptance Criteria

The MVP is complete when a developer can run `make setup`, `uv run lab doctor`, `uv run lab smoke`, and `make check`; the smoke task is executed in a clean worktree; protected scorers verify the patch; metadata and immutable artifacts are persisted; cleanup succeeds; and a baseline/candidate comparison can produce a reproducible promotion recommendation.
