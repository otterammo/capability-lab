import hashlib
import json
from pathlib import Path

import pytest

from capability_lab.adapters.benchmark import BenchmarkIntegrityError, load_benchmark


def test_benchmark_rejects_changed_task(tmp_path: Path) -> None:
    task = tmp_path / "benchmarks/tasks/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "task",
                "version": "1.0.1",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [],
            }
        )
    )
    release = tmp_path / "benchmarks/releases/release.yaml"
    release.parent.mkdir()
    release.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/task.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    assert load_benchmark(release).tasks[0].id == "task"

    task.write_text(task.read_text() + "\n")

    with pytest.raises(BenchmarkIntegrityError, match="hash mismatch"):
        load_benchmark(release)


def test_approved_legacy_benchmark_loads() -> None:
    release = Path(__file__).parents[2] / "benchmarks/releases/smoke@1.0.0.yaml"

    assert load_benchmark(release).version == "1.0.0"


def test_newly_authored_1_0_0_without_schema_is_rejected(tmp_path: Path) -> None:
    task = tmp_path / "benchmarks/tasks/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text(
        json.dumps(
            {
                "id": "task",
                "version": "1.0.0",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [],
            }
        )
    )
    release = tmp_path / "benchmarks/releases/release.yaml"
    release.parent.mkdir()
    release.write_text(
        json.dumps(
            {
                "id": "smoke",
                "version": "1.0.0",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/task.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )

    with pytest.raises(BenchmarkIntegrityError, match="release schema version missing"):
        load_benchmark(release)


def test_benchmark_rejects_changed_protected_dependency(tmp_path: Path) -> None:
    protected = tmp_path / "benchmarks/protected/protected.py"
    protected.parent.mkdir(parents=True)
    protected.write_text("expected = True\n")
    task = tmp_path / "benchmarks/tasks/task.yaml"
    task.parent.mkdir()
    task.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "task",
                "version": "1.0.1",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [
                    {
                        "id": "protected-test",
                        "type": "command",
                        "script": "../protected/protected.py",
                    }
                ],
                "protected_dependencies": [
                    {
                        "path": "../protected/protected.py",
                        "sha256": hashlib.sha256(protected.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    release = tmp_path / "benchmarks/releases/release.yaml"
    release.parent.mkdir()
    release.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/task.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    assert load_benchmark(release).tasks[0].id == "task"

    protected.write_text("expected = False\n")

    with pytest.raises(BenchmarkIntegrityError, match="protected dependency hash mismatch"):
        load_benchmark(release)


def test_schema_v2_requires_protected_dependency_hashes(tmp_path: Path) -> None:
    task = tmp_path / "benchmarks/tasks/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "task",
                "version": "1.0.1",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [
                    {
                        "id": "protected-test",
                        "type": "command",
                        "script": "../protected/protected.py",
                    }
                ],
            }
        )
    )
    release = tmp_path / "benchmarks/releases/release.yaml"
    release.parent.mkdir()
    release.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/task.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )

    with pytest.raises(BenchmarkIntegrityError, match="protected dependency hash missing"):
        load_benchmark(release)


def test_schema_v2_release_rejects_legacy_task(tmp_path: Path) -> None:
    task = tmp_path / "benchmarks/tasks/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text(
        json.dumps(
            {
                "id": "task",
                "version": "1.0.0",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [],
            }
        )
    )
    release = tmp_path / "benchmarks/releases/release.yaml"
    release.parent.mkdir()
    release.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/task.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )

    with pytest.raises(BenchmarkIntegrityError, match="task schema version"):
        load_benchmark(release)


