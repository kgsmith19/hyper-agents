"""The local daemon advances durable SQLite-backed runs one step at a time (P06-4)."""

import random
from datetime import UTC, datetime
from pathlib import Path

from hyper_agents.adapters.sqlite_run_store import SqliteRunStore
from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.catalog import CatalogLoader
from hyper_agents.application.controller import Controller, StepOutcome, StepRequest
from hyper_agents.application.team import Risk, TeamSelector
from hyper_agents.application.workflows import load_workflow
from hyper_agents.interfaces.daemon import LocalDaemon

ROOT = Path(__file__).resolve().parents[2]
SHA = "a" * 40
CONTRACT = {"criteria": [{"id": "AC1", "claim": "it works", "check": "pytest -q"}]}
HAPPY = {
    "planning": "plan.accepted",
    "building": "build.completed",
    "testing": "tests.passed",
    "reviewing": "review.approved",
    "verifying": "verify.passed",
    "awaiting_approval": "approval.granted",
}


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


class Script:
    def __init__(self) -> None:
        self.seen: list[StepRequest] = []

    def perform(self, request: StepRequest) -> object:
        self.seen.append(request)
        return StepOutcome(HAPPY[request.state], 1, request.head_sha)


def build(path: Path, clock: Clock, worker: Script) -> tuple[Controller, SqliteRunStore]:
    store = SqliteRunStore(path, clock=clock, rng=random.Random(3))  # noqa: S311
    registry = CatalogLoader(ROOT / "agents" / "catalog", SafeYamlParser()).load()
    workflow = load_workflow(ROOT / "workflows" / "default.yaml", SafeYamlParser())
    controller = Controller(
        store,
        workflow,
        TeamSelector(registry, workflow),
        worker,
        now=lambda: datetime(2026, 11, 4, tzinfo=UTC),
    )
    return controller, store


def admit(controller: Controller, run_id: str) -> None:
    controller.admit(
        run_id,
        idempotency_key=f"delivery-{run_id}",
        contract=CONTRACT,
        risk=Risk.R1,
        paths=["src/x.py"],
        head_sha=SHA,
        tokens=100,
        attempts=3,
    )


def test_tick_discovers_durable_runs_and_steps_each_once(tmp_path: Path) -> None:
    # Mutation caught: omitting run IDs or stepping twice changes the event and result counts.
    controller, store = build(tmp_path / "runs.sqlite", Clock(), Script())
    admit(controller, "r2")
    admit(controller, "r1")

    results = LocalDaemon(controller, store, holder="daemon-1").tick()

    assert list(results) == ["r1", "r2"]
    assert {run_id: result.status for run_id, result in results.items()} == {
        "r1": "advanced",
        "r2": "advanced",
    }
    assert {run_id: len(store.events(run_id)) for run_id in results} == {"r1": 2, "r2": 2}


def test_restarted_daemon_continues_the_same_persisted_run_without_duplicate_events(
    tmp_path: Path,
) -> None:
    db = tmp_path / "runs.sqlite"
    first_controller, first_store = build(db, Clock(), Script())
    admit(first_controller, "r1")
    LocalDaemon(first_controller, first_store, holder="daemon-1").tick()
    first_store.close()

    second_controller, second_store = build(db, Clock(), Script())
    results = LocalDaemon(second_controller, second_store, holder="daemon-2").tick()

    assert results["r1"].state == "testing"
    assert [event.sequence for event in second_store.events("r1")] == [0, 1, 2]


def test_busy_run_does_not_stop_the_daemon_from_considering_other_runs(tmp_path: Path) -> None:
    # Negative control: one held lease must not turn the batch into a global busy result.
    controller, store = build(tmp_path / "runs.sqlite", Clock(), Script())
    admit(controller, "busy")
    admit(controller, "ready")
    assert store.claim("busy", "other", 60) is not None

    results = LocalDaemon(controller, store, holder="daemon-1").tick()

    assert results["busy"].status == "busy"
    assert results["ready"].status == "advanced"
