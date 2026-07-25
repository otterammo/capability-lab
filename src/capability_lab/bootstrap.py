from __future__ import annotations

import platform
import sqlite3
import subprocess
import sys
import tempfile
from pathlib import Path

from capability_lab.adapters.artifacts import FilesystemArtifactStore
from capability_lab.adapters.benchmark import load_benchmark, validate_fixture_revisions
from capability_lab.adapters.config import resolve_config
from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.adapters.harnesses import FakeHarness
from capability_lab.adapters.models import OllamaError, OllamaModelAdapter
from capability_lab.adapters.persistence import SqliteMetadataRepository
from capability_lab.adapters.pi_harness import PiHarness
from capability_lab.adapters.raw_ollama_harness import RawOllamaHarness
from capability_lab.adapters.sandboxes import DockerSandbox
from capability_lab.adapters.scorers import build_scorer
from capability_lab.adapters.workspaces import GitWorktreeManager
from capability_lab.application.service import LabService, Runtime
from capability_lab.domain.models import (
    BenchmarkRelease,
    DoctorCheck,
    ResolvedConfiguration,
    RunProvenance,
    Task,
)


def _docker_check(image: str) -> DoctorCheck:
    try:
        cli = subprocess.run(
            ["docker", "--version"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except FileNotFoundError:
        return DoctorCheck("Docker", False, "CLI unavailable", required=False)
    except subprocess.TimeoutExpired:
        return DoctorCheck("Docker", False, "CLI timeout", required=False)
    if cli.returncode != 0:
        return DoctorCheck("Docker", False, "CLI unavailable", required=False)
    cli_detail = cli.stdout.strip()

    try:
        server = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        return DoctorCheck("Docker", False, f"{cli_detail}; daemon timeout", required=False)
    if server.returncode != 0:
        detail = (
            "daemon permission denied"
            if "permission denied" in server.stderr.lower()
            else "daemon unavailable"
        )
        return DoctorCheck("Docker", False, f"{cli_detail}; {detail}", required=False)
    server_detail = f"server {server.stdout.strip()}"

    try:
        inspected = subprocess.run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", image],
            capture_output=True,
            text=True,
            check=False,
            timeout=2,
        )
    except subprocess.TimeoutExpired:
        return DoctorCheck(
            "Docker", False, f"{cli_detail}; {server_detail}; image check timeout", required=False
        )
    if inspected.returncode != 0:
        return DoctorCheck(
            "Docker",
            False,
            f"{cli_detail}; {server_detail}; image missing: {image}",
            required=False,
        )
    return DoctorCheck(
        "Docker",
        True,
        f"{cli_detail}; {server_detail}; image {image} present",
        required=False,
    )


def _git(root: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(root), *args], capture_output=True, text=True, check=False
    )
    return result.stdout.strip() if result.returncode == 0 else "unavailable"


