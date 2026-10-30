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
    Per ADR 0001, fail-closed is non-negotiable: no fake success.
    """
    # Hide the openhands module (simulate absence).
    import sys
    from typing import Any

    openhands_sdk_module = sys.modules.pop("openhands_sdk", None)

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        if name == "openhands_sdk":
            raise ImportError("No module named 'openhands_sdk'")
        return __import__(name, *args, **kwargs)

    try:
        # Block the import.
        monkeypatch.setattr("builtins.__import__", mock_import)

        # Instantiation must raise ImportError, not succeed silently.
        # This is a fail-closed requirement per ADR 0001.
        with pytest.raises(ImportError, match="openhands-sdk not installed"):
            OpenHandsExecutor()
    finally:
        # Restore.
        if openhands_sdk_module is not None:
            sys.modules["openhands_sdk"] = openhands_sdk_module


@pytest.mark.requires_docker
def test_openhands_required_imports_validated() -> None:
    """AC3: OpenHands executor validates required imports per ADR 0001.

    Verify that the executor checks for the required imports:
    - register_agent, agent_definition_to_factory, get_agent_factory, AgentFactory
    from openhands_sdk.subagent.registry.

    This test requires openhands-sdk to be installed; skipped in cloud.
    """
    # Instantiation validates all required imports.
    executor = OpenHandsExecutor()
    assert executor is not None

    # Verify required imports are accessible.
    from openhands_sdk.subagent.registry import (  # type: ignore
        AgentFactory,  # type: ignore
        agent_definition_to_factory,  # type: ignore
        get_agent_factory,  # type: ignore
        register_agent,  # type: ignore
    )

    assert callable(register_agent)  # type: ignore
    assert callable(agent_definition_to_factory)  # type: ignore
    assert callable(get_agent_factory)  # type: ignore
    assert AgentFactory is not None


@pytest.mark.requires_docker
def test_probe_fails_when_required_imports_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail-closed: probe detects missing required imports.

    Per ADR 0001, the adapter must fail closed if required imports are absent.
    Verify that a missing required import raises ImportError at instantiation.
    """
    # Hide a required import to simulate incompatibility.
    from typing import Any

    original_import = __builtins__.__dict__.get("__import__")

    def mock_import(name: str, *args: Any, **kwargs: Any) -> Any:
        # Block the AgentFactory import to simulate incompatibility.
        if name == "openhands_sdk.subagent.registry":
            raise ImportError("AgentFactory not found in registry (incompatible version)")
        return __import__(name, *args, **kwargs)

    try:
        monkeypatch.setattr("builtins.__import__", mock_import)

        # Instantiation must fail, not succeed silently.
        with pytest.raises(ImportError, match="required import"):
            OpenHandsExecutor()
    finally:
        # Restore.
        if original_import:
            __builtins__.__dict__["__import__"] = original_import


@pytest.mark.requires_docker
def test_executor_fails_on_unimplemented_run() -> None:
    """AC4: Executor fails closed (not implemented) until P03-3.

    P03-2 validates import structure and dependencies.
    P03-3 implements the actual tool invocation schema and OpenHands orchestration.

    Verify that calling run() raises NotImplementedError (fail-closed)
    rather than returning fabricated success.
    """
    executor = OpenHandsExecutor()

    # The run() method is not implemented until P03-3.
    # Fail-closed requirement (ADR 0001): never fake success.
    with pytest.raises(NotImplementedError, match="Tool invocation schema"):
        executor.run({"task": "test"})
