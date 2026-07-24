import subprocess
from pathlib import Path

from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.adapters.workspaces import GitWorktreeManager
from capability_lab.domain.models import RepositorySpec


def test_worktree_is_isolated_and_removed(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "example.py").write_text("wrong\n")
    source = tmp_path / "fixtures" / "fixture"
    revision = ensure_fixture_repository(template, source)
    manager = GitWorktreeManager(tmp_path / "worktrees", tmp_path / "fixtures")

    workspace = manager.prepare(RepositorySpec("fixture", revision), revision, "run-1")
    (workspace.path / "example.py").write_text("right\n")
    (workspace.path / "new.py").write_text("new\n")
    diff = manager.collect_diff(workspace)
    manager.destroy(workspace)

    assert diff.changed_paths == ("example.py", "new.py")
    assert "diff --git a/example.py b/example.py" in diff.patch
    assert "diff --git a/new.py b/new.py" in diff.patch
    assert "+new" in diff.patch
    assert not workspace.path.exists()
    assert (source / "example.py").read_text() == "wrong\n"
    assert (
        subprocess.run(
            ["git", "status", "--porcelain"], cwd=source, check=True, capture_output=True, text=True
        ).stdout
        == ""
    )


def test_collect_diff_reports_both_paths_for_rename(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "old.py").write_text("content\n")
    source = tmp_path / "fixtures" / "fixture"
    revision = ensure_fixture_repository(template, source)
    manager = GitWorktreeManager(tmp_path / "worktrees", tmp_path / "fixtures")
    workspace = manager.prepare(RepositorySpec("fixture", revision), revision, "run-rename")

    subprocess.run(["git", "-C", str(workspace.path), "mv", "old.py", "new.py"], check=True)
    diff = manager.collect_diff(workspace)
    manager.destroy(workspace)

    assert diff.changed_paths == ("new.py", "old.py")


def test_collect_diff_captures_ignored_untracked_files(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / ".gitignore").write_text("generated/\n")
    (template / "example.py").write_text("wrong\n")
    source = tmp_path / "fixtures" / "fixture"
    revision = ensure_fixture_repository(template, source)
    manager = GitWorktreeManager(tmp_path / "worktrees", tmp_path / "fixtures")
    workspace = manager.prepare(RepositorySpec("fixture", revision), revision, "run-ignored")

    generated = workspace.path / "generated" / "answer.txt"
    generated.parent.mkdir()
    generated.write_text("hidden harness output\n")
    diff = manager.collect_diff(workspace)
    manager.destroy(workspace)

    assert diff.changed_paths == ("generated/answer.txt",)
    assert "diff --git a/generated/answer.txt b/generated/answer.txt" in diff.patch
    assert "+hidden harness output" in diff.patch
