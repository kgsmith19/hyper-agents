"""Pytest configuration: the requires_docker marker and its skip rule."""

import shutil
import subprocess

import pytest


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line(
        "markers", "requires_docker: needs a reachable Docker daemon; skipped otherwise"
    )


def _docker_reachable() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    try:
        subprocess.run([docker, "info"], check=True, capture_output=True, timeout=10)  # noqa: S603
    except (OSError, subprocess.SubprocessError):
        return False
    return True


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    marked = [item for item in items if item.get_closest_marker("requires_docker")]
    if not marked or _docker_reachable():
        return
    skip = pytest.mark.skip(reason="Docker daemon not reachable")
    for item in marked:
        item.add_marker(skip)
