"""Unit tests for task service configuration messaging."""

from typing import Generator

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.secrets_utils import Secret
from sculptor.database.models import MustBeShutDownTaskInputsV1
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import SetUserConfigurationDataUserMessage
from sculptor.primitives.ids import OrganizationReference
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.config_service.data_types import GlobalConfiguration
from sculptor.services.config_service.local_implementation import LocalConfigService
from sculptor.services.config_service.user_config import get_user_config_instance
from sculptor.services.config_service.user_config import set_user_config_instance
from sculptor.services.task_service.threaded_implementation import LocalThreadTaskService
from sculptor.web.auth import authenticate_anonymous

TEST_API_KEY = "sk-ant-test12345678901234567890123456789012345678901234567890"
UPDATED_API_KEY = "sk-ant-updated12345678901234567890123456789012345678901234"
TEST_COMMANDS = ["git"]
ORGANIZATION_NAME = "test_org"


@pytest.fixture
def patch_is_claude_configuration_synchronized() -> Generator[None, None, None]:
    original_config = get_user_config_instance()
    updated_config = original_config.evolve(original_config.ref().is_claude_configuration_synchronized, True)
    set_user_config_instance(updated_config)
    try:
        yield
    finally:
        set_user_config_instance(original_config)


@pytest.fixture
def test_project(test_service_collection: CompleteServiceCollection) -> Project:
    """Create a test project."""
    with test_service_collection.data_model_service.open_transaction(RequestID()) as transaction:
        project = Project(
            object_id=ProjectID(),
            name="Test Project",
            organization_reference=OrganizationReference(ORGANIZATION_NAME),
            user_git_repo_url="file:///tmp/test_repo",
        )
        transaction.upsert_project(project)
        return project


def test_user_configuration_broadcast_to_active_tasks(
    test_service_collection: CompleteServiceCollection,
    test_project: Project,
    patch_is_claude_configuration_synchronized: None,
) -> None:
    user_session = authenticate_anonymous(test_service_collection, RequestID())

    tasks = []
    with test_service_collection.data_model_service.open_transaction(RequestID()) as transaction:
        for _ in range(3):
            task_obj = Task(
                object_id=TaskID(),
                user_reference=user_session.user_reference,
                organization_reference=user_session.organization_reference,
                project_id=test_project.object_id,
                parent_task_id=None,
                input_data=MustBeShutDownTaskInputsV1(),
            )
            task = test_service_collection.task_service.create_task(task_obj, transaction)
            tasks.append(task)

    initial_counts = {}
    for task in tasks:
        with test_service_collection.task_service.subscribe_to_task(task.object_id) as message_queue:
            messages = list(message_queue.queue)
            initial_counts[task.object_id] = len(
                [m for m in messages if isinstance(m, SetUserConfigurationDataUserMessage)]
            )

    updated_config = GlobalConfiguration(
        credentials=Credentials(anthropic=AnthropicApiKey(anthropic_api_key=Secret(UPDATED_API_KEY))),
    )

    assert isinstance(test_service_collection.task_service, LocalThreadTaskService)
    test_service_collection.task_service._on_updated_user_configuration(updated_config)

    for task in tasks:
        with test_service_collection.task_service.subscribe_to_task(task.object_id) as message_queue:
            messages = list(message_queue.queue)
            user_config_msgs = [m for m in messages if isinstance(m, SetUserConfigurationDataUserMessage)]

        expected_count = initial_counts[task.object_id] + 1
        assert len(user_config_msgs) == expected_count, " ".join(
            [
                f"Task {task.object_id} should have {expected_count} config messages",
                f"({initial_counts[task.object_id]} initial + 1 update), got {len(user_config_msgs)}",
            ]
        )

        msg = user_config_msgs[-1]
        assert msg.credentials == updated_config.credentials


def test_user_configuration_watcher_registered_on_start(
    test_service_collection: CompleteServiceCollection,
    patch_is_claude_configuration_synchronized: None,
) -> None:
    config_service = test_service_collection.config_service

    assert hasattr(config_service, "_anthropic_global_configuration_watchers"), (
        "Config service missing _user_config_watchers attribute - watcher mechanism not available"
    )

    assert isinstance(config_service, LocalConfigService)
    assert len(config_service._anthropic_global_configuration_watchers) > 0, (
        "No user config watchers registered - task service should register a watcher on start"
    )
