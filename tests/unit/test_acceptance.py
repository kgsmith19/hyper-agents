import pytest

from hyper_agents.domain.acceptance import admit
from hyper_agents.domain.errors import ErrorCategory, InvalidInput

GOOD = {"criteria": [{"id": "AC1", "claim": "tests pass", "check": "uv run pytest"}]}


def test_a_contract_with_a_checked_criterion_is_admitted() -> None:
    contract = admit(GOOD)
    assert [c.id for c in contract.criteria] == ["AC1"]


@pytest.mark.parametrize(
    "raw",
    [
        {"criteria": []},  # empty acceptance list
        {},  # missing criteria
        {"criteria": [{"id": "AC1", "claim": "done"}]},  # no deterministic check
        {"criteria": [{"id": "AC1", "claim": "  ", "check": "x"}]},  # blank claim
        {"criteria": [{"id": "ac1", "claim": "c", "check": "x"}]},  # bad id
        {"criteria": [GOOD["criteria"][0], GOOD["criteria"][0]]},  # duplicate id
        {"criteria": GOOD["criteria"], "extra": 1},  # unknown field
        "not an object",
    ],
)
def test_malformed_contracts_are_rejected_as_invalid_input(raw: object) -> None:
    with pytest.raises(InvalidInput) as raised:
        admit(raw)
    assert raised.value.category is ErrorCategory.INVALID_INPUT
    assert not raised.value.retryable


def test_contracts_are_immutable() -> None:
    contract = admit(GOOD)
    with pytest.raises(ValueError):
        contract.criteria[0].check = "true"  # type: ignore[misc]
