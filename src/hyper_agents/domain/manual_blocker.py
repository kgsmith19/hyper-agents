"""The ManualBlocker contract: one shape for every step only a human can take.

Shared by LifeOS and hyper-agents. A step counts as manual only once automation has tried
(``attempts``), failed with evidence (``exact_evidence``), and can name the human boundary
(``failure_class``, ``why_human_only``). The fingerprint (provider, capability, resource,
failure class) identifies the blocker, so the same blocker is never recorded twice.
Records carry names and evidence text only, never a secret value.
"""

import hashlib
from collections.abc import Iterable
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

MAX_TEXT = 4000
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_TEXT)]
Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]


class FailureClass(StrEnum):
    """Why no agent can take the step. Anything else is automatable and not a blocker."""

    AUTHORIZATION = "authorization"  # creating or widening a credential (e.g. a PAT)
    CONSENT = "consent"  # OAuth or connector consent bound to a person
    APPROVAL_GATE = "approval_gate"  # an intentional human approval
    PHYSICAL_DEVICE = "physical_device"  # a device step in someone's hand
    REGULATED_DECISION = "regulated_decision"  # financial, legal or medical
    PROVIDER_UNAVAILABLE = "provider_unavailable"  # no API exists for the step
    TOOLING_PERMISSION = "tooling_permission"  # the agent's own policy refuses the write


class BlockerStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    WATCH = "watch"


class ManualBlocker(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    blocker_id: Annotated[str, Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:-]{1,63}$")]
    provider: Name
    capability: Name
    resource: Name
    failure_class: FailureClass
    exact_evidence: Text
    attempts: Annotated[tuple[Text, ...], Field(min_length=1)]
    why_human_only: Text
    minimum_human_action: Text
    exact_steps: Annotated[tuple[Text, ...], Field(min_length=1)]
    required_access: Text
    safety: Text
    validation: Text
    resume_token: Name
    detector: Name
    owner: Name
    freshness: AwareDatetime
    status: BlockerStatus
    source: Name

    @property
    def fingerprint(self) -> str:
        return fingerprint(self.provider, self.capability, self.resource, self.failure_class)


def fingerprint(provider: str, capability: str, resource: str, failure_class: str) -> str:
    """Stable identity: case and surrounding space never make two blockers out of one."""
    key = "|".join(
        " ".join(p.split()).lower() for p in (provider, capability, resource, failure_class)
    )
    return hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]


def dedupe(blockers: Iterable[ManualBlocker]) -> tuple[ManualBlocker, ...]:
    """Refuse a second blocker with a known fingerprint or id; never merge silently."""
    by_print: dict[str, str] = {}
    ids: set[str] = set()
    kept: list[ManualBlocker] = []
    for blocker in blockers:
        if blocker.blocker_id in ids:
            raise ValueError(f"duplicate blocker id {blocker.blocker_id!r}")
        if blocker.fingerprint in by_print:
            raise ValueError(
                f"{blocker.blocker_id} duplicates {by_print[blocker.fingerprint]} "
                f"(fingerprint {blocker.fingerprint})"
            )
        ids.add(blocker.blocker_id)
        by_print[blocker.fingerprint] = blocker.blocker_id
        kept.append(blocker)
    return tuple(kept)


def main() -> None:
    """Print the contract schema. The committed snapshot is regenerated from this."""
    import json

    print(json.dumps(ManualBlocker.model_json_schema(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
