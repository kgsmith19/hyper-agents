"""A single-process local daemon that drives persisted runs one step per tick (P06-4)."""

from typing import Protocol

from hyper_agents.application.controller import Controller, StepResult


class RunIndexPort(Protocol):
    """The minimal durable read surface needed by the local daemon."""

    def run_ids(self) -> list[str]: ...


class LocalDaemon:
    """Discover persisted runs on every tick; all workflow state stays in SQLite."""

    def __init__(self, controller: Controller, store: RunIndexPort, *, holder: str) -> None:
        if not holder:
            raise ValueError("daemon holder must not be empty")
        self._controller = controller
        self._store = store
        self._holder = holder

    def tick(self) -> dict[str, StepResult]:
        """Advance every durable run at most once, including runs that currently cannot move."""
        return {
            run_id: self._controller.step(run_id, self._holder) for run_id in self._store.run_ids()
        }
