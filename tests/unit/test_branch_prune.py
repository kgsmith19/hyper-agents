"""Lease-checked branch prune: use case, GitHub refs, ledger config, owner check."""

import json
from pathlib import Path
from typing import Any, cast

import pytest

from hyper_agents.adapters.fake_forge import FakeForge
from hyper_agents.adapters.forge_github import GithubForge
from hyper_agents.application.repo_maintenance import PruneFailed, branch_prune
from hyper_agents.domain.forge import Access, Change, LedgerBranch
from hyper_agents.ops.__main__ import MAINTAIN_OPS
from hyper_agents.ops.fleet import RepoAdapter, load_branch_ledger, load_fleet
from hyper_agents.ops.owner import OwnerAdapter, load_owner_actions
from hyper_agents.ops.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
REPO = "kgsmith19/hyperbolic-core"
A = "a" * 40
B = "b" * 40
C = "c" * 40


def results(report: dict[str, object]) -> dict[str, str]:
    rows = cast(list[dict[str, str]], report["branches"])
    return {row["branch"]: row["result"] for row in rows}


def test_prune_deletes_only_at_the_lease_and_a_second_run_is_all_gone() -> None:
    forge = FakeForge(branches={"done": A, "moved": C, "keep": B})
    ledger = (LedgerBranch("done", A), LedgerBranch("moved", B), LedgerBranch("absent", A))
    first = branch_prune(forge, REPO, ledger, apply=True)
    assert results(first) == {"done": "deleted", "moved": "moved", "absent": "gone"}
    assert forge.branches == {"moved": C, "keep": B}  # moved and unlisted branches stay
    second = branch_prune(forge, REPO, ledger, apply=True)
    assert set(results(second).values()) == {"gone", "moved"}
    assert cast(dict[str, int], second["counts"]) == {"gone": 2, "moved": 1}


def test_dry_run_reports_and_writes_nothing() -> None:
    forge = FakeForge(branches={"x": A, "y": B})
    ledger = (LedgerBranch("x", A), LedgerBranch("y", B, archive=True))
    report = branch_prune(forge, REPO, ledger, apply=False)
    assert results(report) == {"x": "would_delete", "y": "would_archive_and_delete"}
    assert forge.writes == [] and forge.branches == {"x": A, "y": B}


def test_archive_tags_at_the_lease_before_deleting() -> None:
    forge = FakeForge(branches={"old": A})
    report = branch_prune(forge, REPO, (LedgerBranch("old", A, archive=True),), apply=True)
    assert results(report) == {"old": "archived_and_deleted"}
    assert forge.tags == {"archive/old": A}
    assert forge.writes == [("tag", "archive/old"), ("delete_branch", "old")]


def test_an_archive_tag_at_another_sha_keeps_the_branch_and_fails_loudly() -> None:
    forge = FakeForge(branches={"old": A}, tags={"archive/old": B})
    with pytest.raises(RuntimeError, match="archive/old"):
        branch_prune(forge, REPO, (LedgerBranch("old", A, archive=True),), apply=True)
    assert forge.branches == {"old": A}


def test_a_branch_with_an_open_change_is_never_deleted() -> None:
    forge = FakeForge(branches={"live": A}, open=[Change(number=7, body="", head_ref="live")])
    report = branch_prune(forge, REPO, (LedgerBranch("live", A),), apply=True)
    assert results(report) == {"live": "open_change"} and forge.branches == {"live": A}


def test_a_branch_that_is_the_base_of_an_open_change_is_never_deleted() -> None:
    forge = FakeForge(
        branches={"stack": A}, open=[Change(number=8, body="", head_ref="top", base_ref="stack")]
    )
    report = branch_prune(forge, REPO, (LedgerBranch("stack", A),), apply=True)
    assert results(report) == {"stack": "open_change"} and forge.branches == {"stack": A}


def test_a_refused_delete_fails_red_but_keeps_every_row_and_finishes_the_rest() -> None:
    forge = FakeForge(branches={"ruled": A, "fine": B}, refused={"ruled"})
    ledger = (LedgerBranch("ruled", A), LedgerBranch("fine", B))
    with pytest.raises(PruneFailed) as caught:
        branch_prune(forge, REPO, ledger, apply=True)
    report = json.loads(str(caught.value))
    assert results(report) == {"ruled": "failed", "fine": "deleted"}
    assert "still present" in report["branches"][0]["detail"]
    assert forge.branches == {"ruled": A}


