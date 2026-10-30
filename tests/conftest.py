"""Test configuration and fixtures (P03-2).

Defines markers for conditional test execution:
- `requires_docker`: skipped when Docker is unavailable (cloud sessions).

Cloud sessions test adapter contract via stubs.
Self-hosted CI and local dev test full Docker integration.
"""

import subprocess

import pytest


def pytest_configure(config: pytest.Config) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "requires_docker: test requires Docker (skipped in cloud sessions)"
    )


def docker_available() -> bool:
    """Check if Docker is available and running."""
    try:
        subprocess.run(
            ["docker", "version"],
            check=True,
            capture_output=True,
            timeout=5,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired):
        return False


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip Docker-dependent tests when Docker is unavailable."""
    skip_docker = pytest.mark.skip(reason="Docker required (unavailable in this environment)")
    if not docker_available():
        for item in items:
            if "requires_docker" in item.keywords:
                item.add_marker(skip_docker)
