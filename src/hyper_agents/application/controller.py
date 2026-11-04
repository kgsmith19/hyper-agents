"""The run controller: code, never a model, owns every transition (P04-3).

``admit`` validates the acceptance contract, staffs the run with the
TeamSelector and records ``run.admitted`` (sequence 0) with the team and the
exact head SHA. A re-delivered admission is a no-op that returns the original.

``step`` moves a run by at most one transition:

1. claim the run's lease (a busy or backing-off run is left alone);
2. rebuild the position from the last checkpoint plus every later event, so a
   crash between append and checkpoint loses nothing;
3. dispatch the current slot's role with the idempotency key ``run:sequence``,
   which a re-dispatch after a crash reuses, so the store keeps one event;
4. check the result, charge its tokens, apply its event through the workflow,
   append, checkpoint, release.

Stop conditions are explicit. A malformed result is ``worker_failed`` and is
retried with backoff until the attempt budget runs out. An event the workflow
does not allow (``policy_denied``), a result for another head SHA
(``stale_state``) and an exhausted token or visit budget
(``budget_exhausted``) fail the run, through the workflow's own ``run.fail``.

When the approval slot is the owner's, ``step`` dispatches nothing and the
run waits for ``approve(..., by="owner")``. Below R3 the owner stand-in role
fills that slot and is dispatched like any other.
"""

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

from hyper_agents.application.ports import DurableRunStorePort
from hyper_agents.application.team import OWNER, Risk, TeamSelector
from hyper_agents.domain.acceptance import admit as admit_contract
from hyper_agents.domain.errors import (
    ErrorCategory,
    HyperAgentsError,
    InvalidInput,
    PolicyDenied,
    StaleState,
)
from hyper_agents.domain.events import EventEnvelope, envelope
from hyper_agents.domain.runs import RunLease
from hyper_agents.domain.workflow import RunPosition, Slot, Workflow, advance, start

ADMITTED = "run.admitted"
FAIL = "run.fail"


@dataclass(frozen=True)
class StepRequest:
    run_id: str
    sequence: int
    idempotency_key: str
    state: str
    slot: Slot
    role_id: str
    head_sha: str


@dataclass(frozen=True)
class StepOutcome:
    event: str
    tokens_used: int
    head_sha: str


class StepWorkerPort(Protocol):
    def perform(self, request: StepRequest) -> object:
        """Run one slot; return a StepOutcome. Must be idempotent by idempotency_key."""
        ...


@dataclass(frozen=True)
class StepResult:
    run_id: str
    status: Literal["advanced", "busy", "waiting_for_owner", "finished", "retrying", "failed"]
    state: str | None
    detail: str = ""


