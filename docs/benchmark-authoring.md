# Benchmark Authoring

Task and release files use JSON syntax valid under YAML 1.2. A task pins a semantic version, capabilities, fixture commit, prompt, budget, and deterministic scorers. A release references task paths and exact SHA-256 hashes. Schema version 1 is retained only for the exact immutable `smoke@1.0.0` release and task paths and hashes; new releases and their tasks use `"schema_version": 2`.

Protected command scripts and expected content live in `benchmarks/protected`, never in the generated fixture or harness input. Schema version 2 requires `protected_dependencies` to list every scorer `script` and `expected_path` with its exact SHA-256 hash; validation rejects missing or changed files. Task references must resolve within the benchmark tree, and protected dependency paths must resolve within `benchmarks/protected`, including after following symlinks. Scorer reports expose pass/fail details, not evaluator source.

Validate with:

```bash
uv run lab benchmark validate benchmarks/releases/smoke@1.0.1.yaml
```

Never edit a released task, release, fixture revision, or protected evaluator in place. Copy it to a new semantic version, compute protected dependency and task hashes, and publish a new release manifest.
