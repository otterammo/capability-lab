import json
import socket
import urllib.request
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Thread
from typing import Any
from urllib.parse import urlsplit

import pytest

from capability_lab.adapters.artifacts import FilesystemArtifactStore
from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.adapters.harnesses import FakeHarness
from capability_lab.adapters.models import (
    OllamaDigestChangedError,
    OllamaMalformedResponseError,
    OllamaModelAdapter,
    OllamaModelAmbiguousError,
    OllamaModelMissingError,
    OllamaUnreachableError,
)
from capability_lab.adapters.persistence import SqliteMetadataRepository
from capability_lab.adapters.pi_harness import PiHarness
from capability_lab.adapters.scorers import ExactContentScorer
from capability_lab.adapters.workspaces import GitWorktreeManager
from capability_lab.domain.models import (
    ArtifactPayload,
    EvaluationEvidence,
    ExecutionBudget,
    ExecutionContext,
    HarnessRequest,
    ModelIdentity,
    NetworkPolicy,
    RepositorySpec,
    RunProvenance,
    SandboxIdentity,
    SandboxProvenance,
    SandboxResult,
    Task,
    WorkspaceDiff,
)
from capability_lab.ports.interfaces import (
    MetadataRepository,
    ModelProvider,
    Scorer,
    WorkspaceManager,
)


class _OllamaHandler(BaseHTTPRequestHandler):
    payloads: dict[str, object]
    requests: list[tuple[str, str, bytes]]

    def _respond(self, body: object) -> None:
        content = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        path = urlsplit(self.path).path
        self.requests.append(("GET", path, b""))
        self._respond(self.payloads[path])

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        path = urlsplit(self.path).path
        self.requests.append(("POST", path, body))
        self._respond(self.payloads[path])

    def log_message(self, format: str, *args: object) -> None:
        pass


def _ollama_responses(models: list[dict[str, Any]] | None = None) -> dict[str, object]:
    return {
        "/api/version": {"version": "0.11.4"},
        "/api/tags": {
            "models": models
            if models is not None
            else [
                {
                    "name": "qwen2.5-coder:1.5b",
                    "digest": "a" * 64,
                }
            ]
        },
        "/api/show": {
            "details": {
                "format": "gguf",
                "family": "qwen2",
                "parameter_size": "1.5B",
                "quantization_level": "Q4_K_M",
            },
            "capabilities": ["completion", "tools", "insert"],
        },
    }


