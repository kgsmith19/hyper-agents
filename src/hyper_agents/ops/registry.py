"""Typed operation registry: the only operations the broker will run.

Each op names one adapter action, typed parameters, and a risk tier. Unknown ops and
out-of-schema parameters are refused before any credential is fetched.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

TIERS = ("R0", "R1", "R2", "R3")
GATED_TIERS = ("R2", "R3")
ADAPTERS = ("github", "infisical")


class RegistryError(ValueError):
    """The registry file is malformed."""


class ParamError(ValueError):
    """A request parameter is missing, unknown, or outside its schema."""


@dataclass(frozen=True)
class Param:
    name: str
    required: bool
    pattern: re.Pattern[str] | None
    enum: tuple[str, ...] | None
    max_length: int


@dataclass(frozen=True)
class Op:
    id: str
    adapter: str
    action: str
    tier: str
    gate: str | None
    params: tuple[Param, ...]


def _param(raw: Mapping[str, Any]) -> Param:
    pattern = raw.get("pattern")
    enum = raw.get("enum")
    return Param(
        name=str(raw["name"]),
        required=bool(raw.get("required", True)),
        pattern=re.compile(str(pattern)) if pattern is not None else None,
        enum=tuple(str(item) for item in enum) if enum is not None else None,
        max_length=int(raw.get("max_length", 256)),
    )


def _op(raw: Mapping[str, Any]) -> Op:
    op_id = str(raw["id"])
    adapter = str(raw["adapter"])
    tier = str(raw["tier"])
    gate = raw.get("gate")
    if adapter not in ADAPTERS:
        raise RegistryError(f"{op_id}: unknown adapter {adapter!r}")
    if tier not in TIERS:
        raise RegistryError(f"{op_id}: unknown tier {tier!r}")
    if tier in GATED_TIERS and not gate:
        raise RegistryError(f"{op_id}: tier {tier} requires a gate environment")
    return Op(
        id=op_id,
        adapter=adapter,
        action=str(raw["action"]),
        tier=tier,
        gate=str(gate) if gate else None,
        params=tuple(_param(item) for item in raw.get("params", [])),
    )


def load_registry(path: Path) -> dict[str, Op]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    ops: dict[str, Op] = {}
    for raw in data.get("ops", []):
        op = _op(raw)
        if op.id in ops:
            raise RegistryError(f"duplicate op id {op.id!r}")
        ops[op.id] = op
    return ops


def validate_params(op: Op, params: Mapping[str, Any]) -> dict[str, str]:
    known = {param.name for param in op.params}
    unknown = sorted(set(params) - known)
    if unknown:
        raise ParamError(f"unknown params: {unknown}")
    clean: dict[str, str] = {}
    for param in op.params:
        if param.name not in params:
            if param.required:
                raise ParamError(f"missing param: {param.name}")
            continue
        value = params[param.name]
        if not isinstance(value, str) or len(value) > param.max_length:
            raise ParamError(f"{param.name}: must be a string of at most {param.max_length} chars")
        if param.enum is not None and value not in param.enum:
            raise ParamError(f"{param.name}: not an allowed value")
        if param.pattern is not None and not param.pattern.fullmatch(value):
            raise ParamError(f"{param.name}: does not match the allowed pattern")
        clean[param.name] = value
    return clean
