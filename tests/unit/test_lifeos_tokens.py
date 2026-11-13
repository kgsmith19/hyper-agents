"""LifeOS controller tokens (MB-004, #82): keys created once, never rotated; tokens minted
in the pinned contract format, renewed near expiry, values never in receipts."""

import base64
import json
import urllib.error
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from email.message import Message
from pathlib import Path
from typing import Any

import jwt
import pytest

from hyper_agents.ops.adapters import InfisicalAdapter
from hyper_agents.ops.lifeos_tokens import (
    CONTROLLERS,
    PRIVATE_KEY,
    PUBLIC_KEY,
    TOKENS_PATH,
    LifeosAdapter,
    keygen,
    mint,
    token_state,
    unverified_expiry,
)
from hyper_agents.ops.owner import load_owner_actions
from hyper_agents.ops.registry import load_registry

ROOT = Path(__file__).resolve().parents[2]
NOW = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)


class MemoryVault:
    def __init__(self, held: dict[tuple[str, str], str] | None = None) -> None:
        self.held = dict(held or {})
        self.writes: list[tuple[str, str]] = []

    def get(self, path: str, name: str) -> str:
        if (path, name) not in self.held:
            raise LookupError(name)
        return self.held[(path, name)]

    def upsert_secret(self, path: str, name: str, value: str) -> None:
        self.held[(path, name)] = value
        self.writes.append((path, name))


def renew(vault: MemoryVault, at: datetime = NOW) -> dict[str, Any]:
    return LifeosAdapter(vault, clock=lambda: at).execute("renew", {})


def test_first_run_creates_the_key_pair_and_both_tokens_and_leaks_no_value() -> None:
    vault = MemoryVault()
    receipt = renew(vault)
    assert receipt["keys"] == "created"
    assert {n: t["action"] for n, t in receipt["tokens"].items()} == {
        "CLAUDE_CODE_TOKEN": "minted (invalid)",
        "CHATGPT_TOKEN": "minted (invalid)",
    }
    assert vault.writes[:2] == [PUBLIC_KEY, PRIVATE_KEY]  # public first
    dumped = json.dumps(receipt)
    assert all(value not in dumped for value in vault.held.values())


def test_a_second_run_changes_nothing_and_near_expiry_renews() -> None:
    vault = MemoryVault()
    renew(vault)
    writes = len(vault.writes)
    receipt = renew(vault, NOW + timedelta(days=20))
    assert receipt["keys"] == "existing" and len(vault.writes) == writes
    assert {t["action"] for t in receipt["tokens"].values()} == {"fresh"}
    receipt = renew(vault, NOW + timedelta(days=24))  # 6 days left: renew
    assert {t["action"] for t in receipt["tokens"].values()} == {"minted (expiring)"}
    receipt = renew(vault, NOW + timedelta(days=60))  # long expired: renew too
    assert {t["action"] for t in receipt["tokens"].values()} == {"minted (expiring)"}
    assert vault.writes[-2:] == [(TOKENS_PATH, "CLAUDE_CODE_TOKEN"), (TOKENS_PATH, "CHATGPT_TOKEN")]


def test_half_a_key_pair_or_a_mismatched_pair_is_refused_and_nothing_is_written() -> None:
    private, public = keygen()
    for held in ({PRIVATE_KEY: private}, {PUBLIC_KEY: public}):
        vault = MemoryVault(held)
        with pytest.raises(PermissionError, match="refusing to rotate"):
            renew(vault)
        assert vault.writes == []
    other_private, _ = keygen()
    vault = MemoryVault({PRIVATE_KEY: other_private, PUBLIC_KEY: public})
    with pytest.raises(PermissionError, match="does not match"):
        renew(vault)
    assert vault.writes == []


def test_a_token_with_the_wrong_roles_or_key_is_replaced() -> None:
    vault = MemoryVault()
    renew(vault)
    private = vault.held[PRIVATE_KEY]
    vault.held[(TOKENS_PATH, "CHATGPT_TOKEN")] = mint(
        private, "chatgpt", ("financial_operations",), NOW
    )
    receipt = renew(vault)
    assert receipt["tokens"]["CHATGPT_TOKEN"]["action"] == "minted (invalid)"
    assert receipt["tokens"]["CLAUDE_CODE_TOKEN"]["action"] == "fresh"


