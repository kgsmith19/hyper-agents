"""Repository maintenance for the ops broker, configured by ``ops/fleet.yaml``.

``RepoAdapter`` is the broker adapter named ``repo``. It builds the hosting provider's
``ForgePort`` from the fleet config and runs the provider-free use cases in
``application.repo_maintenance``. Adding a provider means one entry in ``FORGES``.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from hyper_agents.adapters.forge_github import GithubForge
from hyper_agents.application import repo_maintenance as maintenance
from hyper_agents.application.ports import ForgePort
from hyper_agents.domain.forge import LedgerBranch
from hyper_agents.ops.queue_history import QueueConfig, load_queue_config

FORGES: dict[str, Callable[[Callable[[], str], str], ForgePort]] = {
    "github": lambda token, api: GithubForge(token=token, api=api),
}


@dataclass(frozen=True)
class Fleet:
    forge_adapter: str
    api: str
    token_path: str
    token_name: str
    profile_repo: str
    profile_ref: str
    profile_path: str
    close_window_days: int
    repos: tuple[str, ...]
    branch_ledger: dict[str, tuple[LedgerBranch, ...]] = field(
        default_factory=lambda: dict[str, tuple[LedgerBranch, ...]]()
    )
    queue: QueueConfig | None = None


def load_branch_ledger(path: Path, repos: tuple[str, ...]) -> dict[str, tuple[LedgerBranch, ...]]:
    """Read the reconciled-branch ledger. Refuses repositories outside the fleet and repeats."""
    raw = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    ledger: dict[str, tuple[LedgerBranch, ...]] = {}
    for repo, entries in cast(dict[str, Any], raw.get("repos") or {}).items():
        if repo not in repos:
            raise ValueError(f"branch ledger names {repo}, which is not in the fleet")
        branches = tuple(
            LedgerBranch(
                branch=str(e["branch"]), sha=str(e["sha"]), archive=bool(e.get("archive", False))
            )
            for e in cast(list[dict[str, Any]], entries or [])
        )
        names = [b.branch for b in branches]
        if len(set(names)) != len(names):
            raise ValueError(f"branch ledger lists a branch twice in {repo}")
        ledger[str(repo)] = branches
    return ledger


def load_fleet(path: Path) -> Fleet:
    raw = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    forge = cast(dict[str, Any], raw["forge"])
    token = cast(dict[str, Any], forge["token"])
    profile = cast(dict[str, Any], raw["settings_profile"])
    repos = tuple(str(repo) for repo in raw["repos"])
    if str(forge["adapter"]) not in FORGES:
        raise ValueError(f"unknown forge adapter {forge['adapter']!r}; known: {sorted(FORGES)}")
    return Fleet(
        forge_adapter=str(forge["adapter"]),
        api=str(forge["api"]),
        token_path=str(token["path"]),
        token_name=str(token["name"]),
        profile_repo=str(profile["repo"]),
        profile_ref=str(profile["ref"]),
        profile_path=str(profile["path"]),
        close_window_days=int(raw.get("close_window_days", 14)),
        repos=repos,
        branch_ledger=(
            load_branch_ledger(path.parent / str(raw["branch_ledger"]), repos)
            if raw.get("branch_ledger")
            else {}
        ),
        queue=load_queue_config(cast(dict[str, Any] | None, raw.get("queue"))),
    )


class RepoAdapter:
    def __init__(
        self,
        fleet: Fleet,
        secrets: Any,
        forge: ForgePort | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._fleet = fleet
        self._token: Callable[[], str] = lambda: str(
            secrets.get(fleet.token_path, fleet.token_name)
        )
        self._forge = forge or FORGES[fleet.forge_adapter](self._token, fleet.api)
        self._now = clock or (lambda: datetime.now(UTC))
        self._pruned: dict[str, dict[str, Any]] = {}

    @property
    def forge(self) -> ForgePort:
        return self._forge

    @property
    def token(self) -> Callable[[], str]:
        """The fleet forge token, read on use; for tools that call the forge themselves."""
        return self._token

    def desired(self) -> dict[str, object]:
        """The declared settings profile, read through the forge from its single source."""
        text = self._forge.read_file(
            self._fleet.profile_repo, self._fleet.profile_ref, self._fleet.profile_path
        )
        return cast(dict[str, object], json.loads(text))

    def branch_prune(self, repo: str, *, apply: bool) -> dict[str, Any]:
        """Run the prune; remember the latest report per repository, failed runs included."""
        ledger = self._fleet.branch_ledger.get(repo, ())
        try:
            report = maintenance.branch_prune(self._forge, repo, ledger, apply=apply)
        except maintenance.PruneFailed as error:
            self._pruned[repo] = cast(dict[str, Any], json.loads(str(error)))
            raise
        self._pruned[repo] = report
        return report

    def prune_state(self, repo: str) -> dict[str, Any]:
        """The latest prune report for ``repo`` (this process), else a fresh dry run."""
        if repo not in self._pruned:
            try:
                self.branch_prune(repo, apply=False)
            except maintenance.PruneFailed:
                pass
        return self._pruned[repo]

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        repo = params["repo"]
        if repo not in self._fleet.repos:
            raise ValueError(f"{repo} is not in the fleet")
        window = self._fleet.close_window_days
        if action == "report":
            return {
                "close_sweep": maintenance.close_sweep(
                    self._forge, repo, now=self._now(), window_days=window, apply=False
                ),
                "change_health": maintenance.change_health(self._forge, repo, apply=False),
                "settings": maintenance.drift(self._forge, repo, self.desired()),
                "branch_prune": self.branch_prune(repo, apply=False),
            }
        if action == "settings_drift":
            return maintenance.drift(self._forge, repo, self.desired())
        if action == "close_sweep":
            return maintenance.close_sweep(
                self._forge, repo, now=self._now(), window_days=window, apply=True
            )
        if action == "change_health":
            return maintenance.change_health(self._forge, repo, apply=True)
        if action == "ledger_drift":
            ledger = self._fleet.branch_ledger.get(repo, ())
            return maintenance.ledger_drift(self._forge, repo, ledger)
        if action == "branch_prune":
            return self.branch_prune(repo, apply=True)
        if action == "settings_apply":
            return maintenance.apply_settings(self._forge, repo, self.desired(), apply=True)
        raise ValueError(f"unknown repo action: {action}")
