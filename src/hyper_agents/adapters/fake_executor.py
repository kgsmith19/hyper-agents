"""Fake executor for testing and offline workflows (P03-1).

The FakeExecutor implements ExecutorPort and returns deterministic results.
It is always available and never requires Docker, credentials, or network.
"""

from hyper_agents.application.ports import ExecutorPort


class FakeExecutor(ExecutorPort):
    """A no-op executor that returns success."""

    def run(self, request: object) -> object:
        """Run a task on a fake executor.

        Always succeeds immediately. Used for testing and dry runs.
        """
        return {"status": "success", "result": None}
