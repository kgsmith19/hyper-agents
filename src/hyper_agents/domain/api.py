"""The shared API contracts every entrypoint speaks (P06, slice 1).

One application path (CLI, API, service, ExecutorPort) is reached through five
wire shapes: RunRequest in, RunStatus and Event out, Evidence as proof, and
CancelRequest to stop a run. Every model forbids unknown keys, so a client
cannot carry a policy field past the contract. The committed JSON Schema is
generated from these models; a unit test fails when the two drift.
"""

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic.json_schema import models_json_schema

from hyper_agents.domain.events import EventEnvelope

_SHA = r"^[0-9a-f]{40}$"
_HEX256 = r"^[0-9a-f]{64}$"
_RUN_ID = r"^[A-Za-z0-9._-]{1,64}$"


class RunRequest(BaseModel):
    """A request to admit one run. Mirrors Controller.admit's arguments, by name."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    run_id: Annotated[str, Field(pattern=_RUN_ID)]
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]
    contract: dict[str, Any]
    risk: Literal["R0", "R1", "R2", "R3"]
    paths: Annotated[list[Annotated[str, Field(max_length=512)]], Field(max_length=500)] = Field(
        default_factory=list
    )
    head_sha: Annotated[str, Field(pattern=_SHA)]
    tokens: Annotated[int, Field(gt=0, le=10_000_000)]
    attempts: Annotated[int, Field(gt=0, le=20)]
    schema_change: bool = False
    auth_change: bool = False


class RunStatus(BaseModel):
    """What one step did. Its statuses are exactly the controller's StepResult statuses."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    run_id: Annotated[str, Field(pattern=_RUN_ID)]
    status: Literal["advanced", "busy", "waiting_for_owner", "finished", "retrying", "failed"]
    state: str | None
    detail: Annotated[str, Field(max_length=300)] = ""


class CriterionResult(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    criterion_id: Annotated[str, Field(min_length=1, max_length=64)]
    sha: Annotated[str, Field(pattern=_SHA)]
    passed: bool
    output_digest: Annotated[str, Field(min_length=1, max_length=128)]


class Evidence(BaseModel):
    """A verification receipt, as VerificationService produces it: bound to one SHA."""

    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    kind: Literal["verification"]
    sha: Annotated[str, Field(pattern=_SHA)]
    criteria: list[Annotated[str, Field(min_length=1, max_length=64)]]
    results: list[CriterionResult]
    passed: bool
    digest: Annotated[str, Field(pattern=_HEX256)]


class CancelRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)

    run_id: Annotated[str, Field(pattern=_RUN_ID)]
    requested_by: Literal["owner", "operator"]
    reason: Annotated[str, Field(min_length=1, max_length=500)]


CONTRACTS: tuple[type[BaseModel], ...] = (
    RunRequest,
    RunStatus,
    EventEnvelope,
    Evidence,
    CancelRequest,
)


def contract_schema() -> dict[str, Any]:
    """The JSON Schema for every contract, as one document with shared $defs."""
    _, top = models_json_schema(
        [(model, "validation") for model in CONTRACTS],
        ref_template="#/$defs/{model}",
    )
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": "hyper-agents API contracts",
        "$defs": top["$defs"],
    }


def main() -> None:
    """Print the contract schema. The committed snapshot is regenerated from this."""
    import json

    print(json.dumps(contract_schema(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
