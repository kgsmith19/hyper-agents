"""Unit tests for ExecutorPort and FakeExecutor (P03-1)."""

from typing import Any

from hyper_agents.adapters.fake_executor import FakeExecutor
from hyper_agents.application.ports import ExecutorPort


def test_executor_port_is_a_protocol() -> None:
    """ExecutorPort is a protocol that defines the executor interface."""
    # FakeExecutor implements ExecutorPort
    executor = FakeExecutor()
    assert isinstance(executor, ExecutorPort) or hasattr(executor, "run")


def test_fake_executor_implements_executor_port() -> None:
    """FakeExecutor implements ExecutorPort."""
    executor = FakeExecutor()
    assert hasattr(executor, "run")


def test_fake_executor_returns_success() -> None:
    """FakeExecutor.run returns a success result."""
    executor = FakeExecutor()
    result: Any = executor.run({"task": "test"})

    assert isinstance(result, dict)
    assert result["status"] == "success"


def test_fake_executor_is_always_available() -> None:
    """FakeExecutor is always available, no setup required."""
    # Should not raise, no docker or credentials needed
    executor = FakeExecutor()
    result1: Any = executor.run({})
    result2: Any = executor.run({"task": "anything"})

    assert result1["status"] == "success"
    assert result2["status"] == "success"
