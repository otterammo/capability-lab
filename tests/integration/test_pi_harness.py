from __future__ import annotations

import json
from pathlib import Path

import pytest

from capability_lab.adapters.pi_harness import PiHarness
from capability_lab.adapters.sandboxes import SandboxError
from capability_lab.domain.models import (
    ExecutionContext,
    HarnessRequest,
    NetworkPolicy,
    SandboxIdentity,
    SandboxProvenance,
    SandboxResult,
)


def _provenance() -> SandboxProvenance:
    return SandboxProvenance(
        SandboxIdentity("sha256:image", "27.0.0", "501:20", "dockerfile"),
        NetworkPolicy("ollama-only", "http://host.docker.internal:11434", False),
    )


class StubSandbox:
    def __init__(self, result: SandboxResult | None = None, error: Exception | None = None) -> None:
        self.result = result
        self.error = error
        self.argv: tuple[str, ...] = ()
        self.context: ExecutionContext | None = None
        self.models: dict[str, object] | None = None
        self.prompt = ""
        self.runtime_name = ""
        self.extension = ""

    def execute(self, argv: tuple[str, ...], context: ExecutionContext) -> SandboxResult:
        self.argv = argv
        self.context = context
        runtime_dirs = list(context.workspace.glob(".capability-lab-pi-*"))
        assert len(runtime_dirs) == 1
        runtime_dir = runtime_dirs[0]
        self.runtime_name = runtime_dir.name
        self.models = json.loads((runtime_dir / "agent/models.json").read_text())
        self.prompt = (runtime_dir / "prompt.txt").read_text()
        extension = runtime_dir / "tool-budget.js"
        self.extension = extension.read_text() if extension.exists() else ""
        if self.error is not None:
            raise self.error
        assert self.result is not None
        return self.result


def _execute(tmp_path: Path, sandbox: StubSandbox):
    return PiHarness(sandbox, "qwen2.5-coder:1.5b").execute(
        HarnessRequest("task", "fix only src/example.py", "success"),
        ExecutionContext(tmp_path, 30, 1),
    )


def test_pi_invocation_uses_only_sandbox_state_selected_model_and_builtin_tools(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "host-secret")
    (tmp_path / "AGENTS.md").write_text("host instructions")
    (tmp_path / ".evaluation").mkdir()
    (tmp_path / ".evaluation/hidden.py").write_text("protected")
    sandbox = StubSandbox(SandboxResult(0, "", "", False, False, _provenance()))

    _execute(tmp_path, sandbox)

    root = f"/workspace/{sandbox.runtime_name}"
    assert sandbox.argv == (
        "env",
        f"HOME={root}/home",
        f"PI_CODING_AGENT_DIR={root}/agent",
        f"PI_CODING_AGENT_SESSION_DIR={root}/sessions",
        "PI_OFFLINE=1",
        "PI_TELEMETRY=0",
        "pi",
        "--provider",
        "ollama",
        "--model",
        "qwen2.5-coder:1.5b",
        "--api-key",
        "ollama",
        "--mode",
        "json",
        "--print",
        "--no-session",
        "--session-dir",
        f"{root}/sessions",
        "--tools",
        "read,bash,edit,write",
        "--no-extensions",
        "--extension",
        f"{root}/tool-budget.js",
        "--no-skills",
        "--no-prompt-templates",
        "--no-themes",
        "--no-context-files",
        "--no-approve",
        "--offline",
        f"@{root}/prompt.txt",
    )
    assert sandbox.models == {
        "providers": {
            "ollama": {
                "api": "openai-completions",
                "apiKey": "ollama",
                "baseUrl": "http://ollama.local:11434/v1",
                "models": [{"id": "qwen2.5-coder:1.5b"}],
            }
        }
    }
    assert sandbox.context == ExecutionContext(tmp_path, 30, 1)
    assert sandbox.prompt == "fix only src/example.py"
    assert sandbox.extension
    assert "host-secret" not in "\n".join(sandbox.argv)
    assert not (tmp_path / sandbox.runtime_name).exists()


def test_pi_refuses_to_run_without_a_positive_tool_budget(tmp_path: Path) -> None:
    sandbox = StubSandbox(SandboxResult(0, "", "", False, False, _provenance()))

    with pytest.raises(ValueError, match="max_tool_calls must be positive"):
        PiHarness(sandbox, "qwen2.5-coder:1.5b").execute(
            HarnessRequest("task", "fix", "success"),
            ExecutionContext(tmp_path, 30),
        )

    assert sandbox.argv == ()
    assert list(tmp_path.glob(".capability-lab-pi-*")) == []


def test_pi_runtime_does_not_follow_a_tracked_symlink_outside_the_workspace(
    tmp_path: Path,
) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / ".capability-lab-pi").symlink_to(outside, target_is_directory=True)
    stdout = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
                "stopReason": "stop",
            },
        }
    )

    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert list(outside.iterdir()) == []
    assert result.exit_code == 0
    assert (tmp_path / ".capability-lab-pi").is_symlink()
    assert list(tmp_path.glob(".capability-lab-pi-*")) == []


