"""Repository maintenance through ``ForgePort``: provider-free, idempotent, dry-run first.

- ``close_sweep``: close the items that recently merged changes name in ``Closes #N`` lines.
  Native closing is unreliable when a change merges through an automation token, so this is
  the backstop. It acts only on open items, never on an item a person reopened after the
  merge, and closing an already-closed item is skipped, so repeats are no-ops.
- ``change_health``: a conflicted change gets a ``needs-update`` label and one explanatory
  note (CI cannot run on a change the host cannot test-merge, and nothing else says so);
  a change that is only behind gets its branch updated.
- ``settings_drift`` / ``settings_apply``: compare live settings with the declared profile,
  and set only the differing fields.
- ``branch_prune``: delete reconciled branches from a ledger, each only while its live head
  still equals the ledger SHA (a lease), never one an open change uses, and tag
  ``archive/<branch>`` first where the ledger says so. Without ref write access it reports
  ``blocked`` and writes nothing.

Every function returns a plain report. With ``apply=False`` nothing is written.
"""

import json
from datetime import datetime, timedelta

from hyper_agents.application.ports import ForgePort
from hyper_agents.domain.forge import (
    PROTECTED_BRANCHES,
    LedgerBranch,
    closing_items,
    settings_drift,
    unreadable_settings,
)

NEEDS_UPDATE = "needs-update"
HEALTH_MARKER = "<!-- hyper-agents:change-health:v1 -->"
HEALTH_NOTE = (
    "This change conflicts with its base, so the host cannot test-merge it and CI cannot run. "
    "Merge the base branch into it (or restart the branch from the base when the conflict comes "
    "only from history that is already merged). The label clears itself once it is mergeable."
)


def close_sweep(
    forge: ForgePort, repo: str, *, now: datetime, window_days: int = 14, apply: bool
) -> dict[str, object]:
    closed: list[str] = []
    skipped: list[str] = []
    failed: list[str] = []
    for change in forge.merged_changes_since(repo, now - timedelta(days=window_days)):
        if change.merged_at is None:
            continue
        for number in closing_items(change.body, repo):
            item = forge.item(repo, number)
            if item is None or not item.open:
                continue
            if forge.reopened_since(repo, number, change.merged_at):
                skipped.append(f"#{number} (reopened after change #{change.number} merged)")
                continue
            if apply:
                try:
                    forge.close_item(repo, number, f"Closed by merged change #{change.number}.")
                except Exception as error:  # one item's failure never stops the others
                    failed.append(f"#{number}: {error}")
                    continue
            closed.append(f"#{number} (change #{change.number})")
    if failed:
        raise RuntimeError(f"closed {closed}; could not close {failed}")
    return {"repo": repo, "closed": closed, "skipped": skipped, "applied": apply}


def change_health(forge: ForgePort, repo: str, *, apply: bool) -> dict[str, object]:
    flagged: list[int] = []
    cleared: list[int] = []
    updated: list[int] = []
    for change in forge.open_changes(repo):
        labelled = NEEDS_UPDATE in change.labels
        if change.conflicted:
            flagged.append(change.number)
            if apply:
                if not labelled:
                    forge.set_label(repo, change.number, NEEDS_UPDATE, True)
                forge.upsert_note(repo, change.number, HEALTH_MARKER, HEALTH_NOTE)
            continue
        if labelled:
            cleared.append(change.number)
            if apply:
                forge.set_label(repo, change.number, NEEDS_UPDATE, False)
        if change.behind:
            updated.append(change.number)
            if apply:
                forge.update_change_branch(repo, change.number)
    return {
        "repo": repo,
        "conflicted": flagged,
        "cleared": cleared,
        "branch_updated": updated,
        "applied": apply,
    }


def drift(forge: ForgePort, repo: str, desired: dict[str, object]) -> dict[str, object]:
    live = forge.settings(repo)
    return {
        "repo": repo,
        "drift": settings_drift(live, desired),
        "unreadable": unreadable_settings(live, desired),
    }


