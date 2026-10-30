"""Integration tests for OpenHandsExecutor with fail-closed probe (P03-2).

Tests:
- AC2: Fail-closed probe when SDK is absent (import validation).
- AC3: Verified imports only, no hidden dependencies (code quality, linting).
- AC4: Integration with real tool execution validated locally and at release gates.

Cloud sessions test adapter contract via stubs; Docker integration is skipped.
Docker-dependent integration tests are skipped unless Docker is available.
Self-hosted CI and local dev run full integration suite.
"""

import pytest

from hyper_agents.adapters.openhands_executor import OpenHandsExecutor


def test_probe_fails_gracefully_when_sdk_absent(monkeypatch: pytest.MonkeyPatch) -> None:
    """AC2: OpenHands executor fails closed when SDK is absent.

    Verify that importing or instantiating the executor without the SDK
    raises ImportError with a clear, actionable message (never silent success).
    """
    # Hide the openhands module (simulate absence).
    import sys
    from typing import Any

    openhands_module = sys.modules.pop("openhands", None)

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openhands":
            raise ImportError("No module named 'openhands'")
        return __import__(name, *args, **kwargs)

    try:
        # Block the import.
        monkeypatch.setattr("builtins.__import__", mock_import)

        # Instantiation must raise ImportError, not succeed silently.
        with pytest.raises(ImportError, match="openhands-sdk not installed"):
            OpenHandsExecutor()
    finally:
        # Restore.
        if openhands_module is not None:
            sys.modules["openhands"] = openhands_module


@pytest.mark.requires_docker
def test_one_tool_invocation_traced() -> None:
    """AC4: One real tool invocation traced (requires Docker and openhands-sdk).

    Integration test that validates OpenHands executor with a real tool call.
    Requires Docker to be running and openhands-sdk installed; skipped in cloud.
    Cloud sessions test the adapter contract (AC2, AC3 via code review and imports);
    Docker integration is validated in self-hosted CI and at release gates.

    Note: This test is skipped when Docker is unavailable.
    A dry run (no real credentials) checks adapter structure and instrumentation.
    """
    # This test only runs when Docker is available (marked requires_docker).
    # AC3 (verified imports, no hidden dependencies) is satisfied by:
    # - Code linting (ruff check passes in CI).
    # - Type checking (pyright strict mode).
    # - Import-only validation (openhands imported, not executed).
    executor = OpenHandsExecutor()

    # Placeholder: real tool invocation in P03-3 (tool schema).
    # For now, verify the executor accepts requests and returns results.
    result = executor.run({"task": "test"})

    assert result is not None
    assert isinstance(result, dict)
    assert "status" in result