@pytest.mark.parametrize(
    ("result", "expected_kind", "timed_out", "output_limited"),
    [
        (SandboxResult(7, "", "pi failed", False, False, _provenance()), "process", False, False),
        (SandboxResult(137, "", "", True, False, _provenance()), "timeout", True, False),
        (
            SandboxResult(137, "", "", False, True, _provenance()),
            "output_overflow",
            False,
            True,
        ),
    ],
)
def test_pi_process_failures_are_structured_and_keep_provenance(
    tmp_path: Path,
    result: SandboxResult,
    expected_kind: str,
    timed_out: bool,
    output_limited: bool,
) -> None:
    harness_result = _execute(tmp_path, StubSandbox(result))

    assert harness_result.exit_code != 0
    assert harness_result.events[-1]["kind"] == expected_kind
    assert harness_result.failure_kind == "environment"
    assert harness_result.timed_out is timed_out
    assert harness_result.output_limited is output_limited
    assert harness_result.sandbox_provenance == _provenance()
    assert list(tmp_path.glob(".capability-lab-pi-*")) == []


@pytest.mark.parametrize("stdout", ["not-json", "[]", '{"event":"missing-type"}'])
def test_pi_rejects_malformed_json_events_with_structured_evidence(
    tmp_path: Path, stdout: str
) -> None:
    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert result.exit_code == 1
    assert result.failure_kind == "environment"
    assert result.events[-1]["kind"] == "malformed_event"
    assert result.sandbox_provenance == _provenance()


def test_pi_rejects_non_string_assistant_text_with_provenance(tmp_path: Path) -> None:
    stdout = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": 42}],
                "stopReason": "stop",
            },
        }
    )

    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert result.exit_code == 1
    assert result.failure_kind == "environment"
    assert result.events[-1]["kind"] == "malformed_event"
    assert result.sandbox_provenance == _provenance()


@pytest.mark.parametrize("stop_reason", [[], {}, 42, None])
def test_pi_rejects_non_string_assistant_stop_reason_with_provenance(
    tmp_path: Path, stop_reason: object
) -> None:
    stdout = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "done"}],
                "stopReason": stop_reason,
            },
        }
    )

    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert result.exit_code == 1
    assert result.failure_kind == "environment"
    assert result.events[-1]["kind"] == "malformed_event"
    assert result.sandbox_provenance == _provenance()


def test_pi_model_runtime_error_is_not_reported_as_success(tmp_path: Path) -> None:
    stdout = json.dumps(
        {
            "type": "message_end",
            "message": {
                "role": "assistant",
                "content": [{"type": "text", "text": "partial"}],
                "stopReason": "error",
                "errorMessage": "model disconnected",
            },
        }
    )

    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert result.exit_code == 1
    assert result.final_response == "partial"
    assert result.failure_kind == "model_runtime"
    assert result.events[-1] == {
        "type": "harness_failure",
        "kind": "model_runtime",
        "detail": "model disconnected",
    }
    assert result.sandbox_provenance == _provenance()


def test_pi_tool_budget_block_is_structured_and_not_reported_as_success(
    tmp_path: Path,
) -> None:
    stdout = "\n".join(
        (
            '{"type":"tool_execution_end","toolCallId":"first","toolName":"write",'
            '"result":{"content":[{"type":"text","text":"ok"}]},"isError":false}',
            '{"type":"tool_execution_end","toolCallId":"second","toolName":"write",'
            '"result":{"content":[{"type":"text",'
            '"text":"CAPABILITY_LAB_TOOL_BUDGET_EXCEEDED max=1"}]},"isError":true}',
            '{"type":"message_end","message":{"role":"assistant",'
            '"content":[{"type":"text","text":"done"}],"stopReason":"stop"}}',
        )
    )

    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert result.exit_code == 1
    assert result.failure_kind == "tool_budget"
    assert result.events[-1] == {
        "type": "harness_failure",
        "kind": "tool_budget",
        "detail": "CAPABILITY_LAB_TOOL_BUDGET_EXCEEDED max=1",
    }
    assert result.sandbox_provenance == _provenance()


def test_pi_does_not_trust_a_budget_marker_from_an_in_budget_tool_error(
    tmp_path: Path,
) -> None:
    failed_first_tool = (
        '{"type":"tool_execution_end","toolCallId":"first","toolName":"write",'
        '"result":{"content":[{"type":"text",'
        '"text":"CAPABILITY_LAB_TOOL_BUDGET_EXCEEDED max=1"}]},"isError":true}'
    )
    stdout = "\n".join(
        (
            failed_first_tool,
            failed_first_tool,
            '{"type":"message_end","message":{"role":"assistant",'
            '"content":[{"type":"text","text":"recovered"}],"stopReason":"stop"}}',
        )
    )

    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert result.exit_code == 0
    assert result.failure_kind is None
    assert all(event.get("kind") != "tool_budget" for event in result.events)
    assert result.sandbox_provenance == _provenance()


def test_pi_requires_a_final_assistant_event_before_reporting_success(tmp_path: Path) -> None:
    stdout = '{"type":"agent_start"}\n{"type":"agent_end","messages":[]}'

    result = _execute(
        tmp_path,
        StubSandbox(SandboxResult(0, stdout, "", False, False, _provenance())),
    )

    assert result.exit_code == 1
    assert result.failure_kind == "environment"
    assert result.events[-1]["kind"] == "missing_final_response"


def test_pi_translates_sandbox_setup_or_cleanup_error_with_provenance(tmp_path: Path) -> None:
    error = SandboxError("cleanup failed", returncode=9, provenance=_provenance())

    result = _execute(tmp_path, StubSandbox(error=error))

    assert result.exit_code == 9
    assert result.failure_kind == "environment"
    assert result.events == (
        {"type": "harness_failure", "kind": "sandbox", "detail": "cleanup failed"},
    )
    assert result.sandbox_provenance == _provenance()
    assert list(tmp_path.glob(".capability-lab-pi-*")) == []
