# Initial Vertical Slice PR Readiness Plan

## Goal

Prepare `initial-vertical-slice` for pull-request review by resolving every blocker and important finding from the first code review, preserving the deterministic offline scope, and committing the complete branch.

## Global Constraints

- Preserve `cli -> application -> domain` and `application -> ports <- adapters`.
- Keep domain code standard-library-only and free of I/O.
- Keep orchestration in `LabService`.
- Do not expose protected evaluator content to the harness or reports.
- Do not mutate an existing released task or benchmark; create a new version when its identity changes.
- Use test-first fixes and the smallest implementation that closes each finding.
- Do not add live models, Docker, networking, plugin frameworks, or unrelated refactors.

## Task 1: Restore Required Quality Gates

### Goal

Make the documented static, architecture, and dependency-security gates executable and green.

### Context

`make check` currently fails with Pyright errors. `make architecture` exits before evaluating contracts because import-linter is missing its external-package setting. The TDD requires `pip-audit`, but the Makefile and development dependencies omit it.

### Relevant Files

- `pyproject.toml`
- `Makefile`
- `src/capability_lab/adapters/config.py`
- `tests/unit/test_config.py`
- `tests/unit/test_scorers.py`
- `uv.lock`

### Acceptance Criteria

- Pyright reports no errors.
- Import-linter evaluates and passes every configured contract.
- `make security` runs `pip-audit` and is included in `make check`.
- Existing scorer behavior remains unchanged.
- No unrelated dependency or abstraction is introduced.

### Verify

```bash
make typecheck
make architecture
make security
```

## Task 2: Make Protected Evaluation Part of Benchmark Identity

### Goal

Ensure a protected scorer script or expected-content change invalidates benchmark integrity rather than silently changing scoring under the same benchmark identity.

### Context

The release currently hashes only the task manifest. The task contains paths to protected files but no expected hashes. Existing `1.0.0` task and release files are treated as immutable.

### Relevant Files

- `src/capability_lab/adapters/benchmark.py`
- `benchmarks/tasks/`
- `benchmarks/releases/`
- `configs/defaults.yaml`
- `configs/experiments/smoke.yaml`
- `tests/unit/test_benchmark.py`
- `docs/benchmark-authoring.md`

### Acceptance Criteria

- A new task and benchmark version records hashes for every referenced protected evaluator file.
- The loader rejects a protected evaluator whose bytes no longer match its declared hash.
- Existing `1.0.0` files remain unchanged.
- Default smoke configuration uses the new release.
- Tests prove protected-file mutation is rejected.
- Protected contents remain absent from harness input and run reports.

### Verify

```bash
uv run pytest tests/unit/test_benchmark.py
uv run lab benchmark validate
```

## Task 3: Preserve Complete Patches and Accurate Failures

### Goal

Record newly created files in `patch.diff` and classify scorer construction or execution exceptions as evaluator failures.

### Context

`git diff HEAD` excludes untracked files even though status reports their paths. `LabService` catches scorer exceptions as plain failure text, leaving a successful harness with no scores classified as `unknown`.

### Relevant Files

- `src/capability_lab/adapters/workspaces.py`
- `src/capability_lab/application/service.py`
- `tests/integration/test_workspace.py`
- `tests/end_to_end/test_smoke.py`

### Acceptance Criteria

- `WorkspaceDiff.patch` contains reproducible additions for untracked files.
- `WorkspaceDiff.changed_paths` remains correct for tracked and untracked changes.
- Scorer construction and scorer execution exceptions produce `evaluator` classification and preserve useful failure context.
- Cleanup and persistence still occur on failure.
- Regression tests demonstrate each failure before the production fix and pass afterward.

### Verify

```bash
uv run pytest tests/integration/test_workspace.py
uv run pytest tests/end_to_end/test_smoke.py
```

## Task 4: Integrate, Review, Verify, and Commit

### Goal

Prove the combined branch meets its governing documents and leave all intended changes committed.

### Acceptance Criteria

- Subagent changes are reviewed for scope, correctness, and overlap.
- A fresh whole-branch review has no blocker or important findings.
- `make setup`, `make check`, `make smoke`, and `make diff-check` pass.
- The direct end-to-end test passes.
- No runtime state, secret, cache, or unrelated file is staged.
- All intended files are committed with a clear Conventional Commit message.
- `git status --short --branch` is clean on `initial-vertical-slice`.

### Verify

```bash
make setup
make check
make smoke
uv run pytest tests/end_to_end/test_smoke.py
make diff-check
git status --short --branch
```

## Source References

- `PRD.md`
- `TDD.md`
- `AGENTS.md`
- Initial code-review findings from 2026-07-23
