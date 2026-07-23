# Agent Optimization Recommendations

Agents working in this repository should encounter low ambiguity, bounded authority, fast feedback, and recoverable failure.

## Instruction Order

When instructions conflict, use this precedence:

1. Current task instructions
2. Nearest module-level `AGENTS.md`
3. Root `AGENTS.md`
4. Architecture and development documentation
5. Existing code conventions

The root `AGENTS.md` should remain a short operating manual. Add module-level files only when local rules materially differ.

## Root Agent Instructions

The root file should state:

- Project purpose and current scope
- Architectural dependency rules
- Approved setup and validation commands
- Required inspect, edit, test, and diff workflow
- Security and benchmark-integrity restrictions
- Definition of done

Recommended required workflow:

1. Read the governing task and nearest instructions.
2. Inspect the relevant implementation and tests.
3. Make the smallest complete change.
4. Run the closest useful test.
5. Run repository checks appropriate to the change.
6. Inspect the final diff and disclose limitations.

## Local Instructions

Use targeted `AGENTS.md` files for these areas:

- `domain`: standard library only, immutable typed values, no I/O or framework imports
- `adapters`: implement a port, translate external errors, preserve native evidence, add contract tests
- `benchmarks`: released manifests are immutable; scoring is deterministic; hidden evaluation stays inaccessible
- `tests`: do not weaken assertions, modify hidden fixtures, or mask flaky behavior with retries

## Canonical Documentation

Maintain one authoritative file per topic:

```text
README.md
AGENTS.md
CONTRIBUTING.md
SECURITY.md
docs/architecture.md
docs/development.md
docs/testing.md
docs/experiment-methodology.md
docs/benchmark-authoring.md
docs/adding-adapters.md
docs/error-catalog.md
docs/decisions.md
docs/adr/
```

Architecture docs should answer where new code belongs. ADRs record consequential decisions with context, alternatives, consequences, and status. A lightweight decision log handles smaller non-obvious choices.

## Task Shape

Agent tasks should specify:

```text
Objective
Context
In scope
Out of scope
Acceptance criteria
Relevant files
Required tests
Constraints
Validation commands
Expected deliverable
```

Require a written plan when work changes a public interface, persistence, benchmark semantics, a security boundary, an adapter, a dependency, several architectural modules, or more than about five files.

## Tool Surface

Expose intentions through one project CLI and a small set of Make targets:

```text
make setup
make format
make lint
make typecheck
make test
make test-unit
make test-contract
make test-integration
make architecture
make security
make check
make smoke
make diff-check
```

Useful project commands include configuration resolution, benchmark validation, task inspection, run inspection, and repository maps. Add scaffolding commands only after repeated manual creation proves they save work.

## Fast Feedback

Agents should validate the narrowest scope first, then run the required full checks before completion. The full benchmark is scheduled or manually invoked, not part of ordinary CI.

Test preference order:

1. Existing failing test
2. One focused unit test
3. Shared contract test
4. Integration test at an external boundary
5. End-to-end smoke test when the full lifecycle changes

## Guardrails

Agents may read, search, edit the working tree, run tests and static checks, and inspect Git history.

Without explicit permission they may not:

- Commit, push, merge, rebase, force reset, or delete branches
- Discard unknown local changes
- Access production systems or secrets
- Publish packages or open pull requests
- Modify released benchmarks, hidden tests, evaluator code, or reference patches
- Add unrestricted network access
- Download arbitrary binaries
- Weaken tests or budgets to manufacture a passing result
- Omit failed attempts or selectively rerun only preferred outcomes

Use separate branches or worktrees for parallel agents. Assign one owner per overlapping file set and agree on shared interfaces before parallel implementation.

## Automated Enforcement

Documentation is advisory; CI is authoritative. Enforce:

- Import direction with import-linter
- Types with Pyright
- Formatting and linting with Ruff
- Tests with Pytest
- Migration validation with Alembic
- Dependency and vulnerability checks
- Benchmark content hashes and release immutability
- Evaluator separation
- Diff checks for forbidden paths, unexpected generated files, secrets, and unrelated formatting churn

## Dependency Policy

Before adding a dependency, establish that the standard library or an installed package cannot solve the problem simply, that the dependency replaces meaningful custom code, and that it can be isolated at a boundary. New runtime frameworks, databases, queues, telemetry systems, and model providers require an ADR or explicit task approval.

## Handoff

Every completed agent task reports:

- Summary and behavior changed
- Files changed
- Tests and validation actually run
- Known limitations and risks
- Follow-up work, if any

Incomplete work additionally reports current state, remaining work, blocker, and the safest next action.

## Definition of Done

A change is done only when acceptance criteria are met, relevant tests pass, architecture boundaries remain valid, documentation matches public behavior, required migrations exist, released benchmarks and hidden evaluation remain untouched, the final diff contains no unrelated changes, and limitations are disclosed.

The governing operating rule is: make small, verifiable changes and preserve evidence.
