from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from _typeshed import DataclassInstance

from pydantic import BaseModel, ValidationError

from capability_lab.domain.models import (
    HarnessSettings,
    ModelSettings,
    ResolvedConfiguration,
    RunSettings,
    RuntimePaths,
    SandboxSettings,
)
from capability_lab.schemas.config import LabConfig


class ConfigurationError(ValueError):
    pass


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigurationError(f"cannot load JSON-compatible YAML {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise ConfigurationError(f"configuration root must be an object: {path}")
    return value


def _merge(base: dict[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    merged = dict(base)
    for key, value in overlay.items():
        if isinstance(value, Mapping) and isinstance(merged.get(key), dict):
            merged[key] = _merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def canonical_json(value: BaseModel | Mapping[str, Any] | object) -> bytes:
    if isinstance(value, BaseModel):
        data = value.model_dump(mode="json")
    elif is_dataclass(value):
        data = asdict(cast("DataclassInstance", value))
    else:
        data = value
    return json.dumps(data, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def resolve_config(
    defaults: Path,
    harness_profile: Path,
    experiment: Path,
    cli_overrides: Mapping[str, Any] | None = None,
    *,
    model_profile: Path | None = None,
) -> ResolvedConfiguration:
    merged: dict[str, Any] = {}
    provenance: list[str] = []
    paths = (
        defaults,
        *((model_profile,) if model_profile is not None else ()),
        harness_profile,
        experiment,
    )
    for path in paths:
        merged = _merge(merged, _read_yaml(path))
        provenance.append(str(path))
    merged = _merge(merged, cli_overrides or {})
    if cli_overrides:
        provenance.append("cli")
    try:
        config = LabConfig.model_validate(merged)
    except ValidationError as exc:
        raise ConfigurationError(str(exc)) from exc
    settings = RunSettings(
        name=config.name,
        benchmark=config.benchmark,
        harness=HarnessSettings(config.harness.mode, config.harness.kind),
        paths=RuntimePaths(**config.paths.model_dump()),
        sandbox=SandboxSettings(**config.sandbox.model_dump()),
        seed=config.seed,
        repetition_count=config.repetition_count,
        model=ModelSettings(**config.model.model_dump()) if config.model is not None else None,
    )
    digest = hashlib.sha256(canonical_json(settings)).hexdigest()
    return ResolvedConfiguration(settings, digest, tuple(provenance))
