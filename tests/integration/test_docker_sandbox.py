from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import threading
from collections.abc import Iterator
from dataclasses import replace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import pytest

from capability_lab.adapters.fixtures import ensure_fixture_repository
from capability_lab.adapters.pi_harness import PiHarness
from capability_lab.adapters.raw_ollama_harness import RawOllamaHarness
from capability_lab.adapters.sandboxes import DockerSandbox, SandboxError
from capability_lab.adapters.workspaces import GitWorktreeManager
from capability_lab.bootstrap import build_service
from capability_lab.domain.models import (
    ExecutionContext,
    HarnessRequest,
    HarnessResult,
    RepositorySpec,
    SandboxResult,
    SandboxSettings,
)

pytestmark = pytest.mark.skipif(
    os.environ.get("CAPABILITY_LAB_DOCKER_TESTS") != "1",
    reason="run with make test-docker",
)


def _settings(*, max_output_bytes: int = 1_048_576) -> SandboxSettings:
    return SandboxSettings(
        image="capability-lab-sandbox:0.1.0",
        cpus=1.0,
        memory_mb=512,
        pids=128,
        nofile=256,
        max_output_bytes=max_output_bytes,
    )


def _execute(
    workspace: Path,
    argv: tuple[str, ...],
    *,
    timeout_seconds: int = 10,
    max_output_bytes: int = 1_048_576,
) -> SandboxResult:
    return DockerSandbox(_settings(max_output_bytes=max_output_bytes)).execute(
        argv, ExecutionContext(workspace=workspace, timeout_seconds=timeout_seconds)
    )


def _inventory(kind: str) -> str:
    if kind == "container":
        command = [
            "docker",
            "ps",
            "-a",
            "--filter",
            "label=capability-lab.sandbox",
            "--format",
            "{{.ID}}",
        ]
    elif kind == "volume":
        command = [
            "docker",
            "volume",
            "ls",
            "--filter",
            "label=capability-lab.sandbox",
            "--format",
            "{{.Name}}",
        ]
    else:
        command = [
            "docker",
            "network",
            "ls",
            "--filter",
            "label=capability-lab.sandbox",
            "--format",
            "{{.ID}}",
        ]
    return subprocess.run(command, check=True, capture_output=True, text=True).stdout.strip()


def _assert_clean() -> None:
    assert _inventory("container") == ""
    assert _inventory("volume") == ""
    assert _inventory("network") == ""


def _resource_prefix(workspace: Path) -> str:
    token = hashlib.sha256(workspace.name.encode()).hexdigest()[:12]
    return f"capability-lab-{token}"