def test_tokens_match_the_hyperbolic_core_contract_exactly() -> None:
    private, public = keygen()
    agent, roles = CONTROLLERS["CLAUDE_CODE_TOKEN"]
    token = mint(private, agent, roles, NOW)
    pem = base64.b64decode(public).decode()
    claims = jwt.decode(
        token, pem, algorithms=["ES256"], audience="lifeos-mcp", options={"verify_exp": False}
    )
    assert jwt.get_unverified_header(token)["alg"] == "ES256"
    assert {k: v for k, v in claims.items() if k not in ("iat", "exp")} == {
        "iss": "lifeos",
        "aud": "lifeos-mcp",
        "sub": "agent:claude-code",
        "scopes": ["agents:request"],
        "roles": ["financial_operations", "reliability_engineer"],
    }
    assert claims["exp"] - claims["iat"] == 30 * 24 * 3600
    assert token_state(token, public, agent, roles, NOW) == ("fresh", NOW + timedelta(days=30))
    assert unverified_expiry(token) == NOW + timedelta(days=30)
    assert unverified_expiry("not-a-jwt") is None
    huge = jwt.encode({"exp": 10**20}, "k" * 32, algorithm="HS256")
    assert unverified_expiry(huge) is None  # overflow is unreadable, never a crash
    assert token_state(token, public, "chatgpt", roles, NOW)[0] == "invalid"


class Server:
    def __init__(self, patch_status: int | None = None, folder_status: int = 400) -> None:
        self.patch_status = patch_status
        self.folder_status = folder_status
        self.calls: list[tuple[str, str, Any]] = []

    def __call__(
        self, method: str, url: str, token: str, body: Mapping[str, Any] | None
    ) -> dict[str, Any]:
        self.calls.append((method, url, body))
        if method == "POST" and url.endswith("/v1/folders") and body and body["name"] == "platform":
            raise urllib.error.HTTPError(url, self.folder_status, "x", Message(), None)
        if method == "PATCH" and self.patch_status:
            raise urllib.error.HTTPError(url, self.patch_status, "x", Message(), None)
        return {}


def infisical_with(server: Server) -> InfisicalAdapter:
    infisical = InfisicalAdapter(project_id="proj", send=server)
    infisical._token = "tok"  # pyright: ignore[reportPrivateUsage]  # noqa: S105
    return infisical


def test_upsert_patches_first_and_creates_folders_only_when_absent() -> None:
    server = Server()
    infisical_with(server).upsert_secret("/lifeos/agents", "CHATGPT_TOKEN", "value")
    assert [c[0] for c in server.calls] == ["PATCH"]  # the common case: one call
    server = Server(patch_status=404)
    infisical = infisical_with(server)
    infisical._cache["/platform/lifeos-signing"] = (0.0, [])  # pyright: ignore[reportPrivateUsage]
    infisical.upsert_secret("/platform/lifeos-signing", "LIFEOS_AGENT_JWT_PRIVATE_KEY", "v")
    folders = [c[2]["name"] for c in server.calls if c[1].endswith("/v1/folders")]
    assert folders == ["platform", "lifeos-signing"]  # an existing folder is fine
    assert [c[0] for c in server.calls if "secrets/raw" in c[1]] == ["PATCH", "POST"]
    assert "/platform/lifeos-signing" not in infisical._cache  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(ValueError, match="empty"):
        infisical.upsert_secret("/lifeos/agents", "X_TOKEN", "")


def test_upsert_reports_the_real_failure() -> None:
    with pytest.raises(RuntimeError, match=r"update .*\(HTTP 429\)"):
        infisical_with(Server(patch_status=429)).upsert_secret("/lifeos/agents", "A", "v")
    with pytest.raises(RuntimeError, match=r"folder platform \(HTTP 403\)"):
        infisical_with(Server(patch_status=404, folder_status=403)).upsert_secret(
            "/platform/lifeos-signing", "A", "v"
        )


def test_the_signing_key_is_kept_apart_from_the_tokens() -> None:
    assert PRIVATE_KEY[0] != TOKENS_PATH and PRIVATE_KEY[0].startswith("/platform/")


def test_the_op_is_r2_and_the_owner_action_resolves_through_it() -> None:
    registry = load_registry(ROOT / "ops/registry.yaml")
    op = registry["lifeos.controller_tokens.renew"]
    assert (op.adapter, op.tier, op.gate) == ("lifeos", "R2", "ops-r2")
    config = load_owner_actions(ROOT / "ops/owner_actions.yaml", registry)
    action = next(a for a in config.actions if a.id == "controller-tokens")
    assert action.check == "controller_tokens_fresh"
    assert action.resolver is not None and action.resolver.op == op.id
