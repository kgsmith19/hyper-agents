"""Queue history check and resolver (agent-engineering-standard#342).

The Standard owns the queue and its repair CLI; hyper-agents only fetches that CLI through the
forge port and runs it. Here a stub CLI stands in for the Standard's (same arguments, exit
codes and JSON) so the real fetch-write-execute path runs, and each test asserts the owner
action status or receipt the broker would act on.
"""

import json
from pathlib import Path
from typing import Any

import pytest

from hyper_agents.adapters.fake_forge import FakeForge
from hyper_agents.ops.core import run_op
from hyper_agents.ops.fleet import load_fleet
from hyper_agents.ops.owner import load_owner_actions
from hyper_agents.ops.queue_history import (
    QueueCheckError,
    QueueConfig,
    QueueHistory,
    load_queue_config,
)
from hyper_agents.ops.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
FAKE_TOKEN = "tok-123456789"  # noqa: S105 - test value, not a secret
CONFIG = QueueConfig(
    repo="kgsmith19/agent-engineering-standard",
    ref="81f844621381e8e7352d6e819899b2fded6014dd",
    issue=277,
    tools=("tools/queue_core.py", "tools/queue_store.py", "tools/work_queue.py"),
)

# Stand-in for the Standard's work_queue.py. Its state lives in a JSON file named by
# STUB_STATE: {"gap": bool, "recoverable": bool, "handoff_rc": int}. It also records the
# arguments and the token it saw, so tests can prove what was run and with what.
STUB = r"""
import json, os, sys
path = os.environ["STUB_STATE"]
state = json.load(open(path))
state["token"] = os.environ.get("GITHUB_TOKEN")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))  # as work_queue.py does
import queue_core, queue_store  # the sibling modules must be fetched too
def save():
    json.dump(state, open(path, "w"))
args = sys.argv[sys.argv.index("--issue") + 2:]
state.setdefault("calls", []).append(args)
state["prefix"] = sys.argv[1:5]
if args == ["history", "--check"]:
    save()
    if state.get("check_rc"):
        print("error: transport: GET /repos/x failed: provider detail", file=sys.stderr)
        sys.exit(state["check_rc"])
    if state.pop("close_after_check", False):
        state["gap"] = False; save()
        print("history: DEGRADED, history is missing event 583 (7cf7a798000e...); repair: ...")
        sys.exit(2)
    if state["gap"]:
        print("history: DEGRADED, history is missing event 583 (7cf7a798000e...); repair: ...")
        sys.exit(2)
    print("history: ok, complete through seq 616")
    sys.exit(0)
if args == ["handoff", "show"]:
    save()
    print("# handoff")
    sys.exit(state.get("handoff_rc", 0))
if args[0] == "history-repair":
    apply = "--apply" in args
    if not state["gap"] or state.get("probe_says_complete"):
        save(); print(json.dumps({"status": "complete", "seq": 616, "appended": []})); sys.exit(0)
    if not state["recoverable"]:
        save()
        print(json.dumps({"status": "unrecoverable",
                          "blocker": {"kind": "ManualBlocker", "missing_seq": 583}}))
        sys.exit(2)
    if apply and state.get("append_fails"):
        save(); print("error: history is missing event 583 (7cf7a798000e...)", file=sys.stderr)
        sys.exit(2)
    if apply:
        state["gap"] = False
    save()
    report = {"status": "recovered" if apply else "recoverable", "seq": 616,
              "appended": [[583, "7cf7a798000e"]]}
    if apply:
        report["read_back"] = {"history": "ok", "handoff": "ok"}
    print(json.dumps(report))
    sys.exit(0)
save(); sys.exit(9)
"""


