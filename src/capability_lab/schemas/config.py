from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Literal["success", "failure", "timeout"] = "success"


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = ".lab/state.sqlite3"
    artifacts: str = ".lab/artifacts"
    worktrees: str = ".lab/worktrees"
    fixtures: str = ".lab/fixtures"


class LabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "smoke"
    benchmark: str = "benchmarks/releases/smoke@1.0.1.yaml"
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    seed: int = 1
