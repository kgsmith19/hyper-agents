"""The headless JSON command is a thin, policy-free invocation adapter (P06-2)."""

import json

import pytest
from typer.testing import CliRunner

from hyper_agents.application.run_service import HeadlessRunService
from hyper_agents.domain.api import RunRequest, RunStatus
from hyper_agents.interfaces import cli

runner = CliRunner()
SHA = "a" * 40
CONTRACT = {"criteria": [{"id": "AC1", "claim": "it works", "check": "pytest -q"}]}


def request(**overrides: object) -> dict[str, object]:
    value: dict[str, object] = {
        "run_id": "r1",
        "idempotency_key": "delivery-r1",
        "contract": CONTRACT,
        "risk": "R1",
        "paths": ["src/x.py"],
        "head_sha": SHA,
        "tokens": 1000,
        "attempts": 3,
    }
    value.update(overrides)
    return value


class RecordingExecutor:
    """A deterministic port implementation that exposes the received contract."""

    def __init__(self) -> None:
        self.requests: list[RunRequest] = []

    def run(self, request: object) -> object:
        assert isinstance(request, RunRequest)
        self.requests.append(request)
        return RunStatus(run_id=request.run_id, status="finished", state="done")


def test_run_json_emits_one_status_after_the_service_passes_the_request_to_the_port(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Mutation caught: bypassing the service/port or printing prose changes this observable result.
    executor = RecordingExecutor()
    service = HeadlessRunService(executor)
    monkeypatch.setattr(cli, "build_headless_service", lambda: service)

    result = runner.invoke(cli.app, ["run-json"], input=json.dumps(request()))

    assert result.exit_code == 0
    assert json.loads(result.stdout) == {
        "detail": "",
        "run_id": "r1",
        "state": "done",
        "status": "finished",
    }
    assert executor.requests == [RunRequest.model_validate(request())]


@pytest.mark.parametrize(
    "payload",
    [
        "{not-json}",
        json.dumps(request(policy="relax")),
        json.dumps(request(tokens=0)),
    ],
)
def test_run_json_rejects_bad_input_without_calling_the_executor(
    monkeypatch: pytest.MonkeyPatch, payload: str
) -> None:
    # Negative control: accepting a policy field or invoking the port before validation must fail.
    executor = RecordingExecutor()
    monkeypatch.setattr(cli, "build_headless_service", lambda: HeadlessRunService(executor))

    result = runner.invoke(cli.app, ["run-json"], input=payload)

    assert result.exit_code == 2
    error = json.loads(result.stderr)
    assert error["error"] == "invalid_request"
    assert len(error["detail"]) <= 300
    assert executor.requests == []
