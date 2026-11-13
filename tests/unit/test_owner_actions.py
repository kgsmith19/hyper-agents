"""The owner-actions checklist: live checks, one tracker item, weekly reminders, gated apply."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import pytest

from hyper_agents.adapters.fake_forge import FakeForge
from hyper_agents.adapters.forge_github import GithubForge
from hyper_agents.application.owner_actions import actions_status, request_settings_apply
from hyper_agents.domain.forge import Access
from hyper_agents.domain.owner_actions import MARKER, OwnerAction, Status, render
from hyper_agents.ops.core import run_op
from hyper_agents.ops.fleet import load_fleet
from hyper_agents.ops.owner import OwnerAdapter, load_owner_actions
from hyper_agents.ops.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 13, 14, 0, tzinfo=UTC)
TRACKER = "kgsmith19/hyper-agents"
PROFILE = "kgsmith19/agent-engineering-standard@main:TEMPLATES/repository-settings.json"
A = OwnerAction("a", "Step A", "because", ("do it",), "check_a")
B = OwnerAction("b", "Step B", "because", ("do that",), "check_b")


def done(_: OwnerAction) -> Status:
    return Status(True, "verified")


def not_done(_: OwnerAction) -> Status:
    return Status(False, "not yet")


def broken(_: OwnerAction) -> Status:
    raise RuntimeError("vault unreachable")


def test_checklist_ticks_from_live_checks_and_closes_when_all_done() -> None:
    forge = FakeForge()
    report = actions_status(
        forge, TRACKER, (A, B), {"check_a": done, "check_b": not_done}, now=NOW, apply=True
    )
    assert report["pending"] == ["b"] and report["done"] == ["a"]
    number, _, body, is_open = forge.tracked[MARKER]
    assert is_open is True
    assert "- [x] **Done:** verified" in body and "- [ ] **Pending:** not yet" in body
    actions_status(forge, TRACKER, (A, B), {"check_a": done, "check_b": done}, now=NOW, apply=True)
    assert forge.tracked[MARKER][0] == number  # the same item, rewritten in place
    assert forge.tracked[MARKER][3] is False  # closed once everything is done


def test_a_regression_reopens_and_a_broken_check_never_reads_as_done() -> None:
    forge = FakeForge()
    actions_status(forge, TRACKER, (A,), {"check_a": done}, now=NOW, apply=True)
    report = actions_status(forge, TRACKER, (A,), {"check_a": broken}, now=NOW, apply=True)
    assert report["pending"] == ["a"]
    assert "check failed: RuntimeError" in forge.tracked[MARKER][2]
    assert "vault unreachable" not in forge.tracked[MARKER][2]  # no raw error text in the Item
    assert forge.tracked[MARKER][3] is True
    unknown = actions_status(forge, TRACKER, (A,), {}, now=NOW, apply=False)
    assert unknown["pending"] == ["a"]


def test_one_reminder_per_week_and_none_when_everything_is_done() -> None:
    forge = FakeForge()
    checks = {"check_a": not_done}
    first = actions_status(forge, TRACKER, (A,), checks, now=NOW, apply=True)
    again = actions_status(forge, TRACKER, (A,), checks, now=NOW + timedelta(hours=1), apply=True)
    next_week = actions_status(
        forge, TRACKER, (A,), checks, now=NOW + timedelta(days=7), apply=True
    )
    assert first.get("reminded") is True
    assert "reminded" not in again
    assert next_week.get("reminded") is True
    quiet = FakeForge()
    actions_status(quiet, TRACKER, (A,), {"check_a": done}, now=NOW, apply=True)
    assert not quiet.notes


def test_dry_run_writes_nothing() -> None:
    forge = FakeForge()
    actions_status(forge, TRACKER, (A,), {"check_a": not_done}, now=NOW, apply=False)
    assert forge.writes == []


def test_settings_apply_is_requested_once_per_distinct_drift() -> None:
    forge = FakeForge()
    drifts: dict[str, dict[str, object]] = {
        "o/one": {"delete_branch_on_merge": True},
        "o/two": {},
    }
    kwargs: dict[str, Any] = {
        "workflow": "ops.yml",
        "ref": "main",
        "op_id": "repo.settings_apply",
        "apply": True,
    }
    assert request_settings_apply(forge, TRACKER, 7, drifts, **kwargs) == ["o/one"]
    assert request_settings_apply(forge, TRACKER, 7, drifts, **kwargs) == []
    dispatched = [w for w in forge.writes if w[0] == "dispatch"]
    assert dispatched == [
        (
            "dispatch",
            {"op": "repo.settings_apply", "params": json.dumps({"repo": "o/one"}), "mode": "apply"},
        )
    ]
    changed = {"o/one": {"delete_branch_on_merge": True, "allow_auto_merge": True}}
    assert request_settings_apply(forge, TRACKER, 7, changed, **kwargs) == ["o/one"]


def _owner(fake: FakeForge, secrets: Any) -> OwnerAdapter:
    fake.files[PROFILE] = json.dumps({"delete_branch_on_merge": True})
    return OwnerAdapter(
        load_fleet(ROOT / "ops/fleet.yaml"),
        load_owner_actions(ROOT / "ops/owner_actions.yaml"),
        secrets,
        forge=fake,
        clock=lambda: NOW,
    )


class Vault:
    def __init__(self, held: dict[tuple[str, str], str]) -> None:
        self.held = held

    def get(self, path: str, name: str) -> str:
        if (path, name) not in self.held:
            raise LookupError(f"{name} not found at {path}")
        return self.held[(path, name)]


def test_without_the_ops_token_every_action_is_pending_and_nothing_is_dispatched() -> None:
    live: dict[str, object] = {"delete_branch_on_merge": None}
    fake = FakeForge(live_settings=live)
    report = _owner(fake, Vault({})).execute("status", {})
    assert report["pending"] == ["ops-token", "settings-apply", "controller-tokens"]
    details = cast(dict[str, str], report["details"])
    assert "missing administration (read settings), issues (write)" in str(details["ops-token"])
    assert "CLAUDE_CODE_TOKEN, CHATGPT_TOKEN" in str(details["controller-tokens"])
    assert not [w for w in fake.writes if w[0] == "dispatch"]


def test_with_the_token_drift_is_dispatched_and_tokens_tick_off() -> None:
    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    fake = FakeForge(
        live_settings={"delete_branch_on_merge": False},
        access_by_repo={
            repo: Access(True, True, NOW + timedelta(days=300)) for repo in fleet.repos
        },
    )
    vault = Vault(
        {("/lifeos/agents", "CLAUDE_CODE_TOKEN"): "t1", ("/lifeos/agents", "CHATGPT_TOKEN"): "t2"}
    )
    report = _owner(fake, vault).execute("status", {})
    assert report["pending"] == ["settings-apply"]
    assert report["settings_apply_requested"] == sorted(fleet.repos)


def test_a_token_close_to_expiry_comes_back_as_pending() -> None:
    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    fake = FakeForge(
        access_by_repo={repo: Access(True, True, NOW + timedelta(days=5)) for repo in fleet.repos}
    )
    report = _owner(fake, Vault({})).execute("status", {})
    details = cast(dict[str, str], report["details"])
    assert "expires 2026-11-18; renew it" in str(details["ops-token"])


def test_registry_has_the_owner_op_as_r1_and_config_ids_are_unique(tmp_path: Path) -> None:
    registry = load_registry(ROOT / "ops/registry.yaml")
    assert registry["owner.actions_status"].tier == "R1"
    receipt = run_op(
        registry,
        "owner.actions_status",
        {},
        mode="dry-run",
        gate=None,
        adapters={},
        actor="t",
        run_id="r",
    )
    assert receipt["status"] == "planned"
    text = (ROOT / "ops/owner_actions.yaml").read_text(encoding="utf-8")
    path = tmp_path / "dup.yaml"
    path.write_text(text.replace("id: settings-apply", "id: ops-token"), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate owner action"):
        load_owner_actions(path)


def test_render_lists_steps_only_for_pending_actions() -> None:
    body = render((A, B), {"a": Status(True, "ok"), "b": Status(False, "no")}, NOW)
    assert body.startswith(MARKER)
    assert "do that" in body and "do it" not in body
    assert "**1 of 2 pending**" in body


def _github(routes: dict[tuple[str, str], tuple[int, Any]]) -> tuple[GithubForge, list[Any]]:
    calls: list[Any] = []

    def transport(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes, dict[str, str]]:
        path = url.removeprefix("https://api.github.com").split("?")[0]
        calls.append((method, path, json.loads(body) if body else None))
        status, payload = routes.get((method, path), (200, None))
        extra = {"github-authentication-token-expiration": "2027-11-13 09:00:00 UTC"}
        return status, json.dumps(payload).encode() if payload is not None else b"", extra

    return GithubForge(token=lambda: "tok", transport=transport), calls


def test_github_access_probe_changes_nothing_and_reads_the_expiry() -> None:
    repo = "kgsmith19/hyperbolic-core"
    adapter, calls = _github(
        {
            ("GET", f"/repos/{repo}"): (200, {"delete_branch_on_merge": False}),
            ("GET", f"/repos/{repo}/issues"): (200, [{"number": 41}]),
            ("POST", f"/repos/{repo}/issues/41/labels"): (403, {"message": "denied"}),
        }
    )
    access = adapter.access(repo)
    assert access.settings_readable is True and access.items_writable is False
    assert access.credential_expires_at == datetime(2027, 11, 13, 9, 0, tzinfo=UTC)
    assert ("POST", f"/repos/{repo}/issues/41/labels", {"labels": []}) in calls
    # The ref probe points at the all-zero SHA, so it can never create a branch.
    assert calls[-1] == (
        "POST",
        f"/repos/{repo}/git/refs",
        {"ref": "refs/heads/hyper-agents-ref-write-probe", "sha": "0" * 40},
    )
    assert access.refs_writable is False  # 200 from the fake is not the 422 that proves it


def test_github_ref_probe_reads_422_as_writable_and_403_as_not() -> None:
    repo = "kgsmith19/hyperbolic-core"
    for status, writable in ((422, True), (403, False), (404, False)):
        adapter, _ = _github(
            {
                ("GET", f"/repos/{repo}"): (200, {"delete_branch_on_merge": True}),
                ("GET", f"/repos/{repo}/issues"): (200, []),
                ("POST", f"/repos/{repo}/git/refs"): (status, {"message": "x"}),
            }
        )
        assert adapter.access(repo).refs_writable is writable


def test_github_ensure_item_rewrites_the_marked_item_in_place() -> None:
    repo = TRACKER
    adapter, calls = _github(
        {
            ("GET", f"/repos/{repo}/issues"): (
                200,
                [
                    {"number": 3, "body": "other", "state": "open", "title": "x"},
                    {"number": 58, "body": f"{MARKER}\nold", "state": "open", "title": "T"},
                ],
            )
        }
    )
    assert adapter.ensure_item(repo, MARKER, "T", f"{MARKER}\nnew", open=False) == 58
    assert calls[-1] == (
        "PATCH",
        f"/repos/{repo}/issues/58",
        {"body": f"{MARKER}\nnew", "state": "closed", "state_reason": "completed"},
    )


def test_github_note_dedupe_reads_every_page_of_comments() -> None:
    repo = TRACKER
    marker = "<!-- hyper-agents:owner-actions:reminder:2026-W46 -->"
    pages = {
        1: [{"id": n, "body": "noise"} for n in range(100)],
        2: [{"id": 999, "body": f"{marker}\nhi"}],
    }
    calls: list[Any] = []

    def transport(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        calls.append((method, url))
        page = int(url.split("page=")[-1]) if "page=" in url else 1
        return 200, json.dumps(pages.get(page, [])).encode()

    adapter = GithubForge(token=lambda: "tok", transport=transport)
    assert adapter.note_exists(repo, 58, marker) is True
    adapter.upsert_note(repo, 58, marker, "hi")
    assert all(method == "GET" for method, _ in calls)  # found on page 2: nothing posted


def test_github_access_probe_reads_422_as_writable_and_403_as_not() -> None:
    """GitHub validates the empty label list only after authorizing the write (live 2026-11-13)."""
    repo = "kgsmith19/hyper-agents"
    for status, writable in ((422, True), (200, True), (403, False), (404, False)):
        adapter, _ = _github(
            {
                ("GET", f"/repos/{repo}"): (200, {"delete_branch_on_merge": True}),
                ("GET", f"/repos/{repo}/issues"): (200, [{"number": 58}]),
                ("POST", f"/repos/{repo}/issues/58/labels"): (status, {"message": "x"}),
            }
        )
        assert adapter.access(repo).items_writable is writable, status
