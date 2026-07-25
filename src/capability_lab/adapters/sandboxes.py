from __future__ import annotations

import hashlib
import ipaddress
import os
import selectors
import socket
import stat
import subprocess
import time
from pathlib import Path
from typing import IO, cast
from uuid import uuid4

from capability_lab.domain.models import (
    ExecutionContext,
    NetworkPolicy,
    SandboxIdentity,
    SandboxProvenance,
    SandboxResult,
    SandboxSettings,
)
from capability_lab.domain.rules import ollama_hostname

POST_KILL_SECONDS = 5.0
PROCESS_REAP_SECONDS = 1.0
SANDBOX_WRAPPER = (
    "socat TCP-LISTEN:11434,bind=127.0.0.1,fork,reuseaddr "
    "UNIX-CONNECT:/relay/ollama.sock & relay_pid=$!; "
    "until netstat -ltn | grep -q '127.0.0.1:11434'; do "
    'kill -0 "$relay_pid" || exit 125; sleep 0.01; done; exec "$@"'
)


class SandboxError(RuntimeError):
    def __init__(
        self,
        message: str,
        *,
        returncode: int | None = None,
        stderr: str = "",
        provenance: SandboxProvenance | None = None,
    ) -> None:
        super().__init__(message)
        self.returncode = returncode
        self.stderr = stderr
        self.provenance = provenance