def test_a_read_error_on_one_branch_never_stops_the_others() -> None:
    class Flaky(FakeForge):
        def branch_head(self, repo: str, branch: str) -> str | None:
            if branch == "flaky":
                raise RuntimeError("HTTP 500")
            return super().branch_head(repo, branch)

    forge = Flaky(branches={"flaky": A, "ok": B})
    with pytest.raises(PruneFailed) as caught:
        branch_prune(forge, REPO, (LedgerBranch("flaky", A), LedgerBranch("ok", B)), apply=True)
    assert results(json.loads(str(caught.value))) == {"flaky": "failed", "ok": "deleted"}


def test_without_ref_write_it_is_blocked_and_writes_nothing() -> None:
    forge = FakeForge(branches={"x": A, "y": B}, refs_writable=False)
    ledger = (LedgerBranch("x", A), LedgerBranch("y", B, archive=True))
    report = branch_prune(forge, REPO, ledger, apply=True)  # does not raise
    assert results(report) == {"x": "blocked", "y": "blocked"}
    assert "cannot delete refs" in str(report["blocked"])
    assert forge.branches == {"x": A, "y": B} and forge.tags == {}


def test_the_ledger_refuses_default_branches_short_shas_and_odd_names() -> None:
    for name in ("main", "master", "../x", "a//b", "/x", "x/"):
        with pytest.raises(ValueError):
            LedgerBranch(name, A)
    with pytest.raises(ValueError, match="40-hex"):
        LedgerBranch("ok", "abc123")


def test_ledger_loader_refuses_foreign_repos_and_repeats(tmp_path: Path) -> None:
    path = tmp_path / "ledger.yaml"
    path.write_text(f"repos:\n  other/repo:\n    - {{branch: x, sha: {A}}}\n")
    with pytest.raises(ValueError, match="not in the fleet"):
        load_branch_ledger(path, (REPO,))
    path.write_text(
        f"repos:\n  {REPO}:\n    - {{branch: x, sha: {A}}}\n    - {{branch: x, sha: {B}}}\n"
    )
    with pytest.raises(ValueError, match="twice"):
        load_branch_ledger(path, (REPO,))


def test_the_committed_ledger_matches_the_owner_script() -> None:
    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    ledger = {(r, b.branch, b.sha, b.archive) for r, bs in fleet.branch_ledger.items() for b in bs}
    script = (ROOT / "scripts/prune-branches.sh").read_text(encoding="utf-8").splitlines()
    scripted = {
        (f"kgsmith19/{p[1]}", p[2], p[3], p[4] == "ARCH")
        for p in (line.split() for line in script if line.startswith("prune "))
    }
    assert ledger == scripted and len(ledger) == 70


def test_the_op_is_registered_r1_and_runs_in_maintain() -> None:
    op = load_registry(ROOT / "ops/registry.yaml")["repo.branch_prune"]
    assert (op.adapter, op.action, op.tier) == ("repo", "branch_prune", "R1")
    assert "repo.branch_prune" in MAINTAIN_OPS


def test_repo_adapter_prunes_from_the_fleet_ledger_and_reports_dry_run() -> None:
    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    entry = fleet.branch_ledger[REPO][0]
    fake = FakeForge(branches={entry.branch: entry.sha})
    adapter = RepoAdapter(fleet, secrets=None, forge=fake)
    assert results(adapter.branch_prune(REPO, apply=False))[entry.branch].startswith("would_")
    assert fake.writes == []
    report = adapter.execute("branch_prune", {"repo": REPO})
    assert results(report)[entry.branch] in ("deleted", "archived_and_deleted")
    assert entry.branch not in fake.branches