@contextmanager
def _ollama_server(
    responses: dict[str, object] | None = None,
) -> Iterator[tuple[str, list[tuple[str, str, bytes]]]]:
    requests: list[tuple[str, str, bytes]] = []
    handler = type(
        "OllamaHandler",
        (_OllamaHandler,),
        {"payloads": responses or _ollama_responses(), "requests": requests},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


class _RedirectHandler(BaseHTTPRequestHandler):
    location: str
    requests: list[str]

    def do_GET(self) -> None:
        self.requests.append(self.path)
        self.send_response(302)
        self.send_header("Location", self.location)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextmanager
def _redirect_server(location: str) -> Iterator[tuple[str, list[str]]]:
    requests: list[str] = []
    handler = type(
        "RedirectHandler",
        (_RedirectHandler,),
        {"location": location, "requests": requests},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = Thread(target=server.serve_forever)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_fake_harness_contract_success_and_controlled_failure(tmp_path: Path) -> None:
    source = tmp_path / "src/example.py"
    source.parent.mkdir()
    source.write_text("wrong\n")
    harness = FakeHarness()

    success = harness.execute(
        HarnessRequest("task", "fix", "success"), ExecutionContext(tmp_path, 10)
    )
    failure = harness.execute(
        HarnessRequest("task", "fix", "failure"), ExecutionContext(tmp_path, 10)
    )

    assert success.exit_code == 0
    assert "return a + b" in source.read_text()
    assert failure.exit_code != 0
    assert failure.failure_kind == "environment"


def test_pi_harness_contract_preserves_trajectory_and_final_response(tmp_path: Path) -> None:
    provenance = SandboxProvenance(
        SandboxIdentity("sha256:image", "27.0.0", "501:20", "dockerfile"),
        NetworkPolicy("ollama-only", "http://host.docker.internal:11434", False),
    )
    stdout = "\n".join(
        (
            '{"type":"session","version":3}',
            '{"type":"agent_start"}',
            '{"type":"message_end","message":{"role":"assistant",'
            '"content":[{"type":"text","text":"fixed"}],"stopReason":"stop"}}',
            '{"type":"agent_end","messages":[]}',
        )
    )

    class StubSandbox:
        def execute(self, argv: tuple[str, ...], context: ExecutionContext) -> SandboxResult:
            return SandboxResult(0, stdout, "", False, False, provenance)

    result = PiHarness(StubSandbox(), "qwen2.5-coder:1.5b").execute(
        HarnessRequest("task", "fix the bug", "success"),
        ExecutionContext(tmp_path, 30, 1),
    )

    assert result.exit_code == 0
    assert [event["type"] for event in result.events] == [
        "session",
        "agent_start",
        "message_end",
        "agent_end",
    ]
    assert result.final_response == "fixed"
    assert result.sandbox_provenance == provenance


def test_artifact_contract_round_trip(tmp_path: Path) -> None:
    store = FilesystemArtifactStore(tmp_path)
    ref = store.put(ArtifactPayload("run", "result.json", b"{}"))

    with store.open(ref) as stream:
        assert stream.read() == b"{}"


def test_workspace_manager_contract_lifecycle(tmp_path: Path) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "example.py").write_text("wrong\n")
    revision = ensure_fixture_repository(template, tmp_path / "fixtures/fixture")
    manager: WorkspaceManager = GitWorktreeManager(tmp_path / "worktrees", tmp_path / "fixtures")

    workspace = manager.prepare(RepositorySpec("fixture", revision), revision, "run")
    (workspace.path / "example.py").write_text("right\n")
    assert manager.collect_diff(workspace).changed_paths == ("example.py",)
    manager.destroy(workspace)
    assert not workspace.path.exists()


def test_scorer_contract_returns_structured_result(tmp_path: Path) -> None:
    (tmp_path / "example.py").write_text("right\n")
    scorer: Scorer = ExactContentScorer("exact", "example.py", "right\n")
    task = Task(
        "task",
        "1.0.0",
        (),
        RepositorySpec("fixture", "revision"),
        "",
        ExecutionBudget(30, 1),
        (),
        "hash",
    )

    result = scorer.score(task, EvaluationEvidence(tmp_path, WorkspaceDiff("", ())))

    assert result.scorer_id == "exact"
    assert result.passed


def test_metadata_repository_contract_migrates(tmp_path: Path) -> None:
    repository: MetadataRepository = SqliteMetadataRepository(tmp_path / "state.sqlite3")

    repository.migrate()

    assert SqliteMetadataRepository(tmp_path / "state.sqlite3").schema_revision() == "0002"


def test_ollama_model_provider_contract_captures_exact_identity_without_generation() -> None:
    with _ollama_server() as (base_url, requests):
        provider: ModelProvider = OllamaModelAdapter(
            base_url, "qwen2.5-coder:1.5b", timeout_seconds=1
        )

        identity = provider.identity()

    assert identity == ModelIdentity(
        provider="ollama",
        name="qwen2.5-coder:1.5b",
        digest="a" * 64,
        format="gguf",
        family="qwen2",
        parameter_size="1.5B",
        quantization_level="Q4_K_M",
        capabilities=("completion", "tools", "insert"),
        server_version="0.11.4",
    )
    assert requests == [
        ("GET", "/api/version", b""),
        ("GET", "/api/tags", b""),
        ("POST", "/api/show", b'{"model":"qwen2.5-coder:1.5b"}'),
    ]


@pytest.mark.parametrize(
    ("models", "error"),
    [
        ([], OllamaModelMissingError),
        (
            [
                {"name": "qwen2.5-coder:1.5b", "digest": "a" * 64},
                {"name": "qwen2.5-coder:1.5b", "digest": "b" * 64},
            ],
            OllamaModelAmbiguousError,
        ),
    ],
)
def test_ollama_model_provider_rejects_missing_or_ambiguous_exact_model(
    models: list[dict[str, Any]], error: type[Exception]
) -> None:
    with (
        _ollama_server(_ollama_responses(models)) as (base_url, _),
        pytest.raises(error),
    ):
        OllamaModelAdapter(base_url, "qwen2.5-coder:1.5b", 1).identity()


def test_ollama_model_provider_rejects_malformed_json() -> None:
    responses = _ollama_responses()
    responses["/api/tags"] = b"not-json"
    with (
        _ollama_server(responses) as (base_url, _),
        pytest.raises(OllamaMalformedResponseError, match="/api/tags"),
    ):
        OllamaModelAdapter(base_url, "qwen2.5-coder:1.5b", 1).identity()


def test_ollama_model_provider_rejects_digest_change_before_show() -> None:
    with _ollama_server() as (base_url, requests), pytest.raises(OllamaDigestChangedError):
        OllamaModelAdapter(base_url, "qwen2.5-coder:1.5b", 1).identity(expected_digest="b" * 64)

    assert [path for _, path, _ in requests] == ["/api/version", "/api/tags"]


def test_ollama_model_provider_rejects_malformed_server_digest() -> None:
    responses = _ollama_responses(
        [{"name": "qwen2.5-coder:1.5b", "digest": "sha256:not-canonical"}]
    )
    with (
        _ollama_server(responses) as (base_url, _),
        pytest.raises(OllamaMalformedResponseError, match="digest"),
    ):
        OllamaModelAdapter(base_url, "qwen2.5-coder:1.5b", 1).identity()


def test_ollama_model_provider_ignores_ambient_proxy_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with (
        _ollama_server() as (target_url, target_requests),
        _ollama_server() as (
            proxy_url,
            proxy_requests,
        ),
    ):
        monkeypatch.setenv("HTTP_PROXY", proxy_url)
        monkeypatch.setenv("http_proxy", proxy_url)
        monkeypatch.delenv("NO_PROXY", raising=False)
        monkeypatch.delenv("no_proxy", raising=False)
        monkeypatch.delenv("REQUEST_METHOD", raising=False)
        monkeypatch.setattr(urllib.request, "_opener", None)

        OllamaModelAdapter(target_url, "qwen2.5-coder:1.5b", 1).identity()

    assert [path for _, path, _ in target_requests] == [
        "/api/version",
        "/api/tags",
        "/api/show",
    ]
    assert proxy_requests == []


def test_ollama_model_provider_refuses_redirect_without_contacting_target() -> None:
    with (
        _ollama_server() as (remote_url, remote_requests),
        _redirect_server(remote_url + "/api/version") as (redirect_url, redirect_requests),
        pytest.raises(OllamaUnreachableError, match="302"),
    ):
        OllamaModelAdapter(redirect_url, "qwen2.5-coder:1.5b", 1).identity()

    assert redirect_requests == ["/api/version"]
    assert remote_requests == []


def test_ollama_model_provider_reports_unreachable_endpoint() -> None:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        base_url = f"http://127.0.0.1:{probe.getsockname()[1]}"

    with pytest.raises(OllamaUnreachableError):
        OllamaModelAdapter(base_url, "qwen2.5-coder:1.5b", 0.1).identity()


def test_model_identity_serializes_in_run_provenance() -> None:
    identity = ModelIdentity(
        "ollama",
        "qwen2.5-coder:1.5b",
        "a" * 64,
        "gguf",
        "qwen2",
        "1.5B",
        "Q4_K_M",
        ("completion", "tools", "insert"),
        "0.11.4",
    )
    provenance = RunProvenance(
        "benchmark",
        "task",
        "config",
        "revision",
        "3.13.7",
        "platform",
        1,
        "commit",
        False,
        model_identity=identity,
    )

    assert json.loads(json.dumps(asdict(provenance)))["model_identity"]["digest"] == identity.digest
