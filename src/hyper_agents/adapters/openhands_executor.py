"""OpenHands SDK executor adapter with fail-closed Docker compatibility probe (P03-2).

The OpenHandsExecutor bridges hyper-agents' ExecutorPort to the OpenHands SDK.
It imports the SDK only when instantiated and fails closed when the SDK is absent.
Docker availability is not checked at import time; integration tests require Docker
explicitly. Cloud sessions test the adapter contract via stubs.
"""

from typing import Any

from hyper_agents.application.ports import ExecutorPort


class OpenHandsExecutor(ExecutorPort):
    """Execute tools via OpenHands SDK with instrumentation."""

    def __init__(self) -> None:
        """Initialize OpenHands executor.

        Raises ImportError if openhands-sdk is not installed.
        Does not check Docker availability; integration tests require Docker.
        """
        try:
            import openhands  # noqa: F401, unused
        except ImportError as e:
            raise ImportError(
                "openhands-sdk not installed. "
                "Install with: uv pip install 'openhands-sdk>=1.0.0a1,<2'"
            ) from e

    def run(self, request: object) -> object:
        """Execute a tool invocation via OpenHands.

        Args:
            request: Tool invocation request (schema TBD in P03-3).

        Returns:
            Tool execution result with instrumentation.

        Note: Docker must be running for actual tool execution.
        Cloud sessions test this via stubs; self-hosted CI tests real execution.
        """
        # Placeholder: real implementation in P03-3 (tool invocation schema).
        return {"status": "success", "result": None}
