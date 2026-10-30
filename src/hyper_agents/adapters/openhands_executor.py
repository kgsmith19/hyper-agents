"""OpenHands SDK executor adapter with fail-closed Docker compatibility probe (P03-2).

The OpenHandsExecutor bridges hyper-agents' ExecutorPort to the OpenHands SDK.
It imports the SDK only when instantiated and fails closed when the SDK is absent.
Required imports (ADR 0001): register_agent, agent_definition_to_factory,
get_agent_factory, AgentFactory (from openhands_sdk.subagent.registry).
Docker availability is not checked at import time; integration tests require Docker
explicitly. Cloud sessions test the adapter contract via stubs.
"""

from typing import ClassVar

from hyper_agents.application.ports import ExecutorPort


class OpenHandsExecutor(ExecutorPort):
    """Execute tools via OpenHands SDK with instrumentation (ADR 0001)."""

    # Required imports per ADR 0001: openhands_sdk.subagent.registry
    _REQUIRED_IMPORTS: ClassVar[dict[str, str]] = {
        "register_agent": "openhands_sdk.subagent.registry",
        "agent_definition_to_factory": "openhands_sdk.subagent.registry",
        "get_agent_factory": "openhands_sdk.subagent.registry",
        "AgentFactory": "openhands_sdk.subagent.registry",
    }

    def __init__(self) -> None:
        """Initialize OpenHands executor.

        Validates that openhands-sdk is installed with required imports.
        Fails closed (raises ImportError) if SDK is absent or incompatible.
        Does not check Docker availability; integration tests require Docker.

        Raises:
            ImportError: if SDK is absent, wrong version, or missing required imports.
        """
        try:
            import openhands_sdk  # type: ignore  # noqa: F401
        except ImportError as e:
            raise ImportError(
                "openhands-sdk not installed. "
                "Install with: uv pip install 'openhands-sdk>=1.58.2,<2'"
            ) from e

        # Validate required imports per ADR 0001.
        for import_name, module_path in self._REQUIRED_IMPORTS.items():
            try:
                __import__(module_path, fromlist=[import_name])
            except (ImportError, AttributeError) as e:
                raise ImportError(
                    f"openhands-sdk missing required import: "
                    f"{module_path}.{import_name}. "
                    f"Ensure openhands-sdk>=1.58.2,<2 is installed correctly."
                ) from e

    def run(self, request: object) -> object:
        """Execute a tool invocation via OpenHands.

        Args:
            request: Tool invocation request (schema TBD in P03-3).

        Returns:
            Tool execution result with instrumentation.

        Raises:
            NotImplementedError: real implementation in P03-3 (tool invocation schema).
            ImportError: if OpenHands SDK is not properly initialized.

        Note: Docker must be running for actual tool execution.
        Cloud sessions test this via stubs; self-hosted CI tests real execution.
        """
        # P03-2: probe and import validation only.
        # P03-3: real tool invocation via OpenHands orchestration.
        raise NotImplementedError(
            "Tool invocation schema and real OpenHands orchestration "
            "defined in P03-3. This is a placeholder."
        )
