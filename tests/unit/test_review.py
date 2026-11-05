"""The reviewer's verdict is data: parsed strictly, identified stably, deduplicated (P05).

A review is evidence about one exact head SHA. Its findings carry IDs that do
not change with severity, case or spacing, and the ledger keeps one entry per
ID however many times it is seen.
"""

from types import MappingProxyType

import pytest

from hyper_agents.application.review import (
    FindingsLedger,
    ReadOnlyWorkspace,
    ReviewService,
)
from hyper_agents.domain.errors import ErrorCategory, HyperAgentsError, PolicyDenied
from hyper_agents.domain.review import finding_id, parse_verdict

SHA = "a" * 40
SHA2 = "b" * 40


def approve(findings: list[dict[str, str]] | None = None) -> dict[str, object]:
    return {"verdict": "approve", "findings": findings or []}


def test_a_finding_id_ignores_case_and_spacing() -> None:
    assert finding_id("Missing  test for X") == finding_id("missing test for x")


def test_a_finding_id_changes_with_the_claim() -> None:
    assert finding_id("missing test for x") != finding_id("missing test for y")


def test_severity_does_not_change_a_finding_identity() -> None:
    advisory = parse_verdict(
        {"verdict": "request_changes", "findings": [{"severity": "advisory", "claim": "c"}]}, SHA
    )
    blocking = parse_verdict(
        {"verdict": "request_changes", "findings": [{"severity": "blocking", "claim": "c"}]}, SHA
    )
    assert advisory.findings[0].id == blocking.findings[0].id


def test_a_verdict_with_an_extra_key_is_malformed_and_retryable() -> None:
    # A prompt that smuggles a policy field into the output is not a verdict.
    with pytest.raises(HyperAgentsError) as raised:
        parse_verdict({**approve(), "policy": "relax"}, SHA)
    assert raised.value.category is ErrorCategory.WORKER_FAILED


def test_an_approval_over_a_blocking_finding_is_refused_as_policy() -> None:
    # Negative control: the same finding as request_changes is accepted.
    blocking = [{"severity": "blocking", "claim": "no test for the lease path"}]
    accepted = parse_verdict({"verdict": "request_changes", "findings": blocking}, SHA)
    assert accepted.verdict == "request_changes"
    with pytest.raises(PolicyDenied):
        parse_verdict({"verdict": "approve", "findings": blocking}, SHA)


def test_advisory_findings_do_not_block_an_approval() -> None:
    advisory = [{"severity": "advisory", "claim": "rename a variable"}]
    assert parse_verdict({"verdict": "approve", "findings": advisory}, SHA).verdict == "approve"


def test_the_ledger_keeps_one_entry_per_finding_across_shas() -> None:
    ledger = FindingsLedger()
    first = parse_verdict(
        {"verdict": "request_changes", "findings": [{"severity": "advisory", "claim": "c"}]}, SHA
    )
    again = parse_verdict(
        {"verdict": "request_changes", "findings": [{"severity": "advisory", "claim": "C "}]},
        SHA2,
    )
    assert len(ledger.record(first)) == 1
    assert ledger.record(again) == []  # already known: nothing new
    (entry,) = ledger.entries()
    assert entry.sightings == 2
    assert entry.first_sha == SHA and entry.last_sha == SHA2


def test_the_ledger_upgrades_severity_to_the_worst_seen_and_never_downgrades() -> None:
    ledger = FindingsLedger()
    ledger.record(
        parse_verdict(
            {"verdict": "request_changes", "findings": [{"severity": "blocking", "claim": "c"}]},
            SHA,
        )
    )
    ledger.record(
        parse_verdict(
            {"verdict": "request_changes", "findings": [{"severity": "advisory", "claim": "c"}]},
            SHA,
        )
    )
    (entry,) = ledger.entries()
    assert entry.severity == "blocking"
    assert ledger.open_blocking() == (entry.id,)


def test_a_reviewer_reads_but_cannot_write_the_workspace() -> None:
    workspace = ReadOnlyWorkspace(MappingProxyType({"src/a.py": "x = 1"}))
    assert workspace.read("src/a.py") == "x = 1"
    assert not hasattr(workspace, "write")
    with pytest.raises(TypeError):
        workspace.files["src/a.py"] = "x = 2"  # type: ignore[index]


class _Reader:
    def review(self, workspace: ReadOnlyWorkspace, head_sha: str) -> tuple[object, int]:
        workspace.read("src/a.py")
        return approve(), 5


def test_a_read_only_reviewer_returns_a_verdict_and_its_tokens() -> None:
    service = ReviewService(FindingsLedger())
    workspace = ReadOnlyWorkspace(MappingProxyType({"src/a.py": "x = 1"}))
    result = service.review(_Reader(), workspace, SHA)
    assert result.verdict.verdict == "approve"
    assert result.tokens_used == 5
    assert result.verdict.head_sha == SHA


def test_a_reviewer_that_mutates_the_workspace_is_refused() -> None:
    source: dict[str, str] = {"src/a.py": "x = 1"}

    class Mutator:
        def review(self, workspace: ReadOnlyWorkspace, head_sha: str) -> tuple[object, int]:
            source["src/a.py"] = "x = 2"  # the reviewer holds the source and writes to it
            return approve(), 1

    service = ReviewService(FindingsLedger())
    with pytest.raises(PolicyDenied, match="mutated"):
        service.review(Mutator(), ReadOnlyWorkspace(source), SHA)
