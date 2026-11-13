"""LifeOS controller tokens, minted and renewed by the ops broker (MB-004, #82).

The wire format is the contract pinned in hyperbolic-core
``apps/lifeos/backend/tests/api/test_controller_token_contract.py``: an ES256 JWT with
iss="lifeos", aud="lifeos-mcp", sub="agent:<name>", iat, exp, scopes=["agents:request"]
and a sorted, non-empty ``roles`` list. Keys are base64-wrapped PEM (PKCS8 private,
SubjectPublicKeyInfo public).

Vault layout: tokens at ``/lifeos/agents`` (read by the agents), the private signing key
at ``/platform/lifeos-signing`` (broker only: whoever reads it can mint any token), the
public key at ``/platform/lifeos-deploy`` (the LifeOS deploy reads it). A key pair is
created only when neither half exists and is never rotated: a deployed public key must
stay valid. Values
never leave this module except into the vault; receipts carry names, fingerprints and
expiry dates only.
"""

import base64
import binascii
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

import jwt
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)

from hyper_agents.ops.core import fingerprint

ISSUER, AUDIENCE, SCOPE, ALGORITHM = "lifeos", "lifeos-mcp", "agents:request", "ES256"
TOKENS_PATH = "/lifeos/agents"
PRIVATE_KEY: tuple[str, str] = ("/platform/lifeos-signing", "LIFEOS_AGENT_JWT_PRIVATE_KEY")
PUBLIC_KEY: tuple[str, str] = ("/platform/lifeos-deploy", "LIFEOS_AGENT_JWT_PUBLIC_KEY")
VALID_FOR = timedelta(days=30)
RENEW_WITHIN = timedelta(days=7)
# Vault name -> (agent subject, role allowlist). Owner decision on hyperbolic-core #596.
CONTROLLERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "CLAUDE_CODE_TOKEN": ("claude-code", ("financial_operations", "reliability_engineer")),
    "CHATGPT_TOKEN": ("chatgpt", ("communications", "household_operations")),
}


class Vault(Protocol):
    def get(self, path: str, name: str) -> str: ...
    def upsert_secret(self, path: str, name: str, value: str) -> None: ...


def keygen() -> tuple[str, str]:
    """A fresh P-256 key pair as base64-wrapped PEM: (private, public)."""
    key = ec.generate_private_key(ec.SECP256R1())
    private = key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption())
    public = key.public_key().public_bytes(Encoding.PEM, PublicFormat.SubjectPublicKeyInfo)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def _pem(value: str) -> str:
    return base64.b64decode(value, validate=True).decode()


def mint(private_b64: str, agent: str, roles: tuple[str, ...], now: datetime) -> str:
    claims: dict[str, object] = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": f"agent:{agent}",
        "iat": now,
        "exp": now + VALID_FOR,
        "scopes": [SCOPE],
        "roles": sorted(roles),
    }
    return jwt.encode(claims, _pem(private_b64), algorithm=ALGORITHM)


def token_state(
    token: str | None, public_b64: str, agent: str, roles: tuple[str, ...], now: datetime
) -> tuple[str, datetime | None]:
    """(``fresh`` | ``expiring`` | ``invalid``, expiry). ``invalid`` covers missing, wrong
    key, wrong claims and unreadable expiry; ``expiring`` means renew now."""
    if not token:
        return "invalid", None
    try:
        claims = jwt.decode(
            token,
            _pem(public_b64),
            algorithms=[ALGORITHM],
            audience=AUDIENCE,
            issuer=ISSUER,
            # Time is judged against ``now`` below, not the wall clock, so the decision
            # is the same in tests and in the run that renews ahead of expiry.
            options={
                "require": ["exp", "iat", "sub", "aud", "iss", "scopes"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
        expires = datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (jwt.PyJWTError, ValueError, TypeError, OverflowError, OSError, binascii.Error):
        return "invalid", None
    if (
        claims.get("sub") != f"agent:{agent}"
        or claims.get("scopes") != [SCOPE]
        or claims.get("roles") != sorted(roles)
    ):
        return "invalid", expires
    return ("fresh" if expires - now > RENEW_WITHIN else "expiring"), expires


def unverified_expiry(token: str) -> datetime | None:
    """A token's ``exp`` without verifying it (diagnostics only)."""
    try:
        claims = jwt.decode(token, options={"verify_signature": False})
        return datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (jwt.PyJWTError, ValueError, KeyError, TypeError, OverflowError, OSError):
        return None


def _held(vault: Vault, path: str, name: str) -> str | None:
    try:
        return vault.get(path, name) or None
    except LookupError:
        return None


def _store(vault: Vault, path: str, name: str, value: str) -> str:
    """Write, then read back by fingerprint. Returns the fingerprint."""
    vault.upsert_secret(path, name, value)
    held = _held(vault, path, name)
    if held is None or fingerprint(held) != fingerprint(value):
        raise RuntimeError(f"read-back of {path}/{name} does not match what was written")
    return fingerprint(value)


class LifeosAdapter:
    """The ``lifeos`` broker adapter: ``renew`` keeps the controller tokens valid."""

    def __init__(self, vault: Vault, clock: Callable[[], datetime] | None = None) -> None:
        self._vault = vault
        self._now = clock or (lambda: datetime.now(UTC))

    def renew(self) -> dict[str, Any]:
        now = self._now()
        private = _held(self._vault, *PRIVATE_KEY)
        public = _held(self._vault, *PUBLIC_KEY)
        if private is None and public is None:
            private, public = keygen()
            # Public first: if the private write fails, the next run refuses (half a pair)
            # instead of minting tokens nothing can verify.
            _store(self._vault, *PUBLIC_KEY, public)
            _store(self._vault, *PRIVATE_KEY, private)
            keys = "created"
        elif private is None or public is None:
            missing = "private" if private is None else "public"
            raise PermissionError(
                f"the {missing} half of the LifeOS agent key pair is not in the vault; "
                "refusing to rotate (import it, or remove the other half to start over)"
            )
        else:
            keys = "existing"
        # Re-read both halves: a concurrent run may have written a different pair.
        private, public = _held(self._vault, *PRIVATE_KEY), _held(self._vault, *PUBLIC_KEY)
        if private is None or public is None:
            raise RuntimeError("the key pair vanished during the run; retry")
        probe = mint(private, "probe", ("reliability_engineer",), now)
        if token_state(probe, public, "probe", ("reliability_engineer",), now)[0] != "fresh":
            raise PermissionError("the vault key pair does not match; refusing to mint")
        tokens: dict[str, dict[str, str]] = {}
        for name, (agent, roles) in CONTROLLERS.items():
            held = _held(self._vault, TOKENS_PATH, name)
            state, expires = token_state(held, public, agent, roles, now)
            if state == "fresh" and held is not None and expires is not None:
                tokens[name] = {
                    "action": "fresh",
                    "expires": expires.isoformat(),
                    "fingerprint": fingerprint(held),
                }
                continue
            token = mint(private, agent, roles, now)
            minted_expiry = token_state(token, public, agent, roles, now)[1]
            tokens[name] = {
                "action": f"minted ({state})",
                "expires": minted_expiry.isoformat() if minted_expiry else "",
                "fingerprint": _store(self._vault, TOKENS_PATH, name, token),
            }
        return {"keys": keys, "public_key_fingerprint": fingerprint(public), "tokens": tokens}

    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]:
        if action == "renew":
            return self.renew()
        raise ValueError(f"unknown lifeos action: {action}")
