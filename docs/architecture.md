# Architecture

Capability Lab is a modular monolith with one dependency direction:

```text
cli -> application -> domain
                   -> ports <- adapters
```

`domain` contains immutable standard-library records and deterministic classification. `ports` defines the harness, workspace, scorer, metadata, and artifact contracts. `application.LabService` owns configuration loading through cleanup and persistence. `adapters` implement JSON-compatible YAML/Pydantic validation, Git worktrees, the fake harness, protected scorers, SQLAlchemy/SQLite metadata, and filesystem artifacts. `bootstrap.py` is the composition root; `cli` contains no experiment rules.

The smoke flow is:

```text
resolve config -> verify task/release hashes -> prepare fixture/worktree
-> fake edit -> collect patch -> protected score -> classify
-> cleanup -> persist artifacts/metadata -> render
```

The harness receives only task ID, prompt, mode, budget, and worktree path. Protected tests and reference content remain in `benchmarks/protected`, outside the worktree.
