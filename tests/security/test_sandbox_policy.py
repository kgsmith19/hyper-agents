"""Fail-closed host-side policy checks for P03-3."""

from pathlib import Path

import pytest

from hyper_agents.adapters.sandbox import SandboxPolicy, SandboxPolicyDenied, SandboxRunner


def test_reviewer_cannot_request_a_writable_workspace() -> None:
    policy = SandboxPolicy.for_role("reviewer", network="none", writable=False)

    with pytest.raises(SandboxPolicyDenied, match="read-only"):
        policy.validate_workspace_write("src/hyper_agents/domain/roles.py")


def test_policy_rejects_path_that_escapes_workspace_by_symlink(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "escape").symlink_to(tmp_path)
    policy = SandboxPolicy.for_role("builder", network="none", writable=True)

    with pytest.raises(SandboxPolicyDenied, match="escapes workspace"):
        policy.resolve_workspace_path(workspace, "escape/outside.txt")


@pytest.mark.parametrize("command", ["sh -c 'id'", "python -c 'print(1)'", "echo ok; id"])
def test_policy_rejects_shell_shaped_commands(command: str) -> None:
    policy = SandboxPolicy.for_role("builder", network="none", writable=True)

    with pytest.raises(SandboxPolicyDenied, match="shell"):
        policy.validate_command(command)


def test_policy_emits_hardened_docker_arguments() -> None:
    policy = SandboxPolicy.for_role("builder", network="none", writable=True)

    assert policy.docker_arguments() == [
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
        "--network",
        "none",
    ]


def test_runner_fails_closed_when_docker_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def no_binary(_: str) -> None:
        return None

    monkeypatch.setattr("hyper_agents.adapters.sandbox.shutil.which", no_binary)

    with pytest.raises(SandboxPolicyDenied, match="Docker daemon"):
        SandboxRunner(SandboxPolicy.for_role("builder", network="none", writable=True)).start()
