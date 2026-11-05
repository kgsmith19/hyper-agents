"""Framework-neutral HMAC webhook adapter (P06-3)."""

import hashlib
import hmac
import json
from collections.abc import Mapping
from dataclasses import dataclass

from pydantic import ValidationError

from hyper_agents.application.webhook_service import DeliveryConflict, WebhookService
from hyper_agents.domain.api import RunRequest

_SIGNATURE_PREFIX = "sha256="
_DELIVERY_HEADER = "X-Hyper-Agents-Delivery"
_SIGNATURE_HEADER = "X-Hyper-Agents-Signature"


@dataclass(frozen=True)
class WebhookResponse:
    """Transport-neutral response that an HTTP server can emit unchanged."""

    status_code: int
    body: str


class SignedWebhook:
    """Verify before parse; delegate only trusted deliveries to the application service."""

    def __init__(self, *, secret: bytes, service: WebhookService) -> None:
        if not secret:
            raise ValueError("webhook secret must not be empty")
        self._secret = secret
        self._service = service

    def handle(self, body: bytes, headers: Mapping[str, str]) -> WebhookResponse:
        """Validate one signed raw request without exposing secret or payload content."""
        signature = headers.get(_SIGNATURE_HEADER, "")
        if not self._signature_is_valid(body, signature):
            return _error(401, "invalid_signature")
        delivery_id = headers.get(_DELIVERY_HEADER, "")
        if not delivery_id or len(delivery_id) > 128:
            return _error(400, "invalid_delivery")
        try:
            raw = json.loads(body)
            if not isinstance(raw, dict):
                raise ValueError("request must be an object")
            request = RunRequest.model_validate(raw)
        except (json.JSONDecodeError, UnicodeDecodeError, ValidationError, ValueError):
            return _error(400, "invalid_request")
        digest = hashlib.sha256(body).hexdigest()
        try:
            status = self._service.deliver(delivery_id, digest, request)
        except DeliveryConflict:
            return _error(409, "delivery_conflict")
        return WebhookResponse(200, status.model_dump_json())

    def _signature_is_valid(self, body: bytes, signature: str) -> bool:
        """Use an exact, constant-time SHA-256 HMAC comparison."""
        digest = signature.removeprefix(_SIGNATURE_PREFIX)
        if not signature.startswith(_SIGNATURE_PREFIX) or len(digest) != 64:
            return False
        if any(char not in "0123456789abcdef" for char in digest):
            return False
        expected = _SIGNATURE_PREFIX + hmac.new(self._secret, body, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)


def _error(status_code: int, error: str) -> WebhookResponse:
    return WebhookResponse(status_code, json.dumps({"error": error}, separators=(",", ":")))
