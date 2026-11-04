"""Ports: the only way application code reaches the outside world.

Adapters (OpenHands executor, SQLite run store, GitHub tracker, secret broker)
implement these in hyper_agents.adapters. Declared now so the dependency
direction is fixed before any adapter exists; implemented from P03 onward.
"""

from typing import Any, Protocol, runtime_checkable

from hyper_agents.domain.errors import ErrorCategory
from hyper_agents.domain.events import EventEnvelope
from hyper_agents.domain.runs import Budget, Checkpoint, RunLease


class RunStorePort(Protocol):
    def append(self, event: EventEnvelope) -> bool:
        """Append one event; False when its idempotency key was already stored."""
        ...

    def events(self, run_id: str) -> list[EventEnvelope]: ...


class DurableRunStorePort(Protocol):
    """The leased, fenced, budgeted store the controller drives (P04-2's SQLite adapter)."""

    def append(self, event: EventEnvelope, lease: RunLease | None = None) -> bool: ...
    def events(self, run_id: str, after: int = -1) -> list[EventEnvelope]: ...
    def claim(self, run_id: str, holder: str, ttl: float) -> RunLease | None: ...
    def heartbeat(self, lease: RunLease, ttl: float) -> RunLease: ...
    def release(self, lease: RunLease) -> None: ...
    def set_budget(self, run_id: str, *, tokens: int, attempts: int) -> None: ...
    def budget(self, run_id: str) -> Budget: ...
    def charge(self, lease: RunLease, tokens: int) -> int: ...
    def record_failure(self, lease: RunLease, category: ErrorCategory) -> float | None: ...
    def checkpoint(self, lease: RunLease, sequence: int, position: dict[str, Any]) -> None: ...
    def load_checkpoint(self, run_id: str) -> Checkpoint | None: ...


class HttpGetPort(Protocol):
    """Read-only HTTP: the only verb a tracker may use."""

    def get_json(self, url: str, headers: dict[str, str]) -> object: ...


class ClockPort(Protocol):
    def now_iso(self) -> str: ...


class ManifestParserPort(Protocol):
    def parse(self, text: str) -> object:
        """Parse one manifest document. Raise PolicyDenied on anything unsafe."""
        ...


@runtime_checkable
class ExecutorPort(Protocol):
    def run(self, request: object) -> object:
        """Run a task and return a result. The request and result are adapter-specific."""
        ...
