"""Choose the team for a run from its risk and impact (P04-1).

The declared risk is a floor, never a ceiling: impact can only raise it.

- A control-plane path (``.github``, ``agents``, ``ops``) or an
  authorization change is R3.
- A schema change is at least R2.
- A source path (``src``) is at least R1.
- Docs and tests alone stay at the declared risk.

Each workflow slot is then staffed from the role catalog. Who staffs a slot
is fixed in code, per risk tier, so a task cannot talk its way into a smaller
team. R2 and above need an independent reviewer (a different provider family
from the builder, where one is available), and R3 needs the owner's own
approval. A role the catalog does not hold fails closed.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from enum import IntEnum

from hyper_agents.application.catalog import RoleRegistry
from hyper_agents.domain.errors import InvalidInput
from hyper_agents.domain.roles import check_relative
from hyper_agents.domain.workflow import Slot, Workflow


class Risk(IntEnum):
    R0 = 0  # read-only or docs/tests: nothing a user runs changes
    R1 = 1  # ordinary source change
    R2 = 2  # data shape or schema change
    R3 = 3  # control plane or authorization


CONTROL_PLANE = (".github", "agents", "ops")
SOURCE = ("src",)

# Who acts in each slot, per tier. "owner" is the human owner, not a role.
_STAFF: dict[Risk, dict[Slot, str]] = {
    Risk.R0: {
        Slot.PLAN: "planner",
        Slot.BUILD: "dev",
        Slot.TEST: "tester",
        Slot.REVIEW: "reviewer",
        Slot.VERIFY: "verifier",
        Slot.APPROVE: "owner-standin",
    },
    Risk.R1: {
        Slot.PLAN: "planner",
        Slot.BUILD: "dev",
        Slot.TEST: "tester",
        Slot.REVIEW: "reviewer",
        Slot.VERIFY: "verifier",
        Slot.APPROVE: "owner-standin",
    },
    Risk.R2: {
        Slot.PLAN: "business-analyst",
        Slot.BUILD: "senior-dev",
        Slot.TEST: "tester",
        Slot.REVIEW: "reviewer",
        Slot.VERIFY: "verifier",
        Slot.APPROVE: "owner-standin",
    },
    Risk.R3: {
        Slot.PLAN: "business-analyst",
        Slot.BUILD: "senior-dev",
        Slot.TEST: "tester",
        Slot.REVIEW: "reviewer",
        Slot.VERIFY: "verifier",
        Slot.APPROVE: "owner",
    },
}
OWNER = "owner"


@dataclass(frozen=True)
class Team:
    risk: Risk
    staff: dict[Slot, str]
    independent_review: bool
    owner_approval: bool
    reasons: tuple[str, ...]


def _under(path: str, roots: Iterable[str]) -> bool:
    return any(path == root or path.startswith(f"{root}/") for root in roots)


def effective_risk(
    declared: Risk, paths: Iterable[str], *, schema_change: bool, auth_change: bool
) -> tuple[Risk, tuple[str, ...]]:
    risk = declared
    reasons = [f"declared {declared.name}"]
    for path in paths:
        check_relative(path)
        if _under(path, CONTROL_PLANE) and risk < Risk.R3:
            risk = Risk.R3
            reasons.append(f"control-plane path {path}")
        elif _under(path, SOURCE) and risk < Risk.R1:
            risk = Risk.R1
            reasons.append(f"source path {path}")
    if schema_change and risk < Risk.R2:
        risk = Risk.R2
        reasons.append("schema change")
    if auth_change and risk < Risk.R3:
        risk = Risk.R3
        reasons.append("authorization change")
    return risk, tuple(reasons)


class TeamSelector:
    def __init__(self, registry: RoleRegistry, workflow: Workflow) -> None:
        self._known = set(registry.list())
        self._workflow = workflow

    def select(
        self,
        risk: Risk,
        paths: Iterable[str],
        *,
        schema_change: bool = False,
        auth_change: bool = False,
    ) -> Team:
        final, reasons = effective_risk(
            risk, list(paths), schema_change=schema_change, auth_change=auth_change
        )
        staff = {
            slot: role for slot, role in _STAFF[final].items() if slot in self._workflow.slots()
        }
        if missing := sorted(s.value for s in self._workflow.slots() - set(staff)):
            raise InvalidInput(f"no staffing rule for slots: {missing}")
        for slot, role in staff.items():
            if role != OWNER and role not in self._known:
                raise InvalidInput(f"slot {slot.value!r} needs unknown role {role!r}")
        return Team(
            risk=final,
            staff=staff,
            independent_review=final >= Risk.R2,
            owner_approval=staff.get(Slot.APPROVE) == OWNER,
            reasons=reasons,
        )
