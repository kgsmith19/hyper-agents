"""The ManualBlocker contract: strict shape, stable fingerprint, no duplicates, live status."""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from pydantic import ValidationError

from hyper_agents.adapters.fake_forge import FakeForge
from hyper_agents.application.owner_actions import actions_status
from hyper_agents.domain.manual_blocker import FailureClass, ManualBlocker, dedupe, fingerprint
from hyper_agents.domain.owner_actions import BlockerSpec, OwnerAction, Status
from hyper_agents.ops.owner import load_owner_actions

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
SCHEMA = ROOT / "schemas" / "manual-blocker.schema.json"


def record(**overrides: Any) -> ManualBlocker:
    fields: dict[str, Any] = {
        "blocker_id": "MB-900",
        "provider": "github",
        "capability": "branch delete",
        "resource": "kgsmith19/hyper-agents",
        "failure_class": "authorization",
        "exact_evidence": "DELETE git/refs returned 403",
        "attempts": ["ops token lacks Contents write"],
        "why_human_only": "Only the owner edits a PAT.",
        "minimum_human_action": "Set Contents to Read and write.",
        "exact_steps": ["Open the token", "Save"],
        "required_access": "Owner GitHub login",
        "safety": "Lease-checked deletes only.",
        "validation": "Ledger branches are gone.",
        "resume_token": "owner.actions_status:branch-prune",
        "detector": "check:branches_pruned",
        "owner": "kgsmith19",
        "freshness": NOW,
        "status": "open",
        "source": "https://github.com/kgsmith19/hyper-agents/issues/72",
    }
    fields.update(overrides)
    return ManualBlocker.model_validate(fields)


def test_the_exported_schema_matches_the_model() -> None:
    committed = json.loads(SCHEMA.read_text(encoding="utf-8"))
    assert committed == ManualBlocker.model_json_schema(), (
        "regenerate with: uv run python -m hyper_agents.domain.manual_blocker"
        " > schemas/manual-blocker.schema.json"
    )


def test_the_contract_is_strict() -> None:
    record()  # the baseline is valid
    bads: tuple[dict[str, Any], ...] = (
        {"surprise": "x"},  # unknown field
        {"attempts": []},  # manual only after automation tried
        {"exact_steps": []},
        {"failure_class": "it_was_hard"},  # not a named human boundary
        {"freshness": datetime(2026, 11, 13, 9, 0)},  # naive time
        {"blocker_id": "bad id with spaces"},
        {"why_human_only": "   "},
    )
    for bad in bads:
        with pytest.raises(ValidationError):
            record(**bad)


def test_fingerprint_ignores_case_and_spacing_but_not_meaning() -> None:
    assert record().fingerprint == fingerprint(
        " GitHub", "Branch \t Delete ", "kgsmith19/hyper-agents", "AUTHORIZATION"
    )
    assert record().fingerprint != record(resource="kgsmith19/hyperbolic-core").fingerprint
    assert record().fingerprint != record(failure_class="consent").fingerprint


def test_dedupe_refuses_the_same_blocker_twice() -> None:
    with pytest.raises(ValueError, match="duplicates MB-900"):
        dedupe([record(), record(blocker_id="MB-901", provider="GITHUB")])
    with pytest.raises(ValueError, match="duplicate blocker id"):
        dedupe([record(), record(resource="other")])
    assert len(dedupe([record(), record(blocker_id="MB-901", resource="other")])) == 2


SPEC = BlockerSpec(
    blocker_id="MB-900",
    provider="github",
    capability="branch delete",
    resource="fleet",
    failure_class=FailureClass.AUTHORIZATION,
    attempts=("tried the ops token",),
    why_human_only="owner-only",
    minimum_human_action="edit the token",
    required_access="owner",
    safety="lease",
    validation="gone",
    owner="kgsmith19",
    source="#72",
)


def test_the_tracker_emits_contract_records_with_live_status_and_evidence() -> None:
    action = OwnerAction("prune", "Prune", "why", ("step one",), "c", blocker=SPEC)
    plain = OwnerAction("plain", "Plain", "why", ("x",), "c2")
    report = actions_status(
        FakeForge(),
        "kgsmith19/hyper-agents",
        (action, plain),
        {"c": lambda _: Status(False, "3 branches left"), "c2": lambda _: Status(True, "ok")},
        now=NOW,
        apply=False,
    )
    blockers = cast(list[dict[str, Any]], report["blockers"])
    assert len(blockers) == 1
    blocker = blockers[0]
    assert blocker["blocker_id"] == "MB-900" and blocker["status"] == "open"
    assert blocker["exact_evidence"] == "3 branches left"
    assert blocker["exact_steps"] == ["step one"]
    assert blocker["resume_token"] == "owner.actions_status:prune"  # noqa: S105 (not a secret)
    ManualBlocker.model_validate(blocker)  # round-trips through the contract


def test_the_committed_owner_actions_are_valid_distinct_blockers() -> None:
    config = load_owner_actions(ROOT / "ops/owner_actions.yaml")
    ids = {a.blocker.blocker_id for a in config.actions if a.blocker}
    assert {"MB-002", "MB-003", "MB-004", "MB-008"} <= ids
    assert all(a.blocker for a in config.actions), "every owner action carries a contract"


def test_the_loader_refuses_a_duplicate_blocker_and_unknown_keys(tmp_path: Path) -> None:
    raw = (ROOT / "ops/owner_actions.yaml").read_text(encoding="utf-8")
    copy = tmp_path / "owner_actions.yaml"
    copy.write_text(raw.replace("blocker_id: MB-003", "blocker_id: MB-002"), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate blocker id"):
        load_owner_actions(copy)
    copy.write_text(raw.replace("      owner: kgsmith19\n", "      ownr: kgsmith19\n", 1))
    with pytest.raises(TypeError):
        load_owner_actions(copy)
    for bad, why in (
        ("      owner: kgsmith19\n", "      owner:\n"),  # empty value is not the string "None"
        ("      attempts:\n", "      attempts: tried\n      unused:\n"),  # scalar attempts
    ):
        copy.write_text(raw.replace(bad, why, 1))
        with pytest.raises(ValueError):
            load_owner_actions(copy)


def test_a_crashed_check_is_watch_and_a_bad_record_never_stops_the_checklist() -> None:
    action = OwnerAction("prune", "Prune", "why", ("step",), "c", blocker=SPEC)
    forge = FakeForge()
    report = actions_status(
        forge, "kgsmith19/hyper-agents", (action,), {}, now=NOW, apply=True
    )  # no check registered: "check failed: unknown check"
    blockers = cast(list[dict[str, Any]], report["blockers"])
    assert blockers[0]["status"] == "watch"
    twice = OwnerAction("again", "Again", "why", ("step",), "c", blocker=SPEC)
    report = actions_status(
        forge,
        "kgsmith19/hyper-agents",
        (action, twice),
        {"c": lambda _: Status(False, "x")},
        now=NOW,
        apply=True,
    )
    assert report["blockers_error"] == "ValueError" and "item" in report
