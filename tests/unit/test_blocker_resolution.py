"""Blocker auto-resolution: a pending action with a resolver dispatches its op, a bounded
number of times per piece of evidence, before it ever asks a human."""

import json
from datetime import UTC, datetime
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


def run(forge: FakeForge, check: Check) -> dict[str, object]:
    return actions_status(
        forge,
        TRACKER,
        (FIXABLE,),
        {"c": check},
        now=NOW,
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


def test_same_evidence_retries_once_then_escalates_to_the_human_steps() -> None:
    forge = FakeForge()
    run(forge, failing())
    run(forge, failing())
    report = run(forge, failing())  # third run: budget of 2 is spent
    assert len(dispatches(forge)) == 2
    assert report["resolutions_started"] == []
    body = forge.tracked[MARKER][2]
    assert "failed 2 times, so the steps below need you" in body
    assert "do it by hand" in body
    assert cast(list[dict[str, Any]], report["blockers"])[0]["status"] == "open"


def test_new_evidence_gets_a_fresh_budget_and_a_passing_check_dispatches_nothing() -> None:
    forge = FakeForge()
    run(forge, failing())
    run(forge, failing())
    run(forge, fixed("a different identity is admin"))
    assert len(dispatches(forge)) == 3
    run(forge, fixed("no identity holds admin", done=True))
    assert len(dispatches(forge)) == 3


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
    ops = set(load_registry(ROOT / "ops/registry.yaml"))
    load_owner_actions(ROOT / "ops/owner_actions.yaml", ops)  # committed file is valid
    raw = (ROOT / "ops/owner_actions.yaml").read_text(encoding="utf-8")
    copy = tmp_path / "owner_actions.yaml"
    for resolver, error in (
        ("{op: nope.op}", "not in the op registry"),
        ("{op: repo.report, max_attempts: 9}", "1 to 5"),
        ("{op: repo.report, surprise: 1}", "unknown keys"),
        ("repo.report", "must be a mapping"),
    ):
        line = f"    check: forge_access\n    resolver: {resolver}\n"
        copy.write_text(raw.replace("    check: forge_access\n", line, 1), encoding="utf-8")
        with pytest.raises(ValueError, match=error):
            load_owner_actions(copy, ops)