def test_nonlegacy_release_requires_schema_version(tmp_path: Path) -> None:
    task = tmp_path / "benchmarks/tasks/task.yaml"
    task.parent.mkdir(parents=True)
    task.write_text(
        json.dumps(
            {
                "id": "task",
                "version": "1.0.1",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [],
            }
        )
    )
    release = tmp_path / "benchmarks/releases/release.yaml"
    release.parent.mkdir()
    release.write_text(
        json.dumps(
            {
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/task.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )

    with pytest.raises(BenchmarkIntegrityError, match="release schema version missing"):
        load_benchmark(release)


@pytest.mark.parametrize("escape", ["absolute", "parent", "symlink"])
def test_benchmark_rejects_task_path_outside_tree(tmp_path: Path, escape: str) -> None:
    outside = tmp_path / "outside-task.yaml"
    outside.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "task",
                "version": "1.0.1",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [],
            }
        )
    )
    benchmark_root = tmp_path / "benchmarks"
    release = benchmark_root / "releases/release.yaml"
    release.parent.mkdir(parents=True)
    if escape == "absolute":
        task_reference = str(outside)
    elif escape == "parent":
        task_reference = "../../outside-task.yaml"
    else:
        link = benchmark_root / "tasks/task.yaml"
        link.parent.mkdir()
        link.symlink_to(outside)
        task_reference = "../tasks/task.yaml"
    release.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": task_reference,
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )

    with pytest.raises(BenchmarkIntegrityError, match="task path"):
        load_benchmark(release)


@pytest.mark.parametrize("escape", ["absolute", "parent", "symlink"])
def test_benchmark_rejects_protected_dependency_outside_tree(tmp_path: Path, escape: str) -> None:
    outside = tmp_path / "outside.py"
    outside.write_text("expected = True\n")
    benchmark_root = tmp_path / "benchmarks"
    if escape == "absolute":
        dependency_path = str(outside)
    elif escape == "parent":
        dependency_path = "../../outside.py"
    else:
        link = benchmark_root / "protected/protected.py"
        link.parent.mkdir(parents=True)
        link.symlink_to(outside)
        dependency_path = "../protected/protected.py"
    task = benchmark_root / "tasks/task.yaml"
    task.parent.mkdir(parents=True, exist_ok=True)
    task.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "task",
                "version": "1.0.1",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [{"id": "protected-test", "type": "command", "script": dependency_path}],
                "protected_dependencies": [
                    {
                        "path": dependency_path,
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    release = benchmark_root / "releases/release.yaml"
    release.parent.mkdir()
    release.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/task.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )

    with pytest.raises(BenchmarkIntegrityError, match="protected dependency path"):
        load_benchmark(release)


@pytest.mark.parametrize("escape", ["sibling", "symlink"])
def test_benchmark_rejects_protected_dependency_outside_protected_directory(
    tmp_path: Path, escape: str
) -> None:
    benchmark_root = tmp_path / "benchmarks"
    outside = benchmark_root / "tasks/dependency.py"
    outside.parent.mkdir(parents=True)
    outside.write_text("expected = True\n")
    if escape == "sibling":
        dependency_path = "dependency.py"
    else:
        link = benchmark_root / "protected/protected.py"
        link.parent.mkdir()
        link.symlink_to(outside)
        dependency_path = "../protected/protected.py"
    task = benchmark_root / "tasks/task.yaml"
    task.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "task",
                "version": "1.0.1",
                "capabilities": ["editing"],
                "repository": {"fixture": "fixture", "revision": "abc"},
                "prompt": "fix it",
                "budget": {"timeout_seconds": 10, "max_tool_calls": 1},
                "scorers": [{"id": "protected-test", "type": "command", "script": dependency_path}],
                "protected_dependencies": [
                    {
                        "path": dependency_path,
                        "sha256": hashlib.sha256(outside.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )
    release = benchmark_root / "releases/release.yaml"
    release.parent.mkdir()
    release.write_text(
        json.dumps(
            {
                "schema_version": 2,
                "id": "smoke",
                "version": "1.0.1",
                "channel": "development",
                "tasks": [
                    {
                        "path": "../tasks/task.yaml",
                        "sha256": hashlib.sha256(task.read_bytes()).hexdigest(),
                    }
                ],
            }
        )
    )

    with pytest.raises(BenchmarkIntegrityError, match="protected dependency path"):
        load_benchmark(release)
