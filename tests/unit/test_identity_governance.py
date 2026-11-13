"""Infisical identity governance (MB-009): report roles, never grant admin, fix by resolver."""

import json
import urllib.error
from collections.abc import Mapping
from email.message import Message
from pathlib import Path
from typing import Any, cast

import pytest

from hyper_agents.adapters.fake_forge import FakeForge
from hyper_agents.ops.adapters import InfisicalAdapter
from hyper_agents.ops.core import OpRefused, run_op
from hyper_agents.ops.fleet import load_fleet
from hyper_agents.ops.owner import OwnerAdapter, load_owner_actions
from hyper_agents.ops.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
ADMIN = "813dda9e-a986-4448-aa6f-f46e0365deca"
VIEWER = "3801b514-b70d-45cb-99d2-221f9a9ceb2c"
DEPLOY = "00000000-0000-4000-8000-000000000003"
BROKER = ADMIN
SIGNING_PATH = "/platform/lifeos-signing"


class Server:
    """Fake Infisical identity-membership API: paged list, single read, PATCH."""

    def __init__(self, ignore_patch: bool = False, lose_read_on_patch: bool = False) -> None:
        self.roles: dict[str, list[dict[str, Any]]] = {
            ADMIN: [{"role": "admin"}],
            VIEWER: [{"role": "viewer"}],
        }
        self.names = {ADMIN: "ai-coding-harness", VIEWER: "dev-agent"}
        self.ignore_patch = ignore_patch
        self.lose_read_on_patch = lose_read_on_patch
        self.patched = False
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(
        self, method: str, url: str, token: str, body: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        self.calls.append((method, url, body))
        path = url.split("?")[0]
        tail = path.rsplit("/", 1)[1]
        if method == "PATCH":
            self.patched = True
            if not self.ignore_patch:
                self.roles[tail] = [{"role": r["role"]} for r in cast(Any, body)["roles"]]
            return {}
        if tail != "identity-memberships":  # single membership read
            if self.lose_read_on_patch and self.patched:
                raise urllib.error.HTTPError(url, 403, "forbidden", Message(), None)
            if tail not in self.roles:
                raise urllib.error.HTTPError(url, 404, "not found", Message(), None)
            return {"identityMembership": {"identityId": tail, "roles": self.roles[tail]}}
        return {
            "totalCount": len(self.roles),
            "identityMemberships": [
                {"identityId": i, "identity": {"id": i, "name": self.names[i]}, "roles": roles}
                for i, roles in self.roles.items()
            ],
        }


def adapter(server: Server) -> InfisicalAdapter:
    infisical = InfisicalAdapter(project_id="proj", send=server)
    infisical._token = "tok"  # pyright: ignore[reportPrivateUsage]  # noqa: S105
    return infisical


def test_report_lists_names_ids_and_roles_and_flags_admins() -> None:
    result = adapter(Server()).execute("access_report", {})
    assert result["admins"] == ["ai-coding-harness"]
    assert {r["name"]: r["roles"] for r in result["identities"]} == {
        "ai-coding-harness": ["admin"],
        "dev-agent": ["viewer"],
    }


def test_set_role_demotes_an_admin_to_one_builtin_role_and_reads_it_back() -> None:
    server = Server()
    result = adapter(server).execute("set_identity_role", {"identity": ADMIN, "role": "member"})
    assert result == {"identity": ADMIN, "before": ["admin"], "after": ["member"]}
    patch = [c for c in server.calls if c[0] == "PATCH"]
    assert patch == [
        (
            "PATCH",
            f"https://app.infisical.com/api/v1/projects/proj/identity-memberships/{ADMIN}",
            {"roles": [{"role": "member", "isTemporary": False}]},
        )
    ]


def test_it_only_ever_removes_admin_and_never_grants_it() -> None:
    server = Server()
    with pytest.raises(ValueError, match="refusing role 'admin'"):
        adapter(server).set_identity_role(ADMIN, "admin")
    with pytest.raises(PermissionError, match="does not hold admin"):
        adapter(server).set_identity_role(VIEWER, "no-access")  # cannot lock out a viewer
    assert not [c for c in server.calls if c[0] == "PATCH"]
    with pytest.raises(LookupError):
        adapter(Server()).set_identity_role("00000000-0000-0000-0000-000000000000", "member")


def test_a_silent_failure_is_caught_but_a_self_demotion_that_loses_read_is_unverified() -> None:
    with pytest.raises(RuntimeError, match="read-back"):
        adapter(Server(ignore_patch=True)).set_identity_role(ADMIN, "member")
    result = adapter(Server(lose_read_on_patch=True)).set_identity_role(ADMIN, "member")
    assert result["after"] == "unverified (lost read)"


def test_expired_temporary_admin_does_not_count_and_missing_ids_are_skipped() -> None:
    server = Server()
    server.roles[VIEWER] = [
        {"role": "viewer"},
        {"role": "admin", "isTemporary": True, "temporaryAccessEndTime": "2020-01-01T00:00:00Z"},
    ]
    rows = adapter(server).identity_roles()
    assert {r["name"]: r["roles"] for r in rows}["dev-agent"] == ["viewer"]


def test_the_registry_gates_the_write_and_validates_params() -> None:
    registry = load_registry(ROOT / "ops/registry.yaml")
    op = registry["infisical.identity.set_role"]
    assert (op.tier, op.gate) == ("R2", "ops-r2")
    assert registry["infisical.access.report"].tier == "R0"
    for params in ({"identity": ADMIN, "role": "admin"}, {"identity": "x", "role": "member"}):
        with pytest.raises(OpRefused):
            run_op(
                registry,
                "infisical.identity.set_role",
                params,
                mode="dry-run",
                gate=None,
                adapters={},
                actor="t",
                run_id="r",
            )


def test_the_check_turns_each_admin_into_one_resolver_fix_and_maintain_dispatches_it() -> None:
    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    config = load_owner_actions(
        ROOT / "ops/owner_actions.yaml", load_registry(ROOT / "ops/registry.yaml")
    )
    action = next(a for a in config.actions if a.id == "least-privilege-identities")
    assert action.resolver is not None and action.resolver.op == "infisical.identity.set_role"
    fake = FakeForge()
    owner = OwnerAdapter(fleet, config, adapter(Server()), forge=fake)
    status = owner._identities_least_privilege(action)  # pyright: ignore[reportPrivateUsage]
    assert not status.done and "ai-coding-harness" in status.detail
    assert status.fixes == ((("identity", ADMIN), ("role", "member")),)
    report = owner.execute("status", {})
    dispatched = [cast(dict[str, str], w[1]) for w in fake.writes if w[0] == "dispatch"]
    assert {
        "op": "infisical.identity.set_role",
        "params": json.dumps({"identity": ADMIN, "role": "member"}),
        "mode": "apply",
    } in dispatched
    assert "least-privilege-identities" in report["pending"]  # watch, not done, until read back


class FolderServer(Server):
    """Fake native folder-access API. It carries metadata only, never a secret value."""

    def __init__(self) -> None:
        super().__init__()
        self.roles = {
            BROKER: [{"role": "member"}],
            VIEWER: [{"role": "viewer"}],
            DEPLOY: [{"role": "viewer"}],
        }
        self.names = {
            BROKER: "ai-coding-harness",
            VIEWER: "dev-agent",
            DEPLOY: "lifeos-deploy",
        }
        self.grants: dict[str, dict[str, Any]] = {
            DEPLOY: {
                "id": "10000000-0000-4000-8000-000000000003",
                "identityId": DEPLOY,
                "environment": "prod",
                "secretPath": SIGNING_PATH,
                "permission": "read",
                "isTemporary": False,
            }
        }

    def __call__(
        self, method: str, url: str, token: str, body: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        self.calls.append((method, url, body))
        clean = url.split("?", 1)[0]
        if clean.endswith("/identity-memberships"):
            return {
                "totalCount": len(self.roles),
                "identityMemberships": [
                    {
                        "identityId": identity,
                        "identity": {"id": identity, "name": self.names[identity]},
                        "roles": roles,
                    }
                    for identity, roles in self.roles.items()
                ],
            }
        if "/secret-folder-access" in clean:
            identity = clean.split("/memberships/identities/", 1)[1].split("/", 1)[0]
            if method == "GET":
                grant = self.grants.get(identity)
                return {"folderAccess": [grant] if grant else []}
            assert body is not None
            current = self.grants.get(identity)
            grant_id = (current or {}).get("id", f"20000000-0000-4000-8000-{identity[-12:]}")
            self.grants[identity] = {
                "id": grant_id,
                "identityId": identity,
                "environment": body["environmentSlug"],
                "secretPath": body["secretPath"],
                "permission": body["permission"],
                "isTemporary": body.get("type", {}).get("isTemporary", False),
            }
            return {"folderAccess": self.grants[identity]}
        return super().__call__(method, url, token, body)


def test_signing_folder_report_flags_missing_or_value_read_access_and_exempts_broker() -> None:
    result = adapter(FolderServer()).execute("signing_folder_access_report", {})
    assert result == {
        "path": SIGNING_PATH,
        "broker": "ai-coding-harness",
        "protected": [],
        "drift": [
            {"identity": VIEWER, "name": "dev-agent", "permission": "missing"},
            {"identity": DEPLOY, "name": "lifeos-deploy", "permission": "read"},
        ],
    }


def test_restrict_signing_folder_creates_updates_and_is_idempotent() -> None:
    server = FolderServer()
    infisical = adapter(server)
    created = infisical.execute(
        "restrict_signing_folder", {"identity": VIEWER, "permission": "list"}
    )
    assert created["action"] == "created"
    assert created["permission"] == "list" and created["path"] == SIGNING_PATH
    updated = infisical.execute(
        "restrict_signing_folder", {"identity": DEPLOY, "permission": "list"}
    )
    assert updated["action"] == "updated"
    unchanged = infisical.execute(
        "restrict_signing_folder", {"identity": DEPLOY, "permission": "list"}
    )
    assert unchanged["action"] == "unchanged"
    writes = [call for call in server.calls if call[0] in ("POST", "PATCH")]
    assert [call[0] for call in writes] == ["POST", "PATCH"]
    assert all(
        call[2]
        == {
            "environmentSlug": "prod",
            "secretPath": SIGNING_PATH,
            "permission": "list",
            "type": {"isTemporary": False},
        }
        for call in writes
    )


def test_signing_folder_op_refuses_broker_or_any_role_that_reads_values() -> None:
    infisical = adapter(FolderServer())
    with pytest.raises(PermissionError, match="broker"):
        infisical.execute("restrict_signing_folder", {"identity": BROKER, "permission": "list"})
    with pytest.raises(ValueError, match="only 'list'"):
        infisical.execute("restrict_signing_folder", {"identity": VIEWER, "permission": "read"})


def test_folder_boundary_is_registered_r0_r2_and_owner_resolves_each_drift() -> None:
    registry = load_registry(ROOT / "ops/registry.yaml")
    assert registry["infisical.signing_folder.report"].tier == "R0"
    op = registry["infisical.signing_folder.restrict"]
    assert (op.tier, op.gate) == ("R2", "ops-r2")
    with pytest.raises(OpRefused):
        run_op(
            registry,
            op.id,
            {"identity": VIEWER, "permission": "read"},
            mode="dry-run",
            gate=None,
            adapters={},
            actor="t",
            run_id="r",
        )

    fleet = load_fleet(ROOT / "ops/fleet.yaml")
    config = load_owner_actions(ROOT / "ops/owner_actions.yaml", registry)
    action = next(a for a in config.actions if a.id == "signing-folder-boundary")
    assert action.resolver is not None and action.resolver.op == op.id
    fake = FakeForge()
    owner = OwnerAdapter(fleet, config, adapter(FolderServer()), forge=fake)
    status = owner._signing_folder_boundary(action)  # pyright: ignore[reportPrivateUsage]
    assert not status.done
    assert status.fixes == (
        (("identity", VIEWER), ("permission", "list")),
        (("identity", DEPLOY), ("permission", "list")),
    )
    owner.execute("status", {})
    dispatched = [cast(dict[str, str], w[1]) for w in fake.writes if w[0] == "dispatch"]
    assert {
        "op": op.id,
        "params": json.dumps({"identity": VIEWER, "permission": "list"}),
        "mode": "apply",
    } in dispatched
