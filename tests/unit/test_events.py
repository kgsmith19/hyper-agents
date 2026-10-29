from datetime import UTC, datetime

import pytest

from hyper_agents.domain.errors import InvalidInput
from hyper_agents.domain.events import ENVELOPE_SCHEMA, envelope, read_envelope

AT = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def _event(**overrides: object):
    fields: dict[str, object] = {
        "run_id": "run-1",
        "sequence": 0,
        "type": "run.admitted",
        "occurred_at": AT,
        "idempotency_key": "run-1:0",
    }
    fields.update(overrides)
    return envelope(**fields)  # type: ignore[arg-type]


def test_an_envelope_round_trips_through_json_with_its_version() -> None:
    event = _event(head_sha="a" * 40, payload={"role": "builder"})
    text = event.model_dump_json(by_alias=True)
    assert f'"schema":"{ENVELOPE_SCHEMA}"' in text
    assert read_envelope(text) == event
    assert read_envelope(event.model_dump(by_alias=True, mode="json")) == event


@pytest.mark.parametrize(
    "overrides",
    [
        {"sequence": -1},
        {"type": "Admitted"},
        {"head_sha": "abc"},
        {"occurred_at": datetime(2026, 10, 29, 12, 0)},  # naive time is ambiguous
        {"idempotency_key": ""},
    ],
)
def test_invalid_envelopes_are_refused(overrides: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        _event(**overrides)


def test_an_unknown_envelope_version_is_refused_not_guessed() -> None:
    raw = _event().model_dump(by_alias=True, mode="json")
    raw["schema"] = "hyper-agents.event/v2"
    with pytest.raises(InvalidInput, match="unknown event envelope schema"):
        read_envelope(raw)
