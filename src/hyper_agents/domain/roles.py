"""The role manifest: what a role may do, declared once and enforced by grant.

A role is data, never code. Its manifest is strict: every field is typed,
unknown fields are refused, and no role may be granted an elevated tool. Who a
role is (persona, instructions) lives in its bundle; what it may touch lives
here, and the compiler turns those grants into structured controls, never
prompt text.
"""

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Capability(StrEnum):
    READ_REPO = "read_repo"
    WRITE_REPO = "write_repo"
    RUN_CHECKS = "run_checks"
    PROPOSE_PLAN = "propose_plan"
    REVIEW_DIFF = "review_diff"
    VERIFY_CRITERIA = "verify_criteria"
    REPAIR_CODE = "repair_code"


ELEVATED_TOOLS = frozenset({"git_push", "publish", "read_secret", "network_unrestricted"})

_ID = r"^[a-z][a-z0-9-]{1,31}$"
_SEMVER = r"^[0-9]+\.[0-9]+\.[0-9]+$"
_TOOL = r"^[a-z][a-z_]{0,39}$"


def _check_relative(value: str) -> str:
    """Refuse absolute, traversing or ambiguous paths. Symlinks need resolve_inside."""
    if not value or value.startswith("/") or "\\" in value or value.startswith("~"):
        raise ValueError(f"path must be relative and plain: {value!r}")
    if any(part in ("", "..") for part in value.split("/")):
        raise ValueError(f"path must not be empty or traverse upward: {value!r}")
    return value


def resolve_inside(root: Path, relative: str) -> Path:
    """Resolve a permitted path against a real root, refusing a symlink that leaves it.

    Lexical checks alone miss a symlink inside the tree that points outside it,
    so the target is resolved on disk and must still sit under `root`.
    """
    _check_relative(relative)
    base = root.resolve()
    target = (base / relative).resolve()
    if target != base and base not in target.parents:
        raise PermissionError(f"path escapes the role root: {relative!r}")
    return target


class Limits(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_turns: Annotated[int, Field(ge=1, le=200)]
    timeout_seconds: Annotated[int, Field(ge=1, le=7200)]
    max_tokens: Annotated[int, Field(ge=1, le=2_000_000)]


class ApprovalThresholds(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    max_files_changed: Annotated[int, Field(ge=0, le=500)]
    max_lines_changed: Annotated[int, Field(ge=0, le=100_000)]


class RoleManifest(BaseModel):
    # Not strict at this level: a JSON array must become the tuple. Each nested
    # model stays strict, and every scalar is a Literal, pattern or enum.
    model_config = ConfigDict(frozen=True, extra="forbid")

    id: Annotated[str, Field(pattern=_ID)]
    version: Annotated[str, Field(pattern=_SEMVER)]
    summary: Annotated[str, Field(min_length=1, max_length=200)]
    capabilities: tuple[Capability, ...]
    tools: tuple[Annotated[str, Field(pattern=_TOOL)], ...] = ()
    permitted_paths: tuple[str, ...] = ()
    prohibited_writes: tuple[str, ...] = ()
    network: Literal["none", "allowlist"] = "none"
    sandbox: Literal["workspace", "container"] = "workspace"
    provider_tier: Literal["local", "standard"] = "local"
    limits: Limits
    approval_thresholds: ApprovalThresholds
    input_schema: str
    output_schema: str
    evidence: tuple[Literal["test_log", "diff", "check_result"], ...] = ()
    dependencies: tuple[Annotated[str, Field(pattern=_ID)], ...] = ()

    @field_validator("tools")
    @classmethod
    def _no_elevated_tools(cls, tools: tuple[str, ...]) -> tuple[str, ...]:
        elevated = sorted(set(tools) & ELEVATED_TOOLS)
        if elevated:
            raise ValueError(f"elevated tools are never granted to a role: {elevated}")
        return tools

    @field_validator("permitted_paths", "prohibited_writes")
    @classmethod
    def _relative_paths(cls, paths: tuple[str, ...]) -> tuple[str, ...]:
        for path in paths:
            _check_relative(path)
        return paths

    @field_validator("input_schema", "output_schema")
    @classmethod
    def _schema_path(cls, value: str) -> str:
        return _check_relative(value)


def main() -> None:
    """Print the role schema. The committed snapshot is regenerated from this."""
    import json

    print(json.dumps(RoleManifest.model_json_schema(), indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
