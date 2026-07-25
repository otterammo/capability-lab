from __future__ import annotations

import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Annotated

import typer
from rich.console import Console
from rich.table import Table

from capability_lab.bootstrap import build_service
from capability_lab.domain.models import ComparisonResult, FailureClassification, RunResult

app = typer.Typer(help="Deterministic local capability experiments.", no_args_is_help=True)
config_app = typer.Typer(help="Resolve experiment configuration.")
benchmark_app = typer.Typer(help="Validate immutable benchmark releases.")
app.add_typer(config_app, name="config")
app.add_typer(benchmark_app, name="benchmark")
console = Console()


def _root() -> Path:
    return Path.cwd()


def _paths(root: Path) -> tuple[Path, Path, Path]:
    return (
        root / "configs/defaults.yaml",
        root / "configs/harnesses/fake.yaml",
        root / "configs/experiments/smoke.yaml",
    )


@app.command()
def doctor() -> None:
    """Check the deterministic slice's local prerequisites and state."""
    checks = build_service(_root()).doctor()
    table = Table("Check", "Status", "Details")
    for check in checks:
        status = "ok" if check.passed else "fail" if check.required else "optional-missing"
        table.add_row(check.name, status, check.details)
    console.print(table)
    if any(check.required and not check.passed for check in checks):
        raise typer.Exit(1)


@config_app.command("resolve")
def config_resolve(
    experiment: Annotated[Path, typer.Argument()] = Path("configs/experiments/smoke.yaml"),
    seed: Annotated[int | None, typer.Option("--seed")] = None,
    model_profile: Annotated[Path | None, typer.Option("--model-profile")] = None,
) -> None:
    """Resolve defaults, optional model, fake harness, experiment, and CLI overrides."""
    root = _root()
    resolved = build_service(root).resolve_configuration(
        root / "configs/defaults.yaml",
        root / "configs/harnesses/fake.yaml",
        root / experiment,
        {} if seed is None else {"seed": seed},
        model_profile=None if model_profile is None else root / model_profile,
    )
    console.print(json.dumps(asdict(resolved.value), sort_keys=True, separators=(",", ":")))
    console.print(f"config hash: {resolved.hash}")
    console.print(f"provenance: {', '.join(resolved.provenance)}")


@benchmark_app.command("validate")
def benchmark_validate(
    release: Annotated[Path, typer.Argument()] = Path("benchmarks/releases/smoke@1.0.1.yaml"),
) -> None:
    """Validate release and task content hashes."""
    benchmark = build_service(_root()).validate_benchmark(_root() / release)
    console.print(
        f"valid: {benchmark.id}@{benchmark.version} "
        f"({len(benchmark.tasks)} task, sha256:{benchmark.content_hash})"
    )


@app.command()
def smoke() -> None:
    """Run the deterministic smoke benchmark end to end."""
    root = _root()
    result = build_service(root).run(*_paths(root))
    _render_run(result)


@app.command()
def run(
    experiment: Annotated[Path, typer.Argument()] = Path("configs/experiments/pi-smoke.yaml"),
) -> None:
    """Run one explicit Pi experiment profile through the local Ollama model."""
    root = _root()
    ollama_host = os.environ.get("OLLAMA_HOST")
    result = build_service(root).run(
        root / "configs/defaults.yaml",
        root / "configs/harnesses/pi.yaml",
        root / experiment,
        overrides=(None if ollama_host is None else {"model": {"base_url": ollama_host}}),
        model_profile=root / "configs/models/ollama.yaml",
    )
    _render_run(result)


@app.command()
def compare(
    experiment: Annotated[Path, typer.Argument()] = Path("configs/experiments/raw-vs-pi.yaml"),
) -> None:
    """Run one raw-Ollama versus Pi comparison."""
    root = _root()
    ollama_host = os.environ.get("OLLAMA_HOST")
    if ollama_host != "http://desktop:11434":
        console.print("OLLAMA_HOST must be exactly http://desktop:11434")
        raise typer.Exit(2)
    result = build_service(root).compare(
        root / "configs/defaults.yaml",
        root / "configs/harnesses/raw-ollama.yaml",
        root / "configs/harnesses/pi.yaml",
        root / experiment,
        overrides={"model": {"base_url": ollama_host}},
        model_profile=root / "configs/models/ollama.yaml",
    )
    _render_comparison(result)


def _render_run(result: RunResult) -> None:
    table = Table("Scorer", "Passed", "Details")
    for score in result.scores:
        table.add_row(score.scorer_id, str(score.passed).lower(), score.details)
    console.print(f"Experiment ID: {result.experiment_id}")
    console.print(f"Run ID: {result.run_id}")
    console.print(table)
    console.print(f"Diff: {len(result.diff.changed_paths)} changed path(s)")
    console.print(f"Artifacts: {result.artifact_dir}")
    console.print(f"Cleanup: {'ok' if result.cleanup_succeeded else 'failed'}")
    console.print(f"Classification: {result.classification.value}")
    if result.classification is not FailureClassification.SUCCESS:
        if result.failure:
            console.print(f"Failure: {result.failure}")
        raise typer.Exit(1)


def _render_comparison(result: ComparisonResult) -> None:
    console.print(f"Comparison ID: {result.id}")
    console.print(f"Baseline run: {result.baseline.run_id}")
    console.print(f"Candidate run: {result.candidate.run_id}")
    console.print(f"Comparable: {str(result.comparable).lower()}")
    if result.artifact is not None:
        console.print(f"Report: {result.artifact.run_path}")
    if not result.comparable:
        raise typer.Exit(1)


@app.command()
def inspect(run_id: Annotated[str, typer.Argument()]) -> None:
    """Show persisted metadata and scores for one run."""
    console.print_json(json.dumps(build_service(_root()).inspect(run_id), default=str))


if __name__ == "__main__":
    app()
