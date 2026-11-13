"""Keep the owner-actions checklist current through ``ForgePort``. Provider-free.

- ``actions_status``: run every action's live check, rewrite the one tracker item in place,
  keep it open while anything is pending (closed once all are done, reopened on regression),
  and post at most one reminder per week while anything is pending.
- ``request_resolutions``: for every pending action that declares a resolver, dispatch its
  registered op through the broker workflow, at most ``max_attempts`` times per distinct
  piece of evidence. While a fix is in flight the action shows "automated fix running"
  instead of asking the owner; only an exhausted resolver hands the steps to a human.
- ``request_settings_apply``: once the credential can read settings, start one gated
  settings-apply run per drifting repository, at most once per distinct drift. Each run
  runs in the ops-r2 environment without a human reviewer (owner decision 2026-11-13).

A check that raises is reported as "check failed", never as done.
"""

import hashlib
import json
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime

from hyper_agents.application.ports import ForgePort
from hyper_agents.domain.manual_blocker import dedupe
from hyper_agents.domain.owner_actions import (
    CHECK_FAILED,
    MARKER,
    TITLE,
    OwnerAction,
    Status,
    blocker_record,
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
    workflow: str = "",
    ref: str = "",
) -> dict[str, object]:
    statuses = run_checks(actions, checks)
    open_actions = pending(actions, statuses)
    report: dict[str, object] = {
        "pending": [a.id for a in open_actions],
        "done": [a.id for a in actions if a not in open_actions],
    }
    number: int | None = None
    if apply and workflow and any(a.resolver for a in open_actions):
        number = forge.ensure_item(
            repo, MARKER, TITLE, render(actions, statuses, now), open=bool(open_actions)
        )
        statuses, started = request_resolutions(
            forge, repo, number, actions, statuses, workflow=workflow, ref=ref, apply=True
        )
        report["resolutions_started"] = started
    report["details"] = {key: status.detail for key, status in statuses.items()}
    # Report-only: a record that will not build never stops the checklist update below.
    try:
        report["blockers"] = [
            blocker.model_dump(mode="json")
            for blocker in dedupe(
                blocker_record(a, statuses[a.id], now) for a in actions if a.blocker
            )
        ]
    except ValueError as error:  # pydantic's ValidationError is a ValueError
        report["blockers_error"] = type(error).__name__
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


def resolution_marker(action_id: str, params: Mapping[str, str], evidence: str, n: int) -> str:
    """One marker per attempt at one fix for one piece of evidence. New evidence (the
    problem changed) gets a fresh attempt budget; the same evidence never loops."""
    key = json.dumps({"action": action_id, "params": dict(params), "evidence": evidence})
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"<!-- hyper-agents:owner-actions:resolve:{action_id}:{digest}:{n} -->"


def request_resolutions(
    forge: ForgePort,
    repo: str,
    item: int,
    actions: tuple[OwnerAction, ...],
    statuses: Mapping[str, Status],
    *,
    workflow: str,
    ref: str,
    apply: bool,
) -> tuple[dict[str, Status], list[str]]:
    """Dispatch each pending action's resolver op; return the statuses annotated with what
    happened, and the dispatches started as ``action:params``."""
    updated = dict(statuses)
    started: list[str] = []
    for action in actions:
        status = statuses.get(action.id)
        resolver = action.resolver
        if resolver is None or status is None or status.done:
            continue
        if status.detail.startswith(CHECK_FAILED):
            continue  # a broken check proves nothing; never act on it
        attempts: list[int] = []
        exhausted = False
        for fix in status.fixes or ((),):
            params = dict(fix)
            used = 0
            while used < resolver.max_attempts and forge.note_exists(
                repo, item, resolution_marker(action.id, params, status.detail, used + 1)
            ):
                used += 1
            if used >= resolver.max_attempts:
                exhausted = True
                continue
            attempt = used + 1
            attempts.append(attempt)
            started.append(f"{action.id}:{json.dumps(params, sort_keys=True)}")
            if apply:
                forge.dispatch(
                    repo,
                    workflow,
                    ref,
                    {"op": resolver.op, "params": json.dumps(params), "mode": "apply"},
                )
                forge.upsert_note(
                    repo,
                    item,
                    resolution_marker(action.id, params, status.detail, attempt),
                    f"Automated fix for **{action.title}**: started `{resolver.op}` "
                    f"(attempt {attempt} of {resolver.max_attempts}).",
                )
        if attempts:
            updated[action.id] = replace(
                status,
                detail=f"{status.detail}. Automated fix running "
                f"(attempt {max(attempts)} of {resolver.max_attempts})",
                resolving=True,
            )
        elif exhausted:
            updated[action.id] = replace(
                status,
                detail=f"{status.detail}. The automated fix failed "
                f"{resolver.max_attempts} times, so the steps below need you",
            )
    return updated, started
