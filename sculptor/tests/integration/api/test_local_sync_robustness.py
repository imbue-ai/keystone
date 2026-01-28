"""
Integration tests for Local Sync robustness, including restart recovery.

Tests verify:
- Session re-syncs after restart required signal
- EnvironmentRestartHandler correctly receives and dispatches restart events
"""

import threading
import time
from pathlib import Path
from typing import Callable
from typing import ContextManager
from typing import Final
from typing import Generator

import pytest
from fastapi.testclient import TestClient
from pytest_mock import MockerFixture

from imbue_core.event_utils import ShutdownEvent
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.subprocess_utils import run_local_command_modern_version
from imbue_core.testing_utils import integration_test
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import EnvironmentCreatedRunnerMessage
from sculptor.interfaces.agents.agent import EnvironmentStoppedRunnerMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupAndEnabledMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdateCompletedMessage
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import EnvironmentRestartRequired
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.interfaces.environments.constants import ENVIRONMENT_WORKSPACE_DIRECTORY
from sculptor.primitives.ids import DockerImageID
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.services.local_sync_service._environment_restart_helpers import EnvironmentRestartHandler
from sculptor.services.task_service.base_implementation import BaseTaskService
from sculptor.tasks.handlers.run_agent import setup
from sculptor.testing.container_utils import DockerEnvironment
from tests.integration.api.local_sync_test_helpers import BatchExtractor
from tests.integration.api.local_sync_test_helpers import disable_sync
from tests.integration.api.local_sync_test_helpers import enable_sync_and_expect_success
from tests.integration.api.local_sync_test_helpers import task_message_batch_extractor
from tests.integration.api.local_sync_test_helpers import validate_no_local_sync_sessions_or_stashes_bleed_across_tests
from tests.integration.api.local_sync_test_helpers import verify_working_directory_clean

MESSAGE_WAIT_TIMEOUT_SECONDS: Final = 20.0


@pytest.fixture(autouse=True)
def validate_no_bleeding_across_tests(
    mock_repo_path: Path,  # just to force sequencing so a crash doesn't create misleading errors
    request: pytest.FixtureRequest,
    test_service_collection: CompleteServiceCollection,
) -> Generator[None, None, None]:
    function_name = request.function.__name__
    return validate_no_local_sync_sessions_or_stashes_bleed_across_tests(test_service_collection, function_name)


def get_container_image_id(environment: Environment) -> LocalDockerImage:
    assert isinstance(environment, DockerEnvironment), "Environment must be DockerEnvironment"
    result = run_local_command_modern_version(
        command=("docker", "inspect", "--format={{.Image}}", environment.environment_id),
    ).stdout.strip()
    return LocalDockerImage(image_id=DockerImageID(result), project_id=environment.project_id)


_raise_if_not_healthy_implementation = DockerEnvironment.raise_if_not_healthy


class SnapshotRestartController(MutableModel):
    force_once: bool = False
    supress_unforced_restarts: bool = True

    call_count: int = 0

    def force_and_wait(self, timeout_seconds: float) -> None:
        self.force_once = True
        start_time = time.time()
        while time.time() - start_time < timeout_seconds:
            if not self.force_once:
                return
            time.sleep(0.1)
        raise TimeoutError("Timeout waiting for patched_snapshot to be called")

    @property
    def raise_if_not_healthy(self) -> Callable[[DockerEnvironment], None]:
        def patch(env: DockerEnvironment) -> None:
            if self.force_once:
                self.force_once = False
                # don't care about actual `docker commit` here
                raise EnvironmentRestartRequired(get_container_image_id(env))
            return _raise_if_not_healthy_implementation(env)

        return patch


@pytest.fixture
def force_restart_controller(mocker: MockerFixture) -> Generator[SnapshotRestartController, None, None]:
    controller = SnapshotRestartController()
    mocker.patch.object(setup.DockerEnvironment, "raise_if_not_healthy", controller.raise_if_not_healthy)
    yield controller


@pytest.fixture
def extractor(
    fresh_task_and_state: tuple[Task, AgentTaskStateV1, str],
    test_service_collection: CompleteServiceCollection,
) -> Generator[BatchExtractor, None, None]:
    with task_message_batch_extractor(fresh_task_and_state[0], test_service_collection) as extractor:
        yield extractor


