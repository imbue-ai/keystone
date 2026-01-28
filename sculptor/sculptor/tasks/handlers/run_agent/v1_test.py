from datetime import datetime
from datetime import timezone
from queue import Queue
from unittest.mock import MagicMock

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.sculptor.state.messages import ChatInputUserMessage
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.sculptor.state.messages import Message
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Project
from sculptor.database.models import ProjectID
from sculptor.database.models import Task
from sculptor.database.models import TaskID
from sculptor.database.models import TaskState
from sculptor.interfaces.agents.agent import Agent
from sculptor.interfaces.agents.agent import ClaudeCodeSDKAgentConfig
from sculptor.interfaces.agents.agent import CommandInputUserMessage
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.primitives.ids import DockerImageID
from sculptor.primitives.ids import OrganizationReference
from sculptor.primitives.ids import UserReference
from sculptor.tasks.handlers.run_agent.setup import _drop_already_processed_messages
from sculptor.tasks.handlers.run_agent.v1 import _handle_completed_agent


def test_drop_already_processed_messages_with_processed_id() -> None:
    """Test dropping messages up to last_processed_input_message_id."""
    user_queue: Queue[Message] = Queue()

    # Create test messages
    msg1 = ChatInputUserMessage(
        message_id=AgentMessageID(),
        text="First message",
        model_name=LLMModel.CLAUDE_4_SONNET,
    )
    msg2 = CommandInputUserMessage(
        message_id=AgentMessageID(),
        text="ls -la",
        is_included_in_context=True,
    )
    target_msg = ChatInputUserMessage(
        message_id=AgentMessageID(),
        text="Target message",
        model_name=LLMModel.CLAUDE_4_SONNET,
    )
    msg3 = ChatInputUserMessage(
        message_id=AgentMessageID(),
        text="Should remain",
        model_name=LLMModel.CLAUDE_4_SONNET,
    )

    # Add messages to queue
    user_queue.put(msg1)
    user_queue.put(msg2)
    user_queue.put(target_msg)
    user_queue.put(msg3)

    # Drop messages up to target
    dropped, _ = _drop_already_processed_messages(
        last_processed_input_message_id=target_msg.message_id,
        user_message_queue=user_queue,
    )

    # Verify results
    assert len(dropped) == 3
    assert dropped == (msg1, msg2, target_msg)
    assert user_queue.qsize() == 1
    assert user_queue.get() == msg3


def test_drop_already_processed_messages_none_values() -> None:
    """Test edge case with None value for last_processed_input_message_id."""
    user_queue: Queue[Message] = Queue()

    # Create test messages
    msg1 = ChatInputUserMessage(
        message_id=AgentMessageID(),
        text="First message",
        model_name=LLMModel.CLAUDE_4_SONNET,
    )
    msg2 = CommandInputUserMessage(
        message_id=AgentMessageID(),
        text="pwd",
        is_included_in_context=True,
    )

    user_queue.put(msg1)
    user_queue.put(msg2)

    # Test with None - should not drop anything
    dropped, _ = _drop_already_processed_messages(
        last_processed_input_message_id=None,
        user_message_queue=user_queue,
    )

    assert len(dropped) == 0
    assert user_queue.qsize() == 2


def test_drop_already_processed_messages_empty_queue() -> None:
    """Test with empty queue."""
    user_queue: Queue[Message] = Queue()

    dropped, _ = _drop_already_processed_messages(
        last_processed_input_message_id=None,
        user_message_queue=user_queue,
    )

    assert len(dropped) == 0
    assert user_queue.empty()


def test_handle_completed_agent_with_snapshot_during_shutdown() -> None:
    """
    This is a regression test that ensures that that _handle_completed_agent
    uses the up-to-date `task_state`, not the stale `task.current_state`
    """
    # Set up the inputs
    task_input = AgentTaskInputsV1(
        agent_config=ClaudeCodeSDKAgentConfig(),
        image_config=LocalDevcontainerImageConfig(devcontainer_json_path="/test/path"),
        environment_config=LocalDockerEnvironmentConfig(),
        available_secrets=None,
        git_hash="test_hash",
        initial_branch="main",
        is_git_state_clean=True,
        system_prompt=None,
    )
    task = Task(
        created_at=datetime.now(timezone.utc),
        object_id=TaskID(),
        organization_reference=OrganizationReference("test_org"),
        user_reference=UserReference("test_user"),
        project_id=ProjectID(),
        parent_task_id=None,
        input_data=task_input,
        max_seconds=None,
        current_state=None,
        outcome=TaskState.RUNNING,
        error=None,
        is_archived=False,
        is_archiving=False,
        is_deleted=False,
        is_deleting=False,
    )
    initial_image = LocalDockerImage(
        image_id=DockerImageID("initial_image_id"),
        project_id=task.project_id,
    )
    task_state = AgentTaskStateV1(image=initial_image)

    # Mock the agent, environment, services, etc.
    agent_wrapper = MagicMock(spec=Agent)
    agent_wrapper.pop_messages.return_value = []
    agent_wrapper.wait = MagicMock()

    project = MagicMock(spec=Project)
    environment = MagicMock(spec=Environment)

    # This is the new snapshot that we want to see saved to the DB
    environment.snapshot.return_value = LocalDockerImage(
        image_id=DockerImageID("new_snapshot_image_id"),
        project_id=task.project_id,
    )

    persisted_task = None

    def capture_upsert_task(updated_task: Task) -> Task:
        nonlocal persisted_task
        persisted_task = updated_task
        return updated_task

    mock_transaction = MagicMock()
    mock_transaction.get_task.return_value = task
    mock_transaction.upsert_task.side_effect = capture_upsert_task

    services = MagicMock()
    services.data_model_service.open_task_transaction.return_value.__enter__.return_value = mock_transaction
    services.data_model_service.open_task_transaction.return_value.__exit__.return_value = None
    services.task_service.create_message = MagicMock()
    services.config_service.get_user_config.return_value = {}

    settings = MagicMock(spec=SculptorSettings)

    # Call _handle_completed_agent with is_dirty=True to trigger the snapshot path
    result = _handle_completed_agent(
        agent_wrapper=agent_wrapper,
        exit_code=0,
        task=task,
        task_state=task_state,
        project=project,
        environment=environment,
        services=services,
        is_dirty=True,
        last_user_chat_message_id=AgentMessageID(),
        settings=settings,
    )
    assert result is not None

    # Verify that the task was persisted to the database with updated state
    assert persisted_task is not None, "Task should have been persisted to database"
    assert persisted_task.current_state is not None, "Task should have a current_state"
    assert isinstance(persisted_task.current_state, AgentTaskStateV1), "Should be AgentTaskStateV1 instance"
    assert persisted_task.current_state.image is not None, "Task state should include the snapshot image"

    assert persisted_task.current_state.image.image_id == "new_snapshot_image_id", (
        "Image ID should be the new snapshot"
    )
    assert mock_transaction.get_task.called, "Should fetch task from DB before updating"
    assert mock_transaction.upsert_task.called, "Should persist updated task state to DB"
