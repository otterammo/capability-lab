from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from capability_lab.adapters.sandboxes import SandboxError
from capability_lab.domain.models import ExecutionContext, HarnessRequest, HarnessResult
from capability_lab.ports.interfaces import Sandbox

TOOL_BUDGET_MARKER = "CAPABILITY_LAB_TOOL_BUDGET_EXCEEDED"


class PiHarness:
    version = "pi@0.79.3"

    def __init__(
        self,
        sandbox: Sandbox,
        model: str,
        *,
        seed: int | None = None,
        temperature: float | None = None,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
    ) -> None:
        self.sandbox = sandbox
        self.model = model
        self.seed = seed
        self.temperature = temperature
        self.context_window = context_window
        self.max_output_tokens = max_output_tokens

    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult:
        if context.max_tool_calls is None or context.max_tool_calls <= 0:
            raise ValueError("max_tool_calls must be positive for Pi")
        runtime_dir = Path(tempfile.mkdtemp(prefix=".capability-lab-pi-", dir=context.workspace))
        agent_dir = runtime_dir / "agent"
        session_dir = runtime_dir / "sessions"
        home_dir = runtime_dir / "home"
        agent_dir.mkdir(parents=True)
        session_dir.mkdir()
        home_dir.mkdir()
        (agent_dir / "models.json").write_text(
            json.dumps(
                {
                    "providers": {
                        "ollama": {
                            "baseUrl": "http://ollama.local:11434/v1",
                            "api": "openai-completions",
                            "apiKey": "ollama",
                            "models": [
                                {
                                    "id": self.model,
                                    **(
                                        {}
                                        if self.context_window is None
                                        else {"contextWindow": self.context_window}
                                    ),
                                    **(
                                        {}
                                        if self.max_output_tokens is None
                                        else {"maxTokens": self.max_output_tokens}
                                    ),
                                }
                            ],
                        }
                    }
                },
                sort_keys=True,
                separators=(",", ":"),
            )
        )
        (runtime_dir / "prompt.txt").write_text(request.prompt)
        request_settings = (
            ""
            if self.seed is None or self.temperature is None or self.max_output_tokens is None
            else '  pi.on("before_provider_request", (event) => ({\n'
            "    ...event.payload,\n"
            f"    seed: {self.seed},\n"
            f"    temperature: {self.temperature},\n"
            f"    max_tokens: {self.max_output_tokens},\n"
            "  }));\n"
        )
        (runtime_dir / "tool-budget.js").write_text(
            "export default function (pi) {\n" + request_settings + "  let toolCalls = 0;\n"
            '  pi.on("tool_call", () => {\n'
            "    toolCalls += 1;\n"
            f"    if (toolCalls > {context.max_tool_calls}) {{\n"
            "      return { block: true, reason: "
            f'"{TOOL_BUDGET_MARKER} max={context.max_tool_calls}" }};\n'
            "    }\n"
            "  });\n"
            "}\n"
        )
        container_root = f"/workspace/{runtime_dir.name}"
        argv = (
            "env",
            f"HOME={container_root}/home",
            f"PI_CODING_AGENT_DIR={container_root}/agent",
            f"PI_CODING_AGENT_SESSION_DIR={container_root}/sessions",
            "PI_OFFLINE=1",
            "PI_TELEMETRY=0",
            "pi",
            "--provider",
            "ollama",
            "--model",
            self.model,
            "--api-key",
            "ollama",
            "--mode",
            "json",
            "--print",
            "--no-session",
            "--session-dir",
            f"{container_root}/sessions",
            "--tools",
            "read,bash,edit,write",
            "--no-extensions",
            "--extension",
            f"{container_root}/tool-budget.js",
            "--no-skills",
            "--no-prompt-templates",
            "--no-themes",
            "--no-context-files",
            "--no-approve",
            "--offline",
            f"@{container_root}/prompt.txt",
        )
        sandbox_result = None
        sandbox_error = None
        try:
            sandbox_result = self.sandbox.execute(argv, context)
        except SandboxError as error:
            sandbox_error = error
        try:
            shutil.rmtree(runtime_dir)
        except OSError as error:
            provenance = (
                sandbox_result.provenance
                if sandbox_result is not None
                else sandbox_error.provenance
                if sandbox_error is not None
                else None
            )
            return HarnessResult(
                exit_code=1,
                events=(_failure("config_cleanup", str(error)),),
                failure_kind="environment",
                sandbox_provenance=provenance,
            )

        if sandbox_error is not None:
            return HarnessResult(
                exit_code=sandbox_error.returncode or 1,
                events=(_failure("sandbox", str(sandbox_error)),),
                failure_kind="environment",
                sandbox_provenance=sandbox_error.provenance,
            )
        assert sandbox_result is not None

        events, final_response, model_error, malformed, saw_assistant = _parse(
            sandbox_result.stdout
        )
        tool_budget_error = _tool_budget_error(events, context.max_tool_calls)
        failure_kind = None
        exit_code = sandbox_result.exit_code
        if sandbox_result.timed_out:
            events.append(_failure("timeout", "sandbox timeout"))
            failure_kind = "environment"
        elif sandbox_result.output_limited:
            events.append(_failure("output_overflow", "sandbox output limit exceeded"))
            failure_kind = "environment"
        elif malformed is not None:
            events.append(_failure("malformed_event", malformed))
            exit_code = exit_code or 1
            failure_kind = "environment"
        elif tool_budget_error is not None:
            events.append(_failure("tool_budget", tool_budget_error))
            exit_code = exit_code or 1
            failure_kind = "tool_budget"
        elif model_error is not None:
            events.append(_failure("model_runtime", model_error))
            exit_code = exit_code or 1
            failure_kind = "model_runtime"
        elif exit_code == 0 and not saw_assistant:
            events.append(_failure("missing_final_response", "no assistant message"))
            exit_code = 1
            failure_kind = "environment"
        elif exit_code != 0:
            events.append(_failure("process", sandbox_result.stderr or f"exit code {exit_code}"))
            failure_kind = "environment"

        return HarnessResult(
            exit_code=exit_code,
            events=tuple(events),
            final_response=final_response,
            failure_kind=failure_kind,
            timed_out=sandbox_result.timed_out,
            output_limited=sandbox_result.output_limited,
            sandbox_provenance=sandbox_result.provenance,
        )


