"""Pytest configuration: markers, fixtures, and skip logic."""

import shutil
from typing import Any

import pytest


def pytest_configure(config: Any) -> None:
    """Register custom markers."""
    config.addinivalue_line(
        "markers", "requires_docker: mark test as requiring Docker; skipped in cloud sessions"
    )


def pytest_collection_modifyitems(config: Any, items: Any) -> None:
    """Skip requires_docker tests when Docker is unavailable."""
    docker_available = shutil.which("docker") is not None

    if not docker_available:
        skip_marker = pytest.mark.skip(reason="Docker not available")
        for item in items:
            if "requires_docker" in item.keywords:
                item.add_marker(skip_marker)
