"""The signed webhook is a thin, fail-closed adapter around the application path (P06-3)."""

import hashlib
import hmac
import json

from hyper_agents.adapters.in_memory_delivery_store import InMemoryDeliveryStore
from hyper_agents.application.run_service import HeadlessRunService
from hyper_agents.application.webhook_service import WebhookService
from hyper_agents.domain.api import RunRequest, RunStatus
from hyper_agents.interfaces.webhook import SignedWebhook

SECRET = b"test-webhook-secret"
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


def signature(body: bytes) -> str:
    return "sha256=" + hmac.new(SECRET, body, hashlib.sha256).hexdigest()


class RecordingExecutor:
    def __init__(self) -> None:
        self.requests: list[RunRequest] = []

    def run(self, request: object) -> object:
        assert isinstance(request, RunRequest)
        self.requests.append(request)
        return RunStatus(run_id=request.run_id, status="finished", state="done")


def webhook(executor: RecordingExecutor) -> SignedWebhook:
    service = WebhookService(HeadlessRunService(executor), InMemoryDeliveryStore())
    return SignedWebhook(secret=SECRET, service=service)


def test_signed_delivery_emits_one_json_status_through_the_application_path() -> None:
    # Mutation caught: bypassing the service changes the response and port proof.
    executor = RecordingExecutor()
    body = json.dumps(request()).encode()

    response = webhook(executor).handle(
        body,
        {"X-Hyper-Agents-Delivery": "delivery-1", "X-Hyper-Agents-Signature": signature(body)},
    )

    assert response.status_code == 200
    assert json.loads(response.body) == {
        "detail": "",
        "run_id": "r1",
        "state": "done",
        "status": "finished",
    }
    assert executor.requests == [RunRequest.model_validate(request())]


def test_invalid_signature_is_rejected_before_json_parsing_or_execution() -> None:
    # Negative control: a valid-looking body is powerless without an exact HMAC.
    executor = RecordingExecutor()
    body = b"{not-json}"

    response = webhook(executor).handle(
        body,
        {"X-Hyper-Agents-Delivery": "delivery-1", "X-Hyper-Agents-Signature": "sha256=" + "0" * 64},
    )

    assert response.status_code == 401
    assert json.loads(response.body) == {"error": "invalid_signature"}
    assert executor.requests == []


def test_duplicate_delivery_returns_the_original_status_without_a_second_execution() -> None:
    executor = RecordingExecutor()
    handler = webhook(executor)
    body = json.dumps(request()).encode()
    headers = {"X-Hyper-Agents-Delivery": "delivery-1", "X-Hyper-Agents-Signature": signature(body)}

    first = handler.handle(body, headers)
    second = handler.handle(body, headers)

    assert second == first
    assert executor.requests == [RunRequest.model_validate(request())]


def test_delivery_identifier_cannot_be_reused_for_a_different_signed_body() -> None:
    executor = RecordingExecutor()
    handler = webhook(executor)
    first_body = json.dumps(request()).encode()
    changed_body = json.dumps(request(tokens=2000)).encode()

    handler.handle(
        first_body,
        {
            "X-Hyper-Agents-Delivery": "delivery-1",
            "X-Hyper-Agents-Signature": signature(first_body),
        },
    )
    response = handler.handle(
        changed_body,
        {
            "X-Hyper-Agents-Delivery": "delivery-1",
            "X-Hyper-Agents-Signature": signature(changed_body),
        },
    )

    assert response.status_code == 409
    assert json.loads(response.body) == {"error": "delivery_conflict"}
    assert executor.requests == [RunRequest.model_validate(request())]
