from __future__ import annotations

import os
import socket
import stat
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from capability_lab.adapters.sandboxes import DockerSandbox, SandboxError
from capability_lab.domain.models import (
    ExecutionContext,
    HarnessResult,
    NetworkPolicy,
    SandboxIdentity,
    SandboxSettings,
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


def _is_owner_inspect(argv: list[str]) -> bool:
    return argv[1:2] == ["inspect"] and "--type" in argv or argv[1:3] == ["volume", "inspect"]


def _recorded_owner(calls: list[list[str]]) -> str:
    label = next(call[call.index("--label") + 1] for call in reversed(calls) if "--label" in call)
    return label.removeprefix("capability-lab.sandbox=")


def test_harness_sandbox_provenance_is_all_or_nothing() -> None:
    identity = SandboxIdentity("sha256:image", "20.10.17", "501:20", "dockerfile-hash")
    policy = NetworkPolicy("ollama-only", "http://host.docker.internal:11434", False)

    assert "sandbox_provenance" in HarnessResult.__dataclass_fields__
    assert "sandbox_identity" not in HarnessResult.__dataclass_fields__
    assert "network_policy" not in HarnessResult.__dataclass_fields__
    partial: dict[str, Any] = {"sandbox_identity": identity, "network_policy": policy}
    with pytest.raises(TypeError):
        HarnessResult(exit_code=1, **partial)


def test_execute_uses_fixed_security_and_network_policy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    workspace = tmp_path / "run-security"
    workspace.mkdir()
    calls: list[list[str]] = []
    sandbox = DockerSandbox(_settings(), dockerfile=dockerfile)

    monkeypatch.setattr(socket, "getaddrinfo", lambda *args, **kwargs: pytest.fail("DNS invoked"))

    def fake_run(argv: list[str]) -> str:
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return "sha256:image-id\n"
        if argv[1:2] == ["version"]:
            return "20.10.17\n"
        if _is_owner_inspect(argv):
            return _recorded_owner(calls)
        return ""

    monkeypatch.setattr(sandbox, "_run", fake_run)
    monkeypatch.setattr(
        sandbox,
        "_run_attached",
        lambda argv, container_name, timeout_seconds: (0, b"ok\n", b"", False, False),
    )

    result = sandbox.execute(
        ("sh", "-c", "printf ok"), ExecutionContext(workspace=workspace, timeout_seconds=5)
    )

    relay_create = next(call for call in calls if any("UNIX-LISTEN:" in arg for arg in call))
    sandbox_create = next(
        call
        for call in calls
        if "--network" in call and call[call.index("--network") + 1] == "none"
    )
    assert not any(call[1:3] == ["network", "create"] for call in calls)
    assert not any(call[1:3] == ["network", "connect"] for call in calls)
    assert relay_create[relay_create.index("--network") + 1] == "bridge"
    assert relay_create[-3:] == [
        "socat",
        "UNIX-LISTEN:/relay/ollama.sock,fork,mode=0666,unlink-early",
        "TCP:host.docker.internal:11434",
    ]
    assert sandbox_create[sandbox_create.index("--add-host") + 1] == ("ollama.local:127.0.0.1")
    assert "--pull=never" in relay_create
    assert "--pull=never" in sandbox_create
    assert "--read-only" in sandbox_create
    assert sandbox_create[sandbox_create.index("--cap-drop") + 1] == "ALL"
    assert sandbox_create[sandbox_create.index("--security-opt") + 1] == "no-new-privileges"
    assert sandbox_create[sandbox_create.index("--memory") + 1] == "512m"
    assert sandbox_create[sandbox_create.index("--memory-swap") + 1] == "512m"
    assert sandbox_create[sandbox_create.index("--cpus") + 1] == "1.0"
    assert sandbox_create[sandbox_create.index("--pids-limit") + 1] == "128"
    assert sandbox_create[sandbox_create.index("--ulimit") + 1] == "nofile=256:256"
    assert sandbox_create[sandbox_create.index("--user") + 1] == (
        f"{workspace.stat().st_uid}:{workspace.stat().st_gid}"
    )
    sandbox_mounts = [
        sandbox_create[index + 1] for index, arg in enumerate(sandbox_create) if arg == "--mount"
    ]
    assert sandbox_mounts == [
        "type=volume,source=capability-lab-0796c636b9bd-relay-volume,target=/relay,readonly",
        f"type=bind,source={workspace.resolve()},target=/workspace",
    ]
    relay_mounts = [
        relay_create[index + 1] for index, arg in enumerate(relay_create) if arg == "--mount"
    ]
    assert relay_mounts == [
        "type=volume,source=capability-lab-0796c636b9bd-relay-volume,target=/relay"
    ]
    assert sandbox_create[-8:] == [
        "sha256:image-id",
        "sh",
        "-c",
        (
            "socat TCP-LISTEN:11434,bind=127.0.0.1,fork,reuseaddr "
            "UNIX-CONNECT:/relay/ollama.sock & relay_pid=$!; "
            "until netstat -ltn | grep -q '127.0.0.1:11434'; do "
            'kill -0 "$relay_pid" || exit 125; sleep 0.01; done; exec "$@"'
        ),
        "capability-lab-sandbox",
        "sh",
        "-c",
        "printf ok",
    ]
    create_calls = [call for call in calls if call[1:2] == ["create"]]
    assert len(create_calls) == 2
    assert all("sha256:image-id" in call for call in create_calls)
    assert all("capability-lab-sandbox:0.1.0" not in call for call in create_calls)
    assert sum(call[1:3] == ["image", "inspect"] for call in calls) == 1
    rendered = "\0".join(sandbox_create + relay_create)
    assert "--privileged" not in rendered
    assert "/var/run/docker.sock" not in rendered
    assert str(Path.home()) not in rendered
    assert ".evaluation" not in rendered
    assert "0:0" not in rendered
    assert result.stdout == "ok\n"
    assert result.provenance.identity.image_id == "sha256:image-id"
    assert result.provenance.identity.docker_version == "20.10.17"
    assert result.provenance.identity.user == f"{os.getuid()}:{os.getgid()}"
    assert result.provenance.network_policy.name == "ollama-only"
    assert result.provenance.network_policy.endpoint == "http://host.docker.internal:11434"
    assert result.provenance.network_policy.external_access is False


def test_execute_resolves_and_pins_authorized_ollama_hostname(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    workspace = tmp_path / "run-resolved-endpoint"
    workspace.mkdir()
    calls: list[list[str]] = []
    resolutions: list[tuple[str, int, int]] = []
    sandbox = DockerSandbox(
        _settings(),
        ollama_base_url="http://desktop:11434",
        dockerfile=dockerfile,
    )

    def resolve(host: str, port: int, *, type: int):
        resolutions.append((host, port, type))
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("100.114.23.127", port))]

    def fake_run(argv: list[str]) -> str:
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return "sha256:image-id\n"
        if argv[1:2] == ["version"]:
            return "20.10.17\n"
        if _is_owner_inspect(argv):
            return _recorded_owner(calls)
        return ""

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(sandbox, "_run", fake_run)
    monkeypatch.setattr(
        sandbox,
        "_run_attached",
        lambda argv, container_name, timeout_seconds: (0, b"", b"", False, False),
    )

    result = sandbox.execute(("true",), ExecutionContext(workspace, 1))

    relay_create = next(call for call in calls if any("UNIX-LISTEN:" in arg for arg in call))
    assert resolutions == [("desktop", 11434, socket.SOCK_STREAM)]
    assert relay_create[-1] == "TCP:100.114.23.127:11434"
    assert "desktop" not in "\0".join(relay_create)
    assert result.provenance.network_policy.endpoint == "http://100.114.23.127:11434"


