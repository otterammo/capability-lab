# Decision Log

Consequential decisions are recorded in `docs/adr/`:

- ADR-0001: modular monolith with enforced dependency direction
- ADR-0002: SQLite metadata plus filesystem artifacts
- ADR-0003: deterministic fake harness before live models
- ADR-0004: Git worktrees for repository isolation
- ADR-0005: canonical JSON for configuration identity

Smaller current choices: configuration is authored as JSON-compatible YAML to avoid another runtime parser dependency; smoke contains exactly one development task; and cleanup failure makes a run an environment failure even if scoring passed.
