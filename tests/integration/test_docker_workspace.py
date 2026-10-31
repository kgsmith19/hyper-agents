"""Key-free Docker check: the pinned agent server starts and reports healthy (AC4, key-free tier).

No LLM key and no conversation. Needs a reachable Docker daemon; skipped otherwise.
"""

import json
from urllib.request import urlopen

import pytest
from openhands.workspace import DockerWorkspace

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
