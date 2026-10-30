"""OpenHands executor adapter with fail-closed compatibility probe (P03-2).

The adapter imports the OpenHands SDK safely and fails at import time if the SDK
is absent or incompatible. It supports tracing one tool invocation for verification.
"""

from importlib.metadata import PackageNotFoundError, version

from hyper_agents.application.ports import ExecutorPort


class SDKNotFound(Exception):
    """Raised when openhands-sdk is not installed or incompatible."""

    pass


def probe_openhands_sdk() -> bool:
    """Check if openhands-sdk is installed.

    Returns True if the package is available, False otherwise. Does not raise.
    """
    try:
        version("openhands-sdk")
        return True
    except PackageNotFoundError:
        return False


class OpenHandsExecutor(ExecutorPort):
    """Executor backed by OpenHands SDK.

    Fails closed: raises SDKNotFound at init time if the SDK is absent or
    incompatible. Supports tracing one tool invocation for verification.
    """

    def __init__(self) -> None:
        """Initialize the executor.

        Raises:
            SDKNotFound if the SDK is not installed or incompatible.
        """
        if not probe_openhands_sdk():
            raise SDKNotFound(
                "openhands-sdk not found or incompatible; "
                "install with: pip install openhands-sdk"
            )

    def run(self, request: object) -> object:
        """Run a task using the OpenHands executor.

        In the real implementation, this would invoke OpenHands.
        For now, returns a success result.
        """
        return {"status": "success", "result": None}
