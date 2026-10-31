"""LLM-key tier of AC4: one real conversation in the pinned agent server (needs Docker and a key).

Skipped unless LLM_API_KEY is set. CI passes it from a repository secret on same-repo PRs only.
"""

import os

import pytest
from openhands.sdk import LLM, Agent, Conversation
from openhands.sdk.conversation.state import ConversationExecutionStatus
from openhands.workspace import DockerWorkspace
from pydantic import SecretStr

PINNED_IMAGE = "ghcr.io/openhands/agent-server:1.58.2-python"
DEFAULT_MODEL = "anthropic/claude-haiku-4-5-20251001"

api_key = os.environ.get("LLM_API_KEY")


@pytest.mark.requires_docker
@pytest.mark.skipif(not api_key, reason="LLM_API_KEY not set")
def test_one_conversation_finishes_in_pinned_agent_server() -> None:
    assert api_key is not None
    llm = LLM(
        usage_id="ci-smoke",
        model=os.environ.get("LLM_MODEL", DEFAULT_MODEL),
        api_key=SecretStr(api_key),
    )
    with DockerWorkspace(server_image=PINNED_IMAGE) as workspace:
        conversation = Conversation(agent=Agent(llm=llm, tools=[]), workspace=workspace)
        try:
            conversation.send_message("Reply with the single word: pong")
            conversation.run()
            assert conversation.state.execution_status == ConversationExecutionStatus.FINISHED
        finally:
            conversation.close()
