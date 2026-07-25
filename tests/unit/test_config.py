import json
from pathlib import Path

import pytest

from capability_lab.adapters.config import canonical_json, resolve_config
from capability_lab.schemas.config import ModelConfig


def test_config_precedence_and_hash_ignore_yaml_formatting(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    harness = tmp_path / "harness.yaml"
    experiment = tmp_path / "experiment.yaml"
    defaults.write_text('{"harness":{"mode":"success"},"seed":1}\n')
    harness.write_text('{ "harness": { "mode": "failure" } }\n')
    experiment.write_text('{"name":"smoke","harness":{"mode":"success"}}\n')

    first = resolve_config(defaults, harness, experiment, {"seed": 7})
    defaults.write_text('{\n  "seed": 1,\n  "harness": {"mode": "success"}\n}\n')
    second = resolve_config(defaults, harness, experiment, {"seed": 7})

    assert first.value.seed == 7
    assert first.value.harness.mode == "success"
    assert first.hash == second.hash
    encoded = canonical_json(first.value)
    assert list(json.loads(encoded)) == sorted(json.loads(encoded))


def test_sandbox_settings_are_resolved_and_change_the_config_hash(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    harness = tmp_path / "harness.yaml"
    experiment = tmp_path / "experiment.yaml"
    defaults.write_text('{"sandbox":{"image":"capability-lab-sandbox:0.1.0"}}\n')
    harness.write_text("{}\n")
    experiment.write_text("{}\n")

    first = resolve_config(defaults, harness, experiment)
    second = resolve_config(defaults, harness, experiment, {"sandbox": {"cpus": 2.0}})

    assert first.value.sandbox.image == "capability-lab-sandbox:0.1.0"
    assert first.value.sandbox.cpus == 1.0
    assert first.value.sandbox.memory_mb == 512
    assert first.value.sandbox.pids == 128
    assert first.value.sandbox.nofile == 256
    assert first.value.sandbox.max_output_bytes == 1_048_576
    assert first.hash != second.hash


def test_optional_model_profile_layers_between_defaults_and_harness(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    model = tmp_path / "model.yaml"
    harness = tmp_path / "harness.yaml"
    experiment = tmp_path / "experiment.yaml"
    defaults.write_text("{}\n")
    model.write_text(
        json.dumps(
            {
                "model": {
                    "provider": "ollama",
                    "name": "qwen2.5-coder:1.5b",
                    "base_url": "http://127.0.0.1:11434",
                    "timeout_seconds": 2.0,
                    "expected_digest": "a" * 64,
                }
            }
        )
    )
    harness.write_text("{}\n")
    experiment.write_text("{}\n")

    without_model = resolve_config(defaults, harness, experiment)
    harness.write_text('{"model":{"timeout_seconds":3.0}}\n')
    with_model = resolve_config(defaults, harness, experiment, model_profile=model)
    overridden = resolve_config(
        defaults,
        harness,
        experiment,
        {"model": {"base_url": "http://desktop:11434"}},
        model_profile=model,
    )

    assert without_model.value.model is None
    assert with_model.value.model is not None
    assert with_model.value.model.name == "qwen2.5-coder:1.5b"
    assert with_model.value.model.timeout_seconds == 3.0
    assert with_model.value.model.expected_digest == "a" * 64
    assert with_model.provenance == (
        str(defaults),
        str(model),
        str(harness),
        str(experiment),
    )
    assert without_model.hash != with_model.hash
    assert overridden.value.model is not None
    assert overridden.value.model.base_url == "http://desktop:11434"
    assert overridden.hash != with_model.hash


@pytest.mark.parametrize("host", ["127.0.0.1", "localhost", "desktop"])
def test_model_base_url_accepts_only_authorized_local_hosts(host: str) -> None:
    model = ModelConfig.model_validate(
        {
            "provider": "ollama",
            "name": "qwen2.5-coder:1.5b",
            "base_url": f"http://{host}:11434",
        }
    )

    assert model.base_url == f"http://{host}:11434"


@pytest.mark.parametrize(
    ("overrides", "field"),
    [
        ({"temperature": -0.01}, "temperature"),
        ({"temperature": 2.01}, "temperature"),
        ({"context_window": 0}, "context_window"),
        ({"max_output_tokens": 0}, "max_output_tokens"),
        (
            {"context_window": 128, "max_output_tokens": 129},
            "max_output_tokens",
        ),
    ],
)
def test_model_request_settings_reject_invalid_values(
    overrides: dict[str, float | int], field: str
) -> None:
    values: dict[str, object] = {
        "provider": "ollama",
        "name": "qwen2.5-coder:1.5b",
        "base_url": "http://desktop:11434",
        "temperature": 0.0,
        "context_window": 32768,
        "max_output_tokens": 512,
    }
    values.update(overrides)

    with pytest.raises(ValueError, match=field):
        ModelConfig.model_validate(values)


@pytest.mark.parametrize(
    "base_url",
    [
        "https://desktop:11434",
        "http://desktop:11435",
        "http://user:secret@desktop:11434",
        "http://desktop:11434/api",
        "http://desktop:11434?query=1",
        "http://desktop:11434#fragment",
        "http://--help:11434",
        "http://127.0.0.2:11434",
        "http://example.com:11434",
        "http://127.0.0.1.nip.io:11434",
        "http://localhost.example.com:11434",
        "http://169.254.169.254:11434",
        "http://metadata.google.internal:11434",
    ],
)
def test_model_base_url_rejects_non_authorized_or_malformed_hosts(base_url: str) -> None:
    with pytest.raises(ValueError, match="base_url"):
        ModelConfig.model_validate(
            {
                "provider": "ollama",
                "name": "qwen2.5-coder:1.5b",
                "base_url": base_url,
            }
        )


def test_harness_profile_selects_a_validated_concrete_kind(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    harness = tmp_path / "harness.yaml"
    experiment = tmp_path / "experiment.yaml"
    defaults.write_text("{}\n")
    harness.write_text('{"harness":{"kind":"pi"}}\n')
    experiment.write_text("{}\n")

    resolved = resolve_config(defaults, harness, experiment)

    assert resolved.value.harness.kind == "pi"

    harness.write_text('{"harness":{"kind":"plugin"}}\n')
    with pytest.raises(ValueError, match="harness.kind"):
        resolve_config(defaults, harness, experiment)


def test_model_profile_rejects_prefixed_digest(tmp_path: Path) -> None:
    defaults = tmp_path / "defaults.yaml"
    model = tmp_path / "model.yaml"
    harness = tmp_path / "harness.yaml"
    experiment = tmp_path / "experiment.yaml"
    defaults.write_text("{}\n")
    model.write_text(
        json.dumps(
            {
                "model": {
                    "provider": "ollama",
                    "name": "qwen2.5-coder:1.5b",
                    "base_url": "http://127.0.0.1:11434",
                    "expected_digest": "sha256:" + "a" * 64,
                }
            }
        )
    )
    harness.write_text("{}\n")
    experiment.write_text("{}\n")

    with pytest.raises(ValueError, match="expected_digest"):
        resolve_config(defaults, harness, experiment, model_profile=model)


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("cpus", 0),
        ("memory_mb", 5),
        ("pids", 0),
        ("nofile", 15),
        ("max_output_bytes", 16_777_217),
    ],
)
def test_sandbox_settings_reject_values_outside_authored_policy(
    tmp_path: Path, key: str, value: int
) -> None:
    defaults = tmp_path / "defaults.yaml"
    harness = tmp_path / "harness.yaml"
    experiment = tmp_path / "experiment.yaml"
    defaults.write_text(json.dumps({"sandbox": {key: value}}))
    harness.write_text("{}\n")
    experiment.write_text("{}\n")

    with pytest.raises(ValueError) as exc_info:
        resolve_config(defaults, harness, experiment)

    assert f"sandbox.{key}" in str(exc_info.value)
