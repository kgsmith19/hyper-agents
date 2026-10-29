"""Executable acceptance contracts.

A development task is admitted only with at least one acceptance criterion, and
every criterion names the deterministic check that proves it. "Looks done" is
not a criterion: a criterion without a check is rejected at admission.
"""

import re
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from hyper_agents.domain.errors import InvalidInput

_ID = re.compile(r"^AC[0-9]{1,3}$")


class Criterion(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    id: Annotated[str, Field(min_length=3, max_length=5)]
    claim: Annotated[str, Field(min_length=1, max_length=500)]
    # The trusted command the Verifier runs; never text the Builder wrote.
    check: Annotated[str, Field(min_length=1, max_length=500)]

    @field_validator("id")
    @classmethod
    def _id_shape(cls, value: str) -> str:
        if not _ID.fullmatch(value):
            raise ValueError(f"criterion id must look like AC1, not {value!r}")
        return value

    @field_validator("claim", "check")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("must not be blank")
        return value


class AcceptanceContract(BaseModel):
    # Not strict at this level: a JSON array must become the tuple. Each
    # Criterion stays strict, so no field is coerced from another type.
    model_config = ConfigDict(frozen=True, extra="forbid")

    criteria: tuple[Criterion, ...]

    @model_validator(mode="after")
    def _non_empty_and_unique(self) -> "AcceptanceContract":
        if not self.criteria:
            raise ValueError("an acceptance contract needs at least one criterion")
        ids = [criterion.id for criterion in self.criteria]
        if len(set(ids)) != len(ids):
            raise ValueError("criterion ids must be unique")
        return self


def admit(raw: object) -> AcceptanceContract:
    """Validate an untrusted acceptance payload, or raise InvalidInput."""
    try:
        return AcceptanceContract.model_validate(raw)
    except ValueError as error:  # pydantic.ValidationError subclasses ValueError
        raise InvalidInput(f"acceptance contract rejected: {error}") from None
