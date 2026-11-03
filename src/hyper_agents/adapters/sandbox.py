"""Hardened Docker workspace and host-side tool policy gateway (P03-3).

The host checks every command and every write before any container starts.
The container then enforces the same grant at the OS level: it runs non-root,
drops every capability, has a read-only root filesystem, has PID, memory and
CPU limits, and gets no network unless the role grant says ``allowlist``. A
role that may not write gets its workspace mounted read-only, so a write fails
in the kernel even if the policy layer were bypassed.

Cancellation kills the host process group, force-removes the named container,
calls the credential revoker and returns a ``cancelled`` receipt. Without
Docker, the sandbox fails closed with ``SandboxUnavailable``.

Process control is injected so the policy is testable without Docker.
"""

import fnmatch
import os
import re
import shutil
import signal
import subprocess
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Protocol, cast

from hyper_agents.application.compiler import Grant
from hyper_agents.application.factory import Receipt
from hyper_agents.domain.errors import ErrorCategory, HyperAgentsError, PolicyDenied
from hyper_agents.domain.roles import resolve_inside

CONTAINER_WORKSPACE = "/workspace"
UNPRIVILEGED_USER = "65532:65532"

SHELLS = frozenset({"sh", "bash", "dash", "zsh", "ksh", "fish", "ash", "pwsh", "powershell"})
_SHELL_FORMS = (";", "&&", "||", "|", "`", "$(", "${", ">", "<", "\n", "\r")
_MEMORY = re.compile(r"^[1-9][0-9]{0,5}[mg]$")
_CPUS = re.compile(r"^(0\.[1-9]|[1-9][0-9]?(\.[0-9])?)$")
_NETWORK = re.compile(r"^[a-z][a-z0-9_.-]{0,62}$")
_ENV_NAME = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_LEASE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,62}$")


class SandboxUnavailable(HyperAgentsError):
    """Docker is missing or unreachable; the sandbox never falls back to the host."""

    category = ErrorCategory.PROVIDER_UNAVAILABLE


@dataclass(frozen=True)
class SandboxLimits:
    pids: int = 256
    memory: str = "2g"
    cpus: str = "2"
    tmpfs_size: str = "256m"

    def __post_init__(self) -> None:
        if not 16 <= self.pids <= 4096:
            raise ValueError("pids limit must be within 16..4096")
        if not _MEMORY.match(self.memory) or not _MEMORY.match(self.tmpfs_size):
            raise ValueError("memory and tmpfs size must look like 512m or 2g")
        if not _CPUS.match(self.cpus):
            raise ValueError("cpus must look like 0.5, 1 or 2.5")


@dataclass(frozen=True)
class SandboxPolicy:
    """The structured grant, resolved for one workspace on the host."""

    workspace_root: Path
    may_write: bool
    permitted_paths: tuple[str, ...]
    prohibited_writes: tuple[str, ...]
    network: str
    limits: SandboxLimits = field(default_factory=SandboxLimits)
    allowlist_network: str = "hyper-agents-egress"

    @classmethod
    def from_grant(
        cls,
        grant: Grant,
        workspace_root: Path,
        limits: SandboxLimits | None = None,
        allowlist_network: str = "hyper-agents-egress",
    ) -> "SandboxPolicy":
        if grant.network not in ("none", "allowlist"):
            raise PolicyDenied(f"unknown network grant: {grant.network!r}")
        if not _NETWORK.match(allowlist_network):
            raise ValueError("allowlist network name is not a plain docker network name")
        root = workspace_root.resolve(strict=True)
        may_write = "write_repo" in grant.capabilities and "**" not in grant.prohibited_writes
        return cls(
            workspace_root=root,
            may_write=may_write,
            permitted_paths=grant.permitted_paths,
            prohibited_writes=grant.prohibited_writes,
            network=grant.network,
            limits=limits or SandboxLimits(),
            allowlist_network=allowlist_network,
        )


