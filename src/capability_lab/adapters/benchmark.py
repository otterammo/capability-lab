from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from capability_lab.domain.models import (
    BenchmarkRelease,
    ExecutionBudget,
    RepositorySpec,
    ScorerSpec,
    Task,
)

_LEGACY_RELEASE_PATH = Path("releases/smoke@1.0.0.yaml")
_LEGACY_RELEASE_HASH = "749b23212c8a32762f265b450b6071b78b5a0bd259beaac8bd49c6d70b29782f"
_LEGACY_TASK_PATH = Path("tasks/python.fix-incorrect-return@1.0.0.yaml")
_LEGACY_TASK_HASH = "857fc8a724d361739810647eafde1cbca45c77719d10971835d26bda0482fb64"


class BenchmarkIntegrityError(ValueError):
    pass


def _object(path: Path, content: bytes) -> dict[str, Any]:
    try:
        value = json.loads(content)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BenchmarkIntegrityError(f"cannot load {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise BenchmarkIntegrityError(f"manifest root must be an object: {path}")
    return value


def _sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _schema_version(data: dict[str, Any], label: str, *, approved_legacy: bool = False) -> int:
    value = data.get("schema_version")
    if value is None:
        if approved_legacy:
            return 1
        raise BenchmarkIntegrityError(f"{label} schema version missing")
    if type(value) is not int or value not in {1, 2}:
        raise BenchmarkIntegrityError(f"unsupported {label} schema version: {value}")
    if value == 1 and not approved_legacy:
        raise BenchmarkIntegrityError(f"{label} schema version 1 is legacy-only")
    return value


def _benchmark_root(release_path: Path) -> Path:
    resolved = release_path.resolve()
    try:
        return next(parent for parent in resolved.parents if parent.name == "benchmarks")
    except StopIteration as exc:
        raise BenchmarkIntegrityError(
            f"release is outside a benchmark tree: {release_path}"
        ) from exc


def _benchmark_path(root: Path, parent: Path, value: str, label: str) -> Path:
    path = Path(value)
    if path.is_absolute():
        raise BenchmarkIntegrityError(f"{label} path must be relative: {value}")
    resolved = (parent / path).resolve()
    if not resolved.is_relative_to(root):
        raise BenchmarkIntegrityError(f"{label} path escapes benchmark tree: {value}")
    return resolved


def _validate_protected_dependencies(
    root: Path, task_path: Path, data: dict[str, Any], *, required: bool
) -> None:
    referenced = {
        item[key] for item in data["scorers"] for key in ("script", "expected_path") if key in item
    }
    dependencies = data.get("protected_dependencies")
    if dependencies is None:
        if required and referenced:
            raise BenchmarkIntegrityError(
                f"protected dependency hash missing: {sorted(referenced)}"
            )
        return
    declared = {item["path"] for item in dependencies}
    missing = referenced - declared
    if missing:
        raise BenchmarkIntegrityError(f"protected dependency hash missing: {sorted(missing)}")
    for dependency in dependencies:
        dependency_path = _benchmark_path(
            root, task_path.parent, dependency["path"], "protected dependency"
        )
        try:
            content = dependency_path.read_bytes()
        except OSError as exc:
            raise BenchmarkIntegrityError(
                f"cannot load protected dependency: {dependency_path}: {exc}"
            ) from exc
        if _sha256(content) != dependency["sha256"]:
            raise BenchmarkIntegrityError(f"protected dependency hash mismatch: {dependency_path}")


def load_benchmark(release_path: Path) -> BenchmarkRelease:
    benchmark_root = _benchmark_root(release_path)
    protected_root = benchmark_root / "protected"
    release_path = release_path.resolve()
    release_content = release_path.read_bytes()
    release_data = _object(release_path, release_content)
    release_digest = _sha256(release_content)
    approved_legacy_release = (
        release_path == benchmark_root / _LEGACY_RELEASE_PATH
        and release_digest == _LEGACY_RELEASE_HASH
    )
    schema_version = _schema_version(
        release_data, "release", approved_legacy=approved_legacy_release
    )
    tasks: list[Task] = []
    seen: set[tuple[str, str]] = set()
    for reference in release_data.get("tasks", []):
        task_path = _benchmark_path(benchmark_root, release_path.parent, reference["path"], "task")
        content = task_path.read_bytes()
        digest = _sha256(content)
        if digest != reference["sha256"]:
            raise BenchmarkIntegrityError(f"task hash mismatch: {task_path}")
        data = _object(task_path, content)
        approved_legacy_task = (
            approved_legacy_release
            and task_path == benchmark_root / _LEGACY_TASK_PATH
            and digest == _LEGACY_TASK_HASH
        )
        if _schema_version(data, "task", approved_legacy=approved_legacy_task) != schema_version:
            raise BenchmarkIntegrityError(f"task schema version mismatch: {task_path}")
        _validate_protected_dependencies(
            protected_root, task_path, data, required=schema_version == 2
        )
        identity = (data["id"], data["version"])
        if identity in seen:
            raise BenchmarkIntegrityError(f"duplicate task: {identity[0]}@{identity[1]}")
        seen.add(identity)
        scorer_specs = tuple(
            ScorerSpec(
                id=item["id"],
                type=item["type"],
                options={
                    key: (
                        str(
                            _benchmark_path(
                                protected_root,
                                task_path.parent,
                                value,
                                "protected dependency",
                            )
                        )
                        if key in {"script", "expected_path"}
                        else value
                    )
                    for key, value in item.items()
                    if key not in {"id", "type"}
                },
            )
            for item in data["scorers"]
        )
        tasks.append(
            Task(
                id=data["id"],
                version=data["version"],
                capabilities=tuple(data["capabilities"]),
                repository=RepositorySpec(**data["repository"]),
                prompt=data["prompt"],
                budget=ExecutionBudget(**data["budget"]),
                scorers=scorer_specs,
                content_hash=digest,
            )
        )
    if not tasks:
        raise BenchmarkIntegrityError("benchmark release has no tasks")
    return BenchmarkRelease(
        id=release_data["id"],
        version=release_data["version"],
        channel=release_data["channel"],
        tasks=tuple(tasks),
        content_hash=release_digest,
    )


def validate_fixture_revisions(benchmark: BenchmarkRelease, fixtures: Path) -> None:
    for task in benchmark.tasks:
        repository = fixtures / task.repository.fixture
        result = subprocess.run(
            ["git", "-C", str(repository), "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        observed = result.stdout.strip()
        if result.returncode != 0 or observed != task.repository.revision:
            raise BenchmarkIntegrityError(
                "fixture revision mismatch: "
                f"{task.repository.fixture} expected {task.repository.revision}, "
                f"got {observed or 'missing'}"
            )
