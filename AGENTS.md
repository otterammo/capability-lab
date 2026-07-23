# Agent Operating Rules

This repository currently proves one deterministic offline experiment path. Read `PRD.md`, `TDD.md`, and the nearest task before changing code.

- Preserve `cli -> application -> domain` and `application -> ports <- adapters`; domain code is standard-library-only and performs no I/O.
- Keep orchestration in `LabService`; CLI commands only parse, wire, invoke, and render.
- Treat released benchmark manifests, task manifests, protected evaluator files, and fixture revisions as immutable. Create a new version for corrections.
- Never expose protected evaluator contents to a harness or run report.
- Use subprocess argument arrays, keep runtime network disabled, and always attempt worktree cleanup.
- Make the smallest complete change; run the closest test, then `make check`, `make smoke`, and `make diff-check` when the environment is configured.
- Do not commit, push, weaken tests, discard unknown changes, or add live-model/cloud integrations without explicit authorization.

A change is done only when required behavior, migration, tests, architecture checks, docs, and final diff checks agree, with failed validation reported honestly.
