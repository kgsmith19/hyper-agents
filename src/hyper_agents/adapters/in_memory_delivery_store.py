"""Local deterministic storage for completed webhook deliveries (P06-3)."""

from hyper_agents.application.webhook_service import DeliveryRecord


class InMemoryDeliveryStore:
    """A process-local delivery store for the framework-neutral webhook adapter."""

    def __init__(self) -> None:
        self._records: dict[str, DeliveryRecord] = {}

    def get(self, delivery_id: str) -> DeliveryRecord | None:
        return self._records.get(delivery_id)

    def put(self, delivery_id: str, record: DeliveryRecord) -> None:
        self._records[delivery_id] = record
