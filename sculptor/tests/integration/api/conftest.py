"""Fixtures for API integration tests."""

import os
import time
from pathlib import Path
from typing import Any
from typing import Callable
from typing import ContextManager
from typing import Generator

import pytest
from fastapi import Depends
from fastapi.testclient import TestClient
from loguru import logger
from pytest import fixture
from syrupy.assertion import SnapshotAssertion

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.pydantic_serialization import model_dump
from imbue_core.sculptor.state.messages import LLMModel
from imbue_core.secrets_utils import Secret
from sculptor.cli.main import ensure_core_log_levels_configured
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Project
from sculptor.database.models import Task
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.tasks import TaskState
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.service_collections.service_collection import get_services
from sculptor.services.config_service.data_types import AnthropicApiKey
from sculptor.services.config_service.data_types import Credentials
from sculptor.services.data_model_service.data_types import TaskAndDataModelTransaction
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.testing.caching_utils import save_caches_to_snapshot_directory
from sculptor.testing.constants import RUNNING_TIMEOUT_SECS
from sculptor.testing.container_utils import get_containers_with_tasks
from sculptor.testing.resources import AlreadyRunningServiceCollection
from sculptor.testing.resources import get_cache_dir_from_snapshot
from sculptor.testing.resources import sculptor_folder_
from sculptor.utils.build import SCULPTOR_FOLDER_OVERRIDE_ENV_FLAG
from sculptor.utils.build import get_sculptor_folder
from sculptor.web.app import APP
from sculptor.web.auth import UserSession
from sculptor.web.auth import authenticate_anonymous
from sculptor.web.data_types import StartTaskRequest
from sculptor.web.derived import CodingAgentTaskView
from sculptor.web.middleware import get_settings
from sculptor.web.middleware import services_factory

_keep_import_for_fixture_injection = sculptor_folder_


# TODO: overriding to skip pulling in IsolationLevel but maybe we should parameterize everything here also? IDK
@pytest.fixture
def credentials_(snapshot: SnapshotAssertion) -> Credentials:
    # TODO: Currently no snapshot-requiring tests here, but generally IDK how to get them
    # The following didn't seem to work
    # pytest -s -v --snapshot-update sculptor/tests/integration/api/local_sync_stashing_test.py
    anthropic_api_key = "sk-ant-fake-api-key"
    if snapshot.session.update_snapshots:
        anthropic_api_key = os.environ["ANTHROPIC_API_KEY"]
    return Credentials(anthropic=AnthropicApiKey(anthropic_api_key=Secret(anthropic_api_key)))


@pytest.fixture
def sculptor_folder_monkey_patch(
    sculptor_folder_: Path, monkeypatch: pytest.MonkeyPatch
) -> Generator[Path, None, None]:
    monkeypatch.setenv(SCULPTOR_FOLDER_OVERRIDE_ENV_FLAG, str(sculptor_folder_))
    get_sculptor_folder.cache_clear()
    yield sculptor_folder_


@pytest.fixture
def test_settings_for_api(
    sculptor_folder_monkey_patch: Path, test_settings: SculptorSettings, snapshot: SnapshotAssertion
) -> SculptorSettings:
    snapshot_path = get_cache_dir_from_snapshot(snapshot)
    testing_settings_update: dict[str, Any] = {"INTEGRATION_ENABLED": True}
    if not snapshot.session.update_snapshots:
        testing_settings_update["SNAPSHOT_PATH"] = snapshot_path
    testing = test_settings.TESTING.model_copy(update=testing_settings_update)
    copy = test_settings.model_copy(update={"TESTING": testing})
    assert copy.TESTING.INTEGRATION_ENABLED
    return copy


# hate conftest so much.
# Here we shadow test_service_collection higher up to reverso-inject it into other fixtures like test_project
@pytest.fixture
def test_service_collection(
    test_root_concurrency_group: ConcurrencyGroup,
    test_settings_for_api: SculptorSettings,
    sculptor_folder_monkey_patch: Path,
) -> Generator[CompleteServiceCollection, None, None]:
    services = get_services(
        test_root_concurrency_group, test_settings_for_api, should_start_image_downloads_in_background=False
    )
    with services.run_all():
        yield AlreadyRunningServiceCollection.build(services)


@pytest.fixture
def active_test_project(test_project: Project, test_service_collection: CompleteServiceCollection) -> Project:
    repo = test_project.user_git_repo_url
    assert repo is not None and repo.startswith("file://"), f"Got repo: {repo}"
    repo_path = Path(repo[len("file://") :])
    assert repo_path.exists(), f"Repo path does not exist: {repo_path}"
    test_service_collection.project_service.activate_project(test_project)
    return test_project


# TODO: copied from sculptor/web/conftest.py - consider deduplicating
# https://imbue-ai.slack.com/archives/C0799HVGR7W/p1763536756644609
@pytest.fixture
def client(
    test_settings_for_api: SculptorSettings, test_service_collection: CompleteServiceCollection
) -> Generator[TestClient, None, None]:
    """TestClient fixture for API integration tests using test_service_collection."""
    # TODO: not sure why this was necessary...
    ensure_core_log_levels_configured()

    def override_get_settings() -> SculptorSettings:
        return test_settings_for_api

    def override_services_factory(
        concurrency_group: ConcurrencyGroup, settings: SculptorSettings = Depends(get_settings)
    ) -> CompleteServiceCollection:
        return test_service_collection

    APP.dependency_overrides[get_settings] = override_get_settings
    APP.dependency_overrides[services_factory] = override_services_factory
    with TestClient(APP) as test_client:
        yield test_client
    APP.dependency_overrides.clear()


