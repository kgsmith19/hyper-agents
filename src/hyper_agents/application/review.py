"""Independent review: a read-only reviewer, a verdict, and a findings ledger (P05).

The reviewer is handed a ``ReadOnlyWorkspace``: a view with no write method.
The OS sandbox is the hard control (the reviewer role has no write grant at
all); this module is the defence in depth. It digests the workspace before and
after the review, so a reviewer that writes through any handle it holds is
refused as ``policy_denied`` and its verdict is discarded.

The ledger keeps one entry per finding ID. A finding seen again, at the same or
another SHA, adds a sighting rather than a duplicate, and severity only ever
rises to the worst seen.
"""

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from hyper_agents.domain.errors import ErrorCategory, HyperAgentsError, PolicyDenied
from hyper_agents.domain.review import ReviewVerdict, Severity, parse_verdict


class ReadOnlyWorkspace:
    """A read-only view of the files a review may see. No write method exists."""

    def __init__(self, files: Mapping[str, str]) -> None:
        self.files = files

    def read(self, path: str) -> str:
        return self.files[path]

    def digest(self) -> str:
        canonical = json.dumps(sorted(self.files.items()), separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()


class ReviewerPort(Protocol):
    def review(self, workspace: ReadOnlyWorkspace, head_sha: str) -> tuple[object, int]:
        """Return the raw verdict and the tokens it used. The verdict is data, parsed strictly."""
        ...


@dataclass
class LedgerEntry:
    id: str
    claim: str
    severity: Severity
    first_sha: str
    last_sha: str
    sightings: int = 1


class FindingsLedger:
    def __init__(self) -> None:
        self._entries: dict[str, LedgerEntry] = {}

    def record(self, verdict: ReviewVerdict) -> list[str]:
        """Record one verdict; return the IDs seen for the first time."""
        new: list[str] = []
        for finding in verdict.findings:
            entry = self._entries.get(finding.id)
            if entry is None:
                self._entries[finding.id] = LedgerEntry(
                    finding.id, finding.claim, finding.severity, verdict.head_sha, verdict.head_sha
                )
                new.append(finding.id)
                continue
            entry.sightings += 1
            entry.last_sha = verdict.head_sha
            if finding.severity == "blocking":
                entry.severity = "blocking"
        return new

    def entries(self) -> tuple[LedgerEntry, ...]:
        return tuple(self._entries.values())

    def open_blocking(self) -> tuple[str, ...]:
        return tuple(e.id for e in self._entries.values() if e.severity == "blocking")


@dataclass(frozen=True)
class ReviewResult:
    verdict: ReviewVerdict
    tokens_used: int
    new_findings: tuple[str, ...]


class ReviewService:
    def __init__(self, ledger: FindingsLedger) -> None:
        self._ledger = ledger

    @property
    def ledger(self) -> FindingsLedger:
        return self._ledger

    def review(
        self, reviewer: ReviewerPort, workspace: ReadOnlyWorkspace, head_sha: str
    ) -> ReviewResult:
        before = workspace.digest()
        raw, reported = reviewer.review(workspace, head_sha)
        if workspace.digest() != before:
            raise PolicyDenied("reviewer mutated the workspace; its verdict is discarded")
        verdict = parse_verdict(raw, head_sha)
        return ReviewResult(verdict, _token_count(reported), tuple(self._ledger.record(verdict)))


def _token_count(reported: object) -> int:
    """A reviewer is untrusted: its token count is checked at runtime, not trusted by type."""
    if not isinstance(reported, int) or isinstance(reported, bool) or reported < 0:
        raise HyperAgentsError("malformed review tokens", ErrorCategory.WORKER_FAILED)
    return reported