class DockerSandbox:
    def __init__(
        self,
        settings: SandboxSettings,
        *,
        ollama_base_url: str = "http://127.0.0.1:11434",
        dockerfile: Path = Path("docker/sandbox/Dockerfile"),
    ) -> None:
        self.settings = settings
        self.ollama_base_url = ollama_base_url
        self.ollama_host = ollama_hostname(ollama_base_url)
        self.dockerfile = dockerfile

    def _ollama_upstream(self) -> str:
        if self.ollama_host in {"127.0.0.1", "localhost"}:
            return "host.docker.internal"
        try:
            records = socket.getaddrinfo(self.ollama_host, 11434, type=socket.SOCK_STREAM)
        except socket.gaierror as error:
            raise SandboxError(f"cannot resolve Ollama hostname: {self.ollama_host}") from error
        addresses = sorted({str(record[4][0]) for record in records if record[0] == socket.AF_INET})
        if not addresses:
            raise SandboxError(f"cannot resolve Ollama hostname: {self.ollama_host}")
        if len(addresses) != 1:
            raise SandboxError(f"ambiguous Ollama hostname: {self.ollama_host}")
        address = addresses[0]
        return "host.docker.internal" if ipaddress.ip_address(address).is_loopback else address

    def _run(self, argv: list[str]) -> str:
        try:
            result = subprocess.run(
                argv,
                check=False,
                capture_output=True,
                text=True,
                errors="surrogateescape",
                timeout=30,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise SandboxError(f"docker command failed: {error}") from error
        if result.returncode != 0:
            stderr = result.stderr.strip()
            raise SandboxError(
                stderr or "docker command failed",
                returncode=result.returncode,
                stderr=stderr,
            )
        return result.stdout

    @staticmethod
    def _reap(process: subprocess.Popen[bytes] | None) -> None:
        if process is None:
            return
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=PROCESS_REAP_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=PROCESS_REAP_SECONDS)
        else:
            process.wait()

    def _run_attached(
        self, argv: list[str], container_name: str, timeout_seconds: float
    ) -> tuple[int, bytes, bytes, bool, bool]:
        try:
            process = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except OSError as error:
            raise SandboxError(f"docker start failed: {error}") from error
        assert process.stdout is not None
        assert process.stderr is not None
        stdout = cast(IO[bytes], process.stdout)
        stderr = cast(IO[bytes], process.stderr)
        streams: dict[IO[bytes], bytearray] = {
            stdout: bytearray(),
            stderr: bytearray(),
        }
        selector = selectors.DefaultSelector()
        for stream in streams:
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, selectors.EVENT_READ)
        deadline = time.monotonic() + timeout_seconds
        kill_deadline: float | None = None
        captured = 0
        timed_out = False
        output_limited = False
        exit_code: int | None = None
        kill_process: subprocess.Popen[bytes] | None = None
        failure: SandboxError | None = None

        def start_kill() -> None:
            nonlocal kill_process, kill_deadline
            try:
                kill_process = subprocess.Popen(
                    ["docker", "kill", container_name],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                )
            except OSError as error:
                raise SandboxError(f"docker kill failed: {error}") from error
            kill_deadline = time.monotonic() + POST_KILL_SECONDS

        def kill_error() -> SandboxError | None:
            if kill_process is None or kill_process.poll() is None:
                return None
            _, kill_stderr = kill_process.communicate()
            if kill_process.returncode == 0:
                return None
            details = kill_stderr.decode(errors="surrogateescape").strip()
            return SandboxError(
                f"docker kill failed ({kill_process.returncode}): {details or 'no stderr'}",
                returncode=kill_process.returncode,
                stderr=details,
            )

        try:
            while selector.get_map():
                now = time.monotonic()
                if kill_process is None and now >= deadline:
                    timed_out = True
                    start_kill()
                failure = kill_error()
                if failure is not None:
                    break
                if kill_deadline is not None and now >= kill_deadline:
                    failure = SandboxError("docker post-kill deadline exceeded")
                    break
                next_deadline = kill_deadline if kill_deadline is not None else deadline
                events = selector.select(max(0.0, min(next_deadline - now, 0.1)))
                for key, _ in events:
                    stream = cast(IO[bytes], key.fileobj)
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        selector.unregister(stream)
                        continue
                    available = max(0, self.settings.max_output_bytes - captured)
                    streams[stream].extend(chunk[:available])
                    captured += min(len(chunk), available)
                    if captured >= self.settings.max_output_bytes and kill_process is None:
                        output_limited = True
                        start_kill()
            if failure is None:
                failure = kill_error()
            if failure is None and kill_process is not None and kill_process.poll() is None:
                assert kill_deadline is not None
                try:
                    _, kill_stderr = kill_process.communicate(
                        timeout=max(0.0, kill_deadline - time.monotonic())
                    )
                except subprocess.TimeoutExpired:
                    failure = SandboxError("docker post-kill deadline exceeded")
                else:
                    if kill_process.returncode != 0:
                        details = kill_stderr.decode(errors="surrogateescape").strip()
                        failure = SandboxError(
                            f"docker kill failed ({kill_process.returncode}): "
                            f"{details or 'no stderr'}",
                            returncode=kill_process.returncode,
                            stderr=details,
                        )
            if failure is None:
                wait_timeout = (
                    max(0.0, kill_deadline - time.monotonic())
                    if kill_deadline is not None
                    else PROCESS_REAP_SECONDS
                )
                try:
                    exit_code = process.wait(timeout=wait_timeout)
                except subprocess.TimeoutExpired:
                    failure = SandboxError("docker post-kill deadline exceeded")
            if failure is not None:
                raise failure
        finally:
            selector.close()
            for stream in streams:
                stream.close()
            self._reap(kill_process)
            self._reap(process)
        assert exit_code is not None
        return (
            exit_code,
            bytes(streams[stdout]),
            bytes(streams[stderr]),
            timed_out,
            output_limited,
        )

    @staticmethod
    def _conclusively_missing(kind: str, error: SandboxError) -> bool:
        if error.returncode != 1:
            return False
        details = error.stderr.lower()
        return (
            kind == "container"
            and "no such container:" in details
            or kind == "volume"
            and "no such volume" in details
        )

    def _resource_owner(self, kind: str, name: str) -> str:
        if kind == "container":
            command = [
                "docker",
                "inspect",
                "--type",
                "container",
                "--format",
                '{{ index .Config.Labels "capability-lab.sandbox" }}',
                name,
            ]
        else:
            command = [
                "docker",
                "volume",
                "inspect",
                "--format",
                '{{ index .Labels "capability-lab.sandbox" }}',
                name,
            ]
        return self._run(command).strip()

    def execute(self, argv: tuple[str, ...], context: ExecutionContext) -> SandboxResult:
        if not argv or any(not isinstance(arg, str) or "\n" in arg or "\0" in arg for arg in argv):
            raise ValueError("argv must be non-empty strings without newline or NUL")
        if not context.workspace.is_absolute():
            raise ValueError("workspace must be absolute")
        try:
            workspace = context.workspace.resolve(strict=True)
        except FileNotFoundError as error:
            raise ValueError("workspace must exist") from error
        workspace_stat = workspace.stat()
        if not stat.S_ISDIR(workspace_stat.st_mode):
            raise ValueError("workspace must be a directory")
        if workspace_stat.st_uid == 0:
            raise ValueError("root-owned workspace is not allowed")
        if context.timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")

        ollama_upstream = self._ollama_upstream()
        image_id = self._run(
            ["docker", "image", "inspect", "--format", "{{.Id}}", self.settings.image]
        ).strip()
        if not image_id:
            raise SandboxError("image inspect returned no image ID")
        docker_version = self._run(["docker", "version", "--format", "{{.Server.Version}}"])
        run_token = hashlib.sha256(workspace.name.encode()).hexdigest()[:12]
        resource_prefix = f"capability-lab-{run_token}"
        relay_name = f"{resource_prefix}-relay"
        container_name = f"{resource_prefix}-sandbox"
        volume_name = f"{resource_prefix}-relay-volume"
        owner = str(uuid4())
        label = f"capability-lab.sandbox={owner}"
        user = f"{workspace_stat.st_uid}:{workspace_stat.st_gid}"
        provenance = SandboxProvenance(
            identity=SandboxIdentity(
                image_id=image_id,
                docker_version=docker_version.strip(),
                user=user,
                dockerfile_sha256=hashlib.sha256(self.dockerfile.read_bytes()).hexdigest(),
            ),
            network_policy=NetworkPolicy(
                name="ollama-only",
                endpoint=f"http://{ollama_upstream}:11434",
                external_access=False,
            ),
        )
        owned_resources: list[tuple[str, str]] = []
        cleanup_errors: list[SandboxError] = []
        primary_error: BaseException | None = None
        result: SandboxResult | None = None

        def create_owned(kind: str, name: str, command: list[str]) -> None:
            try:
                self._run(command)
            except BaseException:
                try:
                    if self._resource_owner(kind, name) == owner:
                        owned_resources.append((kind, name))
                except SandboxError:
                    pass
                raise
            if self._resource_owner(kind, name) != owner:
                raise SandboxError(f"{kind} {name} is not owned by this sandbox invocation")
            owned_resources.append((kind, name))

        try:
            create_owned(
                "volume",
                volume_name,
                ["docker", "volume", "create", "--label", label, volume_name],
            )
            create_owned(
                "container",
                relay_name,
                [
                    "docker",
                    "create",
                    "--pull=never",
                    "--name",
                    relay_name,
                    "--label",
                    label,
                    "--network",
                    "bridge",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--memory",
                    f"{self.settings.memory_mb}m",
                    "--memory-swap",
                    f"{self.settings.memory_mb}m",
                    "--cpus",
                    str(self.settings.cpus),
                    "--pids-limit",
                    str(self.settings.pids),
                    "--ulimit",
                    f"nofile={self.settings.nofile}:{self.settings.nofile}",
                    "--mount",
                    f"type=volume,source={volume_name},target=/relay",
                    image_id,
                    "socat",
                    "UNIX-LISTEN:/relay/ollama.sock,fork,mode=0666,unlink-early",
                    f"TCP:{ollama_upstream}:11434",
                ],
            )
            self._run(["docker", "start", relay_name])
            create_owned(
                "container",
                container_name,
                [
                    "docker",
                    "create",
                    "--pull=never",
                    "--name",
                    container_name,
                    "--label",
                    label,
                    "--network",
                    "none",
                    "--add-host",
                    "ollama.local:127.0.0.1",
                    "--read-only",
                    "--cap-drop",
                    "ALL",
                    "--security-opt",
                    "no-new-privileges",
                    "--memory",
                    f"{self.settings.memory_mb}m",
                    "--memory-swap",
                    f"{self.settings.memory_mb}m",
                    "--cpus",
                    str(self.settings.cpus),
                    "--pids-limit",
                    str(self.settings.pids),
                    "--ulimit",
                    f"nofile={self.settings.nofile}:{self.settings.nofile}",
                    "--user",
                    user,
                    "--workdir",
                    "/workspace",
                    "--mount",
                    f"type=volume,source={volume_name},target=/relay,readonly",
                    "--mount",
                    f"type=bind,source={workspace},target=/workspace",
                    image_id,
                    "sh",
                    "-c",
                    SANDBOX_WRAPPER,
                    "capability-lab-sandbox",
                    *argv,
                ],
            )
            exit_code, stdout, stderr, timed_out, output_limited = self._run_attached(
                ["docker", "start", "--attach", container_name],
                container_name,
                context.timeout_seconds,
            )
            result = SandboxResult(
                exit_code=exit_code,
                stdout=stdout.decode(errors="surrogateescape"),
                stderr=stderr.decode(errors="surrogateescape"),
                timed_out=timed_out,
                output_limited=output_limited,
                provenance=provenance,
            )
        except BaseException as error:
            primary_error = error
        finally:
            for kind, name in reversed(owned_resources):
                try:
                    if self._resource_owner(kind, name) != owner:
                        cleanup_errors.append(
                            SandboxError(f"refusing to remove {kind} {name}: ownership changed")
                        )
                        continue
                    command = (
                        ["docker", "rm", "--force", name]
                        if kind == "container"
                        else ["docker", "volume", "rm", "--force", name]
                    )
                    self._run(command)
                except SandboxError as error:
                    if not self._conclusively_missing(kind, error):
                        cleanup_errors.append(error)
        if primary_error is not None:
            if isinstance(primary_error, SandboxError) and primary_error.provenance is None:
                primary_error.provenance = provenance
            for error in cleanup_errors:
                primary_error.add_note(f"cleanup failed: {error}")
            raise primary_error
        if cleanup_errors:
            raise SandboxError(
                "cleanup failed: " + "; ".join(map(str, cleanup_errors)),
                provenance=provenance,
            )
        assert result is not None
        return result
