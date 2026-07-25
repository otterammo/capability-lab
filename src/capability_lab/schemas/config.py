from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from capability_lab.domain.rules import ollama_hostname


class HarnessConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    kind: Literal["fake", "pi", "raw-ollama"] = "fake"
    mode: Literal["success", "failure", "timeout"] = "success"


class PathsConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    state: str = ".lab/state.sqlite3"
    artifacts: str = ".lab/artifacts"
    worktrees: str = ".lab/worktrees"
    fixtures: str = ".lab/fixtures"


class SandboxConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    image: str = Field(default="capability-lab-sandbox:0.1.0", min_length=1)
    cpus: float = Field(default=1.0, gt=0, le=8.0)
    memory_mb: int = Field(default=512, ge=6, le=32768)
    pids: int = Field(default=128, ge=1, le=4096)
    nofile: int = Field(default=256, ge=16, le=65536)
    max_output_bytes: int = Field(default=1_048_576, ge=1, le=16_777_216)


class ModelConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["ollama"]
    name: str = Field(min_length=1)
    base_url: str
    timeout_seconds: float = Field(default=2.0, gt=0, le=30)
    temperature: float = Field(default=0.0, ge=0, le=2)
    context_window: int = Field(default=32768, ge=1, le=1_048_576)
    max_output_tokens: int = Field(default=512, ge=1, le=1_048_576)
    expected_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("base_url")
    @classmethod
    def validate_base_url(cls, value: str) -> str:
        ollama_hostname(value)
        return value

    @model_validator(mode="after")
    def validate_output_within_context(self) -> ModelConfig:
        if self.max_output_tokens > self.context_window:
            raise ValueError("max_output_tokens must not exceed context_window")
        return self


class LabConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = "smoke"
    benchmark: str = "benchmarks/releases/smoke@1.0.1.yaml"
    harness: HarnessConfig = Field(default_factory=HarnessConfig)
    paths: PathsConfig = Field(default_factory=PathsConfig)
    sandbox: SandboxConfig = Field(default_factory=SandboxConfig)
    seed: int = 1
    repetition_count: Literal[1] = 1
    model: ModelConfig | None = None
