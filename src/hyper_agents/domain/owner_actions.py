"""Owner actions: the few steps only the owner can take, as one self-updating checklist.

Pure. Each action has a live check; the checklist is rendered from the check results, so
an action ticks itself off when the system can see it is done, and comes back if it
regresses. Nothing here names a provider: steps are owner-facing text from config.
"""

from dataclasses import dataclass
from datetime import datetime

from hyper_agents.domain.manual_blocker import BlockerStatus, ManualBlocker

MARKER = "<!-- hyper-agents:owner-actions:v1 -->"
TITLE = "Owner actions (self-updating checklist)"


@dataclass(frozen=True)
class BlockerSpec:
    """The static half of an action's ManualBlocker record; the check supplies the rest."""

    blocker_id: str
    provider: str
    capability: str
    resource: str
    failure_class: str
    attempts: tuple[str, ...]
    why_human_only: str
    minimum_human_action: str
    required_access: str
    safety: str
    validation: str
    owner: str
    source: str


@dataclass(frozen=True)
class OwnerAction:
    id: str
    title: str
    why: str
    steps: tuple[str, ...]
    check: str
    check_params: tuple[tuple[str, str], ...] = ()
    blocker: BlockerSpec | None = None


@dataclass(frozen=True)
class Status:
    done: bool
    detail: str


def blocker_record(action: OwnerAction, status: "Status", now: datetime) -> ManualBlocker:
    """The action as a ManualBlocker: evidence and status come from its live check."""
    spec = action.blocker
    if spec is None:
        raise ValueError(f"owner action {action.id!r} has no blocker spec")
    return ManualBlocker(
        blocker_id=spec.blocker_id,
        provider=spec.provider,
        capability=spec.capability,
        resource=spec.resource,
        failure_class=spec.failure_class,  # pyright: ignore[reportArgumentType]
        exact_evidence=status.detail,
        attempts=spec.attempts,
        why_human_only=spec.why_human_only,
        minimum_human_action=spec.minimum_human_action,
        exact_steps=action.steps,
        required_access=spec.required_access,
        safety=spec.safety,
        validation=spec.validation,
        resume_token=f"owner.actions_status:{action.id}",
        detector=f"check:{action.check}",
        owner=spec.owner,
        freshness=now,
        status=BlockerStatus.RESOLVED if status.done else BlockerStatus.OPEN,
        source=spec.source,
    )


def reminder_marker(now: datetime) -> str:
    """One reminder per ISO week: the marker names the week, so a repeat run is a no-op."""
    year, week, _ = now.isocalendar()
    return f"<!-- hyper-agents:owner-actions:reminder:{year}-W{week:02d} -->"


def pending(actions: tuple[OwnerAction, ...], statuses: dict[str, Status]) -> list[OwnerAction]:
    """Actions not proven done. A missing status counts as pending, never as done."""
    return [a for a in actions if not statuses.get(a.id, Status(False, "not checked")).done]


def render(actions: tuple[OwnerAction, ...], statuses: dict[str, Status], now: datetime) -> str:
    open_actions = pending(actions, statuses)
    head = (
        f"{MARKER}\n"
        "The only steps that need you. Everything else is automated. This list is rewritten "
        "every maintenance run from live checks: an action ticks itself off once the system "
        "can see it is done. While anything is pending, one reminder is posted per week.\n\n"
        f"**{len(open_actions)} of {len(actions)} pending** "
        f"(checked {now.strftime('%Y-%m-%d %H:%M UTC')}).\n"
    )
    sections: list[str] = [head]
    for index, action in enumerate(actions, start=1):
        status = statuses.get(action.id, Status(False, "not checked"))
        box = "x" if status.done else " "
        lines = [
            f"## {index}. {action.title}",
            "",
            f"- [{box}] **{'Done' if status.done else 'Pending'}:** {status.detail}",
            f"- **Why:** {action.why}",
        ]
        if not status.done:
            lines += ["", "**Steps:**", *[f"{n}. {step}" for n, step in enumerate(action.steps, 1)]]
        sections.append("\n".join(lines) + "\n")
    return "\n".join(sections)


def reminder_text(open_actions: list[OwnerAction]) -> str:
    names = ", ".join(a.title for a in open_actions)
    return f"Weekly reminder: {len(open_actions)} owner step(s) still pending: {names}."
