"""Keep the owner-actions checklist current through ``ForgePort``. Provider-free.

- ``actions_status``: run every action's live check, rewrite the one tracker item in place,
  keep it open while anything is pending (closed once all are done, reopened on regression),
  and post at most one reminder per week while anything is pending.
- ``request_settings_apply``: once the credential can read settings, start one gated
  settings-apply run per drifting repository, at most once per distinct drift. Each run
  runs in the ops-r2 environment without a human reviewer (owner decision 2026-11-13).

A check that raises is reported as "check failed", never as done.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from datetime import datetime

from hyper_agents.application.ports import ForgePort
from hyper_agents.domain.owner_actions import (
    MARKER,
    TITLE,
    OwnerAction,
    Status,
    pending,
    reminder_marker,
    reminder_text,
    render,
)

Check = Callable[[OwnerAction], Status]


def run_checks(actions: tuple[OwnerAction, ...], checks: Mapping[str, Check]) -> dict[str, Status]:
    statuses: dict[str, Status] = {}
    for action in actions:
        check = checks.get(action.check)
        if check is None:
            statuses[action.id] = Status(False, f"check failed: unknown check {action.check!r}")
            continue
        try:
            statuses[action.id] = check(action)
        except Exception as error:  # a broken check must never read as done
            # Only the error type reaches the Item: a message could carry provider detail.
            statuses[action.id] = Status(False, f"check failed: {type(error).__name__}")
    return statuses


def actions_status(
    forge: ForgePort,
    repo: str,
    actions: tuple[OwnerAction, ...],
    checks: Mapping[str, Check],
    *,
    now: datetime,
    apply: bool,
) -> dict[str, object]:
    statuses = run_checks(actions, checks)
    open_actions = pending(actions, statuses)
    report: dict[str, object] = {
        "pending": [a.id for a in open_actions],
        "done": [a.id for a in actions if a not in open_actions],
        "details": {key: status.detail for key, status in statuses.items()},
    }
    if not apply:
        return report
    number = forge.ensure_item(
        repo, MARKER, TITLE, render(actions, statuses, now), open=bool(open_actions)
    )
    report["item"] = number
    if open_actions:
        marker = reminder_marker(now)
        if not forge.note_exists(repo, number, marker):
            forge.upsert_note(repo, number, marker, reminder_text(open_actions))
            report["reminded"] = True
    return report


def drift_marker(target: str, drift: Mapping[str, object]) -> str:
    digest = hashlib.sha256(json.dumps(dict(drift), sort_keys=True).encode()).hexdigest()[:12]
    return f"<!-- hyper-agents:owner-actions:settings-apply:{target}:{digest} -->"


def request_settings_apply(
    forge: ForgePort,
    repo: str,
    item: int,
    drifts: Mapping[str, Mapping[str, object]],
    *,
    workflow: str,
    ref: str,
    op_id: str,
    apply: bool,
) -> list[str]:
    """Dispatch one gated apply run per drifting repository, once per distinct drift."""
    requested: list[str] = []
    for target, drift in sorted(drifts.items()):
        if not drift:
            continue
        marker = drift_marker(target, drift)
        if forge.note_exists(repo, item, marker):
            continue
        requested.append(target)
        if apply:
            forge.dispatch(
                repo,
                workflow,
                ref,
                {"op": op_id, "params": json.dumps({"repo": target}), "mode": "apply"},
            )
            forge.upsert_note(
                repo,
                item,
                marker,
                f"Started a settings-apply run for {target} ({', '.join(sorted(drift))}). "
                "It runs without a reviewer.",
            )
    return requested
