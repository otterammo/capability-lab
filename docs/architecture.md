# Architecture

Capability Lab is a modular monolith with one dependency direction:

```text
cli -> application -> domain
                   -> ports <- adapters
```

`domain` contains immutable standard-library records and deterministic classification. `ports` defines the harness, workspace, scorer, metadata, and artifact contracts. `application.LabService` owns configuration loading through cleanup and persistence. `adapters` implement validation, Git worktrees, fake/raw-Ollama/Pi harnesses, the Ollama identity client, Docker isolation, protected scorers, SQLite metadata, and filesystem artifacts. `bootstrap.py` is the composition root; `cli` contains no experiment rules.

The smoke flow is:

```text
resolve config -> verify task/release hashes -> prepare fixture/worktree
-> fake edit -> collect patch -> protected score -> classify
-> cleanup -> persist artifacts/metadata -> render
```

The harness receives only task ID, prompt, mode, budget, and worktree path. Protected tests and reference content remain in `benchmarks/protected`, outside the worktree.

Docker-backed harnesses use the same `LabService` lifecycle. `DockerSandbox` mounts only the prepared worktree, runs the pinned image as the worktree owner with fixed limits, and gives the untrusted container network mode `none`. A separate read-only relay container uses the built-in bridge network and forwards one pinned Ollama IPv4 endpoint over a shared Unix-socket volume. The relay container, untrusted container, and volume receive a unique invocation label; create and cleanup verify that label so name collisions cannot adopt or remove foreign resources. No custom network is created.

`LabService.compare` resolves exactly one raw-Ollama baseline and one Pi candidate, requires the desktop Ollama endpoint before runtime construction, executes those resolved objects once each, and writes a compact report referencing both ordinary runs.
