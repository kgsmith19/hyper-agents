"""Blocker auto-resolution: a pending action with a resolver dispatches its op, a bounded
number of times per piece of evidence, before it ever asks a human."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from hyper_agents.adapters.fake_forge import FakeForge
from hyper_agents.application.owner_actions import (
    Check,
    actions_status,
    request_resolutions,
)
from hyper_agents.domain.manual_blocker import FailureClass
from hyper_agents.domain.owner_actions import (
    MARKER,
    BlockerSpec,
    OwnerAction,
    Resolver,
    Status,
    render,
)
from hyper_agents.ops.owner import load_owner_actions
from hyper_agents.ops.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 13, 9, 0, tzinfo=UTC)
TRACKER = "kgsmith19/hyper-agents"
SPEC = BlockerSpec(
    blocker_id="MB-950",
    provider="infisical",
    capability="identity role",
    resource="fleet identities",
    failure_class=FailureClass.TOOLING_PERMISSION,
    attempts=("maintain dispatches the resolver",),
    why_human_only="only if the resolver fails",
    minimum_human_action="set the role by hand",
    required_access="Infisical admin",
    safety="never grants admin",
    validation="no identity holds admin",
    owner="kgsmith19",
    source="#76",
)
FIXABLE = OwnerAction(
    "roles", "Identity roles", "why", ("do it by hand",), "c", blocker=SPEC,
    resolver=Resolver(op="infisical.identity.set_role", max_attempts=2),
)  # fmt: skip


def dispatches(forge: FakeForge) -> list[dict[str, str]]:
    return [cast(dict[str, str], w[1]) for w in forge.writes if w[0] == "dispatch"]


def failing(fixes: tuple[tuple[tuple[str, str], ...], ...] = ()) -> Check:
    def check(_: OwnerAction) -> Status:
        return Status(False, "ai-coding-harness is admin", fixes=fixes)

    return check


def fixed(detail: str, done: bool = False) -> Check:
    def check(_: OwnerAction) -> Status:
        return Status(done, detail)

    return check


def run(forge: FakeForge, check: Check, hours: float = 0) -> dict[str, object]:
    return actions_status(
        forge,
        TRACKER,
        (FIXABLE,),
        {"c": check},
        now=NOW + timedelta(hours=hours),
        apply=True,
        workflow="ops.yml",
        ref="main",
    )


def test_a_failing_check_dispatches_its_resolver_and_shows_watch_not_steps() -> None:
    forge = FakeForge()
    fix = ((("identity", "813dda9e"), ("role", "member")),)
    report = run(forge, failing(fix))
    assert dispatches(forge) == [
        {
            "op": "infisical.identity.set_role",
            "params": json.dumps({"identity": "813dda9e", "role": "member"}),
            "mode": "apply",
        }
    ]
    body = forge.tracked[MARKER][2]
    assert "Automated fix running (attempt 1 of 2)" in body
    assert "do it by hand" not in body  # no human steps while the fix is in flight
    blockers = cast(list[dict[str, Any]], report["blockers"])
    assert blockers[0]["status"] == "watch"


def test_an_attempt_settles_before_the_next_and_the_budget_then_escalates() -> None:
    forge = FakeForge()
    run(forge, failing(), hours=0)
    report = run(forge, failing(), hours=0.5)  # attempt 1 still settling: no new dispatch
    assert len(dispatches(forge)) == 1 and report["resolutions_started"] == []
    assert "Automated fix running (attempt 1 of 2)" in forge.tracked[MARKER][2]
    run(forge, failing(), hours=2)  # attempt 1 had its chance: attempt 2
    run(forge, failing(), hours=2.5)  # attempt 2 settling
    report = run(forge, failing(), hours=4)  # budget spent
    assert len(dispatches(forge)) == 2
    body = forge.tracked[MARKER][2]
    assert "failed 2 times for 1 of 1 target(s), so the steps below need you" in body
    assert "do it by hand" in body
    assert cast(list[dict[str, Any]], report["blockers"])[0]["status"] == "open"


def test_changing_evidence_text_never_resets_the_budget_but_a_new_week_does() -> None:
    forge = FakeForge()
    for hours, left in ((0, 3), (2, 2), (4, 1), (6, 0)):
        run(forge, fixed(f"left: {left}"), hours=hours)
    assert len(dispatches(forge)) == 2  # counts in the text do not buy more attempts
    run(forge, fixed("left: 1"), hours=24 * 7)  # next ISO week: a fresh, bounded budget
    assert len(dispatches(forge)) == 3
    run(forge, fixed("all good", done=True), hours=24 * 7 + 2)
    assert len(dispatches(forge)) == 3


def test_param_order_does_not_change_the_fix_identity() -> None:
    forge = FakeForge()
    run(forge, failing(((("b", "2"), ("a", "1")),)), hours=0)
    run(forge, failing(((("a", "1"), ("b", "2")),)), hours=2)
    run(forge, failing(((("b", "2"), ("a", "1")),)), hours=4)
    assert len(dispatches(forge)) == 2


def test_one_exhausted_target_surfaces_the_steps_even_while_another_runs() -> None:
    forge = FakeForge()
    run(forge, failing(((("identity", "a"),),)), hours=0)
    run(forge, failing(((("identity", "a"),),)), hours=2)
    both = ((("identity", "a"),), (("identity", "b"),))
    report = run(forge, failing(both), hours=4)
    assert report["resolutions_started"] == ['roles:{"identity": "b"}']
    body = forge.tracked[MARKER][2]
    assert "1 of 2 target(s)" in body and "do it by hand" in body


def test_a_failed_dispatch_spends_its_attempt_and_never_breaks_the_checklist() -> None:
    class Broken(FakeForge):
        def dispatch(self, repo: str, workflow: str, ref: str, inputs: dict[str, str]) -> None:
            raise PermissionError("actions write denied")

    forge = Broken()
    report = run(forge, failing(), hours=0)
    assert "item" in report  # the Item was still rewritten
    assert "could not start (PermissionError)" in forge.tracked[MARKER][2]
    for hours in (0.5, 2, 4, 6, 8):
        run(forge, failing(), hours=hours)
    marks = [m for m in forge.notes.get(900, []) if ":resolve:" in m]
    assert len(marks) == 2  # bounded: a broken dispatch path never loops


def test_the_weekly_reminder_skips_actions_being_fixed() -> None:
    forge = FakeForge()
    run(forge, failing(), hours=0)
    assert not any("Weekly reminder" in t for t in forge.note_texts.values())


def test_one_dispatch_per_fix_and_a_broken_check_is_never_acted_on() -> None:
    forge = FakeForge()
    fixes = ((("identity", "a"),), (("identity", "b"),))
    statuses, started = request_resolutions(
        forge,
        TRACKER,
        1,
        (FIXABLE,),
        {"roles": Status(False, "two admins", fixes=fixes)},
        workflow="ops.yml",
        ref="main",
        now=NOW,
        apply=True,
    )
    assert len(dispatches(forge)) == 2 and len(started) == 2
    assert statuses["roles"].resolving
    forge = FakeForge()
    request_resolutions(
        forge,
        TRACKER,
        1,
        (FIXABLE,),
        {"roles": Status(False, "check failed: HTTPError")},
        workflow="ops.yml",
        ref="main",
        now=NOW,
        apply=True,
    )
    assert dispatches(forge) == []


def test_without_a_workflow_nothing_is_dispatched() -> None:
    forge = FakeForge()
    actions_status(forge, TRACKER, (FIXABLE,), {"c": failing()}, now=NOW, apply=True)
    assert dispatches(forge) == []


def test_render_hides_steps_only_while_resolving() -> None:
    body = render((FIXABLE,), {"roles": Status(False, "x", resolving=True)}, NOW)
    assert "Nothing to do yet" in body and "do it by hand" not in body


def test_the_loader_validates_resolvers_against_the_registry(tmp_path: Path) -> None:
    ops = load_registry(ROOT / "ops/registry.yaml")
    load_owner_actions(ROOT / "ops/owner_actions.yaml", ops)  # committed file is valid
    raw = (ROOT / "ops/owner_actions.yaml").read_text(encoding="utf-8")
    copy = tmp_path / "owner_actions.yaml"
    for resolver, error in (
        ("{op: nope.op}", "not in the op registry"),
        ("{op: github.secret.set}", None),  # R2 in ops-r2 is fine
        ("{op: repo.settings_apply}", None),
        ("{op: repo.report, max_attempts: 9}", "1 to 5"),
        ("{op: repo.report, surprise: 1}", "unknown keys"),
        ("repo.report", "must be a mapping"),
    ):
        line = f"    check: forge_access\n    resolver: {resolver}\n"
        copy.write_text(raw.replace("    check: forge_access\n", line, 1), encoding="utf-8")
        if error is None:
            load_owner_actions(copy, ops)
            continue
        with pytest.raises(ValueError, match=error):
            load_owner_actions(copy, ops)
