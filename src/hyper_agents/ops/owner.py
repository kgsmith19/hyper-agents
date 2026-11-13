"""The ``owner`` broker adapter: keep the owner-actions checklist current.

Loads ``ops/owner_actions.yaml`` and supplies the live checks it names. The checks reach the
forge and the secrets store only through the fleet's ports, so no provider is named here.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import yaml

from hyper_agents.application import owner_actions as tracker
from hyper_agents.application import repo_maintenance as maintenance
from hyper_agents.application.ports import ForgePort
from hyper_agents.domain.forge import Access
from hyper_agents.domain.manual_blocker import dedupe
from hyper_agents.domain.owner_actions import BlockerSpec, OwnerAction, Status, blocker_record
from hyper_agents.ops.fleet import Fleet, RepoAdapter


@dataclass(frozen=True)
class TrackerConfig:
    repo: str
    broker_workflow: str
    broker_ref: str
    settings_apply_op: str
    expiry_warning_days: int
    actions: tuple[OwnerAction, ...]


def _blocker_spec(raw: object) -> BlockerSpec | None:
    if raw is None:
        return None
    spec = cast(dict[str, Any], raw)
    text = {k: str(v).strip() for k, v in spec.items() if k != "attempts"}
    return BlockerSpec(
        attempts=tuple(str(a).strip() for a in cast(list[Any], spec.get("attempts") or [])),
        **text,  # unknown or missing keys fail here
    )


def load_owner_actions(path: Path) -> TrackerConfig:
    raw = cast(dict[str, Any], yaml.safe_load(path.read_text(encoding="utf-8")))
    head = cast(dict[str, Any], raw["tracker"])
    actions: list[OwnerAction] = []
    seen: set[str] = set()
    for entry in cast(list[dict[str, Any]], raw["actions"]):
        action_id = str(entry["id"])
        if action_id in seen:
            raise ValueError(f"duplicate owner action id {action_id!r}")
        seen.add(action_id)
        params = cast(dict[str, Any], entry.get("params") or {})
        actions.append(
            OwnerAction(
                id=action_id,
                title=str(entry["title"]),
                why=str(entry["why"]).strip(),
                steps=tuple(str(step).strip() for step in entry["steps"]),
                check=str(entry["check"]),
                check_params=tuple(sorted((str(k), str(v)) for k, v in params.items())),
                blocker=_blocker_spec(entry.get("blocker")),
            )
        )
    # Validate every contract now (a typo fails the load, not a maintenance run) and refuse
    # two actions that are the same blocker.
    probe = Status(False, "loaded")
    dedupe(blocker_record(a, probe, datetime.now(UTC)) for a in actions if a.blocker)
    return TrackerConfig(
        repo=str(head["repo"]),
        broker_workflow=str(head["broker_workflow"]),
        broker_ref=str(head["broker_ref"]),
        settings_apply_op=str(head["settings_apply_op"]),
        expiry_warning_days=int(head.get("expiry_warning_days", 14)),
        actions=tuple(actions),
    )


class OwnerAdapter:
    def __init__(
        self,
        fleet: Fleet,
        config: TrackerConfig,
        secrets: Any,
        forge: ForgePort | None = None,
        clock: Callable[[], datetime] | None = None,
        repos: RepoAdapter | None = None,
    ) -> None:
        self._fleet = fleet
        self._config = config
        self._secrets = secrets
        # Sharing maintain's RepoAdapter lets checks reuse reports it already produced.
        self._repos = repos or RepoAdapter(fleet, secrets, forge=forge, clock=clock)
        self._forge = self._repos.forge
        self._now = clock or (lambda: datetime.now(UTC))
        self._access: dict[str, Access] | None = None

    def _fleet_access(self) -> dict[str, Access]:
        if self._access is None:
            self._access = {repo: self._forge.access(repo) for repo in self._fleet.repos}
        return self._access

    def _forge_access(self, action: OwnerAction) -> Status:
        access = self._fleet_access()
        problems = [
            f"{repo}: missing {', '.join(a.missing())}" for repo, a in access.items() if not a.ok
        ]
        if problems:
            return Status(False, "; ".join(problems))
        expiries = [a.credential_expires_at for a in access.values() if a.credential_expires_at]
        if expiries:
            soonest = min(expiries)
            if soonest - self._now() < timedelta(days=self._config.expiry_warning_days):
                return Status(False, f"the token works but expires {soonest:%Y-%m-%d}; renew it")
            return Status(
                True, f"works in all {len(access)} repositories; expires {soonest:%Y-%m-%d}"
            )
        return Status(True, f"works in all {len(access)} repositories")

    def _drifts(self) -> dict[str, dict[str, object]]:
        desired = self._repos.desired()
        return {
            repo: cast(dict[str, object], maintenance.drift(self._forge, repo, desired)["drift"])
            for repo in self._fleet.repos
        }

    def _settings_clean(self, action: OwnerAction) -> Status:
        if not all(a.settings_readable for a in self._fleet_access().values()):
            return Status(False, "waiting for the ops token (settings are not readable yet)")
        drifting = {repo: drift for repo, drift in self._drifts().items() if drift}
        if drifting:
            parts = [f"{repo}: {', '.join(sorted(d))}" for repo, d in sorted(drifting.items())]
            return Status(False, "drift being applied: " + "; ".join(parts))
        return Status(True, f"settings match the declared profile in all {len(self._fleet.repos)}")

    def _secrets_present(self, action: OwnerAction) -> Status:
        params = dict(action.check_params)
        path = params["path"]
        missing: list[str] = []
        for name in params["names"].split(","):
            try:
                self._secrets.get(path, name.strip())
            except LookupError:
                missing.append(name.strip())
        if missing:
            return Status(False, f"not stored yet at {path}: {', '.join(missing)}")
        return Status(True, f"stored at {path}")

    def _branches_pruned(self, action: OwnerAction) -> Status:
        remaining: list[str] = []
        for repo in self._fleet.repos:
            rows = cast(list[dict[str, str]], self._repos.prune_state(repo)["branches"])
            left = sum(1 for row in rows if row["result"] in maintenance.PRUNE_PENDING)
            if left:
                remaining.append(f"{repo.split('/')[-1]}: {left}")
        if not remaining:
            return Status(True, "every reconciled branch is pruned")
        writable = all(a.refs_writable for a in self._fleet_access().values())
        why = (
            "pruning on the next run"
            if writable
            else "the ops token needs Contents: Read and write"
        )
        return Status(False, f"{why}; left: {', '.join(remaining)}")

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        if action != "status":
            raise ValueError(f"unknown owner action: {action}")
        checks: dict[str, tracker.Check] = {
            "forge_access": self._forge_access,
            "settings_clean": self._settings_clean,
            "secrets_present": self._secrets_present,
            "branches_pruned": self._branches_pruned,
        }
        report = tracker.actions_status(
            self._forge,
            self._config.repo,
            self._config.actions,
            checks,
            now=self._now(),
            apply=True,
        )
        access = self._fleet_access()
        if all(a.settings_readable for a in access.values()) and "item" in report:
            report["settings_apply_requested"] = tracker.request_settings_apply(
                self._forge,
                self._config.repo,
                int(cast(int, report["item"])),
                self._drifts(),
                workflow=self._config.broker_workflow,
                ref=self._config.broker_ref,
                op_id=self._config.settings_apply_op,
                apply=True,
            )
        return report