class Controller:
    def __init__(
        self,
        store: DurableRunStorePort,
        workflow: Workflow,
        selector: TeamSelector,
        worker: StepWorkerPort,
        *,
        lease_ttl: float = 300.0,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._store = store
        self._workflow = workflow
        self._selector = selector
        self._worker = worker
        self._ttl = lease_ttl
        self._now = now

    # --- admission ------------------------------------------------------

    def admit(
        self,
        run_id: str,
        *,
        idempotency_key: str,
        contract: object,
        risk: Risk,
        paths: Iterable[str],
        head_sha: str,
        tokens: int,
        attempts: int,
        schema_change: bool = False,
        auth_change: bool = False,
    ) -> EventEnvelope:
        accepted = admit_contract(contract)
        team = self._selector.select(
            risk, list(paths), schema_change=schema_change, auth_change=auth_change
        )
        event = envelope(
            run_id=run_id,
            sequence=0,
            type=ADMITTED,
            occurred_at=self._now(),
            idempotency_key=idempotency_key,
            head_sha=head_sha,
            payload={
                "risk": team.risk.name,
                "reasons": list(team.reasons),
                "staff": {slot.value: role for slot, role in team.staff.items()},
                "independent_review": team.independent_review,
                "owner_approval": team.owner_approval,
                "criteria": [c.id for c in accepted.criteria],
            },
        )
        history = self._store.events(run_id)
        if history:
            if history[0].idempotency_key != idempotency_key:
                raise StaleState(f"run {run_id!r} was already admitted under another key")
            return history[0]  # a re-delivered admission changes nothing
        self._store.set_budget(run_id, tokens=tokens, attempts=attempts)
        if not self._store.append(event):
            raise InvalidInput("idempotency key already used by another run")
        return event

    # --- stepping -------------------------------------------------------

    def position(self, run_id: str) -> RunPosition:
        history = self._history(run_id)
        checkpoint = self._store.load_checkpoint(run_id)
        if checkpoint is None:
            position, after = start(self._workflow), 0
        else:
            position = RunPosition.model_validate(checkpoint.position)
            after = checkpoint.sequence
        for event in history:
            if event.sequence > after:
                position = advance(self._workflow, position, event.type)
        return position

    def step(self, run_id: str, holder: str) -> StepResult:
        lease = self._store.claim(run_id, holder, self._ttl)
        if lease is None:
            return StepResult(run_id, "busy", None)
        history = self._history(run_id)
        admitted = history[0]
        position = self.position(run_id)
        state = self._workflow.state(position.state)
        if state.terminal or state.slot is None:
            self._store.release(lease)
            return StepResult(run_id, "finished", state.name)
        role = str(admitted.payload["staff"][state.slot.value])
        if state.slot is Slot.APPROVE and role == OWNER:
            self._store.release(lease)
            return StepResult(run_id, "waiting_for_owner", state.name)
        sequence = history[-1].sequence + 1
        head_sha = admitted.head_sha or ""
        request = StepRequest(
            run_id=run_id,
            sequence=sequence,
            idempotency_key=f"{run_id}:{sequence}",
            state=state.name,
            slot=state.slot,
            role_id=role,
            head_sha=head_sha,
        )
        try:
            outcome = _checked(self._worker.perform(request), state.slot)
            if outcome.head_sha != head_sha:
                raise StaleState(f"worker acted on {outcome.head_sha[:12]}, run is {head_sha[:12]}")
            self._store.charge(lease, outcome.tokens_used)
            moved = advance(self._workflow, position, outcome.event)
        except HyperAgentsError as error:
            return self._stop(lease, position, sequence, error)
        self._record(
            lease,
            sequence,
            outcome.event,
            moved,
            {
                "role": role,
                "slot": state.slot.value,
                "tokens": outcome.tokens_used,
                "head_sha": outcome.head_sha,
            },
        )
        self._store.release(lease)
        status = "finished" if self._workflow.state(moved.state).terminal else "advanced"
        return StepResult(run_id, status, moved.state)

    def approve(self, run_id: str, *, granted: bool, by: str) -> StepResult:
        """The owner's own decision on an R3 run. Nobody else moves this slot."""
        if by != OWNER:
            raise PolicyDenied("only the owner approves this run")
        lease = self._store.claim(run_id, OWNER, self._ttl)
        if lease is None:
            return StepResult(run_id, "busy", None)
        history = self._history(run_id)
        position = self.position(run_id)
        state = self._workflow.state(position.state)
        staff = history[0].payload["staff"]
        if state.slot is not Slot.APPROVE or staff.get(Slot.APPROVE.value) != OWNER:
            self._store.release(lease)
            raise PolicyDenied(f"run {run_id!r} is not waiting for the owner")
        event = "approval.granted" if granted else "approval.declined"
        moved = advance(self._workflow, position, event)
        self._record(lease, history[-1].sequence + 1, event, moved, {"by": OWNER})
        self._store.release(lease)
        return StepResult(run_id, "advanced", moved.state)

    # --- internals ------------------------------------------------------

    def _history(self, run_id: str) -> list[EventEnvelope]:
        history = self._store.events(run_id)
        if not history or history[0].type != ADMITTED:
            raise InvalidInput(f"unknown run: {run_id!r}")
        return history

    def _record(
        self,
        lease: RunLease,
        sequence: int,
        event_type: str,
        moved: RunPosition,
        payload: dict[str, Any],
    ) -> None:
        event = envelope(
            run_id=lease.run_id,
            sequence=sequence,
            type=event_type,
            occurred_at=self._now(),
            idempotency_key=f"{lease.run_id}:{sequence}",
            payload={**payload, "to": moved.state},
        )
        self._store.append(event, lease=lease)
        self._store.checkpoint(lease, sequence, moved.model_dump())

    def _stop(
        self, lease: RunLease, position: RunPosition, sequence: int, error: HyperAgentsError
    ) -> StepResult:
        budget = self._store.budget(lease.run_id)
        last_attempt = budget.attempts_used + 1 >= budget.attempts_limit
        if error.retryable and not last_attempt:
            retry_at = self._store.record_failure(lease, error.category)
            return StepResult(lease.run_id, "retrying", position.state, f"next at {retry_at}")
        failed = advance(self._workflow, position, FAIL)
        self._record(
            lease,
            sequence,
            FAIL,
            failed,
            {"category": error.category.value, "reason": str(error)[:300]},
        )
        self._store.record_failure(lease, error.category)
        return StepResult(lease.run_id, "failed", failed.state, error.category.value)


def _checked(raw: object, slot: Slot) -> StepOutcome:
    """A worker's result is data: anything but a well-formed StepOutcome is worker_failed."""
    if not isinstance(raw, StepOutcome):
        raise HyperAgentsError("malformed worker result", ErrorCategory.WORKER_FAILED)
    # Dataclass fields are not type-checked at runtime, so a worker can still
    # hand back the right class holding the wrong values.
    fields: dict[str, object] = vars(raw)
    event, tokens, sha = fields["event"], fields["tokens_used"], fields["head_sha"]
    if (
        not isinstance(event, str)
        or not isinstance(tokens, int)
        or isinstance(tokens, bool)
        or tokens < 0
        or not isinstance(sha, str)
    ):
        raise HyperAgentsError("malformed worker result", ErrorCategory.WORKER_FAILED)
    if raw.event.startswith("run."):
        # Cancel and fail are the controller's events, never a worker's.
        raise PolicyDenied(f"a worker may not emit {raw.event!r}")
    if raw.event.startswith("approval.") and slot is not Slot.APPROVE:
        # Only the approval slot approves; a builder cannot approve its own work.
        raise PolicyDenied(f"slot {slot.value!r} may not emit {raw.event!r}")
    return raw


__all__ = [
    "Controller",
    "StepOutcome",
    "StepRequest",
    "StepResult",
    "StepWorkerPort",
]
