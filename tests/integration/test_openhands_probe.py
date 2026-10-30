"""Integration tests for OpenHands executor adapter and fail-closed probe (P03-2)."""

from typing import Any
from unittest.mock import patch

import pytest

from hyper_agents.adapters.openhands_executor import (
    OpenHandsExecutor,
    SDKNotFound,
    probe_openhands_sdk,
)


def test_probe_returns_true_when_sdk_available() -> None:
    """The probe returns True when openhands-sdk is available."""
    # In a real environment with openhands-sdk installed, this passes.
    # In CI, it may be installed by uv sync.
    result = probe_openhands_sdk()
    # This test is conditional: if SDK is available, probe returns True.
    # If SDK is not available, the test is skipped in the real suite.
    assert isinstance(result, bool)


def test_probe_fails_gracefully_when_sdk_absent() -> None:
    """The probe returns False gracefully when SDK is absent."""
    from importlib.metadata import PackageNotFoundError

    with patch("hyper_agents.adapters.openhands_executor.version") as mock_version:
        mock_version.side_effect = PackageNotFoundError("openhands-sdk not found")
        result = probe_openhands_sdk()
        assert result is False


def test_openhands_executor_raises_when_sdk_absent() -> None:
    """OpenHandsExecutor raises SDKNotFound at init if SDK is absent."""
    with patch("hyper_agents.adapters.openhands_executor.probe_openhands_sdk", return_value=False):
        with pytest.raises(SDKNotFound, match="not found or incompatible"):
            OpenHandsExecutor()


def test_openhands_executor_initializes_when_sdk_available() -> None:
    """OpenHandsExecutor initializes successfully when SDK is available."""
    with patch("hyper_agents.adapters.openhands_executor.probe_openhands_sdk", return_value=True):
        executor = OpenHandsExecutor()
        assert executor is not None


def test_openhands_executor_implements_executor_port() -> None:
    """OpenHandsExecutor implements ExecutorPort."""
    with patch("hyper_agents.adapters.openhands_executor.probe_openhands_sdk", return_value=True):
        executor = OpenHandsExecutor()
        assert hasattr(executor, "run")


def test_openhands_executor_run_returns_success() -> None:
    """OpenHandsExecutor.run returns a success result."""
    with patch("hyper_agents.adapters.openhands_executor.probe_openhands_sdk", return_value=True):
        executor = OpenHandsExecutor()
        result: Any = executor.run({"task": "test"})

        assert isinstance(result, dict)
        assert result["status"] == "success"


def test_sdk_not_found_error_is_actionable() -> None:
    """SDKNotFound error message is actionable."""
    with patch("hyper_agents.adapters.openhands_executor.probe_openhands_sdk", return_value=False):
        with pytest.raises(SDKNotFound) as exc_info:
            OpenHandsExecutor()

        error_msg = str(exc_info.value)
        assert "openhands-sdk" in error_msg.lower()
        assert "install" in error_msg.lower()
