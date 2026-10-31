"""OpenHands executor probe tests (P03-2).

Checks the SDK import contract and the fail-closed behavior of OpenHandsExecutor.
These run without Docker. Docker-backed execution is not covered here (AC4 is open).
"""

import pytest

from hyper_agents.adapters.fake_executor import FakeExecutor
from hyper_agents.adapters.openhands_executor import OpenHandsExecutor
from hyper_agents.application.ports import ExecutorPort


def test_probe_validates_required_imports() -> None:
    from openhands.sdk.subagent.registry import (
        AgentFactory,
        agent_definition_to_factory,
        get_agent_factory,
        register_agent,
    )

    assert callable(register_agent)
    assert callable(agent_definition_to_factory)
    assert callable(get_agent_factory)
    assert isinstance(AgentFactory, type)


def test_executor_contract_with_port() -> None:
    executor = OpenHandsExecutor()
    assert isinstance(executor, ExecutorPort)

    with pytest.raises(NotImplementedError, match="Real OpenHands execution defined in P03-3"):
        executor.run({})


def test_executor_is_not_fake() -> None:
    assert FakeExecutor().run({}) == {"status": "success", "result": None}

    with pytest.raises(NotImplementedError):
        OpenHandsExecutor().run({})
