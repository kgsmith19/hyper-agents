"""OpenHands executor adapter (P03-2).

Implements ExecutorPort by delegating to the OpenHands SDK.
Real tool invocation is validated in CI via Docker; fail-closed here per ADR 0001.

This module validates that required OpenHands SDK imports are available.
If any import fails, the module cannot be loaded, triggering the fail-closed probe.
"""

from openhands.sdk.subagent.registry import (
    AgentFactory,
    agent_definition_to_factory,
    get_agent_factory,
    register_agent,
)

from hyper_agents.application.ports import ExecutorPort


class OpenHandsExecutor(ExecutorPort):
    """Orchestrates agent execution via OpenHands SDK.

    Real tool invocation is validated in CI (requires Docker).
    Fail-closed: raises NotImplementedError instead of fake success.

    The 4 required imports (register_agent, agent_definition_to_factory,
    get_agent_factory, AgentFactory) are validated at module load time.
    If any is missing, the module cannot be imported, triggering graceful failure.
    """

    def run(self, request: object) -> object:
        """Run a task via OpenHands executor.

        Real OpenHands execution (tool invocation, workspace, recovery) is
        defined in P03-3. This method fails closed: raises NotImplementedError
        instead of returning fake success.

        Args:
            request: Task request (format defined by OpenHands SDK schema).

        Raises:
            NotImplementedError: Always. Real execution deferred to P03-3.
        """
        raise NotImplementedError(
            "Real OpenHands execution defined in P03-3. "
            "Docker-backed execution is validated in CI (self-hosted runner). "
            "Cloud sessions test adapter contract only; full integration "
            "validated at release gate per Stage 61b (canary discipline)."
        )


__all__ = [
    "AgentFactory",
    "OpenHandsExecutor",
    "agent_definition_to_factory",
    "get_agent_factory",
    "register_agent",
]
