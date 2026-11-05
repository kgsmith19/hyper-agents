"""Remote Agent Server calls stay behind the ExecutorPort adapter (P06-5)."""

import pytest

from hyper_agents.adapters.openhands_agent_server_executor import OpenHandsAgentServerExecutor
from hyper_agents.application.ports import ExecutorPort
from hyper_agents.domain.api import RunRequest, RunStatus

SHA = "a" * 40
CONTRACT = {"criteria": [{"id": "AC1", "claim": "it works", "check": "pytest -q"}]}


def request() -> RunRequest:
    return RunRequest(
        run_id="r1",
        idempotency_key="delivery-r1",
        contract=CONTRACT,
        risk="R1",
        paths=["src/x.py"],
        head_sha=SHA,
        tokens=1000,
        attempts=3,
    )


class RecordingClient:
    def __init__(self, response: object) -> None:
        self.response = response
        self.payloads: list[dict[str, object]] = []

    def submit_run(self, payload: dict[str, object]) -> object:
        self.payloads.append(payload)
        return self.response


def test_remote_adapter_implements_executor_port_and_forwards_json_safe_run_request() -> None:
    # Mutation caught: changing the payload or bypassing the client breaks the response and receipt.
    client = RecordingClient({"run_id": "r1", "status": "finished", "state": "done"})
    executor = OpenHandsAgentServerExecutor(client)

    result = executor.run(request())

    assert isinstance(executor, ExecutorPort)
    assert result == RunStatus(run_id="r1", status="finished", state="done")
    assert client.payloads == [request().model_dump(mode="json")]


def test_remote_adapter_rejects_non_contract_input_without_calling_the_client() -> None:
    # Negative control: an unvalidated object cannot reach the remote boundary.
    client = RecordingClient({"run_id": "r1", "status": "finished", "state": "done"})

    result = OpenHandsAgentServerExecutor(client).run({"run_id": "r1"})

    assert result == RunStatus(
        run_id="invalid", status="failed", state=None, detail="invalid request"
    )
    assert client.payloads == []


@pytest.mark.parametrize(
    "response",
    [
        {"run_id": "r1", "status": "unexpected", "state": "done", "detail": "remote-secret"},
        {"run_id": "other", "status": "finished", "state": "done"},
    ],
)
def test_remote_adapter_fails_closed_on_malformed_or_wrong_run_response(response: object) -> None:
    client = RecordingClient(response)

    result = OpenHandsAgentServerExecutor(client).run(request())

    assert isinstance(result, RunStatus)
    assert result == RunStatus(
        run_id="r1", status="failed", state=None, detail="agent server response invalid"
    )
    assert "remote-secret" not in result.detail


def test_remote_adapter_hides_client_failure_details() -> None:
    class FailingClient:
        def submit_run(self, payload: dict[str, object]) -> object:
            raise RuntimeError("remote-secret")

    result = OpenHandsAgentServerExecutor(FailingClient()).run(request())

    assert result == RunStatus(
        run_id="r1", status="failed", state=None, detail="agent server unavailable"
    )