@pytest.fixture
def stub(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[FakeForge, Path]:
    forge = FakeForge()
    for tool in CONFIG.tools:
        body = STUB if tool.endswith("work_queue.py") else "# module\n"
        forge.files[f"{CONFIG.repo}@{CONFIG.ref}:{tool}"] = body
    state = tmp_path / "state.json"
    state.write_text(json.dumps({"gap": True, "recoverable": True}))
    monkeypatch.setenv("STUB_STATE", str(state))
    return forge, state


def _env() -> dict[str, str]:
    import os

    return {"PATH": os.environ["PATH"], "STUB_STATE": os.environ["STUB_STATE"]}


def _queue(forge: FakeForge) -> QueueHistory:
    return QueueHistory(forge, CONFIG, token=lambda: FAKE_TOKEN, base_env=_env())


def _state(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text())


def test_complete_history_and_readable_handoff_is_done(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": False, "recoverable": True}))
    status = _queue(forge).status()
    assert status.done is True
    assert "616" in status.detail
    seen = _state(state)
    assert seen["calls"][-2:] == [["history", "--check"], ["handoff", "show"]]
    assert seen["token"] == FAKE_TOKEN  # the fleet token reaches the CLI by env only
    assert seen["prefix"] == ["--repo", CONFIG.repo, "--issue", "277"]


def test_unreadable_handoff_is_not_done(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": False, "recoverable": True, "handoff_rc": 2}))
    status = _queue(forge).status()
    assert status.done is False and "handoff" in status.detail
    assert status.needs_owner is True  # a history repair cannot fix a handoff: no resolver


def test_no_handoff_yet_still_counts_as_complete(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": False, "recoverable": True, "handoff_rc": 3}))
    assert _queue(forge).status().done is True


def test_recoverable_gap_dispatches_the_resolver_without_asking_the_owner(
    stub: tuple[FakeForge, Path],
) -> None:
    forge, _ = stub
    status = _queue(forge).status()
    assert status.done is False and status.needs_owner is False
    assert "583" in status.detail and "7cf7a798000e" in status.detail
    assert "exact copy found" in status.detail


def test_unrecoverable_gap_asks_the_owner_at_once(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": True, "recoverable": False}))
    status = _queue(forge).status()
    assert status.done is False and status.needs_owner is True
    assert "no exact copy" in status.detail


def test_recover_applies_then_verifies_and_is_idempotent(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    result = _queue(forge).recover()
    assert result == {
        "status": "recovered",
        "seq": 616,
        "appended": [[583, "7cf7a798000e"]],
        "verified": True,
    }
    assert _state(state)["calls"] == [["history-repair", "--apply"]]  # the CLI reads back
    again = _queue(forge).recover()
    assert again["status"] == "complete" and again["verified"] is True


def test_recover_that_does_not_verify_fails(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": True, "recoverable": True, "append_fails": True}))
    with pytest.raises(RuntimeError, match="did not verify"):
        _queue(forge).recover()


def test_unrecoverable_recover_fails_closed(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": True, "recoverable": False}))
    with pytest.raises(RuntimeError, match="unrecoverable"):
        _queue(forge).recover()
    assert ["history-repair", "--apply"] in _state(state)["calls"]


def test_missing_tool_fails_before_running_anything(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    del forge.files[f"{CONFIG.repo}@{CONFIG.ref}:tools/queue_store.py"]
    with pytest.raises(KeyError):
        _queue(forge).status()
    assert "calls" not in _state(state)


def test_token_never_appears_in_status_or_receipt(stub: tuple[FakeForge, Path]) -> None:
    forge, _ = stub
    status = _queue(forge).status()
    assert FAKE_TOKEN not in status.detail
    assert FAKE_TOKEN not in json.dumps(_queue(forge).recover())


def test_config_registry_and_owner_action_are_wired() -> None:
    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    assert fleet.queue == CONFIG
    registry = load_registry(ROOT / "ops/registry.yaml")
    assert (registry["queue.history_check"].tier, registry["queue.history_check"].adapter) == (
        "R0",
        "queue",
    )
    recover = registry["queue.history_recover"]
    assert (recover.tier, recover.gate, recover.params) == ("R2", "ops-r2", ())
    actions = load_owner_actions(ROOT / "ops/owner_actions.yaml", registry).actions
    action = next(a for a in actions if a.id == "queue-history")
    assert action.check == "queue_history_complete"
    assert action.resolver is not None and action.resolver.op == "queue.history_recover"
    assert action.blocker is not None and action.blocker.source.endswith("/issues/342")


def test_recover_op_refused_outside_its_gate(stub: tuple[FakeForge, Path]) -> None:
    from hyper_agents.ops.core import OpRefused

    forge, state = stub
    registry = load_registry(ROOT / "ops/registry.yaml")
    with pytest.raises(OpRefused):
        run_op(
            registry,
            "queue.history_recover",
            {},
            mode="apply",
            gate=None,
            adapters={"queue": _queue(forge)},
            actor="t",
            run_id="r",
        )
    assert "calls" not in _state(state)


def test_transport_failure_is_check_failed_never_an_owner_request(
    stub: tuple[FakeForge, Path],
) -> None:
    """Review finding: exit 5 (transport) must not read as an unrecoverable gap."""
    forge, state = stub
    state.write_text(json.dumps({"gap": True, "recoverable": True, "check_rc": 5}))
    with pytest.raises(QueueCheckError) as caught:
        _queue(forge).status()
    assert "provider detail" not in str(caught.value)  # CLI free text never surfaces
    assert _state(state)["calls"] == [["history", "--check"]]  # no probe, no write


def test_gap_closed_between_reads_rechecks_instead_of_asking_the_owner(
    stub: tuple[FakeForge, Path],
) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": True, "recoverable": True, "close_after_check": True}))
    status = _queue(forge).status()
    assert status.done is True and status.needs_owner is False


def test_a_check_and_probe_that_keep_disagreeing_fail_instead_of_looping(
    stub: tuple[FakeForge, Path],
) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": True, "recoverable": True, "probe_says_complete": True}))
    with pytest.raises(QueueCheckError):
        _queue(forge).status()
    assert _state(state)["calls"].count(["history", "--check"]) == 2  # one re-check only


def test_tools_are_fetched_once_per_instance(stub: tuple[FakeForge, Path]) -> None:
    forge, _ = stub
    reads: list[str] = []
    original = forge.read_file

    def counting(repo: str, ref: str, path: str) -> str:
        reads.append(path)
        return original(repo, ref, path)

    forge.read_file = counting  # type: ignore[method-assign]
    queue = _queue(forge)
    queue.status()
    queue.recover()
    assert sorted(reads) == sorted(CONFIG.tools)


def test_a_branch_ref_is_refused() -> None:
    with pytest.raises(ValueError, match="commit SHA"):
        load_queue_config(
            {"repo": CONFIG.repo, "ref": "main", "issue": 277, "tools": list(CONFIG.tools)}
        )


def _maintain_status(forge: FakeForge) -> dict[str, Any]:
    """owner.actions_status over the real config, with every other check healthy."""
    from datetime import UTC, datetime, timedelta

    from hyper_agents.domain.forge import Access
    from hyper_agents.domain.owner_actions import Status
    from hyper_agents.ops.owner import OwnerAdapter

    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    now = datetime(2026, 11, 14, 17, 0, tzinfo=UTC)
    forge.access_by_repo = {r: Access(True, True, now + timedelta(days=300)) for r in fleet.repos}
    forge.live_settings = {"delete_branch_on_merge": True}
    forge.files["kgsmith19/agent-engineering-standard@main:TEMPLATES/repository-settings.json"] = (
        json.dumps({"delete_branch_on_merge": True})
    )
    adapter = OwnerAdapter(
        fleet,
        load_owner_actions(
            ROOT / "ops/owner_actions.yaml", load_registry(ROOT / "ops/registry.yaml")
        ),
        object(),
        forge=forge,
        clock=lambda: now,
        queue=_queue(forge),
    )

    def healthy(_action: object) -> Status:
        return Status(True, "ok")

    for name in (
        "_controller_tokens_fresh",
        "_identities_least_privilege",
        "_signing_folder_boundary",
        "_branches_pruned",
    ):
        setattr(adapter, name, healthy)
    return adapter.execute("status", {})


def _dispatched(forge: FakeForge) -> list[str]:
    return [str(w[1]["op"]) for w in forge.writes if w[0] == "dispatch"]  # type: ignore[index]


def test_maintain_dispatches_the_resolver_for_a_recoverable_gap(
    stub: tuple[FakeForge, Path],
) -> None:
    forge, _ = stub
    report = _maintain_status(forge)
    assert report["pending"] == ["queue-history"]
    assert _dispatched(forge) == ["queue.history_recover"]


def test_maintain_asks_the_owner_and_dispatches_nothing_when_unrecoverable(
    stub: tuple[FakeForge, Path],
) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": True, "recoverable": False}))
    report = _maintain_status(forge)
    assert report["pending"] == ["queue-history"]
    assert _dispatched(forge) == []
    body = next(b for _, _, b, _ in forge.tracked.values())
    assert "authorize re-anchor" in body  # the ManualBlocker's steps are shown at once


def test_maintain_closes_the_item_once_history_is_complete(stub: tuple[FakeForge, Path]) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": False, "recoverable": True}))
    report = _maintain_status(forge)
    assert report["pending"] == [] and "queue-history" in report["done"]
    assert _dispatched(forge) == []


def test_maintain_treats_a_transport_failure_as_watch_and_dispatches_nothing(
    stub: tuple[FakeForge, Path],
) -> None:
    forge, state = stub
    state.write_text(json.dumps({"gap": True, "recoverable": True, "check_rc": 5}))
    report = _maintain_status(forge)
    assert report["pending"] == ["queue-history"]
    assert _dispatched(forge) == []
    body = next(b for _, _, b, _ in forge.tracked.values())
    assert "check failed: QueueCheckError" in body
    assert "provider detail" not in body
