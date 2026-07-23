from __future__ import annotations

import subprocess
from pathlib import Path

from capability_lab.domain.models import PreparedWorkspace, RepositorySpec, WorkspaceDiff


class WorkspaceError(RuntimeError):
    pass


class GitWorktreeManager:
    def __init__(self, root: Path, fixtures: Path) -> None:
        self.root = root
        self.fixtures = fixtures

    @staticmethod
    def _run(repository: Path, *args: str, allowed_returncodes: tuple[int, ...] = (0,)) -> str:
        result = subprocess.run(
            ["git", "-C", str(repository), *args],
            check=False,
            capture_output=True,
            text=True,
            errors="surrogateescape",
        )
        if result.returncode not in allowed_returncodes:
            raise WorkspaceError(result.stderr.strip() or "git command failed")
        return result.stdout

    def prepare(self, repository: RepositorySpec, revision: str, run_id: str) -> PreparedWorkspace:
        source = self.fixtures / repository.fixture
        resolved = self._run(source, "rev-parse", f"{revision}^{{commit}}").strip()
        if resolved != revision:
            raise WorkspaceError(f"revision must be an exact commit: {revision}")
        self.root.mkdir(parents=True, exist_ok=True)
        path = self.root / run_id
        if path.exists():
            raise WorkspaceError(f"worktree path already exists: {path}")
        self._run(source, "worktree", "add", "--quiet", "--detach", str(path), revision)
        return PreparedWorkspace(path=path, repository=source, revision=revision)

    def collect_diff(self, workspace: PreparedWorkspace) -> WorkspaceDiff:
        patch = self._run(workspace.path, "diff", "--binary", "--no-ext-diff", "HEAD")
        untracked = self._run(workspace.path, "ls-files", "--others", "--exclude-standard", "-z")
        for path in filter(None, untracked.split("\0")):
            patch += self._run(
                workspace.path,
                "diff",
                "--binary",
                "--no-ext-diff",
                "--no-index",
                "--",
                "/dev/null",
                path,
                allowed_returncodes=(0, 1),
            )
        status = self._run(workspace.path, "status", "--porcelain", "-z")
        entries = iter(status.split("\0"))
        changed: list[str] = []
        for entry in entries:
            if not entry:
                continue
            changed.append(entry[3:])
            if "R" in entry[:2] or "C" in entry[:2]:
                previous_path = next(entries, "")
                if previous_path:
                    changed.append(previous_path)
        return WorkspaceDiff(patch=patch, changed_paths=tuple(sorted(changed)))

    def destroy(self, workspace: PreparedWorkspace) -> None:
        try:
            self._run(workspace.repository, "worktree", "remove", "--force", str(workspace.path))
        finally:
            self._run(workspace.repository, "worktree", "prune")
