"""Repository maintenance for the ops broker, configured by ``ops/fleet.yaml``.

``RepoAdapter`` is the broker adapter named ``repo``. It builds the hosting provider's
``ForgePort`` from the fleet config and runs the provider-free use cases in
``application.repo_maintenance``. Adding a provider means one entry in ``FORGES``.
"""

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import yaml

from hyper_agents.adapters.forge_github import GithubForge
from hyper_agents.application import repo_maintenance as maintenance
from hyper_agents.application.ports import ForgePort

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


def load_fleet(path: Path) -> Fleet:
    raw = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    forge = cast(dict[str, Any], raw["forge"])
    token = cast(dict[str, Any], forge["token"])
    profile = cast(dict[str, Any], raw["settings_profile"])
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
        repos=tuple(str(repo) for repo in raw["repos"]),
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
        self._forge = forge or FORGES[fleet.forge_adapter](
            lambda: str(secrets.get(fleet.token_path, fleet.token_name)), fleet.api
        )
        self._now = clock or (lambda: datetime.now(UTC))

    def _desired(self) -> dict[str, object]:
        text = self._forge.read_file(
            self._fleet.profile_repo, self._fleet.profile_ref, self._fleet.profile_path
        )
        return cast(dict[str, object], json.loads(text))

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
                "settings": maintenance.drift(self._forge, repo, self._desired()),
            }
        if action == "settings_drift":
            return maintenance.drift(self._forge, repo, self._desired())
        if action == "close_sweep":
            return maintenance.close_sweep(
                self._forge, repo, now=self._now(), window_days=window, apply=True
            )
        if action == "change_health":
            return maintenance.change_health(self._forge, repo, apply=True)
        if action == "settings_apply":
            return maintenance.apply_settings(self._forge, repo, self._desired(), apply=True)
        raise ValueError(f"unknown repo action: {action}")