@pytest.mark.parametrize("resource", ["sandbox-container", "relay-container", "volume"])
def test_foreign_same_name_resource_survives_sandbox_create_collision(
    tmp_path: Path,
    resource: str,
) -> None:
    workspace = tmp_path / f"foreign-{resource}"
    workspace.mkdir()
    prefix = _resource_prefix(workspace)
    suffix = "relay-volume" if resource == "volume" else resource.removesuffix("-container")
    name = f"{prefix}-{suffix}"
    foreign_label = "capability-lab.foreign=collision-test"
    if resource != "volume":
        create = [
            "docker",
            "create",
            "--name",
            name,
            "--label",
            foreign_label,
            _settings().image,
            "true",
        ]
        inspect = [
            "docker",
            "inspect",
            "--format",
            '{{ index .Config.Labels "capability-lab.foreign" }}',
            name,
        ]
        remove = ["docker", "rm", "--force", name]
    else:
        create = ["docker", "volume", "create", "--label", foreign_label, name]
        inspect = [
            "docker",
            "volume",
            "inspect",
            "--format",
            '{{ index .Labels "capability-lab.foreign" }}',
            name,
        ]
        remove = ["docker", "volume", "rm", "--force", name]

    subprocess.run(create, check=True, capture_output=True, text=True)
    try:
        with pytest.raises(SandboxError):
            _execute(workspace, ("true",))
        label = subprocess.run(
            inspect,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert label == "collision-test"
    finally:
        subprocess.run(remove, check=False, capture_output=True, text=True)
        _assert_clean()


class DockerSandboxHarness:
    def __init__(self, sandbox: DockerSandbox, argv: tuple[str, ...]) -> None:
        self.sandbox = sandbox
        self.argv = argv
        self.result: SandboxResult | None = None
        self.error: SandboxError | None = None

    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult:
        try:
            self.result = self.sandbox.execute(self.argv, context)
        except SandboxError as error:
            self.error = error
            return HarnessResult(
                exit_code=error.returncode or 1,
                failure_kind="environment",
                sandbox_provenance=error.provenance,
            )
        return HarnessResult(
            exit_code=self.result.exit_code,
            final_response=self.result.stdout,
            failure_kind="environment" if self.result.exit_code else None,
            timed_out=self.result.timed_out,
            sandbox_provenance=self.result.provenance,
        )


def _copy_smoke_project(project: Path, timeout_seconds: int) -> None:
    root = Path(__file__).resolve().parents[2]
    shutil.copytree(root / "configs", project / "configs")
    shutil.copytree(root / "benchmarks", project / "benchmarks")
    task_path = project / "benchmarks/tasks/python.fix-incorrect-return@1.0.1.yaml"
    task = json.loads(task_path.read_text())
    task["budget"]["timeout_seconds"] = timeout_seconds
    task_path.write_text(json.dumps(task))
    release_path = project / "benchmarks/releases/smoke@1.0.1.yaml"
    release = json.loads(release_path.read_text())
    release["tasks"][0]["sha256"] = hashlib.sha256(task_path.read_bytes()).hexdigest()
    release_path.write_text(json.dumps(release))


def _expected_provenance() -> dict[str, object]:
    image_id = subprocess.run(
        ["docker", "image", "inspect", "--format", "{{.Id}}", _settings().image],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    docker_version = subprocess.run(
        ["docker", "version", "--format", "{{.Server.Version}}"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert image_id
    assert docker_version
    return {
        "identity": {
            "image_id": image_id,
            "docker_version": docker_version,
            "user": f"{os.getuid()}:{os.getgid()}",
            "dockerfile_sha256": hashlib.sha256(
                Path("docker/sandbox/Dockerfile").read_bytes()
            ).hexdigest(),
        },
        "network_policy": {
            "name": "ollama-only",
            "endpoint": "http://host.docker.internal:11434",
            "external_access": False,
        },
    }


def _persisted_provenance(
    project: Path, run_id: str
) -> tuple[dict[str, object], dict[str, object]]:
    artifact = json.loads(
        (project / ".lab/artifacts/runs" / run_id / "provenance.json").read_text()
    )
    with sqlite3.connect(project / ".lab/state.sqlite3") as connection:
        snapshot = json.loads(
            connection.execute(
                "SELECT provenance FROM environment_snapshots WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
        )
    return artifact, snapshot


@pytest.mark.parametrize(
    ("case", "argv", "timeout_seconds"),
    [
        ("success", ("sh", "-c", "sed -i 's/return a - b/return a + b/' src/example.py"), 5),
        ("failure", ("sh", "-c", "exit 7"), 5),
        ("timeout", ("sleep", "30"), 1),
    ],
)
def test_lab_service_persists_docker_sandbox_provenance_for_every_result(
    tmp_path: Path,
    case: str,
    argv: tuple[str, ...],
    timeout_seconds: int,
) -> None:
    project = tmp_path / "project"
    _copy_smoke_project(project, timeout_seconds)
    service = build_service(project)
    runtime_builder = service.runtime_builder
    harness = DockerSandboxHarness(DockerSandbox(_settings()), argv)
    service.runtime_builder = lambda config: replace(runtime_builder(config), harness=harness)

    result = service.run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )

    assert result.status == ("succeeded" if case == "success" else "failed")
    expected_provenance = _expected_provenance()
    artifact_provenance, snapshot = _persisted_provenance(project, result.run_id)
    assert artifact_provenance["sandbox_provenance"] == expected_provenance
    assert snapshot["sandbox_provenance"] == expected_provenance
    _assert_clean()


def test_lab_service_persists_provenance_after_post_start_cleanup_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "project"
    _copy_smoke_project(project, 5)
    service = build_service(project)
    runtime_builder = service.runtime_builder
    sandbox = DockerSandbox(_settings())
    original_run = sandbox._run

    def fail_after_sandbox_cleanup(argv: list[str]) -> str:
        output = original_run(argv)
        if argv[1:3] == ["rm", "--force"] and argv[-1].endswith("-sandbox"):
            raise SandboxError("forced cleanup failure")
        return output

    monkeypatch.setattr(sandbox, "_run", fail_after_sandbox_cleanup)
    harness = DockerSandboxHarness(
        sandbox, ("sh", "-c", "sed -i 's/return a - b/return a + b/' src/example.py")
    )
    service.runtime_builder = lambda config: replace(runtime_builder(config), harness=harness)

    result = service.run(
        project / "configs/defaults.yaml",
        project / "configs/harnesses/fake.yaml",
        project / "configs/experiments/smoke.yaml",
    )

    assert result.status == "failed"
    assert harness.error is not None
    expected_provenance = _expected_provenance()
    artifact_provenance, snapshot = _persisted_provenance(project, result.run_id)
    assert artifact_provenance["sandbox_provenance"] == expected_provenance
    assert snapshot["sandbox_provenance"] == expected_provenance
    _assert_clean()


def test_non_root_workspace_write_read_only_root_and_cgroup_limits(tmp_path: Path) -> None:
    workspace = tmp_path / "run-security-live"
    workspace.mkdir()
    script = " ".join(
        (
            "set -eu;",
            "id -u;",
            "printf 'written\\n' > /workspace/write.txt;",
            "cat /workspace/write.txt;",
            "if printf forbidden > /etc/forbidden 2>/dev/null; then exit 91; fi;",
            "printf 'nofile=%s\\n' \"$(ulimit -n)\";",
            "if [ -f /sys/fs/cgroup/memory.max ]; then",
            "printf 'memory=%s\\n' \"$(cat /sys/fs/cgroup/memory.max)\";",
            "printf 'pids=%s\\n' \"$(cat /sys/fs/cgroup/pids.max)\";",
            "printf 'cpu=%s\\n' \"$(cat /sys/fs/cgroup/cpu.max)\";",
            "else",
            "printf 'memory=%s\\n' \"$(cat /sys/fs/cgroup/memory/memory.limit_in_bytes)\";",
            "printf 'pids=%s\\n' \"$(cat /sys/fs/cgroup/pids/pids.max)\";",
            (
                "printf 'cpu=%s/%s\\n' "
                '"$(cat /sys/fs/cgroup/cpu/cpu.cfs_quota_us)" '
                '"$(cat /sys/fs/cgroup/cpu/cpu.cfs_period_us)";'
            ),
            "fi",
        )
    )

    result = _execute(workspace, ("sh", "-c", script))

    lines = result.stdout.splitlines()
    assert result.exit_code == 0
    assert int(lines[0]) != 0
    assert lines[1:] == [
        "written",
        "nofile=256",
        "memory=536870912",
        "pids=128",
        "cpu=100000 100000",
    ] or lines[1:] == [
        "written",
        "nofile=256",
        "memory=536870912",
        "pids=128",
        "cpu=100000/100000",
    ]
    assert (workspace / "write.txt").read_text() == "written\n"
    _assert_clean()


def test_pinned_node_and_pi_are_available_to_the_non_root_sandbox_user(tmp_path: Path) -> None:
    workspace = tmp_path / "run-pi-version-live"
    workspace.mkdir()

    result = _execute(workspace, ("sh", "-c", "node --version; pi --version; id -u"))

    node, pi, uid = result.stdout.splitlines()
    assert result.exit_code == 0
    assert node == "v22.21.1"
    assert pi == "0.79.3"
    assert uid != "0"
    _assert_clean()


def test_timeout_and_output_limit_are_distinguishable(tmp_path: Path) -> None:
    timeout_workspace = tmp_path / "run-timeout-live"
    timeout_workspace.mkdir()
    timeout = _execute(timeout_workspace, ("sleep", "30"), timeout_seconds=1)

    output_workspace = tmp_path / "run-output-live"
    output_workspace.mkdir()
    output = _execute(
        output_workspace,
        ("sh", "-c", "while :; do printf 1234567890; done"),
        max_output_bytes=1024,
    )

    assert timeout.timed_out is True
    assert timeout.output_limited is False
    assert output.timed_out is False
    assert output.output_limited is True
    assert len(output.stdout.encode(errors="surrogateescape")) <= 1024
    _assert_clean()


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        body = (
            b'{"version":"capability-lab-test"}\n'
            if self.path == "/api/version"
            else b"capability-lab-test-upstream\n"
        )
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _OpenAIStubHandler(BaseHTTPRequestHandler):
    requests: list[dict[str, Any]]
    tool_budget_case: bool

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.requests.append(json.loads(body))
        if self.tool_budget_case and len(self.requests) == 1:
            chunks = (
                {
                    "id": "stub-tools",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "qwen2.5-coder:1.5b",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "role": "assistant",
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "first",
                                        "type": "function",
                                        "function": {
                                            "name": "write",
                                            "arguments": json.dumps(
                                                {"path": "first.txt", "content": "first"}
                                            ),
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "second",
                                        "type": "function",
                                        "function": {
                                            "name": "write",
                                            "arguments": json.dumps(
                                                {"path": "second.txt", "content": "second"}
                                            ),
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                },
            )
        else:
            chunks = (
                {
                    "id": "stub",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "qwen2.5-coder:1.5b",
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant", "content": "stub complete"},
                        }
                    ],
                },
                {
                    "id": "stub",
                    "object": "chat.completion.chunk",
                    "created": 1,
                    "model": "qwen2.5-coder:1.5b",
                    "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                },
            )
        content = (
            b"".join(f"data: {json.dumps(chunk)}\n\n".encode() for chunk in chunks)
            + b"data: [DONE]\n\n"
        )
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: object) -> None:
        pass


class _RawStubHandler(BaseHTTPRequestHandler):
    requests: list[tuple[str, dict[str, Any]]]

    def do_POST(self) -> None:
        body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
        self.requests.append((self.path, json.loads(body)))
        response = json.dumps(
            {
                "model": "qwen2.5-coder:1.5b",
                "response": "touch /workspace/model-output-was-executed",
                "done": True,
                "done_reason": "stop",
            }
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(response)))
        self.end_headers()
        self.wfile.write(response)

    def log_message(self, format: str, *args: object) -> None:
        pass


@contextlib.contextmanager
def _openai_stub(*, tool_budget_case: bool = False) -> Iterator[tuple[int, list[dict[str, Any]]]]:
    requests: list[dict[str, Any]] = []
    handler = type(
        "OpenAIStubHandler",
        (_OpenAIStubHandler,),
        {"requests": requests, "tool_budget_case": tool_budget_case},
    )
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextlib.contextmanager
def _raw_stub() -> Iterator[tuple[int, list[tuple[str, dict[str, Any]]]]]:
    requests: list[tuple[str, dict[str, Any]]] = []
    handler = type("RawStubHandler", (_RawStubHandler,), {"requests": requests})
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server.server_port, requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


@contextlib.contextmanager
def _controlled_or_verified_ollama_upstream() -> Iterator[str | None]:
    try:
        server = ThreadingHTTPServer(("127.0.0.1", 11434), _Handler)
    except OSError:
        try:
            with urlopen("http://127.0.0.1:11434/api/version", timeout=2) as response:
                payload = json.load(response)
            with urlopen("http://127.0.0.1:11434/", timeout=2) as response:
                banner = response.read().strip()
        except (OSError, ValueError):
            yield None
            return
        yield (
            "ollama"
            if isinstance(payload.get("version"), str) and banner == b"Ollama is running"
            else None
        )
        return
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield "controlled"
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_fixed_relay_succeeds_and_public_network_fails(tmp_path: Path) -> None:
    workspace = tmp_path / "run-network-live"
    workspace.mkdir()
    script = " ".join(
        (
            "set -eu;",
            'test -z "$(ip route)"; test -z "$(ip -6 route)";',
            "! timeout 2 wget -T 1 -qO- http://host.docker.internal:11434/;",
            "wget -T 2 -qO- http://ollama.local:11434/ >/dev/null;",
            "! timeout 2 wget -T 1 -qO- http://1.1.1.1/;",
            "! timeout 2 wget -T 1 -qO- http://example.com/",
        )
    )

    with _controlled_or_verified_ollama_upstream() as upstream:
        if upstream is None:
            pytest.skip("host port 11434 is occupied by an unverified service")
        result = _execute(workspace, ("sh", "-c", script))

    assert result.exit_code == 0
    assert result.provenance.network_policy.endpoint == "http://host.docker.internal:11434"
    assert result.provenance.network_policy.external_access is False
    _assert_clean()


def test_ollama_version_is_reachable_through_fixed_sandbox_relay(tmp_path: Path) -> None:
    workspace = tmp_path / "run-ollama-version-live"
    workspace.mkdir()

    with _controlled_or_verified_ollama_upstream() as upstream:
        if upstream is None:
            pytest.skip("host port 11434 is occupied by an unverified service")
        result = _execute(
            workspace,
            ("wget", "-T", "2", "-qO-", "http://ollama.local:11434/api/version"),
        )

    payload = json.loads(result.stdout)
    assert result.exit_code == 0
    assert isinstance(payload.get("version"), str)
    assert result.provenance.network_policy.endpoint == "http://host.docker.internal:11434"
    assert result.provenance.network_policy.external_access is False
    _assert_clean()


def test_pi_harness_contract_against_stub_ollama_through_sandbox(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "run-pi-stub-live"
    workspace.mkdir()
    (workspace / "AGENTS.md").write_text("host-only instructions")
    (tmp_path / "protected-evaluator.py").write_text("protected evaluator")
    sandbox = DockerSandbox(_settings())
    original_run = sandbox._run
    commands: list[list[str]] = []

    with _openai_stub() as (port, requests):

        def redirect_relay(argv: list[str]) -> str:
            commands.append(argv)
            if argv[-1:] == ["TCP:host.docker.internal:11434"]:
                argv = [*argv[:-1], f"TCP:host.docker.internal:{port}"]
            return original_run(argv)

        monkeypatch.setattr(sandbox, "_run", redirect_relay)
        result = PiHarness(
            sandbox,
            "qwen2.5-coder:1.5b",
            seed=7,
            temperature=0.25,
            context_window=32768,
            max_output_tokens=256,
        ).execute(
            HarnessRequest("task", "answer\nwithout tools", "success"),
            ExecutionContext(workspace, 30, 1),
        )

    assert result.exit_code == 0
    assert result.final_response == "stub complete"
    assert result.events[0]["type"] == "session"
    assert result.events[1]["type"] == "agent_start"
    assert result.events[-1]["type"] == "agent_end"
    assert result.sandbox_provenance is not None
    assert result.sandbox_provenance.network_policy.external_access is False
    assert len(requests) == 1
    request = requests[0]
    assert request["model"] == "qwen2.5-coder:1.5b"
    assert request["seed"] == 7
    assert request["temperature"] == 0.25
    assert request["max_tokens"] == 256
    assert "num_ctx" not in request
    assert "options" not in request
    assert {tool["function"]["name"] for tool in request["tools"]} == {
        "read",
        "bash",
        "edit",
        "write",
    }
    serialized = json.dumps(request)
    assert "answer\\nwithout tools" in serialized
    assert "host-only instructions" not in serialized
    assert "protected evaluator" not in serialized
    assert "protected-evaluator.py" not in "\n".join(map(" ".join, commands))
    assert list(workspace.glob(".capability-lab-pi-*")) == []
    _assert_clean()


def test_raw_ollama_uses_one_native_request_without_tools_or_output_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "run-raw-stub-live"
    workspace.mkdir()
    sandbox = DockerSandbox(_settings())
    original_run = sandbox._run

    with _raw_stub() as (port, requests):

        def redirect_relay(argv: list[str]) -> str:
            if argv[-1:] == ["TCP:host.docker.internal:11434"]:
                argv = [*argv[:-1], f"TCP:host.docker.internal:{port}"]
            return original_run(argv)

        monkeypatch.setattr(sandbox, "_run", redirect_relay)
        result = RawOllamaHarness(
            sandbox,
            "qwen2.5-coder:1.5b",
            seed=7,
            temperature=0.25,
            max_output_tokens=256,
        ).execute(
            HarnessRequest("task", "answer without tools", "success"),
            ExecutionContext(workspace, 30, 1),
        )

    assert requests == [
        (
            "/api/generate",
            {
                "model": "qwen2.5-coder:1.5b",
                "prompt": "answer without tools",
                "stream": False,
                "options": {
                    "seed": 7,
                    "temperature": 0.25,
                    "num_predict": 256,
                },
            },
        )
    ]
    assert "tools" not in requests[0][1]
    assert result.exit_code == 0
    assert result.final_response == "touch /workspace/model-output-was-executed"
    assert not (workspace / "model-output-was-executed").exists()
    assert result.sandbox_provenance is not None
    assert result.sandbox_provenance.network_policy.external_access is False
    assert list(workspace.glob(".capability-lab-raw-*")) == []
    _assert_clean()


def test_pi_blocks_a_second_tool_before_execution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "run-pi-budget-live"
    workspace.mkdir()
    sandbox = DockerSandbox(_settings())
    original_run = sandbox._run

    with _openai_stub(tool_budget_case=True) as (port, requests):

        def redirect_relay(argv: list[str]) -> str:
            if argv[-1:] == ["TCP:host.docker.internal:11434"]:
                argv = [*argv[:-1], f"TCP:host.docker.internal:{port}"]
            return original_run(argv)

        monkeypatch.setattr(sandbox, "_run", redirect_relay)
        result = PiHarness(sandbox, "qwen2.5-coder:1.5b").execute(
            HarnessRequest("task", "write both files", "success"),
            ExecutionContext(workspace, 30, 1),
        )

    assert (workspace / "first.txt").read_text() == "first"
    assert not (workspace / "second.txt").exists()
    assert len(requests) == 2
    assert result.exit_code == 1
    assert result.failure_kind == "tool_budget"
    assert result.events[-1]["kind"] == "tool_budget"
    assert {
        event["toolCallId"] for event in result.events if event.get("type") == "tool_execution_end"
    } == {"first", "second"}
    assert result.sandbox_provenance is not None
    _assert_clean()


@pytest.mark.parametrize(
    "case",
    ["success", "nonzero", "timeout", "overflow", "setup-failure"],
)
def test_workspace_owner_removes_worktree_for_every_sandbox_outcome(
    tmp_path: Path, case: str
) -> None:
    template = tmp_path / "template"
    template.mkdir()
    (template / "example.txt").write_text("fixture\n")
    source = tmp_path / "fixtures" / "fixture"
    revision = ensure_fixture_repository(template, source)
    manager = GitWorktreeManager(tmp_path / "worktrees", tmp_path / "fixtures")
    workspace = manager.prepare(RepositorySpec("fixture", revision), revision, f"run-{case}")
    settings = _settings(max_output_bytes=128 if case == "overflow" else 1_048_576)
    if case == "setup-failure":
        settings = replace(settings, image="capability-lab-sandbox:missing")
    argv = {
        "success": ("true",),
        "nonzero": ("sh", "-c", "exit 7"),
        "timeout": ("sleep", "30"),
        "overflow": ("yes",),
        "setup-failure": ("true",),
    }[case]

    try:
        if case == "setup-failure":
            with pytest.raises(SandboxError):
                DockerSandbox(settings).execute(
                    argv, ExecutionContext(workspace.path, timeout_seconds=1)
                )
        else:
            DockerSandbox(settings).execute(
                argv,
                ExecutionContext(workspace.path, timeout_seconds=1 if case == "timeout" else 5),
            )
    finally:
        manager.destroy(workspace)

    assert not workspace.path.exists()
    _assert_clean()


@pytest.mark.parametrize(
    ("case", "argv", "timeout_seconds", "max_output_bytes"),
    [
        ("normal", ("true",), 5, 1_048_576),
        ("nonzero", ("sh", "-c", "exit 7"), 5, 1_048_576),
        ("timeout", ("sleep", "30"), 1, 1_048_576),
        ("output", ("yes",), 5, 128),
    ],
)
def test_cleanup_after_every_termination(
    tmp_path: Path,
    case: str,
    argv: tuple[str, ...],
    timeout_seconds: int,
    max_output_bytes: int,
) -> None:
    workspace = tmp_path / f"run-cleanup-{case}"
    workspace.mkdir()

    _execute(
        workspace,
        argv,
        timeout_seconds=timeout_seconds,
        max_output_bytes=max_output_bytes,
    )

    _assert_clean()