@pytest.mark.parametrize(
    "base_url",
    [
        "http://127.0.0.2:11434",
        "http://ollama-loopback:11434",
    ],
)
def test_sandbox_rejects_unauthorized_loopback_aliases_before_resolution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    base_url: str,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kwargs: pytest.fail("DNS invoked"),
    )

    with pytest.raises(ValueError, match="host must be"):
        DockerSandbox(_settings(), ollama_base_url=base_url, dockerfile=dockerfile)


@pytest.mark.parametrize(
    ("addresses", "message"),
    [
        (None, "cannot resolve"),
        ([], "cannot resolve"),
        (["100.114.23.127", "100.114.23.128"], "ambiguous"),
    ],
)
def test_execute_rejects_failed_or_ambiguous_ollama_resolution_before_docker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    addresses: list[str] | None,
    message: str,
) -> None:
    workspace = tmp_path / "run-bad-resolution"
    workspace.mkdir()
    sandbox = DockerSandbox(
        _settings(),
        ollama_base_url="http://desktop:11434",
        dockerfile=tmp_path / "Dockerfile",
    )

    def resolve(host: str, port: int, *, type: int):
        if addresses is None:
            raise socket.gaierror("not found")
        return [
            (socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, port)) for address in addresses
        ]

    monkeypatch.setattr(socket, "getaddrinfo", resolve)
    monkeypatch.setattr(sandbox, "_run", lambda argv: pytest.fail("docker invoked"))

    with pytest.raises(SandboxError, match=message):
        sandbox.execute(("true",), ExecutionContext(workspace, 1))


