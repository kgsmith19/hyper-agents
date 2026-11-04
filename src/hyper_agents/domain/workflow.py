"""The run workflow: a strictly validated state machine (P04-1).

A workflow is data. Its states name the slot that acts in them (who acts is
the TeamSelector's choice, never the workflow's), its transitions are keyed
by a dotted event name, and every non-terminal state carries a bounded
``max_visits`` so a review/build loop cannot run forever. Validation is total
and happens once, at load: an unknown field or state, a duplicate
``(from, event)`` pair, an edge leaving a terminal state, an unreachable state,
or a state that can never reach a terminal one is ``invalid_input``.

``advance`` is the only way a run moves. An event with no edge from the
current state is ``policy_denied`` and changes nothing; re-entering a state
past its ``max_visits`` is ``budget_exhausted``, which is never retried.
"""

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from hyper_agents.domain.errors import (
    ErrorCategory,
    HyperAgentsError,
    InvalidInput,
    PolicyDenied,
)

_NAME = r"^[a-z][a-z_]{1,31}$"
_EVENT = r"^[a-z]+(\.[a-z_]+)+$"


class Slot(StrEnum):
    """Who acts in a state, as a job rather than a role id."""

    PLAN = "plan"
    BUILD = "build"
    TEST = "test"
    REVIEW = "review"
    VERIFY = "verify"
    APPROVE = "approve"


class State(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    name: Annotated[str, Field(pattern=_NAME)]
    # Lax only here: YAML gives a string, and the enum still refuses anything off the list.
    slot: Annotated[Slot | None, Field(strict=False)] = None
    terminal: bool = False
    max_visits: Annotated[int, Field(ge=1, le=10)] | None = None

    @model_validator(mode="after")
    def _bounded(self) -> "State":
        if self.terminal and (self.slot is not None or self.max_visits is not None):
            raise ValueError(f"terminal state {self.name!r} takes no slot and no max_visits")
        if not self.terminal and self.max_visits is None:
            raise ValueError(f"state {self.name!r} needs max_visits")
        return self


class Transition(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    from_: Annotated[str, Field(alias="from", pattern=_NAME)]
    # Not `on`: YAML 1.1 reads a bare `on` key as the boolean true.
    event: Annotated[str, Field(pattern=_EVENT)]
    to: Annotated[str, Field(pattern=_NAME)]


class Workflow(BaseModel):
    # Not strict at this level, so YAML lists become tuples; nested models are.
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Annotated[str, Field(pattern=r"^[a-z][a-z0-9-]{1,31}$")]
    version: Annotated[str, Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+$")]
    initial: Annotated[str, Field(pattern=_NAME)]
    states: Annotated[tuple[State, ...], Field(min_length=2)]
    transitions: Annotated[tuple[Transition, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _well_formed(self) -> "Workflow":
        names = [s.name for s in self.states]
        if len(set(names)) != len(names):
            raise ValueError("duplicate state name")
        by_name = {s.name: s for s in self.states}
        if self.initial not in by_name or by_name[self.initial].terminal:
            raise ValueError(f"initial state {self.initial!r} must be a known non-terminal state")
        seen: set[tuple[str, str]] = set()
        for t in self.transitions:
            for end in (t.from_, t.to):
                if end not in by_name:
                    raise ValueError(f"transition names unknown state {end!r}")
            if by_name[t.from_].terminal:
                raise ValueError(f"terminal state {t.from_!r} has an outgoing transition")
            if (t.from_, t.event) in seen:
                raise ValueError(f"duplicate transition from {t.from_!r} on {t.event!r}")
            seen.add((t.from_, t.event))
        if not any(s.terminal for s in self.states):
            raise ValueError("a workflow needs at least one terminal state")
        reachable = _closure({self.initial}, self.transitions, forward=True)
        if unreachable := sorted(set(names) - reachable):
            raise ValueError(f"unreachable states: {unreachable}")
        terminals = {s.name for s in self.states if s.terminal}
        if stuck := sorted(set(names) - _closure(terminals, self.transitions, forward=False)):
            raise ValueError(f"states that can never finish: {stuck}")
        return self

    def state(self, name: str) -> State:
        for s in self.states:
            if s.name == name:
                return s
        raise InvalidInput(f"unknown state: {name!r}")

    def slots(self) -> frozenset[Slot]:
        return frozenset(s.slot for s in self.states if s.slot is not None)


def _closure(start: set[str], transitions: tuple[Transition, ...], *, forward: bool) -> set[str]:
    found = set(start)
    frontier = list(start)
    while frontier:
        here = frontier.pop()
        for t in transitions:
            src, dst = (t.from_, t.to) if forward else (t.to, t.from_)
            if src == here and dst not in found:
                found.add(dst)
                frontier.append(dst)
    return found


class RunPosition(BaseModel):
    """Where a run is, and how often it has entered each state."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    state: Annotated[str, Field(pattern=_NAME)]
    visits: dict[str, int]


def start(workflow: Workflow) -> RunPosition:
    return RunPosition(state=workflow.initial, visits={workflow.initial: 1})


def advance(workflow: Workflow, position: RunPosition, event: str) -> RunPosition:
    """Apply one event. Never mutates ``position``; refuses what the workflow does not allow."""
    edge = next(
        (t for t in workflow.transitions if t.from_ == position.state and t.event == event), None
    )
    if edge is None:
        raise PolicyDenied(f"no transition from {position.state!r} on {event!r}")
    target = workflow.state(edge.to)
    visits = dict(position.visits)
    visits[target.name] = visits.get(target.name, 0) + 1
    if target.max_visits is not None and visits[target.name] > target.max_visits:
        raise HyperAgentsError(
            f"state {target.name!r} entered more than {target.max_visits} times",
            ErrorCategory.BUDGET_EXHAUSTED,
        )
    return RunPosition(state=target.name, visits=visits)


def parse_workflow(data: object) -> Workflow:
    """Validate parsed workflow data; any defect is ``invalid_input``."""
    try:
        return Workflow.model_validate(data)
    except ValidationError as error:
        first = error.errors()[0]
        raise InvalidInput(
            f"workflow rejected: {error.error_count()} error(s), first: {first['msg']}"
        ) from None