@integration_test
def test_local_sync_session_resyncs_after_restart(
    force_restart_controller: SnapshotRestartController,
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
    open_repo: Callable[[], ContextManager[WritableGitRepo]],
    fresh_task_and_state: tuple[Task, AgentTaskStateV1, str],
    extractor: BatchExtractor,
) -> None:
    """
    Test that local sync session re-syncs after a restart required signal from environment.snapshot.

    This test verifies:
    1. Local sync can be enabled
    2. The scheduler flushes a batch immediately
    3. When environment.snapshot raises EnvironmentRestartRequired, the session handles it
    4. The session sends a sync update message after handling the restart signal
    5. That file changes are correctly synced into the repo after the restart
    """
    message_wait_timeout = MESSAGE_WAIT_TIMEOUT_SECONDS
    # Create task but don't wait for it to fully halt - we need it running
    task, _state, _branch_name = fresh_task_and_state
    # Enable sync
    enable_sync_and_expect_success(client, active_test_project, task, is_stashing_ok=False)

    # Verify working directory is clean and sync is active
    verify_working_directory_clean(active_test_project, test_service_collection)

    # Get initial sync message count (should have setup message)
    _initial_messages = extractor.wait_for_new_messages(
        expected_types_in_order=(LocalSyncSetupAndEnabledMessage, LocalSyncUpdateCompletedMessage),
        timeout=message_wait_timeout,
    )

    force_restart_controller.force_and_wait(message_wait_timeout)

    # Wait for the sync to process the file change
    # After the restart (triggered by our patched snapshot), sync should send another update
    # Note: The restart happens in the agent loop, which may take some time
    _post_expected_restart_messages = extractor.wait_for_new_messages(
        expected_types_in_order=(EnvironmentCreatedRunnerMessage, LocalSyncUpdateCompletedMessage),
        timeout=message_wait_timeout,
    )

    file_name = "test_restart_trigger.txt"
    file_content = "trigger restart test content"
    with open_repo() as repo:
        test_file = Path(repo.get_repo_path()) / "test_restart_trigger.txt"
        test_file.write_text(file_content)
        repo._run_git(["add", file_name])
        repo._run_git(["commit", "-m", "Test commit for restart"])

    extractor.wait_for_new_messages(
        expected_types_in_order=(LocalSyncUpdateCompletedMessage,), timeout=message_wait_timeout, clear_first=True
    )
    with test_service_collection.data_model_service.open_transaction(RequestID()) as transaction:
        new_env = test_service_collection.task_service.get_task_environment(task.object_id, transaction)
        assert new_env is not None
    new_content = new_env.read_file(str(ENVIRONMENT_WORKSPACE_DIRECTORY / file_name))
    assert new_content == file_content, "Local Sync content should be synced to the environment after restart"

    new_content_from_agent = "new_content_from_agent_after_restart"
    new_env.write_file(str(ENVIRONMENT_WORKSPACE_DIRECTORY / file_name), new_content_from_agent)

    extractor.wait_for_new_messages(
        expected_types_in_order=(LocalSyncUpdateCompletedMessage,), timeout=message_wait_timeout, clear_first=True
    )
    user_content = test_file.read_text()
    assert new_content_from_agent == user_content, (
        "Local Sync content from agent should be synced back to the repo after restart"
    )

    # Disable sync to clean up
    status, result, _resp = disable_sync(client, active_test_project, task)
    assert status == 200, f"Should successfully disable sync. {status=}, {result=}"


@integration_test
def test_environment_restart_handler_lifecycle(
    test_service_collection: CompleteServiceCollection,
    fresh_task_and_state: tuple[Task, AgentTaskStateV1, str],
) -> None:
    """
    Test that EnvironmentRestartHandler calls on_new_environment when receiving EnvironmentCreatedRunnerMessage.

    This is an integration test that uses real task infrastructure to verify the handler
    correctly receives messages published through the task service.
    """
    task_service = test_service_collection.task_service
    assert isinstance(task_service, BaseTaskService), "cast to access _publish_task_update"
    task, _, _ = fresh_task_and_state

    # Get the task's real environment
    with test_service_collection.data_model_service.open_transaction(RequestID()) as transaction:
        env = test_service_collection.task_service.get_task_environment(task.object_id, transaction)
    assert isinstance(env, DockerEnvironment), "Task should have an environment"

    handler = EnvironmentRestartHandler(
        task_id=task.object_id,
        task_service=test_service_collection.task_service,
        queue_poll_interval_seconds=0.1,
    )

    received_environments: list[Environment] = []
    callback_called = threading.Event()

    def on_new_environment(new_env: Environment) -> None:
        received_environments.append(new_env)
        callback_called.set()

    shutdown_event = ShutdownEvent.build_root()

    # Create and start the background thread
    thread = handler.create_background_thread(shutdown_event, on_new_environment)
    thread.start()

    try:
        # Wait for subscription to be established
        time.sleep(0.2)

        # Publish an EnvironmentCreatedRunnerMessage through the real task service
        message = EnvironmentCreatedRunnerMessage(environment=env)
        task_service._publish_task_update(task, message)

        # Wait for the callback to be called
        assert callback_called.wait(timeout=2.0), "on_new_environment callback should have been called"
        assert len(received_environments) == 1, "Should have received exactly one environment"
        assert received_environments[0] is env, "Should have received the correct environment"
        assert thread.is_alive(), "Thread should still be running"

        # Publish an EnvironmentStoppedRunnerMessage through the real task service (should be ignored)
        task_service._publish_task_update(task, EnvironmentStoppedRunnerMessage())

        time.sleep(0.3)
        assert thread.is_alive(), "Thread should still be running"
        assert len(received_environments) == 1, "Should have received no more environments"

        shutdown_event.set()

        task_service._publish_task_update(task, message)
        assert len(received_environments) == 1, "Should have received no more environments after shutdown"

        # Thread should stop within a reasonable time
        thread.join(timeout=2.0)
        assert not thread.is_alive(), "Thread should have stopped after shutdown event"

    finally:
        shutdown_event.set()
        thread.join(timeout=2.0)
