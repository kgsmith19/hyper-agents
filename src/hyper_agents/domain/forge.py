"""Provider-neutral repository hosting nouns: changes (pull/merge requests) and items (issues).

Pure data and pure rules. Nothing here names a hosting provider; adapters translate their
provider's API into these shapes. A capability a provider lacks raises ``NotSupported``.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime

_CLOSING = re.compile(
    r"(?im)^\s*(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s+"
    r"(?:([A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]+))?#([1-9][0-9]*)\b"
)


class NotSupported(RuntimeError):
    """The hosting provider has no equivalent for this operation."""


@dataclass(frozen=True)
class Change:
    """A proposed change (a pull or merge request)."""

    number: int
    body: str
    head_ref: str
    merged_at: datetime | None = None
    conflicted: bool = False
    behind: bool = False
    labels: tuple[str, ...] = field(default_factory=tuple)
    base_ref: str = ""


@dataclass(frozen=True)
class Item:
    """A tracked work item (an issue)."""

    number: int
    open: bool


@dataclass(frozen=True)
class Access:
    """What the forge credential can do in one repository, probed without changing anything."""

    settings_readable: bool
    items_writable: bool
    credential_expires_at: datetime | None = None
    # Whether the credential may create and delete refs (branch prune). ``None``: not probed.
    # Kept out of ``ok`` so a token without it still counts as a working ops token.
    refs_writable: bool | None = None

    @property
    def ok(self) -> bool:
        return self.settings_readable and self.items_writable

    def missing(self) -> list[str]:
        return [
            name
            for name, held in (
                ("administration (read settings)", self.settings_readable),
                ("issues (write)", self.items_writable),
            )
            if not held
        ]


# Branches a prune must never touch, whatever a ledger says.
PROTECTED_BRANCHES = frozenset({"main", "master"})
_SHA = re.compile(r"^[0-9a-f]{40}$")
_BRANCH = re.compile(r"^(?!/)(?!.*//)(?!.*\.\.)[A-Za-z0-9._/-]{1,200}(?<!/)$")


@dataclass(frozen=True)
class LedgerBranch:
    """A reconciled branch: delete it only while its head is still ``sha`` (the lease).

    ``archive`` keeps the work reachable: tag ``archive/<branch>`` at ``sha`` before deleting.
    """

    branch: str
    sha: str
    archive: bool = False

    def __post_init__(self) -> None:
        if not _BRANCH.fullmatch(self.branch) or self.branch in PROTECTED_BRANCHES:
            raise ValueError(f"not a prunable branch name: {self.branch!r}")
        if not _SHA.fullmatch(self.sha):
            raise ValueError(f"{self.branch}: the lease must be a full 40-hex SHA")


def closing_items(body: str, repo: str) -> list[int]:
    """Items in ``repo`` that ``body`` closes with a ``Closes #N`` line, once each, in order.

    A reference to another repository is ignored: one repository's token never acts on another.
    """
    found: list[int] = []
    for other, number in _CLOSING.findall(body or ""):
        if other and other.lower() != repo.lower():
            continue
        value = int(number)
        if value not in found:
            found.append(value)
    return found


def settings_drift(live: dict[str, object], desired: dict[str, object]) -> dict[str, object]:
    """The desired fields whose readable live value differs.

    A field the credential cannot read comes back as ``None``; that is not drift, it is
    unreadable (see ``unreadable_settings``), so it is never reported or written as drift.
    """
    return {
        key: value
        for key, value in desired.items()
        if live.get(key) is not None and live.get(key) != value
    }


def unreadable_settings(live: dict[str, object], desired: dict[str, object]) -> list[str]:
    """Desired fields the credential could not read (usually: it lacks admin access)."""
    return sorted(key for key in desired if live.get(key) is None)
