"""Application-level at-least-once delivery handling for signed webhooks (P06-3)."""

from dataclasses import dataclass
from typing import Protocol

from hyper_agents.application.run_service import HeadlessRunService
from hyper_agents.domain.api import RunRequest, RunStatus


@dataclass(frozen=True)
class DeliveryRecord:
    """The immutable result bound to one provider delivery identifier and body digest."""

    body_digest: str
    status: RunStatus


class DeliveryStorePort(Protocol):
    """Stores a completed webhook delivery outside the inbound adapter."""

    def get(self, delivery_id: str) -> DeliveryRecord | None: ...
    def put(self, delivery_id: str, record: DeliveryRecord) -> None: ...


class DeliveryConflict(Exception):
    """A provider reused a delivery identifier for a different signed body."""


class WebhookService:
    """Deduplicate trusted deliveries before they can reach the executor path."""

    def __init__(self, runner: HeadlessRunService, deliveries: DeliveryStorePort) -> None:
        self._runner = runner
        self._deliveries = deliveries

    def deliver(self, delivery_id: str, body_digest: str, request: RunRequest) -> RunStatus:
        """Return the stored result for a retry, or execute and store one new delivery."""
        existing = self._deliveries.get(delivery_id)
        if existing is not None:
            if existing.body_digest != body_digest:
                raise DeliveryConflict
            return existing.status
        status = self._runner.run(request)
        self._deliveries.put(delivery_id, DeliveryRecord(body_digest, status))
        return status
