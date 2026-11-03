"""Fail-closed local sandbox policy for Docker-backed workers."""

import shutil
from dataclasses import dataclass
from pathlib import Path


class SandboxPolicyDenied(PermissionError):
    """A worker request violates the host-side sandbox policy."""


@dataclass(frozen=True)
class SandboxPolicy:
    """Structured constraints that are checked before a worker is launched."""

    role_id: str
    network: str
    writable: bool

    @classmethod
    def for_role(cls, role_id: str, *, network: str, writable: bool) -> "SandboxPolicy":
        if network not in {"none", "allowlist"}:
            raise SandboxPolicyDenied("network must be none or allowlist")
        return cls(role_id=role_id, network=network, writable=writable)

    def validate_workspace_write(self, relative_path: str) -> None:
        if not self.writable:
            raise SandboxPolicyDenied("role has a read-only workspace")
        if (
            not relative_path
            or Path(relative_path).is_absolute()
            or ".." in Path(relative_path).parts
        ):
            raise SandboxPolicyDenied("workspace write path is invalid")

    def resolve_workspace_path(self, workspace: Path, relative_path: str) -> Path:
        self.validate_workspace_write(relative_path)
        root = workspace.resolve()
        target = (root / relative_path).resolve()
        if target != root and root not in target.parents:
            raise SandboxPolicyDenied("path escapes workspace")
        return target

    def validate_command(self, command: str) -> None:
        if not command or any(
            token in command for token in (";", "|", "&", "`", "$(", "sh -c", "python -c")
        ):
            raise SandboxPolicyDenied("shell-shaped commands are denied")

    def docker_arguments(self) -> list[str]:
        args = [
            "--user",
            "10001:10001",
            "--cap-drop",
            "ALL",
            "--pids-limit",
            "128",
            "--memory",
            "1g",
            "--cpus",
            "1.0",
        ]
        if self.network == "none":
            args.extend(["--network", "none"])
        return args


@dataclass(frozen=True)
class SandboxRunner:
    """Refuses to launch unless a Docker client is available to the host."""

    policy: SandboxPolicy

    def start(self) -> None:
        if shutil.which("docker") is None:
            raise SandboxPolicyDenied("Docker daemon is unavailable; refusing an unsandboxed run")
        raise NotImplementedError("Docker launch and cancellation are the remaining P03-3 slice")
