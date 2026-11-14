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
import re
import urllib.error
import urllib.parse
from collections.abc import Callable, Mapping
from dataclasses import replace
from datetime import datetime, timedelta

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

# Path segments that are ids (uuids, hex digests, numbers) are replaced before display.
_ID_SEGMENT = re.compile(r"^(?:[0-9a-fA-F-]{16,}|\d+)$")


def describe_failure(error: Exception) -> str:
    """A check failure as shown on the tracker: the error type, and for an HTTP error its status
    and path template, so a wrong endpoint diagnoses itself. Never a message, query or id: those
    could carry provider detail."""
    if isinstance(error, urllib.error.HTTPError):
        path = urllib.parse.urlsplit(error.url or "").path
        template = "/".join("{id}" if _ID_SEGMENT.match(part) else part for part in path.split("/"))
        return f"HTTPError {error.code} at {template or '?'}"
    return type(error).__name__


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
            # Only the type (and HTTP status + path template) reaches the Item, never a message.
            statuses[action.id] = Status(False, f"check failed: {describe_failure(error)}")
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
    if apply and workflow and any(a.resolver for a in open_actions):
        # The attempt notes live on the tracker Item, so it must exist first.
        item = forge.ensure_item(
            repo, MARKER, TITLE, render(actions, statuses, now), open=bool(open_actions)
        )
        statuses, started = request_resolutions(
            forge, repo, item, actions, statuses, workflow=workflow, ref=ref, now=now, apply=True
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
    # Remind only about steps that need the owner now, not ones being fixed automatically.
    needs_owner = [a for a in open_actions if not statuses[a.id].resolving]
    if needs_owner:
        marker = reminder_marker(now)
        if not forge.note_exists(repo, number, marker):
            forge.upsert_note(repo, number, marker, reminder_text(needs_owner))
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


# An attempt counts as failed only once it has had this long to work; until then it is
# "running", so the 30-minute maintenance cadence never spends the budget by itself.
SETTLE = timedelta(minutes=90)
_STARTED = "started_at="


def resolution_marker(action_id: str, params: Mapping[str, str], week: str, n: int) -> str:
    """One marker per attempt at one fix, keyed by the fix's params (sorted) and the ISO
    week. Free-text evidence is not part of the key, so changing counts never reset the
    budget; the weekly key bounds attempts to ``max_attempts`` a week per fix."""
    key = json.dumps({"action": action_id, "params": dict(params)}, sort_keys=True)
    digest = hashlib.sha256(key.encode()).hexdigest()[:12]
    return f"<!-- hyper-agents:owner-actions:resolve:{action_id}:{digest}:{week}:{n} -->"


def _started_at(body: str | None) -> datetime | None:
    if not body or _STARTED not in body:
        return None
    try:
        return datetime.fromisoformat(body.split(_STARTED, 1)[1].split()[0].rstrip(".)"))
    except ValueError:
        return None


def request_resolutions(
    forge: ForgePort,
    repo: str,
    item: int,
    actions: tuple[OwnerAction, ...],
    statuses: Mapping[str, Status],
    *,
    workflow: str,
    ref: str,
    now: datetime,
    apply: bool,
    settle: timedelta = SETTLE,
) -> tuple[dict[str, Status], list[str]]:
    """Dispatch each pending action's resolver op; return the statuses annotated with what
    happened, and the dispatches started as ``action:params``. Never raises for one fix."""
    updated = dict(statuses)
    started: list[str] = []
    year, week_no, _ = now.isocalendar()
    week = f"{year}-W{week_no:02d}"
    for action in actions:
        status = statuses.get(action.id)
        resolver = action.resolver
        if resolver is None or status is None or status.done:
            continue
        if status.detail.startswith(CHECK_FAILED):
            continue  # a broken check proves nothing; never act on it
        if status.needs_owner:
            continue  # the fix cannot succeed until the owner acts; spend no attempt
        running: list[int] = []
        exhausted = 0
        errors: list[str] = []
        for fix in status.fixes or ((),):
            params = dict(sorted(fix))
            used = 0
            while used < resolver.max_attempts and forge.note_exists(
                repo, item, resolution_marker(action.id, params, week, used + 1)
            ):
                used += 1
            if used:
                last = _started_at(
                    forge.note_body(repo, item, resolution_marker(action.id, params, week, used))
                )
                if last is not None and now - last < settle:
                    running.append(used)
                    continue
            if used >= resolver.max_attempts:
                exhausted += 1
                continue
            attempt = used + 1
            try:
                if apply:
                    # Marker first: a failed dispatch still spends the attempt, so a broken
                    # dispatch path can never loop.
                    forge.upsert_note(
                        repo,
                        item,
                        resolution_marker(action.id, params, week, attempt),
                        f"Automated fix for **{action.title}**: started `{resolver.op}` "
                        f"(attempt {attempt} of {resolver.max_attempts}, "
                        f"{_STARTED}{now.isoformat()}).",
                    )
                    forge.dispatch(
                        repo,
                        workflow,
                        ref,
                        {"op": resolver.op, "params": json.dumps(params), "mode": "apply"},
                    )
                running.append(attempt)
                started.append(f"{action.id}:{json.dumps(params, sort_keys=True)}")
            except Exception as error:  # one fix's failure never stops the checklist
                errors.append(type(error).__name__)
        if exhausted or errors:
            reasons: list[str] = []
            if exhausted:
                reasons.append(
                    f"the automated fix failed {resolver.max_attempts} times for "
                    f"{exhausted} of {len(status.fixes or ((),))} target(s)"
                )
            if errors:
                reasons.append(f"an automated fix could not start ({', '.join(errors)})")
            updated[action.id] = replace(
                status, detail=f"{status.detail}. {'; '.join(reasons)}, so the steps below need you"
            )
        elif running:
            updated[action.id] = replace(
                status,
                detail=f"{status.detail}. Automated fix running "
                f"(attempt {max(running)} of {resolver.max_attempts})",
                resolving=True,
            )
    return updated, started
