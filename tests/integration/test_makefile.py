import os
import subprocess
from pathlib import Path

PROJECT_ROOT = Path(__file__).parents[2]


def test_setup_creates_runtime_directory_before_migration(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    uv = bin_dir / "uv"
    uv.write_text(
        "#!/bin/sh\n"
        'if [ "$1 $2 $3" = "run alembic upgrade" ] && [ ! -d .lab ]; then\n'
        "  exit 1\n"
        "fi\n"
    )
    uv.chmod(0o755)

    result = subprocess.run(
        ["make", "-f", str(PROJECT_ROOT / "Makefile"), "setup"],
        cwd=tmp_path,
        env=os.environ | {"PATH": f"{bin_dir}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0
    assert (tmp_path / ".lab").is_dir()


def test_diff_check_rejects_staged_whitespace_error(tmp_path: Path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(["git", "init", "--quiet"], cwd=repository, check=True)
    tracked = repository / "tracked.txt"
    tracked.write_text("clean\n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)
    environment = os.environ | {
        "GIT_AUTHOR_NAME": "Capability Lab",
        "GIT_AUTHOR_EMAIL": "lab@example.invalid",
        "GIT_COMMITTER_NAME": "Capability Lab",
        "GIT_COMMITTER_EMAIL": "lab@example.invalid",
    }
    subprocess.run(
        ["git", "commit", "--quiet", "-m", "fixture"],
        cwd=repository,
        env=environment,
        check=True,
    )
    tracked.write_text("staged trailing whitespace  \n")
    subprocess.run(["git", "add", "tracked.txt"], cwd=repository, check=True)

    result = subprocess.run(
        ["make", "-f", str(PROJECT_ROOT / "Makefile"), "diff-check"],
        cwd=repository,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode != 0
    assert "trailing whitespace" in result.stdout + result.stderr
