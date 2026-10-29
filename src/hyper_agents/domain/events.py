"""The versioned event envelope (docs/design/v1.md, "Event envelope").

Every state change in a run is one immutable envelope. `schema` names the
envelope version; a reader refuses any version it does not know instead of
guessing. Payloads are redacted before they are wrapped: an envelope never
holds a secret value.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hyper_agents.domain.errors import InvalidInput

ENVELOPE_SCHEMA = "hyper-agents.event/v1"

_SHA = r"^[0-9a-f]{40}$"


class EventEnvelope(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    schema_: Annotated[Literal["hyper-agents.event/v1"], Field(alias="schema")]
    run_id: Annotated[str, Field(min_length=1, max_length=64)]
    sequence: Annotated[int, Field(ge=0)]
    type: Annotated[str, Field(pattern=r"^[a-z]+(\.[a-z_]+)+$")]
    occurred_at: AwareDatetime
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]
    head_sha: Annotated[str | None, Field(pattern=_SHA)] = None
    payload: dict[str, Any] = Field(default_factory=dict)


def envelope(
    *,
    run_id: str,
    sequence: int,
    type: str,
    occurred_at: datetime,
    idempotency_key: str,
    head_sha: str | None = None,
    payload: dict[str, Any] | None = None,
) -> EventEnvelope:
    return EventEnvelope.model_validate(
        {
            "schema": ENVELOPE_SCHEMA,
            "run_id": run_id,
            "sequence": sequence,
            "type": type,
            "occurred_at": occurred_at,
            "idempotency_key": idempotency_key,
            "head_sha": head_sha,
            "payload": payload or {},
        }
    )


def read_envelope(raw: object) -> EventEnvelope:
    """Parse a stored or received envelope; an unknown version is refused."""
    if isinstance(raw, dict) and raw.get("schema") != ENVELOPE_SCHEMA:  # pyright: ignore[reportUnknownMemberType]
        raise InvalidInput(f"unknown event envelope schema: {raw.get('schema')!r}")  # pyright: ignore[reportUnknownMemberType]
    try:
        return (
            EventEnvelope.model_validate_json(raw)
            if isinstance(raw, str | bytes)
            else EventEnvelope.model_validate(raw, strict=False)
        )
    except ValueError as error:
        raise InvalidInput(f"event envelope rejected: {error}") from None
