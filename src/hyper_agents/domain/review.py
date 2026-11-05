"""Review verdicts: a reviewer's output is data, parsed strictly (P05).

A reviewer returns a verdict and findings and nothing else. Any extra key is a
malformed result, so a prompt cannot smuggle a policy field into the output.
Each finding's ID comes from its claim alone, so the same finding keeps its ID
across runs, severities and spacing, and the ledger can deduplicate it. An
approval that carries a blocking finding is refused as ``policy_denied``: the
reviewer cannot approve its way past a blocking defect.
"""

import hashlib
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from hyper_agents.domain.errors import ErrorCategory, HyperAgentsError, PolicyDenied

Severity = Literal["blocking", "advisory"]
Verdict = Literal["approve", "request_changes"]

_SHA = r"^[0-9a-f]{40}$"


def finding_id(claim: str) -> str:
    """A stable identity: the claim, case-folded and with runs of whitespace collapsed."""
    normalized = " ".join(claim.casefold().split())
    return "F-" + hashlib.sha256(normalized.encode()).hexdigest()[:16]


class Finding(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: Annotated[str, Field(pattern=r"^F-[0-9a-f]{16}$")]
    severity: Severity
    claim: Annotated[str, Field(min_length=1, max_length=500)]


class ReviewVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    verdict: Verdict
    findings: tuple[Finding, ...]
    head_sha: Annotated[str, Field(pattern=_SHA)]


class _RawFinding(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    severity: Severity
    claim: Annotated[str, Field(min_length=1, max_length=500)]


class _RawVerdict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    verdict: Verdict
    findings: list[_RawFinding]


def parse_verdict(raw: object, head_sha: str) -> ReviewVerdict:
    """Parse one reviewer output for one exact head SHA.

    A malformed output is ``worker_failed`` and may be retried. An approval over a
    blocking finding is ``policy_denied`` and fails the run.
    """
    try:
        parsed = _RawVerdict.model_validate(raw)
    except ValidationError:
        raise HyperAgentsError("malformed review verdict", ErrorCategory.WORKER_FAILED) from None
    if parsed.verdict == "approve" and any(f.severity == "blocking" for f in parsed.findings):
        raise PolicyDenied("an approval may not carry a blocking finding")
    return ReviewVerdict(
        verdict=parsed.verdict,
        findings=tuple(
            Finding(id=finding_id(f.claim), severity=f.severity, claim=f.claim)
            for f in parsed.findings
        ),
        head_sha=head_sha,
    )
