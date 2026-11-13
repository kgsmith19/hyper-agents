"""Infisical identity governance (MB-009): report roles, never grant admin, fix by resolver."""

import json
from collections.abc import Mapping
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


class Server:
    """Fake Infisical identity-membership API that applies PATCHes."""

    def __init__(self, ignore_patch: bool = False) -> None:
        self.roles = {ADMIN: ["admin"], VIEWER: ["viewer"]}
        self.names = {ADMIN: "ai-coding-harness", VIEWER: "dev-agent"}
        self.ignore_patch = ignore_patch
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(
        self, method: str, url: str, token: str, body: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        self.calls.append((method, url, body))
        if method == "PATCH":
            identity = url.rsplit("/", 1)[1]
            if not self.ignore_patch:
                self.roles[identity] = [r["role"] for r in cast(Any, body)["roles"]]
            return {}
        return {
            "identityMemberships": [
                {
                    "identityId": i,
                    "identity": {"id": i, "name": self.names[i]},
                    "roles": [{"role": r} for r in roles],
                }
                for i, roles in self.roles.items()
            ]
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


def test_set_role_patches_one_builtin_role_and_reads_it_back() -> None:
    server = Server()
    result = adapter(server).execute("set_identity_role", {"identity": ADMIN, "role": "member"})
    assert result == {
        "identity": ADMIN,
        "name": "ai-coding-harness",
        "before": ["admin"],
        "after": ["member"],
    }
    patch = [c for c in server.calls if c[0] == "PATCH"]
    assert patch == [
        (
            "PATCH",
            f"https://app.infisical.com/api/v1/projects/proj/identity-memberships/{ADMIN}",
            {"roles": [{"role": "member", "isTemporary": False}]},
        )
    ]


def test_admin_is_never_granted_and_a_silent_failure_is_caught() -> None:
    server = Server()
    with pytest.raises(ValueError, match="refusing role 'admin'"):
        adapter(server).set_identity_role(VIEWER, "admin")
    assert not [c for c in server.calls if c[0] == "PATCH"]
    with pytest.raises(RuntimeError, match="read-back"):
        adapter(Server(ignore_patch=True)).set_identity_role(ADMIN, "member")
    with pytest.raises(LookupError):
        adapter(Server()).set_identity_role("00000000-0000-0000-0000-000000000000", "member")


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