def test_execute_rejects_root_owned_workspace_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "run-root"
    workspace.mkdir()
    sandbox = DockerSandbox(_settings(), dockerfile=tmp_path / "Dockerfile")
    root_directory = SimpleNamespace(st_mode=stat.S_IFDIR | 0o755, st_uid=0, st_gid=0)
    monkeypatch.setattr(Path, "stat", lambda self, **kwargs: root_directory)
    monkeypatch.setattr(sandbox, "_run", lambda argv: pytest.fail("docker invoked"))

    with pytest.raises(ValueError, match="root-owned"):
        sandbox.execute(("true",), ExecutionContext(workspace=workspace, timeout_seconds=1))


@pytest.mark.parametrize("bad_arg", ["line\nbreak", "nul\0byte"])
def test_execute_rejects_unsafe_argv_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, bad_arg: str
) -> None:
    workspace = tmp_path / "run-invalid"
    workspace.mkdir()
    sandbox = DockerSandbox(_settings(), dockerfile=tmp_path / "Dockerfile")
    monkeypatch.setattr(sandbox, "_run", lambda *args, **kwargs: pytest.fail("docker invoked"))

    with pytest.raises(ValueError, match="argv"):
        sandbox.execute(
            ("printf", bad_arg), ExecutionContext(workspace=workspace, timeout_seconds=1)
        )


@pytest.mark.parametrize("workspace_kind", ["relative", "missing"])
def test_execute_rejects_invalid_workspace_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, workspace_kind: str
) -> None:
    workspace = Path("relative") if workspace_kind == "relative" else tmp_path / "missing"
    sandbox = DockerSandbox(_settings(), dockerfile=tmp_path / "Dockerfile")
    monkeypatch.setattr(sandbox, "_run", lambda *args, **kwargs: pytest.fail("docker invoked"))

    with pytest.raises(ValueError, match="workspace"):
        sandbox.execute(("true",), ExecutionContext(workspace=workspace, timeout_seconds=1))


def test_image_inspection_failure_happens_before_container_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "run-inspect"
    workspace.mkdir()
    calls: list[list[str]] = []
    sandbox = DockerSandbox(_settings(), dockerfile=tmp_path / "Dockerfile")

    def fail_inspect(argv: list[str]) -> str:
        calls.append(argv)
        raise SandboxError("image inspect failed")

    monkeypatch.setattr(sandbox, "_run", fail_inspect)

    with pytest.raises(SandboxError, match="image inspect failed") as raised:
        sandbox.execute(("true",), ExecutionContext(workspace=workspace, timeout_seconds=1))

    assert raised.value.provenance is None
    assert calls == [
        [
            "docker",
            "image",
            "inspect",
            "--format",
            "{{.Id}}",
            "capability-lab-sandbox:0.1.0",
        ]
    ]


def test_cleanup_attempts_every_resource_when_one_removal_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    workspace = tmp_path / "run-cleanup"
    workspace.mkdir()
    calls: list[list[str]] = []
    sandbox = DockerSandbox(_settings(), dockerfile=dockerfile)

    def fake_run(argv: list[str]) -> str:
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return "sha256:image-id\n"
        if argv[1:2] == ["version"]:
            return "20.10.17\n"
        if _is_owner_inspect(argv):
            return _recorded_owner(calls)
        if argv[1:3] == ["rm", "--force"] and "sandbox" in argv[-1]:
            raise SandboxError("container removal failed")
        return ""

    monkeypatch.setattr(sandbox, "_run", fake_run)
    monkeypatch.setattr(
        sandbox,
        "_run_attached",
        lambda argv, container_name, timeout_seconds: (0, b"", b"", False, False),
    )

    with pytest.raises(SandboxError, match="cleanup failed"):
        sandbox.execute(("true",), ExecutionContext(workspace=workspace, timeout_seconds=1))

    cleanup_calls = [call for call in calls if call[1:3] in (["rm", "--force"], ["volume", "rm"])]
    assert len(cleanup_calls) == 3
    assert cleanup_calls[-1][1:3] == ["volume", "rm"]


