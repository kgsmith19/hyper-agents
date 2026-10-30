"""Ports: the only way application code reaches the outside world.

Adapters (OpenHands executor, SQLite run store, GitHub tracker, secret broker)
implement these in hyper_agents.adapters. Declared now so the dependency
direction is fixed before any adapter exists; implemented from P03 onward.
"""

from typing import Protocol, runtime_checkable

from hyper_agents.domain.events import EventEnvelope


class RunStorePort(Protocol):
    def append(self, event: EventEnvelope) -> bool:
        """Append one event; False when its idempotency key was already stored."""
        ...

    def events(self, run_id: str) -> list[EventEnvelope]: ...


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
