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
from hyper_agents.domain.manual_blocker import FailureClass, dedupe
from hyper_agents.domain.owner_actions import (
    BlockerSpec,
    OwnerAction,
    Resolver,
    Status,
    blocker_record,
)
from hyper_agents.ops.adapters import ASSIGNABLE_ROLES
from hyper_agents.ops.fleet import Fleet, RepoAdapter
from hyper_agents.ops.lifeos_tokens import RENEW_WITHIN, unverified_expiry


@dataclass(frozen=True)
class TrackerConfig:
    repo: str
    broker_workflow: str
    broker_ref: str
    settings_apply_op: str
    expiry_warning_days: int
    actions: tuple[OwnerAction, ...]


def _blocker_spec(raw: object) -> BlockerSpec | None:
    """Parse a ``blocker:`` mapping strictly: every value a non-empty string, ``attempts`` a
    list of them. Unknown or missing keys fail in the constructor."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("blocker must be a mapping")
    spec = cast(dict[str, Any], raw)
    attempts = spec.get("attempts")
    if not isinstance(attempts, list) or not attempts:
        raise ValueError("blocker.attempts must be a non-empty list")
    values: dict[str, str] = {}
    for key, value in [*spec.items(), *(("attempt", a) for a in cast(list[Any], attempts))]:
        if key == "attempts":
            continue
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"blocker.{key} must be a non-empty string")
        if key != "attempt":
            values[key] = value.strip()
    failure_class = FailureClass(values.pop("failure_class", ""))
    return BlockerSpec(
        attempts=tuple(str(a).strip() for a in cast(list[str], attempts)),
        failure_class=failure_class,
        **values,  # pyright: ignore[reportArgumentType]
    )


def _resolver(raw: object, known_ops: Mapping[str, Any] | None) -> Resolver | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("resolver must be a mapping")
    spec = cast(dict[str, Any], raw)
    unknown = set(spec) - {"op", "max_attempts"}
    if unknown:
        raise ValueError(f"resolver has unknown keys {sorted(unknown)}")
    op = spec.get("op")
    if not isinstance(op, str) or not op:
        raise ValueError("resolver.op must be a registered op id")
    if known_ops is not None:
        registered = known_ops.get(op)
        if registered is None:
            raise ValueError(f"resolver.op {op!r} is not in the op registry")
        # maintain dispatches resolvers unattended: R0/R1, or R2 in the reviewer-free ops-r2
        # environment. R3, or an R2 behind any other gate, would hang waiting for a person.
        tier, gate = getattr(registered, "tier", ""), getattr(registered, "gate", None)
        if tier == "R3" or (tier == "R2" and gate != "ops-r2"):
            raise ValueError(f"resolver.op {op!r} is {tier}; resolvers must run unattended")
    attempts = spec.get("max_attempts", 2)
    if not isinstance(attempts, int) or isinstance(attempts, bool) or not 1 <= attempts <= 5:
        raise ValueError("resolver.max_attempts must be an integer from 1 to 5")
    return Resolver(op=op, max_attempts=attempts)


def load_owner_actions(path: Path, known_ops: Mapping[str, Any] | None = None) -> TrackerConfig:
    """Load the checklist. With ``known_ops`` (the op registry), a resolver naming an
    unregistered op, or one that cannot run unattended, fails the load."""
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
                resolver=_resolver(entry.get("resolver"), known_ops),
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

    def _controller_tokens_fresh(self, action: OwnerAction) -> Status:
        """Each controller token is in the vault with more than the renewal margin left.
        Reads ``exp`` without verifying (the check holds no key); the renew op verifies."""
        params = dict(action.check_params)
        path = params["path"]
        problems: list[str] = []
        soonest: datetime | None = None
        for name in params["names"].split(","):
            name = name.strip()
            try:
                expires = unverified_expiry(self._secrets.get(path, name))
            except LookupError:
                problems.append(f"{name} missing")
                continue
            if expires is None:
                problems.append(f"{name} unreadable")
            elif expires - self._now() <= RENEW_WITHIN:
                problems.append(f"{name} expires {expires:%Y-%m-%d}")
            else:
                soonest = expires if soonest is None else min(soonest, expires)
        if problems:
            return Status(False, f"at {path}: {', '.join(problems)}")
        when = f"; next renewal before {soonest - RENEW_WITHIN:%Y-%m-%d}" if soonest else ""
        return Status(True, f"stored at {path} and valid{when}")

    def _identities_least_privilege(self, action: OwnerAction) -> Status:
        """No workload identity may hold the project admin role. Each admin found becomes one
        fix (identity, replacement role) for the action's resolver op."""
        replacement = dict(action.check_params).get("replacement", "member")
        if replacement not in ASSIGNABLE_ROLES:  # a config typo must not spend attempts
            raise ValueError(f"replacement {replacement!r} is not an assignable role")
        rows = cast(list[dict[str, Any]], self._secrets.identity_roles())
        admins = [row for row in rows if "admin" in row["roles"]]
        if not admins:
            return Status(True, f"no workload identity holds admin ({len(rows)} checked)")
        names = ", ".join(sorted(row["name"] for row in admins))
        return Status(
            False,
            f"admin held by {names}; target role {replacement}",
            fixes=tuple((("identity", str(row["id"])), ("role", replacement)) for row in admins),
        )

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        if action != "status":
            raise ValueError(f"unknown owner action: {action}")
        checks: dict[str, tracker.Check] = {
            "forge_access": self._forge_access,
            "settings_clean": self._settings_clean,
            "secrets_present": self._secrets_present,
            "branches_pruned": self._branches_pruned,
            "identities_least_privilege": self._identities_least_privilege,
            "controller_tokens_fresh": self._controller_tokens_fresh,
        }
        report = tracker.actions_status(
            self._forge,
            self._config.repo,
            self._config.actions,
            checks,
            now=self._now(),
            apply=True,
            workflow=self._config.broker_workflow,
            ref=self._config.broker_ref,
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
