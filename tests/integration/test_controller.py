"""The controller drives a run end to end, and stops where it must (P04-3).

Real SQLite store, real catalog and workflow; only the worker is scripted, and
time is a controllable clock so leases and backoff are exact.
"""

# Seeded generators make retry jitter reproducible; nothing here is a secret.
# ruff: noqa: S311

import random
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hyper_agents.adapters.sqlite_run_store import SqliteRunStore
from hyper_agents.adapters.yaml_parser import SafeYamlParser
from hyper_agents.application.catalog import CatalogLoader
from hyper_agents.application.controller import Controller, StepOutcome, StepRequest
from hyper_agents.application.team import Risk, TeamSelector
from hyper_agents.application.workflows import load_workflow
from hyper_agents.domain.errors import InvalidInput, PolicyDenied, StaleState
from hyper_agents.domain.workflow import Slot

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


def happy(request: StepRequest) -> object:
    return StepOutcome(HAPPY[request.state], 10, request.head_sha)


class Script:
    """A worker that answers each state from a table, and records what it saw."""

    def __init__(self, answer: Callable[[StepRequest], object] | None = None) -> None:
        self.seen: list[StepRequest] = []
        self.answer: Callable[[StepRequest], object] = answer or happy

    def perform(self, request: StepRequest) -> object:
        self.seen.append(request)
        return self.answer(request)


@pytest.fixture
def clock() -> Clock:
    return Clock()


def build(tmp_path: Path, clock: Clock, worker: Script) -> tuple[Controller, SqliteRunStore]:
    store = SqliteRunStore(tmp_path / "runs.sqlite", clock=clock, rng=random.Random(3))
    registry = CatalogLoader(ROOT / "agents" / "catalog", SafeYamlParser()).load()
    workflow = load_workflow(ROOT / "workflows" / "default.yaml", SafeYamlParser())
    controller = Controller(
        store,
        workflow,
        TeamSelector(registry, workflow),
        worker,
        lease_ttl=30,
        now=lambda: datetime(2026, 11, 4, tzinfo=UTC),
    )
    return controller, store


def admit(controller: Controller, run: str = "r1", risk: Risk = Risk.R1, **kw: object) -> None:
    controller.admit(
        run,
        idempotency_key=f"delivery-{run}",
        contract=CONTRACT,
        risk=risk,
        paths=kw.pop("paths", ["src/x.py"]),  # type: ignore[arg-type]
        head_sha=SHA,
        tokens=int(kw.pop("tokens", 1000)),  # type: ignore[arg-type]
        attempts=int(kw.pop("attempts", 3)),  # type: ignore[arg-type]
    )


def run_to_end(controller: Controller, run: str = "r1", limit: int = 20) -> str | None:
    state: str | None = None
    for _ in range(limit):
        result = controller.step(run, "w1")
        state = result.state
        if result.status != "advanced":
            return result.status
    return state


def test_an_r1_run_goes_end_to_end_with_the_stand_in_approving(
    tmp_path: Path, clock: Clock
) -> None:
    worker = Script()
    controller, store = build(tmp_path, clock, worker)
    admit(controller)
    assert run_to_end(controller) == "finished"
    assert controller.position("r1").state == "done"
    roles = [(r.slot, r.role_id) for r in worker.seen]
    assert roles[-1] == (Slot.APPROVE, "owner-standin")
    assert (Slot.BUILD, "dev") in roles
    assert store.budget("r1").tokens_used == 10 * len(worker.seen)
    types = [e.type for e in store.events("r1")]
    assert types[0] == "run.admitted" and types[-1] == "approval.granted"


def test_a_redelivered_admission_changes_nothing(tmp_path: Path, clock: Clock) -> None:
    controller, store = build(tmp_path, clock, Script())
    admit(controller)
    admit(controller)  # the same webhook, delivered twice
    assert len(store.events("r1")) == 1
    with pytest.raises(StaleState, match="another key"):
        controller.admit(
            "r1",
            idempotency_key="different",
            contract=CONTRACT,
            risk=Risk.R1,
            paths=[],
            head_sha=SHA,
            tokens=10,
            attempts=1,
        )


def test_an_empty_contract_is_never_admitted(tmp_path: Path, clock: Clock) -> None:
    controller, store = build(tmp_path, clock, Script())
    with pytest.raises(InvalidInput):
        controller.admit(
            "r1",
            idempotency_key="k",
            contract={"criteria": []},
            risk=Risk.R0,
            paths=[],
            head_sha=SHA,
            tokens=10,
            attempts=1,
        )
    assert store.events("r1") == []


def test_an_r3_run_waits_for_the_owner_and_nobody_else_can_approve(
    tmp_path: Path, clock: Clock
) -> None:
    worker = Script()
    controller, _ = build(tmp_path, clock, worker)
    admit(controller, paths=[".github/workflows/ci.yml"])
    assert run_to_end(controller) == "waiting_for_owner"
    dispatched = len(worker.seen)
    assert controller.step("r1", "w1").status == "waiting_for_owner"
    assert len(worker.seen) == dispatched  # nothing was dispatched for the owner's slot
    assert all(r.slot is not Slot.APPROVE for r in worker.seen)
    with pytest.raises(PolicyDenied, match="only the owner"):
        controller.approve("r1", granted=True, by="owner-standin")
    assert controller.approve("r1", granted=True, by="owner").state == "done"


