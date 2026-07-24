from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path


class FixtureError(RuntimeError):
    pass


def _git(repository: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repository), *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return result.stdout.strip()


def ensure_fixture_repository(template: Path, destination: Path) -> str:
    if (destination / ".git").is_dir():
        if _git(destination, "status", "--porcelain"):
            raise FixtureError(f"fixture repository is dirty: {destination}")
        return _git(destination, "rev-parse", "HEAD")
    if destination.exists():
        raise FixtureError(f"fixture destination exists but is not a Git repository: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(template, destination)
    _git(destination, "init", "--quiet", "--initial-branch=main")
    _git(destination, "add", ".")
    environment = os.environ.copy()
    environment.update(
        {
            "GIT_AUTHOR_NAME": "Capability Lab",
            "GIT_AUTHOR_EMAIL": "lab@example.invalid",
            "GIT_AUTHOR_DATE": "2000-01-01T00:00:00Z",
            "GIT_COMMITTER_NAME": "Capability Lab",
            "GIT_COMMITTER_EMAIL": "lab@example.invalid",
            "GIT_COMMITTER_DATE": "2000-01-01T00:00:00Z",
        }
    )
    subprocess.run(
        ["git", "-C", str(destination), "commit", "--quiet", "-m", "fixture v1"],
        check=True,
        env=environment,
    )
    return _git(destination, "rev-parse", "HEAD")
