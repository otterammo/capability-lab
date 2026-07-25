import os

import pytest

from capability_lab.adapters.models import OllamaModelAdapter, OllamaModelMissingError


@pytest.mark.skipif(
    os.environ.get("CAPABILITY_LAB_OLLAMA_TESTS") != "1",
    reason="set CAPABILITY_LAB_OLLAMA_TESTS=1 to inspect the local Ollama endpoint",
)
def test_local_ollama_reports_configured_model_identity_without_pulling() -> None:
    adapter = OllamaModelAdapter("http://127.0.0.1:11434", "qwen2.5-coder:1.5b", timeout_seconds=2)
    try:
        identity = adapter.identity()
    except OllamaModelMissingError:
        pytest.skip("local Ollama is reachable but qwen2.5-coder:1.5b is not installed")

    assert identity.name == "qwen2.5-coder:1.5b"
    assert identity.digest
