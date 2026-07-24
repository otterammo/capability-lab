from pathlib import Path

from capability_lab.domain.models import ExecutionContext, HarnessRequest, HarnessResult


class FakeHarness:
    version = "fake@1.0.0"

    def execute(self, request: HarnessRequest, context: ExecutionContext) -> HarnessResult:
        if request.mode == "failure":
            return HarnessResult(
                exit_code=1,
                events=({"event": "controlled_failure", "kind": "environment"},),
                final_response="controlled fake harness failure",
                failure_kind="environment",
            )
        if request.mode == "timeout":
            return HarnessResult(
                exit_code=124,
                events=({"event": "timeout"},),
                final_response="controlled fake harness timeout",
                timed_out=True,
            )
        target = Path(context.workspace) / "src/example.py"
        target.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
        return HarnessResult(
            exit_code=0,
            events=({"event": "file_written", "path": "src/example.py"},),
            final_response="fixed src/example.py",
        )