class ToolPolicyGateway:
    """Host-side checks that run before any container launch."""

    def __init__(self, policy: SandboxPolicy) -> None:
        self._policy = policy

    def check_command(self, command: object) -> tuple[str, ...]:
        if isinstance(command, str | bytes) or not isinstance(command, Sequence):
            raise PolicyDenied("a command must be an argv list, never a shell string")
        items: tuple[object, ...] = tuple(cast("Sequence[object]", command))
        argv: tuple[str, ...] = tuple(item for item in items if isinstance(item, str) and item)
        if not argv or len(argv) != len(items):
            raise PolicyDenied("a command must be a non-empty list of non-empty strings")
        for index, arg in enumerate(argv):
            is_shell = PurePosixPath(arg).name in SHELLS
            if is_shell and (index == 0 or argv[index + 1 : index + 2] == ("-c",)):
                raise PolicyDenied(f"shell interpreters are refused: {arg!r}")
            if any(form in arg for form in _SHELL_FORMS):
                raise PolicyDenied(f"shell syntax is refused in argument {index}")
        return argv

    def check_write(self, relative: str) -> Path:
        policy = self._policy
        if not policy.may_write:
            raise PolicyDenied(f"this role may not write: {relative!r}")
        try:
            target = resolve_inside(policy.workspace_root, relative)
        except (ValueError, PermissionError) as exc:
            raise PolicyDenied(str(exc)) from exc
        rel = target.relative_to(policy.workspace_root).as_posix()
        for pattern in policy.prohibited_writes:
            if _matches(rel, pattern):
                raise PolicyDenied(f"write to {rel!r} is prohibited by {pattern!r}")
        if policy.permitted_paths and not any(
            _matches(rel, path) for path in policy.permitted_paths
        ):
            raise PolicyDenied(f"write to {rel!r} is outside the permitted paths")
        return target

    def check_writes(self, paths: Sequence[str]) -> tuple[Path, ...]:
        return tuple(self.check_write(path) for path in paths)


def _matches(rel: str, pattern: str) -> bool:
    if pattern == "**":
        return True
    return rel == pattern or rel.startswith(pattern + "/") or fnmatch.fnmatchcase(rel, pattern)


class ProcessLike(Protocol):
    pid: int
    returncode: int | None

    def wait(self, timeout: float | None = None) -> int: ...

    def poll(self) -> int | None: ...


Spawn = Callable[[list[str], Mapping[str, str]], ProcessLike]
Run = Callable[[list[str]], int]
KillGroup = Callable[[int], None]


def _spawn(argv: list[str], env: Mapping[str, str]) -> ProcessLike:
    return subprocess.Popen(  # noqa: S603 - argv list built by build_run_argv, no shell
        argv, env=dict(env), start_new_session=True
    )


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, capture_output=True, check=False, timeout=60).returncode  # noqa: S603


def _kill_group(pid: int) -> None:
    try:
        os.killpg(pid, signal.SIGKILL)
    except ProcessLookupError:
        return


def _now() -> str:
    return datetime.now(UTC).isoformat()


@dataclass
class SandboxRun:
    lease_id: str
    container: str
    process: ProcessLike
    credential_revoked: bool = False
    receipt: Receipt | None = None


