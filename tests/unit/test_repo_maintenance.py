"""Repository maintenance through the provider-neutral ForgePort, against an in-memory forge."""

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

from hyper_agents.adapters.fake_forge import FakeForge
from hyper_agents.application.repo_maintenance import (
    HEALTH_MARKER,
    NEEDS_UPDATE,
    apply_settings,
    change_health,
    close_sweep,
    drift,
)
from hyper_agents.domain.forge import Change, Item, closing_items, settings_drift

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 12, 20, 0, tzinfo=UTC)
REPO = "owner/repo"


def merged(number: int, body: str, hours_ago: int = 1) -> Change:
    return Change(
        number=number, body=body, head_ref="x", merged_at=NOW - timedelta(hours=hours_ago)
    )


def test_closing_items_reads_only_closing_lines_in_this_repo() -> None:
    """Prose mentions and other repositories never close anything here."""
    body = "Closes #3\nfixes owner/repo#4\nresolves other/repo#5\nsee #6\ncloses #3"
    assert closing_items(body, REPO) == [3, 4]
    assert closing_items("", REPO) == []


def test_sweep_closes_open_items_named_by_recent_merges() -> None:
    forge = FakeForge(merged=[merged(10, "Closes #3")], items={3: Item(3, open=True)})
    report = close_sweep(forge, REPO, now=NOW, apply=True)
    assert report["closed"] == ["#3 (change #10)"]
    assert forge.writes == [("close", 3)]


def test_sweep_dry_run_writes_nothing() -> None:
    forge = FakeForge(merged=[merged(10, "Closes #3")], items={3: Item(3, open=True)})
    report = close_sweep(forge, REPO, now=NOW, apply=False)
    assert report["closed"] == ["#3 (change #10)"]
    assert forge.writes == []


def test_sweep_is_idempotent_and_skips_closed_missing_and_old() -> None:
    """A repeat run, a closed or unknown item, or a merge outside the window: no-op."""
    forge = FakeForge(
        merged=[merged(10, "Closes #3\nCloses #4\nCloses #9"), merged(11, "Closes #5", 24 * 30)],
        items={3: Item(3, open=True), 4: Item(4, open=False), 5: Item(5, open=True)},
    )
    close_sweep(forge, REPO, now=NOW, apply=True)
    assert forge.writes == [("close", 3)]
    close_sweep(forge, REPO, now=NOW, apply=True)
    assert forge.writes == [("close", 3)]


def test_sweep_respects_a_human_reopen_after_the_merge() -> None:
    forge = FakeForge(
        merged=[merged(10, "Closes #3", 5)],
        items={3: Item(3, open=True)},
        reopened={3: NOW - timedelta(hours=1)},
    )
    report = close_sweep(forge, REPO, now=NOW, apply=True)
    assert forge.writes == []
    assert report["skipped"] == ["#3 (reopened after change #10 merged)"]


def test_health_flags_conflicts_once_and_clears_when_fixed() -> None:
    """A conflicted change is labelled and noted; the label never duplicates and clears later."""
    forge = FakeForge(open=[Change(7, "", "x", conflicted=True)])
    change_health(forge, REPO, apply=True)
    assert forge.writes == [("label", 7), ("note", 7)]
    assert NEEDS_UPDATE in forge.open[0].labels
    change_health(forge, REPO, apply=True)
    assert forge.writes.count(("label", 7)) == 1
    forge.open = [Change(7, "", "x", labels=(NEEDS_UPDATE,))]
    report = change_health(forge, REPO, apply=True)
    assert report["cleared"] == [7]
    assert forge.writes[-1] == ("unlabel", 7)
    assert HEALTH_MARKER.startswith("<!--")


def test_health_updates_a_branch_that_is_only_behind() -> None:
    forge = FakeForge(open=[Change(8, "", "x", behind=True), Change(9, "", "x")])
    report = change_health(forge, REPO, apply=True)
    assert report["branch_updated"] == [8]
    assert forge.writes == [("update_branch", 8)]


def test_health_dry_run_writes_nothing() -> None:
    forge = FakeForge(open=[Change(7, "", "x", conflicted=True), Change(8, "", "x", behind=True)])
    report = change_health(forge, REPO, apply=False)
    assert report["conflicted"] == [7] and report["branch_updated"] == [8]
    assert forge.writes == []


def test_settings_drift_and_apply_change_only_differing_fields() -> None:
    desired: dict[str, object] = {"delete_branch_on_merge": True, "allow_auto_merge": True}
    forge = FakeForge(live_settings={"delete_branch_on_merge": False, "allow_auto_merge": True})
    assert drift(forge, REPO, desired)["drift"] == {"delete_branch_on_merge": True}
    assert apply_settings(forge, REPO, desired, apply=False)["applied"] is False
    assert forge.writes == []
    report = apply_settings(forge, REPO, desired, apply=True)
    assert report["changed"] == ["delete_branch_on_merge"]
    assert forge.writes == [("settings", {"delete_branch_on_merge": True})]
    assert apply_settings(forge, REPO, desired, apply=True)["applied"] is False
    assert settings_drift({}, {"a": 1}) == {"a": 1}


def test_application_and_domain_never_name_a_hosting_provider() -> None:
    """Provider names live only in adapters and in the one fleet config."""
    for path in (
        ROOT / "src/hyper_agents/application/repo_maintenance.py",
        ROOT / "src/hyper_agents/domain/forge.py",
    ):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        text = " ".join(
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant) and isinstance(node.value, str)
        ).lower()
        names = " ".join(
            alias.name
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom | ast.Import)
            for alias in node.names
        )
        for provider in ("github", "gitlab", "forgejo", "bitbucket"):
            assert provider not in text, (path.name, provider)
            assert provider not in names.lower(), (path.name, provider)