def test_the_owner_cannot_approve_a_run_that_is_not_waiting(tmp_path: Path, clock: Clock) -> None:
    controller, _ = build(tmp_path, clock, Script())
    admit(controller, paths=[".github/x.yml"])
    with pytest.raises(PolicyDenied, match="not waiting"):
        controller.approve("r1", granted=True, by="owner")


def test_a_worker_event_the_workflow_forbids_fails_the_run(tmp_path: Path, clock: Clock) -> None:
    controller, store = build(
        tmp_path, clock, Script(lambda r: StepOutcome("verify.passed", 1, SHA))
    )
    admit(controller)
    result = controller.step("r1", "w1")
    assert (result.status, result.state, result.detail) == ("failed", "failed", "policy_denied")
    assert store.events("r1")[-1].payload["category"] == "policy_denied"


@pytest.mark.parametrize("event", ["approval.granted", "run.fail", "run.cancel"])
def test_a_worker_cannot_approve_or_fail_on_purpose(
    tmp_path: Path, clock: Clock, event: str
) -> None:
    controller, _ = build(tmp_path, clock, Script(lambda r: StepOutcome(event, 1, SHA)))
    admit(controller)
    assert controller.step("r1", "w1").detail == "policy_denied"


def test_a_stale_sha_is_never_green(tmp_path: Path, clock: Clock) -> None:
    controller, store = build(
        tmp_path, clock, Script(lambda r: StepOutcome(HAPPY[r.state], 1, "b" * 40))
    )
    admit(controller)
    result = controller.step("r1", "w1")
    assert (result.status, result.detail) == ("failed", "stale_state")
    assert controller.position("r1").state == "failed"
    assert store.budget("r1").tokens_used == 0  # nothing was charged for the stale result


def test_malformed_output_is_retried_with_backoff_then_fails(tmp_path: Path, clock: Clock) -> None:
    worker = Script(lambda r: {"event": "plan.accepted"})  # not a StepOutcome
    controller, store = build(tmp_path, clock, worker)
    admit(controller, attempts=3)
    first = controller.step("r1", "w1")
    assert first.status == "retrying" and first.state == "planning"
    assert controller.step("r1", "w1").status == "busy"  # backing off
    clock.now = store.budget("r1").not_before + 1
    assert controller.step("r1", "w1").status == "retrying"
    clock.now = store.budget("r1").not_before + 1
    last = controller.step("r1", "w1")
    assert (last.status, last.detail) == ("failed", "worker_failed")
    assert len(worker.seen) == 3


def test_wrong_field_types_are_malformed_too(tmp_path: Path, clock: Clock) -> None:
    controller, _ = build(
        tmp_path,
        clock,
        Script(lambda r: StepOutcome("plan.accepted", "many", SHA)),  # type: ignore[arg-type]
    )
    admit(controller)
    assert controller.step("r1", "w1").status == "retrying"


def test_budget_exhaustion_fails_the_run(tmp_path: Path, clock: Clock) -> None:
    controller, store = build(
        tmp_path, clock, Script(lambda r: StepOutcome(HAPPY[r.state], 600, r.head_sha))
    )
    admit(controller, tokens=1000)
    assert controller.step("r1", "w1").status == "advanced"
    result = controller.step("r1", "w1")
    assert (result.status, result.detail) == ("failed", "budget_exhausted")
    assert store.budget("r1").tokens_used == 600


def test_an_endless_review_loop_is_stopped_by_max_visits(tmp_path: Path, clock: Clock) -> None:
    def churn(r: StepRequest) -> object:
        nxt = {"testing": "tests.failed"}.get(r.state, HAPPY[r.state])
        return StepOutcome(nxt, 1, r.head_sha)

    controller, _ = build(tmp_path, clock, Script(churn))
    admit(controller)
    status = run_to_end(controller, limit=50)
    assert status == "failed"
    assert controller.position("r1").state == "failed"


def test_a_crash_after_dispatch_is_recovered_once(tmp_path: Path, clock: Clock) -> None:
    class Crash(BaseException):
        pass

    crashed = {"done": False}

    def flaky(r: StepRequest) -> object:
        if r.state == "building" and not crashed["done"]:
            crashed["done"] = True
            raise Crash  # the controller process dies mid-dispatch
        return StepOutcome(HAPPY[r.state], 5, r.head_sha)

    worker = Script(flaky)
    controller, store = build(tmp_path, clock, worker)
    admit(controller)
    controller.step("r1", "w1")  # planning -> building
    with pytest.raises(Crash):
        controller.step("r1", "w1")
    assert controller.step("r1", "w2").status == "busy"  # the dead lease still holds
    clock.now += 31
    other, _ = build(tmp_path, clock, worker)  # a restarted controller on the same file
    assert other.step("r1", "w2").state == "testing"
    keys = [r.idempotency_key for r in worker.seen if r.state == "building"]
    assert keys == ["r1:2", "r1:2"]  # re-dispatched under the same key
    building = [e for e in store.events("r1") if e.type == "build.completed"]
    assert len(building) == 1


def test_an_unknown_run_is_invalid(tmp_path: Path, clock: Clock) -> None:
    controller, _ = build(tmp_path, clock, Script())
    with pytest.raises(InvalidInput, match="unknown run"):
        controller.step("nope", "w1")
