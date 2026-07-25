from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from capability_lab.adapters.sandboxes import SandboxError
from capability_lab.domain.models import ExecutionContext, HarnessRequest, HarnessResult
from capability_lab.ports.interfaces import Sandbox

_REQUEST_SCRIPT = """\
import { readFile } from "node:fs/promises";
import http from "node:http";

const payload = await readFile(process.argv[2]);
const result = await new Promise((resolve, reject) => {
  const request = http.request("http://ollama.local:11434/api/generate", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      "content-length": String(payload.length),
    },
  }, (response) => {
    const chunks = [];
    response.on("data", (chunk) => chunks.push(chunk));
    response.on("end", () => resolve({
      status: response.statusCode,
      body: Buffer.concat(chunks).toString("utf8"),
    }));
  });
  request.on("error", reject);
  request.end(payload);
});
process.stdout.write(JSON.stringify(result));
"""


class RawOllamaHarness:
    version = "raw-ollama@1.0.0"

    def __init__(
        self,
        sandbox: Sandbox,
        model: str,
        *,
        seed: int,
        temperature: float,
        max_output_tokens: int,
    ) -> None:
        self.sandbox = sandbox
        self.model = model
        self.seed = seed
        self.temperature = temperature
        self.max_output_tokens = max_output_tokens

    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult:
        runtime_dir = Path(tempfile.mkdtemp(prefix=".capability-lab-raw-", dir=context.workspace))
        sandbox_result = None
        sandbox_error = None
        cleanup_error = None
        try:
            (runtime_dir / "request.mjs").write_text(_REQUEST_SCRIPT)
            (runtime_dir / "request.json").write_text(
                json.dumps(
                    {
                        "model": self.model,
                        "prompt": request.prompt,
                        "stream": False,
                        "options": {
                            "seed": self.seed,
                            "temperature": self.temperature,
                            "num_predict": self.max_output_tokens,
                        },
                    },
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            container_root = f"/workspace/{runtime_dir.name}"
            try:
                sandbox_result = self.sandbox.execute(
                    (
                        "node",
                        f"{container_root}/request.mjs",
                        f"{container_root}/request.json",
                    ),
                    context,
                )
            except SandboxError as error:
                sandbox_error = error
        finally:
            try:
                shutil.rmtree(runtime_dir)
            except OSError as error:
                cleanup_error = error

        provenance = (
            sandbox_result.provenance
            if sandbox_result is not None
            else sandbox_error.provenance
            if sandbox_error is not None
            else None
        )
        if cleanup_error is not None:
            return HarnessResult(
                exit_code=1,
                events=(_failure("config_cleanup", str(cleanup_error)),),
                failure_kind="environment",
                sandbox_provenance=provenance,
            )
        if sandbox_error is not None:
            return HarnessResult(
                exit_code=sandbox_error.returncode or 1,
                events=(_failure("sandbox", str(sandbox_error)),),
                failure_kind="environment",
                sandbox_provenance=provenance,
            )
        assert sandbox_result is not None
        if sandbox_result.timed_out:
            return _process_failure(sandbox_result, "timeout", "sandbox timeout")
        if sandbox_result.output_limited:
            return _process_failure(
                sandbox_result, "output_overflow", "sandbox output limit exceeded"
            )
        if sandbox_result.exit_code != 0:
            return _process_failure(
                sandbox_result,
                "process",
                sandbox_result.stderr or f"exit code {sandbox_result.exit_code}",
            )

        try:
            envelope = json.loads(sandbox_result.stdout)
        except json.JSONDecodeError as error:
            return _malformed(sandbox_result, f"sandbox output: {error.msg}")
        if (
            not isinstance(envelope, dict)
            or not isinstance(envelope.get("status"), int)
            or not isinstance(envelope.get("body"), str)
        ):
            return _malformed(sandbox_result, "sandbox output: expected status and body")
        try:
            body = json.loads(envelope["body"])
        except json.JSONDecodeError as error:
            return _malformed(sandbox_result, f"Ollama response: {error.msg}")
        if not isinstance(body, dict):
            return _malformed(sandbox_result, "Ollama response: expected an object")
        event = {"type": "model_response", "status": envelope["status"], "body": body}
        if not 200 <= envelope["status"] < 300:
            detail = body.get("error")
            detail = detail if isinstance(detail, str) else f"HTTP {envelope['status']}"
            return HarnessResult(
                exit_code=1,
                events=(event, _failure("model_runtime", detail)),
                failure_kind="model_runtime",
                sandbox_provenance=sandbox_result.provenance,
            )
        response = body.get("response")
        if not isinstance(response, str):
            return _malformed(sandbox_result, "Ollama response: missing response text", event)
        if body.get("done") is not True:
            return _malformed(sandbox_result, "Ollama response: expected completed response", event)
        return HarnessResult(
            exit_code=0,
            events=(event,),
            final_response=response,
            sandbox_provenance=sandbox_result.provenance,
        )


def _failure(kind: str, detail: str) -> dict[str, Any]:
    return {"type": "harness_failure", "kind": kind, "detail": detail}


def _process_failure(result: Any, kind: str, detail: str) -> HarnessResult:
    return HarnessResult(
        exit_code=result.exit_code or 1,
        events=(_failure(kind, detail),),
        failure_kind="environment",
        timed_out=result.timed_out,
        output_limited=result.output_limited,
        sandbox_provenance=result.provenance,
    )


def _malformed(result: Any, detail: str, event: dict[str, Any] | None = None) -> HarnessResult:
    return HarnessResult(
        exit_code=1,
        events=(*(() if event is None else (event,)), _failure("malformed_response", detail)),
        failure_kind="environment",
        sandbox_provenance=result.provenance,
    )
