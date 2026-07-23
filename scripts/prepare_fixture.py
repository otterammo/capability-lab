from pathlib import Path

from capability_lab.adapters.fixtures import ensure_fixture_repository

root = Path.cwd()
revision = ensure_fixture_repository(
    root / "benchmarks/fixtures/incorrect-function",
    root / ".lab/fixtures/incorrect-function",
)
print(revision)
