"""Run one registered op: validate, gate by tier, execute through one adapter, emit a receipt.

Receipts carry names, references, fingerprints and status. They never carry secret values.
"""

import hashlib
import time
from collections.abc import Mapping
from typing import Any, Protocol

from hyper_agents.ops.registry import GATED_TIERS, Op, ParamError, validate_params

MODES = ("dry-run", "apply")


class OpRefused(RuntimeError):
    """The request was refused before any adapter ran."""


class Adapter(Protocol):
    def execute(self, action: str, params: Mapping[str, str]) -> dict[str, Any]: ...


def fingerprint(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]


def redact(text: str, secrets: list[str]) -> str:
    for secret in secrets:
        if secret:
            text = text.replace(secret, "[REDACTED]")
    return text


def run_op(
    registry: Mapping[str, Op],
    op_id: str,
    raw_params: Mapping[str, Any],
    *,
    mode: str,
    gate: str | None,
    adapters: Mapping[str, Adapter],
    actor: str,
    run_id: str,
) -> dict[str, Any]:
    op = registry.get(op_id)
    if op is None:
        raise OpRefused(f"unregistered op: {op_id!r}")
    if mode not in MODES:
        raise OpRefused(f"unknown mode: {mode!r}")
    try:
        params = validate_params(op, raw_params)
    except ParamError as error:
        raise OpRefused(str(error)) from error
    if op.tier in GATED_TIERS and mode == "apply" and gate != op.gate:
        raise OpRefused(f"{op.id} is tier {op.tier}; apply requires gate {op.gate!r}, got {gate!r}")

    receipt: dict[str, Any] = {
        "op": op.id,
        "tier": op.tier,
        "mode": mode,
        "params": params,
        "actor": actor,
        "run_id": run_id,
        "started_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if mode == "dry-run":
        receipt["status"] = "planned"
        return receipt

    try:
        receipt["result"] = adapters[op.adapter].execute(op.action, params)
        receipt["status"] = "applied"
    except Exception as error:
        receipt["status"] = "failed"
        receipt["error"] = redact(str(error), _values_in(params))
    return receipt


def _values_in(params: Mapping[str, str]) -> list[str]:
    return [value for value in params.values() if len(value) >= 8]