def _is_task_inert(task: CodingAgentTaskView) -> bool:
    return task.status not in (TaskState.QUEUED, TaskState.RUNNING)


def _inert_task_ratio(services: CompleteServiceCollection, project_id: ProjectID) -> tuple[int, int]:
    with services.data_model_service.open_transaction(RequestID()) as transaction:
        assert isinstance(transaction, TaskAndDataModelTransaction)
        tasks = transaction.get_tasks_for_project(project_id, is_archived=False)
        views = []
        for task in tasks:
            view = CodingAgentTaskView()
            view._task_container.append(task)
            for message in services.task_service.get_saved_messages_for_task(task.object_id, transaction):
                view.add_message(message)
            views.append(view)
    inert_count = len([task for task in views if _is_task_inert(task)])
    return inert_count, len(tasks)


def wait_for_all_tasks_to_halt(
    services: CompleteServiceCollection, project_id: ProjectID, timeout_seconds: int = RUNNING_TIMEOUT_SECS
) -> None:
    start_time = time.time()
    inert, total = _inert_task_ratio(services, project_id)
    while inert < total:
        if time.time() - start_time > timeout_seconds:
            raise TimeoutError(f"Timed out waiting for tasks to halt ({inert}/{total} stopped, {timeout_seconds=})")
        time.sleep(1)
        inert, total = _inert_task_ratio(services, project_id)
    logger.info(f"All tasks halted for project {project_id} ({inert}/{total})")


# TODO: copied from sculptor/sculptor/testing/server_utils.py
# where logic accounts for error handling but is very entangled.
# Am just trying to get CI to pass at all
@pytest.fixture(autouse=True)
def update_snapshots_when_requested(
    request: pytest.FixtureRequest,
    snapshot: SnapshotAssertion,
    test_settings_for_api: SculptorSettings,
    test_service_collection: CompleteServiceCollection,  # ensure containers are alive while inspecting
    active_test_project: Project,
) -> Generator[None, None, None]:
    yield

    wait_for_all_tasks_to_halt(test_service_collection, active_test_project.object_id)

    if not snapshot.session.update_snapshots:
        return

    snapshot_dir = get_cache_dir_from_snapshot(snapshot)
    logger.debug(f"Snapshot dir for test: {snapshot_dir}")
    if request.node.rep_setup.failed or request.node.rep_call.failed:
        logger.debug("Ignoring snapshot update due to test failure for {}", request.node.name)
        return

    with ConcurrencyGroup(name="adhoc_testing_concurrency_group") as concurrency_group:
        containers_with_tasks = get_containers_with_tasks(test_settings_for_api.DATABASE_URL, concurrency_group)

    logger.info(
        "Saving snapshot caches for {} into {} ({} containers)",
        request.node.name,
        snapshot_dir,
        len(containers_with_tasks),
    )
    save_caches_to_snapshot_directory(local_path=snapshot_dir, containers_with_tasks=containers_with_tasks)


def wait_for_task_environment_ready(
    services: CompleteServiceCollection,
    user_session: UserSession,
    task_id: TaskID,
    timeout_seconds: float = 120.0,
) -> Task:
    """Wait for the task environment to be ready (task_repo_path to be set)."""
    poll_interval = 0.5
    elapsed = 0.0

    while elapsed < timeout_seconds:
        with user_session.open_transaction(services) as transaction:
            task = services.task_service.get_task(task_id, transaction)
            if not (task and task.current_state):
                continue
            assert isinstance(task.current_state, AgentTaskStateV1)
            if task.current_state.task_repo_path:
                return task

        time.sleep(poll_interval)
        elapsed += poll_interval

    raise TimeoutError(f"Task {task_id} did not become ready within {timeout_seconds} seconds")


def create_test_task_with_state(
    client: TestClient,
    user_session: UserSession,
    project: Project,
    services: CompleteServiceCollection,
    branch: str = "main",
    wait_for_halt: bool = True,
    prompt: str = "Test task for stashing",
) -> Task:
    response = client.post(
        f"/api/v1/projects/{project.object_id}/tasks",
        json=model_dump(
            StartTaskRequest(
                prompt=prompt,
                source_branch=branch,
                model=LLMModel.CLAUDE_4_SONNET,
            ),
            is_camel_case=True,
        ),
    )
    assert response.status_code == 200, f"Failed to create task: {response.status_code} - {response.text}"

    task_view = response.json()
    task = wait_for_task_environment_ready(services, user_session, TaskID(task_view["id"]))
    if wait_for_halt:
        wait_for_all_tasks_to_halt(services, project.object_id)
    return task


@pytest.fixture
def fresh_task_and_state(
    client: TestClient,
    test_service_collection: CompleteServiceCollection,
    active_test_project: Project,
) -> tuple[Task, AgentTaskStateV1, str]:
    user_session = authenticate_anonymous(test_service_collection, RequestID())
    task = create_test_task_with_state(client, user_session, active_test_project, test_service_collection)
    assert isinstance(task.current_state, AgentTaskStateV1)
    task_branch = task.current_state.branch_name
    assert task_branch is not None, "Task branch name should be set"
    return (task, task.current_state, task_branch)


@fixture
def open_repo(
    test_service_collection: CompleteServiceCollection, active_test_project: Project
) -> Callable[[], ContextManager[WritableGitRepo]]:
    return lambda: test_service_collection.git_repo_service.open_local_user_git_repo_for_write(active_test_project)
