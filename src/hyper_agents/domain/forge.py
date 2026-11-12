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


@dataclass(frozen=True)
class Item:
    """A tracked work item (an issue)."""

    number: int
    open: bool


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
