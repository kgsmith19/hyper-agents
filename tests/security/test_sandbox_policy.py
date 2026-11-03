"""P03-3 sandbox policy: host-side gateway and hardened container argv.

Each test is a negative control for one way out of the sandbox: a reviewer
write, a symlink escape, an injected shell command, an open network, a root or
capability-holding container, an orphaned container after cancel, and a
missing Docker daemon falling back to the host.
"""

import subprocess
from collections.abc import Mapping
from pathlib import Path

import pytest

from hyper_agents.adapters.sandbox import (
    DockerSandbox,
    SandboxLimits,
    SandboxPolicy,
    SandboxUnavailable,
    ToolPolicyGateway,
)
from hyper_agents.application.compiler import Grant, GrantApproval, GrantLimit
from hyper_agents.domain.errors import ErrorCategory, PolicyDenied


def grant(
    capabilities: tuple[str, ...] = ("read_repo", "write_repo"),
    prohibited: tuple[str, ...] = (".github", "agents"),
    permitted: tuple[str, ...] = ("src", "tests"),
    network: str = "none",
) -> Grant:
    return Grant(
        limit=GrantLimit(max_turns=5, timeout_seconds=60, max_tokens=1000),
        approval=GrantApproval(max_files_changed=5, max_lines_changed=100),
        capabilities=capabilities,
        tools=("read_file",),
        permitted_paths=permitted,
        prohibited_writes=prohibited,
        network=network,
        sandbox="container",
    )


REVIEWER = grant(capabilities=("read_repo", "review_diff"), prohibited=("**",))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "ws"
    for sub in ("src", "tests", ".github", "agents"):
        (root / sub).mkdir(parents=True)
    return root


class FakeProcess:
    def __init__(self, code: int | None = 0, hang: bool = False) -> None:
        self.pid = 4242
        self.returncode = code
        self._hang = hang

    def wait(self, timeout: float | None = None) -> int:
        if self._hang:
            raise subprocess.TimeoutExpired("docker", timeout or 0)
        return self.returncode or 0

    def poll(self) -> int | None:
        return None if self._hang else self.returncode


class FakeDocker:
    """Records docker calls; `docker inspect` succeeds while the container lives."""

    def __init__(self, available: bool = True, rm_works: bool = True) -> None:
        self.available = available
        self.rm_works = rm_works
        self.calls: list[list[str]] = []
        self.spawned: list[tuple[list[str], dict[str, str]]] = []
        self.killed: list[int] = []
        self.live: set[str] = set()
        self.process = FakeProcess()

    def run(self, argv: list[str]) -> int:
        self.calls.append(argv)
        verb = argv[1]
        if verb == "info":
            return 0 if self.available else 1
        if verb == "rm" and self.rm_works:
            self.live.discard(argv[-1])
            return 0
        if verb == "inspect":
            return 0 if argv[-1] in self.live else 1
        return 1

    def spawn(self, argv: list[str], env: Mapping[str, str]) -> FakeProcess:
        self.spawned.append((argv, dict(env)))
        self.live.add(argv[argv.index("--name") + 1])
        return self.process

    def kill(self, pid: int) -> None:
        self.killed.append(pid)


def sandbox(
    workspace: Path, g: Grant | None = None, fake: FakeDocker | None = None, **kwargs: object
) -> tuple[DockerSandbox, FakeDocker]:
    fake = fake or FakeDocker()
    box = DockerSandbox(
        SandboxPolicy.from_grant(g or grant(), workspace),
        "ghcr.io/example/worker:1",
        docker="/usr/bin/docker",
        spawn=fake.spawn,
        run=fake.run,
        kill_group=fake.kill,
        **kwargs,  # type: ignore[arg-type]
    )
    return box, fake


def flag(argv: list[str], name: str) -> str:
    return argv[argv.index(name) + 1]


# --- writes ---------------------------------------------------------------


def test_reviewer_write_is_denied_on_the_host(workspace: Path) -> None:
    gateway = ToolPolicyGateway(SandboxPolicy.from_grant(REVIEWER, workspace))
    with pytest.raises(PolicyDenied, match="may not write"):
        gateway.check_write("src/app.py")


def test_reviewer_workspace_is_mounted_read_only(workspace: Path) -> None:
    box, _ = sandbox(workspace, REVIEWER)
    argv = box.build_run_argv(["pytest"], "c1")
    assert flag(argv, "--mount").endswith(",readonly")


