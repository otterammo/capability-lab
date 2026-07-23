# Contributing

Use Python 3.13.7 and `uv`. Start with `make setup`, make one bounded change, and run the narrowest relevant test before `make check` and `make smoke`.

New business rules belong in `domain`, orchestration in `application`, boundary interfaces in `ports`, external behavior in `adapters`, and presentation in `cli`. Do not add a package, abstraction, or dependency until the current slice needs it.

Released benchmarks are immutable. A benchmark change requires a new task or release version and updated content hashes. Evaluator material stays outside harness-visible worktrees.

Before handoff run `git status --short`, `git diff --stat`, and `git diff --check`, then report only checks actually run.
