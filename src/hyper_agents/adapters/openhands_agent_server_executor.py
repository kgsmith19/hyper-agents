"""Fail-closed remote OpenHands Agent Server adapter (P06-5)."""

from typing import Protocol

from pydantic import ValidationError

from hyper_agents.application.ports import ExecutorPort
from hyper_agents.domain.api import RunRequest, RunStatus


class AgentServerClient(Protocol):
    """The narrow transport boundary for a remote Agent Server client."""

    def submit_run(self, payload: dict[str, object]) -> object:
        """Submit a JSON-safe run payload without exposing transport credentials here."""
        ...


class OpenHandsAgentServerExecutor(ExecutorPort):
    """Adapt an injected remote Agent Server client to the local ExecutorPort contract."""

    def __init__(self, client: AgentServerClient) -> None:
        self._client = client

    def run(self, request: object) -> object:
        """Submit only a validated request and convert unsafe remote failures to a safe status."""
        if not isinstance(request, RunRequest):
            return RunStatus(
                run_id="invalid", status="failed", state=None, detail="invalid request"
            )
        try:
            raw = self._client.submit_run(request.model_dump(mode="json"))
        except Exception:
            return RunStatus(
                run_id=request.run_id,
                status="failed",
                state=None,
                detail="agent server unavailable",
            )
        try:
            status = RunStatus.model_validate(raw)
        except ValidationError:
            return _invalid_response(request)
        if status.run_id != request.run_id:
            return _invalid_response(request)
        return status


def _invalid_response(request: RunRequest) -> RunStatus:
    return RunStatus(
        run_id=request.run_id, status="failed", state=None, detail="agent server response invalid"
    )


__all__ = ["AgentServerClient", "OpenHandsAgentServerExecutor"]
