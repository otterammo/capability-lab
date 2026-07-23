import json
from pathlib import Path

from capability_lab.adapters.config import canonical_json, resolve_config


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