def test_cleanup_refuses_to_remove_a_resource_whose_ownership_changed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    workspace = tmp_path / "run-owner-changed"
    workspace.mkdir()
    calls: list[list[str]] = []
    execution_finished = False
    sandbox = DockerSandbox(_settings(), dockerfile=dockerfile)

    def fake_run(argv: list[str]) -> str:
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return "sha256:image-id\n"
        if argv[1:2] == ["version"]:
            return "20.10.17\n"
        if _is_owner_inspect(argv):
            if execution_finished and argv[-1].endswith("-sandbox"):
                return "foreign-owner"
            return _recorded_owner(calls)
        return ""

    def finish_execution(
        argv: list[str], container_name: str, timeout_seconds: float
    ) -> tuple[int, bytes, bytes, bool, bool]:
        nonlocal execution_finished
        execution_finished = True
        return 0, b"", b"", False, False

    monkeypatch.setattr(sandbox, "_run", fake_run)
    monkeypatch.setattr(sandbox, "_run_attached", finish_execution)

    with pytest.raises(SandboxError, match="ownership changed"):
        sandbox.execute(("true",), ExecutionContext(workspace=workspace, timeout_seconds=1))

    removals = [call[-1] for call in calls if call[1:3] in (["rm", "--force"], ["volume", "rm"])]
    assert not any(name.endswith("-sandbox") for name in removals)
    assert any(name.endswith("-relay") for name in removals)
    assert any(name.endswith("-relay-volume") for name in removals)


def test_cleanup_removes_an_ambiguously_created_resource_only_when_owner_matches(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "run-ambiguous"
    workspace.mkdir()
    calls: list[list[str]] = []
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    sandbox = DockerSandbox(_settings(), dockerfile=dockerfile)

    def fake_run(argv: list[str]) -> str:
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return "sha256:image-id\n"
        if argv[1:2] == ["version"]:
            return "20.10.17\n"
        if argv[1:3] == ["volume", "create"]:
            raise SandboxError("docker command timed out")
        if _is_owner_inspect(argv):
            return _recorded_owner(calls)
        return ""

    monkeypatch.setattr(sandbox, "_run", fake_run)

    with pytest.raises(SandboxError, match="timed out"):
        sandbox.execute(("true",), ExecutionContext(workspace=workspace, timeout_seconds=1))

    assert [call[1:3] for call in calls if call[1:3] == ["volume", "rm"]] == [["volume", "rm"]]
    assert not any(call[1:3] == ["rm", "--force"] for call in calls)


def test_cleanup_ignores_only_conclusive_missing_resource_errors(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM scratch\n")
    workspace = tmp_path / "run-missing-cleanup"
    workspace.mkdir()
    sandbox = DockerSandbox(_settings(), dockerfile=dockerfile)
    calls: list[list[str]] = []

    def fake_run(argv: list[str]) -> str:
        calls.append(argv)
        if argv[1:3] == ["image", "inspect"]:
            return "sha256:image-id\n"
        if argv[1:2] == ["version"]:
            return "20.10.17\n"
        if _is_owner_inspect(argv):
            return _recorded_owner(calls)
        if argv[1:3] == ["rm", "--force"]:
            raise SandboxError(
                f"No such container: {argv[-1]}",
                returncode=1,
                stderr=f"No such container: {argv[-1]}",
            )
        if argv[1:3] == ["volume", "rm"]:
            raise SandboxError(
                f"get {argv[-1]}: no such volume",
                returncode=1,
                stderr=f"get {argv[-1]}: no such volume",
            )
        return ""

    monkeypatch.setattr(sandbox, "_run", fake_run)
    monkeypatch.setattr(
        sandbox,
        "_run_attached",
        lambda argv, container_name, timeout_seconds: (0, b"", b"", False, False),
    )

    result = sandbox.execute(("true",), ExecutionContext(workspace=workspace, timeout_seconds=1))

    assert result.exit_code == 0


@pytest.mark.parametrize("kill_mode", ["fail", "hang"])
def test_attached_run_has_a_post_kill_deadline_and_reaps_processes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kill_mode: str,
) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_docker = fake_bin / "docker"
    fake_docker.write_text(
        "#!/bin/sh\n"
        'if [ "$FAKE_KILL_MODE" = fail ]; then echo "kill rejected" >&2; exit 42; fi\n'
        "sleep 1.2\n"
    )
    fake_docker.chmod(0o755)
    monkeypatch.setenv("FAKE_KILL_MODE", kill_mode)
    monkeypatch.setenv("PATH", f"{fake_bin}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setattr("capability_lab.adapters.sandboxes.POST_KILL_SECONDS", 0.1, raising=False)
    sandbox = DockerSandbox(_settings())
    started = time.monotonic()

    with pytest.raises(SandboxError, match="kill rejected|post-kill deadline"):
        sandbox._run_attached(
            [sys.executable, "-c", "import time; time.sleep(1.2)"],
            "sandbox-under-test",
            0.05,
        )

    assert time.monotonic() - started < 0.8
