"""A verification receipt is bound to one exact SHA and cannot be edited (P05).

The receipt's digest covers the SHA, the criteria and every result. Changing
any of them, or presenting the receipt for another SHA, is refused, so a stale
SHA can never be reported green.
"""

import dataclasses

import pytest

from hyper_agents.application.verify import (
    CheckResult,
    VerificationService,
    verify_receipt,
)
from hyper_agents.domain.errors import ErrorCategory, HyperAgentsError, InvalidInput, StaleState

SHA = "a" * 40
OTHER = "b" * 40
CRITERIA = ("AC1", "AC2")


def results(sha: str = SHA, *, ac2_passed: bool = True) -> list[CheckResult]:
    return [
        CheckResult("AC1", sha, passed=True, output_digest="d1"),
        CheckResult("AC2", sha, passed=ac2_passed, output_digest="d2"),
    ]


def test_all_criteria_passing_at_the_expected_sha_give_a_green_receipt() -> None:
    receipt = VerificationService().verify(SHA, CRITERIA, results(), expected_sha=SHA)
    assert receipt.passed is True
    assert verify_receipt(receipt) is True


def test_a_failing_criterion_gives_a_red_receipt_that_still_verifies() -> None:
    receipt = VerificationService().verify(
        SHA, CRITERIA, results(ac2_passed=False), expected_sha=SHA
    )
    assert receipt.passed is False
    assert verify_receipt(receipt) is True


def test_a_result_for_a_moved_head_is_stale_and_never_green() -> None:
    with pytest.raises(StaleState):
        VerificationService().verify(SHA, CRITERIA, results(), expected_sha=OTHER)


def test_results_gathered_at_another_sha_are_stale() -> None:
    with pytest.raises(StaleState):
        VerificationService().verify(SHA, CRITERIA, results(OTHER), expected_sha=SHA)


def test_a_missing_criterion_result_is_refused() -> None:
    with pytest.raises(InvalidInput, match="AC2"):
        VerificationService().verify(SHA, CRITERIA, results()[:1], expected_sha=SHA)


def test_a_receipt_edited_to_pass_is_detected() -> None:
    # Negative control: the honest failing receipt is not forged green.
    receipt = VerificationService().verify(
        SHA, CRITERIA, results(ac2_passed=False), expected_sha=SHA
    )
    forged = dataclasses.replace(receipt, passed=True)
    assert verify_receipt(forged) is False


def test_a_receipt_moved_to_another_sha_is_detected() -> None:
    receipt = VerificationService().verify(SHA, CRITERIA, results(), expected_sha=SHA)
    assert verify_receipt(dataclasses.replace(receipt, sha=OTHER)) is False


def test_a_stale_refusal_is_retryable_only_as_stale_state() -> None:
    with pytest.raises(HyperAgentsError) as raised:
        VerificationService().verify(SHA, CRITERIA, results(), expected_sha=OTHER)
    assert raised.value.category is ErrorCategory.STALE_STATE