def test_owner_check_ticks_once_every_ledger_branch_is_gone() -> None:
    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    config = load_owner_actions(ROOT / "ops/owner_actions.yaml")
    action = next(a for a in config.actions if a.id == "branch-prune")
    entry = fleet.branch_ledger[REPO][0]
    fake = FakeForge(
        branches={entry.branch: entry.sha},
        access_by_repo={r: Access(True, True, None, False) for r in fleet.repos},
    )
    owner = OwnerAdapter(fleet, config, secrets=None, forge=fake)
    status = owner._branches_pruned(action)  # pyright: ignore[reportPrivateUsage]
    assert status.done is False and "Contents: Read and write" in status.detail
    assert "hyperbolic-core: 1" in status.detail
    fake.branches.clear()
    owner = OwnerAdapter(fleet, config, secrets=None, forge=fake)
    assert owner._branches_pruned(action).done is True  # pyright: ignore[reportPrivateUsage]


def _github(routes: dict[tuple[str, str], tuple[int, Any]]) -> tuple[GithubForge, list[Any]]:
    calls: list[Any] = []

    def transport(
        method: str, url: str, headers: dict[str, str], body: bytes | None
    ) -> tuple[int, bytes]:
        path = url.removeprefix("https://api.github.com")
        calls.append((method, path, json.loads(body) if body else None))
        status, payload = routes.get((method, path), (200, None))
        return status, json.dumps(payload).encode() if payload is not None else b""

    return GithubForge(token=lambda: "tok", transport=transport), calls


def test_github_reads_exact_refs_and_maps_permission_errors() -> None:
    base = f"/repos/{REPO}/git"
    adapter, calls = _github(
        {
            ("GET", f"{base}/ref/heads/issue/1-x"): (
                200,
                {"ref": "refs/heads/issue/1-x", "object": {"sha": A}},
            ),
            ("GET", f"{base}/ref/heads/gone"): (404, {"message": "Not Found"}),
            ("GET", f"{base}/ref/tags/archive/x"): (
                200,
                {"ref": "refs/tags/archive/x-other", "object": {"sha": B}},
            ),
            ("DELETE", f"{base}/refs/heads/denied"): (403, {"message": "no"}),
            ("DELETE", f"{base}/refs/heads/already"): (422, {"message": "missing"}),
            ("DELETE", f"{base}/refs/heads/raced"): (404, {"message": "Not Found"}),
            ("POST", f"{base}/refs"): (403, {"message": "no"}),
        }
    )
    assert adapter.branch_head(REPO, "issue/1-x") == A
    assert adapter.branch_head(REPO, "gone") is None
    assert adapter.tag_head(REPO, "archive/x") is None  # not an exact match
    with pytest.raises(PermissionError):
        adapter.delete_branch(REPO, "denied")
    adapter.delete_branch(REPO, "already")  # 422 is left to the read-back
    adapter.delete_branch(REPO, "raced")  # 404 (deleted concurrently) is not a credential issue
    with pytest.raises(PermissionError):
        adapter.create_tag(REPO, "archive/y", A)
    assert ("DELETE", f"{base}/refs/heads/denied", None) in calls


def test_open_change_refs_pages_the_listing_and_includes_bases() -> None:
    pulls = f"/repos/{REPO}/pulls?state=open&per_page=100"
    page1 = [{"head": {"ref": f"h{n}"}, "base": {"ref": "main"}} for n in range(100)]
    page2 = [{"head": {"ref": "top"}, "base": {"ref": "stack"}}]
    adapter, calls = _github(
        {("GET", f"{pulls}&page=1"): (200, page1), ("GET", f"{pulls}&page=2"): (200, page2)}
    )
    refs = adapter.open_change_refs(REPO)
    assert {"h0", "h99", "top", "stack", "main"} <= refs
    assert [c[1] for c in calls] == [f"{pulls}&page=1", f"{pulls}&page=2"]  # no per-change reads


def test_ledger_drift_reports_only_unledgered_idle_branches_and_writes_nothing() -> None:
    from hyper_agents.application.repo_maintenance import ledger_drift

    forge = FakeForge(
        branches={"main": A, "old": A, "stale": B, "live": C},
        open=[Change(number=1, body="", head_ref="live", base_ref="main")],
    )
    report = ledger_drift(forge, REPO, (LedgerBranch("old", A),))
    assert report == {"repo": REPO, "unledgered": ["stale"], "count": 1}
    assert forge.writes == []
    assert "repo.ledger_drift" in MAINTAIN_OPS
    assert load_registry(ROOT / "ops/registry.yaml")["repo.ledger_drift"].tier == "R0"