def apply_settings(
    forge: ForgePort, repo: str, desired: dict[str, object], *, apply: bool
) -> dict[str, object]:
    live = forge.settings(repo)
    unreadable = unreadable_settings(live, desired)
    if unreadable:
        raise PermissionError(
            f"cannot read {unreadable} on {repo}: the credential needs repository admin access"
        )
    changes = settings_drift(live, desired)
    if apply and changes:
        forge.set_settings(repo, changes)
    return {"repo": repo, "changed": sorted(changes), "applied": apply and bool(changes)}


PRUNE_RESULTS = (
    "deleted",
    "archived_and_deleted",
    "would_delete",
    "would_archive_and_delete",
    "gone",
    "moved",
    "open_change",
    "protected",
    "blocked",
    "failed",
)
# Results that leave a ledger branch in place at its lease (work still to do).
PRUNE_PENDING = frozenset({"would_delete", "would_archive_and_delete", "blocked", "failed"})


class PruneFailed(RuntimeError):
    """Some branches failed; the message carries the full per-branch report as JSON."""


def branch_prune(
    forge: ForgePort, repo: str, ledger: tuple[LedgerBranch, ...], *, apply: bool
) -> dict[str, object]:
    """Prune the ledger's branches in ``repo``. Idempotent: a second run reports ``gone``.

    The lease is check-then-delete: the host's ref-delete API takes no expected SHA, so a
    push landing between the read and the delete (well under a second) would be lost. Ledger
    branches are reconciled and idle, which keeps that window theoretical; ``moved`` covers
    every push that lands before the read.
    """
    rows: list[dict[str, str]] = []
    blocked: str | None = None
    in_use = forge.open_change_refs(repo) if ledger else set[str]()
    for entry in ledger:
        result = _prune_one(forge, repo, entry, in_use, apply=apply, blocked=blocked)
        if result.startswith("blocked:"):
            blocked = result.removeprefix("blocked:")
            result = "blocked"
        detail = ""
        if result.startswith("failed:"):
            result, detail = "failed", result.removeprefix("failed:")
        row = {"branch": entry.branch, "sha": entry.sha[:12], "result": result}
        if detail:
            row["detail"] = detail
        rows.append(row)
    counts = {name: sum(1 for r in rows if r["result"] == name) for name in PRUNE_RESULTS}
    report: dict[str, object] = {
        "repo": repo,
        "applied": apply,
        "counts": {name: n for name, n in counts.items() if n},
        "branches": rows,
    }
    if blocked:
        report["blocked"] = blocked
    if counts["failed"]:
        # Raise so the run goes red, but keep the whole report (deletions included) in the
        # receipt's error text: names and short SHAs only, never a credential.
        raise PruneFailed(json.dumps(report, sort_keys=True))
    return report


def _prune_one(
    forge: ForgePort,
    repo: str,
    entry: LedgerBranch,
    in_use: set[str],
    *,
    apply: bool,
    blocked: str | None,
) -> str:
    if entry.branch in PROTECTED_BRANCHES:
        return "protected"
    if entry.branch in in_use:
        return "open_change"
    try:
        live = forge.branch_head(repo, entry.branch)
        if live is None:
            return "gone"
        if live != entry.sha:
            return "moved"
        if not apply:
            return "would_archive_and_delete" if entry.archive else "would_delete"
        if blocked:
            return f"blocked:{blocked}"
        if entry.archive:
            tag = f"archive/{entry.branch}"
            if forge.tag_head(repo, tag) is None:
                forge.create_tag(repo, tag, entry.sha)
            held = forge.tag_head(repo, tag)
            if held != entry.sha:
                return f"failed:{tag} is at {(held or 'nothing')[:12]}, not the lease"
        forge.delete_branch(repo, entry.branch)
        if forge.branch_head(repo, entry.branch) is not None:
            return "failed:still present after delete (refused by the host?)"
    except PermissionError as error:
        return f"blocked:{error}"
    except Exception as error:  # one branch's failure never stops the others
        return f"failed:{type(error).__name__}: {error}"
    return "archived_and_deleted" if entry.archive else "deleted"
