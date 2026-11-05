"""Provable completion: a verification receipt bound to one exact SHA (P05).

``VerificationService.verify`` turns per-criterion check results into a receipt.
The receipt's digest covers the SHA, the criteria, every result and the verdict,
so an edited receipt, or one moved to another SHA, fails ``verify_receipt``.
Results gathered at any SHA other than the expected head are ``stale_state``
and are never reported green.
"""

import hashlib
import json
from dataclasses import asdict, dataclass

from hyper_agents.domain.errors import InvalidInput, StaleState


@dataclass(frozen=True)
class CheckResult:
    criterion_id: str
    sha: str
    passed: bool
    output_digest: str


@dataclass(frozen=True)
class VerificationReceipt:
    sha: str
    criteria: tuple[str, ...]
    results: tuple[CheckResult, ...]
    passed: bool
    digest: str


def _digest(
    sha: str, criteria: tuple[str, ...], results: tuple[CheckResult, ...], passed: bool
) -> str:
    body = {
        "sha": sha,
        "criteria": list(criteria),
        "results": [asdict(r) for r in results],
        "passed": passed,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def verify_receipt(receipt: VerificationReceipt) -> bool:
    """True only when the receipt's digest matches its own contents."""
    return receipt.digest == _digest(receipt.sha, receipt.criteria, receipt.results, receipt.passed)


class VerificationService:
    def verify(
        self,
        sha: str,
        criteria: tuple[str, ...],
        results: list[CheckResult],
        *,
        expected_sha: str,
    ) -> VerificationReceipt:
        if sha != expected_sha:
            raise StaleState(f"verification is for {sha[:12]}, the head is {expected_sha[:12]}")
        if any(r.sha != sha for r in results):
            raise StaleState("a check result was gathered at another SHA")
        ids = [r.criterion_id for r in results]
        if len(set(ids)) != len(ids):
            raise InvalidInput("duplicate check result for a criterion")
        if missing := sorted(set(criteria) - set(ids)):
            raise InvalidInput(f"no check result for criteria: {missing}")
        if extra := sorted(set(ids) - set(criteria)):
            raise InvalidInput(f"check results for unknown criteria: {extra}")
        ordered = tuple(sorted(results, key=lambda r: r.criterion_id))
        passed = all(r.passed for r in ordered)
        return VerificationReceipt(
            sha=sha,
            criteria=tuple(criteria),
            results=ordered,
            passed=passed,
            digest=_digest(sha, tuple(criteria), ordered, passed),
        )