def test_builder_gets_writable_workspace_with_prohibited_paths_read_only(
    workspace: Path,
) -> None:
    box, _ = sandbox(workspace)
    argv = box.build_run_argv(["pytest"], "c1")
    mounts = [argv[i + 1] for i, a in enumerate(argv) if a == "--mount"]
    assert not mounts[0].endswith(",readonly")
    assert any("dst=/workspace/.github,readonly" in m for m in mounts)
    assert any("dst=/workspace/agents,readonly" in m for m in mounts)


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "agents/catalog/x.yaml"])
def test_builder_write_to_prohibited_path_is_denied(workspace: Path, path: str) -> None:
    with pytest.raises(PolicyDenied, match="prohibited"):
        ToolPolicyGateway(SandboxPolicy.from_grant(grant(), workspace)).check_write(path)


def test_write_outside_permitted_paths_is_denied(workspace: Path) -> None:
    with pytest.raises(PolicyDenied, match="outside the permitted"):
        ToolPolicyGateway(SandboxPolicy.from_grant(grant(), workspace)).check_write("README.md")


def test_permitted_write_resolves_inside_workspace(workspace: Path) -> None:
    gateway = ToolPolicyGateway(SandboxPolicy.from_grant(grant(), workspace))
    assert gateway.check_write("src/app.py") == workspace.resolve() / "src" / "app.py"