def _failure(kind: str, detail: str) -> dict[str, Any]:
    return {"type": "harness_failure", "kind": kind, "detail": detail}


def _tool_budget_error(events: list[dict[str, Any]], max_tool_calls: int) -> str | None:
    tool_call_ids = {
        tool_call_id
        for event in events
        if event.get("type") == "tool_execution_end"
        for tool_call_id in (event.get("toolCallId"),)
        if isinstance(tool_call_id, str) and tool_call_id
    }
    if len(tool_call_ids) <= max_tool_calls:
        return None
    for event in events:
        if event.get("type") != "tool_execution_end" or event.get("isError") is not True:
            continue
        marker_call_id = event.get("toolCallId")
        if not isinstance(marker_call_id, str) or marker_call_id not in tool_call_ids:
            continue
        result = event.get("result")
        content = result.get("content") if isinstance(result, dict) else None
        if not isinstance(content, list):
            continue
        for item in content:
            if not isinstance(item, dict) or item.get("type") != "text":
                continue
            text = item.get("text")
            if isinstance(text, str) and text.startswith(TOOL_BUDGET_MARKER):
                return text
    return None


def _parse(
    stdout: str,
) -> tuple[list[dict[str, Any]], str, str | None, str | None, bool]:
    events: list[dict[str, Any]] = []
    final_response = ""
    model_error = None
    saw_assistant = False
    for line_number, line in enumerate(stdout.splitlines(), 1):
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as error:
            return events, final_response, model_error, f"line {line_number}: {error.msg}", False
        if not isinstance(event, dict) or not isinstance(event.get("type"), str):
            return (
                events,
                final_response,
                model_error,
                f"line {line_number}: expected an object with a string type",
                False,
            )
        events.append(event)
        messages = (
            event.get("messages", ())
            if event["type"] == "agent_end"
            else (event.get("message"),)
            if event["type"] in {"message_end", "turn_end"}
            else ()
        )
        if not isinstance(messages, (list, tuple)):
            return (
                events,
                final_response,
                model_error,
                f"line {line_number}: invalid messages",
                saw_assistant,
            )
        for message in messages:
            if not isinstance(message, dict) or message.get("role") != "assistant":
                continue
            saw_assistant = True
            content = message.get("content")
            if not isinstance(content, list):
                return (
                    events,
                    final_response,
                    model_error,
                    f"line {line_number}: invalid content",
                    saw_assistant,
                )
            text: list[str] = []
            for item in content:
                if not isinstance(item, dict) or item.get("type") != "text":
                    continue
                value = item.get("text")
                if not isinstance(value, str):
                    return (
                        events,
                        final_response,
                        model_error,
                        f"line {line_number}: invalid text content",
                        saw_assistant,
                    )
                text.append(value)
            final_response = "".join(text)
            stop_reason = message.get("stopReason")
            if not isinstance(stop_reason, str):
                return (
                    events,
                    final_response,
                    model_error,
                    f"line {line_number}: invalid stop reason",
                    saw_assistant,
                )
            if stop_reason in {"error", "aborted"}:
                model_error = str(message.get("errorMessage") or stop_reason)
    return events, final_response, model_error, None, saw_assistant