def build_service(project_root: Path) -> LabService:
    root = project_root.resolve()

    def initialize_fixture() -> str:
        return ensure_fixture_repository(
            root / "benchmarks/fixtures/incorrect-function",
            root / ".lab/fixtures/incorrect-function",
        )

    def runtime(config: ResolvedConfiguration) -> Runtime:
        paths = config.value.paths
        model = config.value.model
        if config.value.harness.kind == "fake":
            harness = FakeHarness()
        elif config.value.harness.kind == "pi":
            if model is None:
                raise ValueError("Pi harness requires a model profile")
            harness = PiHarness(
                DockerSandbox(
                    config.value.sandbox,
                    ollama_base_url=model.base_url,
                    dockerfile=root / "docker/sandbox/Dockerfile",
                ),
                model.name,
                seed=config.value.seed,
                temperature=model.temperature,
                context_window=model.context_window,
                max_output_tokens=model.max_output_tokens,
            )
        else:
            if model is None:
                raise ValueError("raw Ollama harness requires a model profile")
            harness = RawOllamaHarness(
                DockerSandbox(
                    config.value.sandbox,
                    ollama_base_url=model.base_url,
                    dockerfile=root / "docker/sandbox/Dockerfile",
                ),
                model.name,
                seed=config.value.seed,
                temperature=model.temperature,
                max_output_tokens=model.max_output_tokens,
            )
        return Runtime(
            metadata=SqliteMetadataRepository(root / paths.state),
            artifacts=FilesystemArtifactStore(root / paths.artifacts),
            workspaces=GitWorktreeManager(root / paths.worktrees, root / paths.fixtures),
            harness=harness,
        )

    def provenance(
        config: ResolvedConfiguration, benchmark: BenchmarkRelease, task: Task
    ) -> RunProvenance:
        status = _git(root, "status", "--porcelain")
        model = config.value.model
        model_identity = (
            None
            if model is None
            else OllamaModelAdapter(model.base_url, model.name, model.timeout_seconds).identity(
                model.expected_digest
            )
        )
        return RunProvenance(
            benchmark_hash=benchmark.content_hash,
            task_hash=task.content_hash,
            config_hash=config.hash,
            fixture_revision=task.repository.revision,
            python_version=platform.python_version(),
            platform=platform.platform(),
            seed=config.value.seed,
            platform_commit=_git(root, "rev-parse", "HEAD"),
            platform_dirty=bool(status and status != "unavailable"),
            harness_version=(
                FakeHarness.version
                if config.value.harness.kind == "fake"
                else PiHarness.version
                if config.value.harness.kind == "pi"
                else RawOllamaHarness.version
            ),
            model_identity=model_identity,
        )

    def validate_fixtures(benchmark: BenchmarkRelease) -> None:
        validate_fixture_revisions(benchmark, root / ".lab/fixtures")

    def health() -> tuple[DoctorCheck, ...]:
        checks: list[DoctorCheck] = [
            DoctorCheck("Python", sys.version_info >= (3, 13), platform.python_version())
        ]
        try:
            config = resolve_config(
                root / "configs/defaults.yaml",
                root / "configs/harnesses/fake.yaml",
                root / "configs/experiments/smoke.yaml",
                {},
                model_profile=root / "configs/models/ollama.yaml",
            )
            checks.append(_docker_check(config.value.sandbox.image))
        except ValueError:
            checks.append(DoctorCheck("Docker", False, "configuration unavailable", required=False))
            checks.append(DoctorCheck("Ollama", False, "configuration unavailable", required=False))
        else:
            model = config.value.model
            if model is None:
                checks.append(
                    DoctorCheck("Ollama", False, "configuration unavailable", required=False)
                )
            else:
                try:
                    identity = OllamaModelAdapter(
                        model.base_url, model.name, model.timeout_seconds
                    ).identity(model.expected_digest)
                    checks.append(
                        DoctorCheck(
                            "Ollama",
                            True,
                            f"{identity.name}; {identity.digest}; "
                            f"{identity.parameter_size} {identity.quantization_level}; "
                            f"server {identity.server_version or 'unknown'}",
                            required=False,
                        )
                    )
                except OllamaError as exc:
                    checks.append(DoctorCheck("Ollama", False, str(exc), required=False))
        git = subprocess.run(["git", "--version"], capture_output=True, text=True, check=False)
        checks.append(
            DoctorCheck("Git", git.returncode == 0, git.stdout.strip() or git.stderr.strip())
        )
        try:
            with sqlite3.connect(":memory:") as connection:
                connection.execute("SELECT 1").fetchone()
            checks.append(DoctorCheck("SQLite", True, sqlite3.sqlite_version))
        except sqlite3.Error as exc:
            checks.append(DoctorCheck("SQLite", False, str(exc)))
        revision = SqliteMetadataRepository(root / ".lab/state.sqlite3").schema_revision()
        checks.append(DoctorCheck("Migrations", revision == "0002", revision or "not applied"))
        artifact_root = root / ".lab/artifacts"
        try:
            artifact_root.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(dir=artifact_root):
                pass
            checks.append(DoctorCheck("Artifacts", True, str(artifact_root)))
        except OSError as exc:
            checks.append(DoctorCheck("Artifacts", False, str(exc)))
        try:
            benchmark = load_benchmark(root / "benchmarks/releases/smoke@1.0.1.yaml")
            validate_fixtures(benchmark)
            dirty = _git(root / ".lab/fixtures/incorrect-function", "status", "--porcelain")
            checks.append(DoctorCheck("Fixture", not dirty, benchmark.tasks[0].repository.revision))
        except Exception as exc:
            checks.append(DoctorCheck("Fixture", False, str(exc)))
        return tuple(checks)

    def read_run(run_id: str) -> dict[str, object]:
        return SqliteMetadataRepository(root / ".lab/state.sqlite3").get_run(run_id)

    return LabService(
        root,
        resolve_config,
        load_benchmark,
        initialize_fixture,
        runtime,
        build_scorer,
        provenance,
        validate_fixtures,
        health,
        read_run,
    )