def test_symlink_escape_is_denied(workspace: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (workspace / "src" / "link").symlink_to(outside)
    gateway = ToolPolicyGateway(SandboxPolicy.from_grant(grant(), workspace))
    with pytest.raises(PolicyDenied, match="escapes"):
        gateway.check_write("src/link/stolen.txt")


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", "~/x", "src/../../x"])
def test_traversal_and_absolute_paths_are_denied(workspace: Path, path: str) -> None:
    with pytest.raises(PolicyDenied):
        ToolPolicyGateway(SandboxPolicy.from_grant(grant(), workspace)).check_write(path)


# --- commands -------------------------------------------------------------


@pytest.mark.parametrize(
    "command",
    [
        "pytest -q",
        b"pytest",
        [],
        ["sh", "-c", "pytest"],
        ["/bin/bash", "-lc", "id"],
        ["env", "bash", "-c", "id"],
        ["pytest", "; rm -rf /"],
        ["pytest", "a && curl evil"],
        ["echo", "$(cat /etc/shadow)"],
        ["echo", "`id`"],
        ["cat", "x", "|", "nc"],
        ["echo", "hi > /workspace/x"],
        ["pytest", "a\nid"],
        ["pytest", ""],
    ],
)
def test_injected_shell_forms_are_refused(workspace: Path, command: object) -> None:
    gateway = ToolPolicyGateway(SandboxPolicy.from_grant(grant(), workspace))
    with pytest.raises(PolicyDenied):
        gateway.check_command(command)


def test_plain_argv_is_accepted(workspace: Path) -> None:
    gateway = ToolPolicyGateway(SandboxPolicy.from_grant(grant(), workspace))
    assert gateway.check_command(["uv", "run", "pytest", "-k", "sandbox"]) == (
        "uv",
        "run",
        "pytest",
        "-k",
        "sandbox",
    )


# --- container hardening ----------------------------------------------------


def test_container_is_non_root_without_capabilities_and_limited(workspace: Path) -> None:
    box, _ = sandbox(workspace)
    argv = box.build_run_argv(["pytest"], "c1")
    assert flag(argv, "--user") == "65532:65532"
    assert flag(argv, "--cap-drop") == "ALL"
    assert flag(argv, "--security-opt") == "no-new-privileges"
    assert "--read-only" in argv
    assert flag(argv, "--pids-limit") == "256"
    assert flag(argv, "--memory") == "2g"
    assert flag(argv, "--memory-swap") == "2g"
    assert flag(argv, "--cpus") == "2"
    assert "--privileged" not in argv


def test_network_is_denied_by_default(workspace: Path) -> None:
    box, _ = sandbox(workspace)
    assert flag(box.build_run_argv(["pytest"], "c1"), "--network") == "none"


def test_network_opens_only_for_explicit_allowlist_grant(workspace: Path) -> None:
    box, _ = sandbox(workspace, grant(network="allowlist"))
    assert flag(box.build_run_argv(["pytest"], "c1"), "--network") == "hyper-agents-egress"


def test_unknown_network_grant_is_refused(workspace: Path) -> None:
    with pytest.raises(PolicyDenied, match="network grant"):
        SandboxPolicy.from_grant(grant(network="host"), workspace)


def test_command_runs_as_entrypoint_not_through_a_shell(workspace: Path) -> None:
    box, _ = sandbox(workspace)
    argv = box.build_run_argv(["pytest", "-q"], "c1")
    assert flag(argv, "--entrypoint") == "pytest"
    assert argv[-2:] == ["ghcr.io/example/worker:1", "-q"]


@pytest.mark.parametrize(
    "limits",
    [{"pids": 0}, {"pids": 100_000}, {"memory": "unlimited"}, {"cpus": "0"}, {"cpus": "all"}],
)
def test_limits_cannot_be_disabled(limits: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        SandboxLimits(**limits)  # type: ignore[arg-type]


def test_secret_values_go_by_env_never_argv(workspace: Path) -> None:
    box, fake = sandbox(workspace)
    box.launch("lease-1", ["pytest"], env={"LLM_API_KEY": "planted-secret-value"})
    argv, env = fake.spawned[0]
    assert "planted-secret-value" not in " ".join(argv)
    assert flag(argv, "--env") == "LLM_API_KEY"
    assert env["LLM_API_KEY"] == "planted-secret-value"


@pytest.mark.parametrize("name", ["lower", "A=B", "PATH;id"])
def test_bad_env_names_are_refused(workspace: Path, name: str) -> None:
    box, _ = sandbox(workspace)
    with pytest.raises(PolicyDenied):
        box.build_run_argv(["pytest"], "c1", [name])


# --- launch, cancel and Docker availability ----------------------------------


def test_policy_violation_is_refused_before_any_docker_call(workspace: Path) -> None:
    box, fake = sandbox(workspace)
    with pytest.raises(PolicyDenied):
        box.launch("lease-1", ["sh", "-c", "id"])
    assert fake.calls == [] and fake.spawned == []


def test_missing_docker_fails_closed(workspace: Path) -> None:
    box = DockerSandbox(
        SandboxPolicy.from_grant(grant(), workspace),
        "img",
        docker="",
        spawn=FakeDocker().spawn,
    )
    with pytest.raises(SandboxUnavailable) as raised:
        box.launch("lease-1", ["pytest"])
    assert raised.value.category is ErrorCategory.PROVIDER_UNAVAILABLE


def test_unreachable_daemon_fails_closed(workspace: Path) -> None:
    box, fake = sandbox(workspace, fake=FakeDocker(available=False))
    with pytest.raises(SandboxUnavailable):
        box.launch("lease-1", ["pytest"])
    assert fake.spawned == []


def test_cancel_kills_tree_removes_container_and_revokes_credential(workspace: Path) -> None:
    revoked: list[str] = []
    box, fake = sandbox(workspace, revoke_credential=revoked.append)
    handle = box.launch("lease-7", ["sleep", "600"])
    receipt = box.cancel(handle, "operator stop")
    assert fake.killed == [fake.process.pid]
    assert ["/usr/bin/docker", "rm", "-f", "hyper-agents-lease-7"] in fake.calls
    assert fake.live == set()
    assert revoked == ["lease-7"]
    assert receipt.status == "cancelled"
    assert receipt.error is not None
    assert "orphaned container=False" in receipt.error
    assert "credential revoked=True" in receipt.error


def test_cancel_reports_an_orphan_when_removal_fails(workspace: Path) -> None:
    box, _ = sandbox(workspace, fake=FakeDocker(rm_works=False))
    receipt = box.cancel(box.launch("lease-8", ["sleep", "600"]))
    assert receipt.error is not None and "orphaned container=True" in receipt.error


def test_cancel_is_idempotent(workspace: Path) -> None:
    box, fake = sandbox(workspace)
    handle = box.launch("lease-9", ["sleep", "600"])
    first = box.cancel(handle)
    assert box.cancel(handle) is first
    assert len(fake.killed) == 1


def test_timeout_cancels_the_run(workspace: Path) -> None:
    fake = FakeDocker()
    fake.process = FakeProcess(hang=True)
    box, _ = sandbox(workspace, fake=fake)
    receipt = box.wait(box.launch("lease-10", ["sleep", "600"]), timeout=0.01)
    assert receipt.status == "cancelled"
    assert receipt.error is not None and receipt.error.startswith("timeout")


@pytest.mark.parametrize(("code", "status"), [(0, "completed"), (3, "failed")])
def test_exit_code_maps_to_receipt(workspace: Path, code: int, status: str) -> None:
    fake = FakeDocker()
    fake.process = FakeProcess(code=code)
    box, _ = sandbox(workspace, fake=fake)
    assert box.wait(box.launch("lease-11", ["pytest"]), timeout=5).status == status


@pytest.mark.parametrize("lease", ["", "../x", "a b", "x;id"])
def test_lease_id_must_be_plain(workspace: Path, lease: str) -> None:
    box, _ = sandbox(workspace)
    with pytest.raises(PolicyDenied):
        box.launch(lease, ["pytest"])
