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

Every function returns a plain report. With ``apply=False`` nothing is written.
"""

from datetime import datetime, timedelta

from hyper_agents.application.ports import ForgePort
from hyper_agents.domain.forge import closing_items, settings_drift, unreadable_settings

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
