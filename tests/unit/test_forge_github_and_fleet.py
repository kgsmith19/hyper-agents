"""The GitHub ForgePort adapter (fake transport) and the fleet-configured repo broker adapter."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from hyper_agents.adapters.fake_forge import FakeForge
from hyper_agents.adapters.forge_github import GithubForge
from hyper_agents.domain.forge import Change, Item
from hyper_agents.ops.core import run_op
from hyper_agents.ops.fleet import RepoAdapter, load_fleet
from hyper_agents.ops.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)
REPO = "kgsmith19/hyperbolic-core"


class Transport:
    def __init__(self, routes: dict[tuple[str, str], tuple[int, Any]]) -> None:
        self.routes = routes
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(
        self, method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        path = url.removeprefix("https://api.github.com")
        self.calls.append((method, path, json.loads(body) if body else None))
        assert headers["Authorization"] == "Bearer tok"
        status, payload = self.routes.get((method, path.split("?")[0]), (200, None))
        return status, json.dumps(payload).encode() if payload is not None else b""


def forge(routes: dict[tuple[str, str], tuple[int, Any]]) -> tuple[GithubForge, Transport]:
    transport = Transport(routes)
    return GithubForge(token=lambda: "tok", transport=transport), transport


def test_open_changes_reads_mergeable_state_from_the_single_change_read() -> None:
    """The listing has no mergeable state; a dirty change must still be seen as conflicted."""
    adapter, _ = forge(
        {
            ("GET", f"/repos/{REPO}/pulls"): (200, [{"number": 7}, {"number": 8}]),
            ("GET", f"/repos/{REPO}/pulls/7"): (
                200,
                {"number": 7, "mergeable_state": "dirty", "labels": [{"name": "x"}]},
            ),
            ("GET", f"/repos/{REPO}/pulls/8"): (200, {"number": 8, "mergeable_state": "behind"}),
        }
    )
    changes = adapter.open_changes(REPO)
    assert [(c.number, c.conflicted, c.behind, c.labels) for c in changes] == [
        (7, True, False, ("x",)),
        (8, False, True, ()),
    ]


def test_merged_changes_skip_unmerged_and_old() -> None:
    old = (NOW - timedelta(days=30)).isoformat()
    adapter, _ = forge(
        {
            ("GET", f"/repos/{REPO}/pulls"): (
                200,
                [
                    {"number": 1, "merged_at": "2026-11-12T19:00:00Z", "body": "Closes #3"},
                    {"number": 2, "merged_at": None},
                    {"number": 3, "merged_at": old},
                ],
            )
        }
    )
    changes = adapter.merged_changes_since(REPO, NOW - timedelta(days=14))
    assert [c.number for c in changes] == [1]


def test_close_comments_then_closes_as_completed() -> None:
    adapter, transport = forge({})
    adapter.close_item(REPO, 3, "Closed by merged change #10.")
    assert [(m, p) for m, p, _ in transport.calls] == [
        ("POST", f"/repos/{REPO}/issues/3/comments"),
        ("PATCH", f"/repos/{REPO}/issues/3"),
    ]
    assert transport.calls[1][2] == {"state": "closed", "state_reason": "completed"}


def test_item_ignores_pull_requests_and_missing_items() -> None:
    adapter, _ = forge(
        {
            ("GET", f"/repos/{REPO}/issues/5"): (200, {"state": "open", "pull_request": {}}),
            ("GET", f"/repos/{REPO}/issues/6"): (404, {"message": "Not Found"}),
            ("GET", f"/repos/{REPO}/issues/7"): (200, {"state": "open"}),
        }
    )
    assert adapter.item(REPO, 5) is None
    assert adapter.item(REPO, 6) is None
    assert adapter.item(REPO, 7) == Item(7, open=True)


def test_upsert_note_edits_in_place_and_never_duplicates() -> None:
    marker = "<!-- m -->"
    adapter, transport = forge(
        {("GET", f"/repos/{REPO}/issues/7/comments"): (200, [{"id": 9, "body": f"{marker}\nold"}])}
    )
    adapter.upsert_note(REPO, 7, marker, "new")
    assert transport.calls[-1][:2] == ("PATCH", f"/repos/{REPO}/issues/comments/9")
    adapter.upsert_note(REPO, 7, marker, "old")
    assert [m for m, _, _ in transport.calls].count("POST") == 0


def test_settings_writes_only_known_fields_and_refuses_bad_input() -> None:
    adapter, transport = forge({})
    with pytest.raises(ValueError, match="unsupported settings"):
        adapter.set_settings(REPO, {"visibility": "public"})
    with pytest.raises(ValueError, match="owner/name"):
        adapter.settings("not a repo")
    adapter.set_settings(REPO, {"delete_branch_on_merge": True})
    assert transport.calls[-1] == ("PATCH", f"/repos/{REPO}", {"delete_branch_on_merge": True})


def test_http_errors_fail_loudly() -> None:
    adapter, _ = forge({("PATCH", f"/repos/{REPO}"): (403, {"message": "nope"})})
    with pytest.raises(RuntimeError, match="HTTP 403"):
        adapter.set_settings(REPO, {"delete_branch_on_merge": True})


def test_fleet_config_names_four_repos_and_a_known_forge() -> None:
    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    assert len(fleet.repos) == 4 and fleet.forge_adapter == "github"
    registry = load_registry(ROOT / "ops/registry.yaml")
    for op_id in (
        "repo.report",
        "repo.settings_drift",
        "repo.close_sweep",
        "repo.change_health",
        "repo.settings_apply",
    ):
        enum = registry[op_id].params[0].enum
        assert enum is not None and set(enum) == set(fleet.repos), op_id
    assert registry["repo.settings_apply"].tier == "R2"
    assert registry["repo.close_sweep"].tier == "R1"


def test_unknown_forge_adapter_is_refused(tmp_path: Path) -> None:
    text = (
        (ROOT / "ops/fleet.yaml")
        .read_text(encoding="utf-8")
        .replace("adapter: github", "adapter: nowhere")
    )
    path = tmp_path / "fleet.yaml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError, match="unknown forge adapter"):
        load_fleet(path)


def _adapter(fake: FakeForge) -> RepoAdapter:
    fake.files["kgsmith19/agent-engineering-standard@main:TEMPLATES/repository-settings.json"] = (
        json.dumps({"delete_branch_on_merge": True})
    )
    return RepoAdapter(load_fleet(ROOT / "ops/fleet.yaml"), None, forge=fake, clock=lambda: NOW)


def test_repo_adapter_report_writes_nothing_and_settings_apply_is_gated() -> None:
    fake = FakeForge(
        merged=[Change(10, "Closes #3", "x", merged_at=NOW - timedelta(hours=1))],
        items={3: Item(3, open=True)},
        live_settings={"delete_branch_on_merge": False},
    )
    registry = load_registry(ROOT / "ops/registry.yaml")
    adapters = {"repo": _adapter(fake)}
    receipt = run_op(
        registry,
        "repo.report",
        {"repo": REPO},
        mode="apply",
        gate=None,
        adapters=adapters,
        actor="t",
        run_id="r",
    )
    assert receipt["status"] == "applied"
    assert receipt["result"]["settings"]["drift"] == {"delete_branch_on_merge": True}
    assert fake.writes == []
    from hyper_agents.ops.core import OpRefused

    with pytest.raises(OpRefused, match="requires gate"):
        run_op(
            registry,
            "repo.settings_apply",
            {"repo": REPO},
            mode="apply",
            gate=None,
            adapters=adapters,
            actor="t",
            run_id="r",
        )
    receipt = run_op(
        registry,
        "repo.close_sweep",
        {"repo": REPO},
        mode="apply",
        gate=None,
        adapters=adapters,
        actor="t",
        run_id="r",
    )
    assert fake.writes == [("close", 3)]


def test_repo_adapter_refuses_repositories_outside_the_fleet() -> None:
    with pytest.raises(ValueError, match="not in the fleet"):
        _adapter(FakeForge()).execute("report", {"repo": "someone/else"})


def test_rate_limits_back_off_and_retry() -> None:
    """HTTP 429 is retried with backoff instead of failing the whole maintenance run."""
    sleeps: list[float] = []
    answers = iter([(429, b""), (429, b""), (200, b"{}")])

    def transport(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        return next(answers)

    adapter = GithubForge(token=lambda: "tok", transport=transport, sleep=sleeps.append)
    assert adapter.settings(REPO) == dict.fromkeys(
        (
            "allow_squash_merge",
            "allow_merge_commit",
            "allow_rebase_merge",
            "allow_auto_merge",
            "delete_branch_on_merge",
            "allow_update_branch",
            "squash_merge_commit_title",
            "squash_merge_commit_message",
            "has_issues",
        )
    )
    assert sleeps == [5.0, 20.0]


def test_rate_limit_that_persists_still_fails_loudly() -> None:
    def limited(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        return 429, b""

    def no_sleep(seconds: float) -> None:
        return None

    adapter = GithubForge(token=lambda: "tok", transport=limited, sleep=no_sleep)
    with pytest.raises(RuntimeError, match="HTTP 429"):
        adapter.settings(REPO)
