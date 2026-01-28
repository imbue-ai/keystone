import os
from pathlib import Path

import pytest

from imbue_core.git import get_repo_base_path
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import CleanupImagesInputsV1
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import HelloAgentConfig
from sculptor.interfaces.agents.agent import MessageTypes
from sculptor.interfaces.environments.base import LocalImageConfig
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.config_service.data_types import GlobalConfiguration
from sculptor.services.data_model_service.data_types import DataModelTransaction
from sculptor.services.task_service.base_implementation import BaseTaskService
from sculptor.web.auth import authenticate_anonymous


# This is copied from threaded_implementation_test
@pytest.fixture
def specimen_project(test_service_collection: CompleteServiceCollection) -> Project:
    project_path: str | Path | None = os.getenv("PROJECT_PATH")
    if isinstance(project_path, str):
        project_path = Path(project_path)
    if not project_path:
        project_path = get_repo_base_path()
    user_session = authenticate_anonymous(test_service_collection, RequestID())
    with user_session.open_transaction(test_service_collection) as transaction:
        project = test_service_collection.project_service.initialize_project(
            project_path=project_path,
            organization_reference=user_session.organization_reference,
            transaction=transaction,
        )
    test_service_collection.project_service.activate_project(project)
    assert project is not None, "By now, the project should be initialized."
    return project


# FIXME: Get this used so we can localize our testing to BaseTaskService instead of ThreadedImplementation
class NoOpTaskService(BaseTaskService):
    """This is a version of task service used for testing"""

    def create_message(self, message: MessageTypes, task_id: TaskID, transaction: DataModelTransaction) -> None:
        pass

    def on_new_task(self, task: Task) -> None:
        pass

    def on_restore_task(self, task: Task) -> None:
        pass


# FIXME: Re-enable this test once we fix the teardown issue with threads not finishing in time.
# Also add a test with nonzero data (actual ClaudeGlobalConfiguration values).
@pytest.mark.skip(reason="Teardown issue: threads don't finish in time")
def test_refresh_global_anthropic_configuration(
    test_service_collection: CompleteServiceCollection, specimen_project: Project
):
    """
    We expect _refresh_global_anthropic_configuration to send a SetUserConfigurationDataUserMessage to every user task.

    Returns: the number of updated tasks
    """

    task_service = test_service_collection.task_service
    user_session = authenticate_anonymous(test_service_collection, RequestID())
    user_task = Task(
        object_id=TaskID(),
        user_reference=user_session.user_reference,
        organization_reference=user_session.organization_reference,
        project_id=specimen_project.object_id,
        parent_task_id=None,
        input_data=AgentTaskInputsV1(
            agent_config=HelloAgentConfig(),
            image_config=LocalImageConfig(code_directory=Path()),
            git_hash="",
            initial_branch="",
            is_git_state_clean=True,
        ),
    )

    periodic_task = Task(
        object_id=TaskID(),
        user_reference=user_session.user_reference,
        organization_reference=user_session.organization_reference,
        project_id=specimen_project.object_id,
        parent_task_id=None,
        input_data=CleanupImagesInputsV1(),
    )

    with user_session.open_transaction(test_service_collection) as transaction:
        task_service.create_task(task=user_task, transaction=transaction)
        task_service.create_task(task=periodic_task, transaction=transaction)
        # pyre-fixme[16]: `TaskService` has no attribute `_refresh_global_anthropic_configuration`.
        modified_tasks = task_service._refresh_global_anthropic_configuration(
            config=GlobalConfiguration(), transaction=transaction
        )
        # These don't seem to see any messages
        user_task_messages = task_service.get_saved_messages_for_task(
            task_id=user_task.object_id, transaction=transaction
        )
        periodic_task_messages = task_service.get_saved_messages_for_task(
            task_id=periodic_task.object_id, transaction=transaction
        )

    assert modified_tasks == 1
