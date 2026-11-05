"""The shared API contracts every entrypoint speaks (P06, slice 1).

The five shapes (RunRequest, RunStatus, Event, Evidence, CancelRequest) are the
one application path's wire format. Each is strict: an unknown key is refused,
so a client cannot smuggle a policy field past the contract. Two oracles tie
them to the code that already exists: RunRequest mirrors Controller.admit, and
Evidence accepts a receipt the real VerificationService produced.
"""

import inspect
import json
from pathlib import Path
from typing import get_args

import pytest
from pydantic import ValidationError

from hyper_agents.application.controller import Controller, StepResult
from hyper_agents.application.verify import CheckResult, VerificationService
from hyper_agents.domain.api import (
    CancelRequest,
    Evidence,
    RunRequest,
    RunStatus,
    contract_schema,
)
from hyper_agents.domain.events import EventEnvelope

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schemas" / "api.schema.json"
SHA = "a" * 40
CONTRACT = {"criteria": [{"id": "AC1", "claim": "it works", "check": "pytest -q"}]}


def request(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "run_id": "r1",
        "idempotency_key": "delivery-r1",
        "contract": CONTRACT,
        "risk": "R1",
        "paths": ["src/x.py"],
        "head_sha": SHA,
        "tokens": 1000,
        "attempts": 3,
    }
    base.update(overrides)
    return base


def test_a_well_formed_run_request_is_accepted() -> None:
    assert RunRequest.model_validate(request()).risk == "R1"


def test_a_run_request_with_an_extra_key_is_refused() -> None:
    # Negative control: a client cannot smuggle a policy field past the contract.
    with pytest.raises(ValidationError):
        RunRequest.model_validate(request(policy="relax"))


@pytest.mark.parametrize(
    "bad",
    [
        {"head_sha": "not-a-sha"},
        {"risk": "R9"},
        {"tokens": 0},
        {"attempts": 0},
        {"paths": ["x"] * 501},
    ],
)
def test_a_run_request_outside_its_bounds_is_refused(bad: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        RunRequest.model_validate(request(**bad))


def test_run_request_fields_are_exactly_the_controller_admit_arguments() -> None:
    # Oracle: the wire format carries what admit() takes, under the same names.
    admit = inspect.signature(Controller.admit).parameters
    admit_args = set(admit) - {"self", "run_id"}
    assert set(RunRequest.model_fields) - {"run_id"} == admit_args


def test_run_status_statuses_are_exactly_the_step_result_statuses() -> None:
    status_type = StepResult.__dataclass_fields__["status"].type
    assert set(get_args(status_type)) == set(
        RunStatus.model_fields["status"].annotation.__args__  # type: ignore[attr-defined]
    )


def test_evidence_accepts_a_receipt_the_real_verifier_produced() -> None:
    receipt = VerificationService().verify(
        SHA,
        ("AC1",),
        [CheckResult("AC1", SHA, passed=True, output_digest="d")],
        expected_sha=SHA,
    )
    evidence = Evidence.model_validate(
        {
            "kind": "verification",
            "sha": receipt.sha,
            "criteria": list(receipt.criteria),
            "results": [
                {
                    "criterion_id": r.criterion_id,
                    "sha": r.sha,
                    "passed": r.passed,
                    "output_digest": r.output_digest,
                }
                for r in receipt.results
            ],
            "passed": receipt.passed,
            "digest": receipt.digest,
        }
    )
    assert evidence.passed is True


def test_evidence_digest_must_be_a_sha256_hex() -> None:
    with pytest.raises(ValidationError):
        Evidence.model_validate(
            {
                "kind": "verification",
                "sha": SHA,
                "criteria": [],
                "results": [],
                "passed": True,
                "digest": "xyz",
            }
        )


def test_cancel_request_needs_a_reason_and_a_known_requester() -> None:
    CancelRequest.model_validate({"run_id": "r1", "requested_by": "owner", "reason": "stop"})
    with pytest.raises(ValidationError):
        CancelRequest.model_validate({"run_id": "r1", "requested_by": "owner", "reason": ""})
    with pytest.raises(ValidationError):
        CancelRequest.model_validate({"run_id": "r1", "requested_by": "worker", "reason": "x"})


def test_an_event_is_the_existing_event_envelope() -> None:
    assert "EventEnvelope" in contract_schema()["$defs"]
    assert EventEnvelope.model_fields["schema_"].alias == "schema"


def test_the_committed_schema_matches_the_models() -> None:
    committed = json.loads(SCHEMA.read_text())
    assert committed == contract_schema(), (
        "regenerate with: uv run python -m hyper_agents.domain.api > schemas/api.schema.json"
    )
