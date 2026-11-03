"""Key-free Docker checks: the pinned agent server starts healthy, and the P03-3 hardened
sandbox enforces its grant at the OS level (read-only mount, no network, no orphans).

No LLM key and no conversation. Needs a reachable Docker daemon; skipped otherwise.
"""

import json
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.request import urlopen

import pytest
from openhands.workspace import DockerWorkspace

if TYPE_CHECKING:
    from hyper_agents.adapters.sandbox import DockerSandbox

PINNED_IMAGE = "ghcr.io/openhands/agent-server:1.58.2-python"


@pytest.mark.requires_docker
def test_pinned_agent_server_reports_healthy() -> None:
    with DockerWorkspace(server_image=PINNED_IMAGE) as workspace:
        with urlopen(f"{workspace.host.rstrip('/')}/health", timeout=10) as response:  # noqa: S310
            assert response.status == 200
            assert json.load(response) == {"status": "ok"}


@pytest.mark.requires_docker
def test_missing_image_fails_closed() -> None:
    with pytest.raises(RuntimeError):
        with DockerWorkspace(server_image="ghcr.io/openhands/agent-server:0.0.0-does-not-exist"):
            pass


def _hardened(tmp_path: Path, capabilities: tuple[str, ...]) -> "DockerSandbox":
    from hyper_agents.adapters.sandbox import DockerSandbox, SandboxPolicy
    from hyper_agents.application.compiler import Grant, GrantApproval, GrantLimit

    workspace = tmp_path / "ws"
    workspace.mkdir()
    workspace.chmod(0o777)
    grant = Grant(
        limit=GrantLimit(max_turns=1, timeout_seconds=60, max_tokens=1),
        approval=GrantApproval(max_files_changed=0, max_lines_changed=0),
        capabilities=capabilities,
        tools=(),
        permitted_paths=(),
        prohibited_writes=() if "write_repo" in capabilities else ("**",),
        network="none",
        sandbox="container",
    )
    return DockerSandbox(SandboxPolicy.from_grant(grant, workspace), PINNED_IMAGE)


@pytest.mark.requires_docker
def test_reviewer_write_fails_at_the_os_level(tmp_path: Path) -> None:
    """P03-3 red test: the read-only mount stops the write even past the host gateway."""
    box = _hardened(tmp_path, ("read_repo", "review_diff"))
    receipt = box.wait(box.launch("it-ro", ["touch", "/workspace/probe"]), timeout=120)
    assert receipt.status == "failed"
    assert not (tmp_path / "ws" / "probe").exists()


@pytest.mark.requires_docker
def test_container_has_no_network_and_runs_unprivileged(tmp_path: Path) -> None:
    box = _hardened(tmp_path, ("read_repo", "write_repo"))
    assert box.wait(box.launch("it-id", ["test", "-w", "/etc/passwd"]), timeout=120).status == (
        "failed"
    )
    assert box.wait(
        box.launch("it-net", ["getent", "hosts", "github.com"]), timeout=120
    ).status == ("failed")


@pytest.mark.requires_docker
def test_cancel_leaves_no_orphan_container(tmp_path: Path) -> None:
    box = _hardened(tmp_path, ("read_repo",))
    handle = box.launch("it-cancel", ["sleep", "600"])
    receipt = box.cancel(handle, "test stop")
    assert receipt.status == "cancelled"
    assert receipt.error is not None and "orphaned container=False" in receipt.error