class DockerSandbox:
    """Run one argv in a hardened container, under a host-checked policy."""

    def __init__(
        self,
        policy: SandboxPolicy,
        image: str,
        *,
        docker: str | None = None,
        spawn: Spawn = _spawn,
        run: Run = _run,
        kill_group: KillGroup = _kill_group,
        revoke_credential: Callable[[str], None] | None = None,
    ) -> None:
        self._policy = policy
        self._gateway = ToolPolicyGateway(policy)
        self._image = image
        self._docker = docker if docker is not None else shutil.which("docker")
        self._spawn = spawn
        self._run = run
        self._kill_group = kill_group
        self._revoke = revoke_credential

    @property
    def gateway(self) -> ToolPolicyGateway:
        return self._gateway

    def build_run_argv(
        self, command: Sequence[str], container: str, env_names: Sequence[str] = ()
    ) -> list[str]:
        argv = list(self._gateway.check_command(command))
        policy, limits = self._policy, self._policy.limits
        network = policy.allowlist_network if policy.network == "allowlist" else "none"
        mount = f"type=bind,src={policy.workspace_root},dst={CONTAINER_WORKSPACE}"
        run = [
            self._docker or "docker",
            "run",
            "--rm",
            "--init",
            "--name",
            container,
            "--label",
            f"hyper-agents.container={container}",
            "--user",
            UNPRIVILEGED_USER,
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--read-only",
            "--tmpfs",
            f"/tmp:rw,noexec,nosuid,size={limits.tmpfs_size}",  # noqa: S108 - container tmpfs
            "--pids-limit",
            str(limits.pids),
            "--memory",
            limits.memory,
            "--memory-swap",
            limits.memory,
            "--cpus",
            limits.cpus,
            "--network",
            network,
            "--mount",
            mount if policy.may_write else mount + ",readonly",
        ]
        if policy.may_write:
            for path in policy.prohibited_writes:
                if any(ch in path for ch in "*?[") or not (policy.workspace_root / path).exists():
                    continue
                sub = f"{CONTAINER_WORKSPACE}/{path}"
                run += [
                    "--mount",
                    f"type=bind,src={policy.workspace_root / path},dst={sub},readonly",
                ]
        for name in env_names:
            if not _ENV_NAME.match(name):
                raise PolicyDenied(f"environment name is not allowed: {name!r}")
            run += ["--env", name]
        return [
            *run,
            "--workdir",
            CONTAINER_WORKSPACE,
            "--entrypoint",
            argv[0],
            self._image,
            *argv[1:],
        ]

    def launch(
        self,
        lease_id: str,
        command: Sequence[str],
        env: Mapping[str, str] | None = None,
    ) -> SandboxRun:
        """Check policy, then start the container. Secret values travel by env, never argv."""
        if not _LEASE.match(lease_id):
            raise PolicyDenied("lease id must be a plain identifier")
        container = f"hyper-agents-{lease_id}"
        env = dict(env or {})
        argv = self.build_run_argv(command, container, sorted(env))
        if not self._docker or self._run([self._docker, "info"]) != 0:
            raise SandboxUnavailable("Docker is not available; refusing to run on the host")
        child_env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), **env}
        return SandboxRun(lease_id, container, self._spawn(argv, child_env))

    def wait(self, handle: SandboxRun, timeout: float) -> Receipt:
        if handle.receipt is not None:
            return handle.receipt
        try:
            code = handle.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            return self.cancel(handle, "timeout")
        handle.receipt = Receipt(
            lease_id=handle.lease_id,
            status="completed" if code == 0 else "failed",
            tokens_used=0,
            completed_at_iso=_now(),
            error=None if code == 0 else f"exit code {code}",
        )
        return handle.receipt

    def cancel(self, handle: SandboxRun, reason: str = "cancelled") -> Receipt:
        """Kill the process tree, remove the container, revoke the credential."""
        if handle.receipt is not None:
            return handle.receipt
        self._kill_group(handle.process.pid)
        docker = self._docker or "docker"
        self._run([docker, "rm", "-f", handle.container])
        orphaned = self._run([docker, "inspect", handle.container]) == 0
        if self._revoke is not None:
            self._revoke(handle.lease_id)
            handle.credential_revoked = True
        handle.receipt = Receipt(
            lease_id=handle.lease_id,
            status="cancelled",
            tokens_used=0,
            completed_at_iso=_now(),
            error=f"{reason}; orphaned container={orphaned}; "
            f"credential revoked={handle.credential_revoked}",
        )
        return handle.receipt
