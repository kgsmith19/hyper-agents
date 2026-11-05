"""Application service for a single contract-validated headless run (P06-2)."""

from typing import cast

from hyper_agents.application.ports import ExecutorPort
from hyper_agents.domain.api import RunRequest, RunStatus


class HeadlessRunService:
    """Drive a validated request through the single executor boundary."""

    def __init__(self, executor: ExecutorPort) -> None:
        self._executor = executor

    def run(self, request: RunRequest) -> RunStatus:
        """Execute one admitted request and normalize only the port's result shape."""
        try:
            result = self._executor.run(request)
        except Exception:
            return RunStatus(
                run_id=request.run_id, status="failed", state=None, detail="executor failed"
            )
        if isinstance(result, RunStatus):
            return result
        if isinstance(result, dict):
            result_data = cast(dict[str, object], result)
            if result_data.get("status") == "success":
                return RunStatus(run_id=request.run_id, status="finished", state="done")
        return RunStatus(
            run_id=request.run_id, status="failed", state=None, detail="invalid executor result"
        )
