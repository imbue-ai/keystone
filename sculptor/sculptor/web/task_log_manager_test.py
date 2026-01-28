from contextlib import contextmanager
from typing import Iterator

import pytest

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import HelloAgentConfig
from sculptor.interfaces.agents.agent import TaskState
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.primitives.ids import OrganizationReference
from sculptor.primitives.ids import UserReference
from sculptor.services.task_service.api import TaskMessageContainer
from sculptor.web import task_log_manager


class _FakeLogWatcher:
    def __init__(self, task_id: TaskID, queue, settings, concurrency_group) -> None:
        self.task_id = task_id
        self.started = False
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


def _make_task(state: TaskState = TaskState.RUNNING) -> Task:
    return Task(
        object_id=TaskID(),
        organization_reference=OrganizationReference("org"),
        user_reference=UserReference("user"),
        project_id=ProjectID(),
        parent_task_id=None,
        input_data=AgentTaskInputsV1(
            agent_config=HelloAgentConfig(),
            image_config=LocalDevcontainerImageConfig(devcontainer_json_path="devcontainer.json"),
            git_hash="abc",
            initial_branch="main",
            is_git_state_clean=True,
        ),
        outcome=state,
    )


class _FakeTransaction:
    def __init__(self, tasks: list[Task]) -> None:
        self._tasks = tasks

    def get_tasks_for_user(self, _user_reference: UserReference) -> list[Task]:
        return self._tasks


class _FakeDataModelService:
    def __init__(self) -> None:
        self._tasks: list[Task] = []

    @contextmanager
    def open_transaction(self, request_id):
        yield _FakeTransaction(self._tasks)

    def set_tasks(self, tasks: list[Task]) -> None:
        self._tasks = tasks


class _FakeLogServices:
    def __init__(self, data_model_service: _FakeDataModelService) -> None:
        self.settings = SculptorSettings()
        self.data_model_service = data_model_service


class _FakeUserSession:
    def __init__(self, data_model_service: _FakeDataModelService) -> None:
        self.user_reference = UserReference("user")
        self._data_model_service = data_model_service

    @contextmanager
    def open_transaction(self, services=None) -> Iterator[_FakeTransaction]:
        with self._data_model_service.open_transaction(None) as transaction:
            yield transaction


class _TaskLogManagerFixture:
    def __init__(self, monkeypatch) -> None:
        self.watchers: dict[TaskID, _FakeLogWatcher] = {}
        self.data_model_service = _FakeDataModelService()
        self.services = _FakeLogServices(self.data_model_service)
        self.user_session = _FakeUserSession(self.data_model_service)
        monkeypatch.setattr(task_log_manager, "LogWatcher", self._fake_watcher_factory)

    def _fake_watcher_factory(self, task_id: TaskID, queue, settings, concurrency_group) -> _FakeLogWatcher:
        watcher = _FakeLogWatcher(task_id, queue, settings, concurrency_group)
        watcher.started = True  # Mark as started when created
        self.watchers[task_id] = watcher
        return watcher

    def create_manager(
        self, tasks: list[Task]
    ) -> tuple[task_log_manager._TaskLogWatcherManager, dict[TaskID, _FakeLogWatcher]]:
        self.watchers.clear()
        self.data_model_service.set_tasks(list(tasks))
        manager = task_log_manager._TaskLogWatcherManager(
            services=self.services,  # pyre-ignore[6]: deliberately using a fake services object
            queue=None,  # pyre-ignore[6]: not using a queue for the test even though this is invalid in general
            concurrency_group=None,  # pyre-ignore[6]: not using a concurrency group for the test even though this is invalid in general
            user_session=self.user_session,  # pyre-ignore[6]: deliberately using a fake user session
        )
        return manager, self.watchers


@pytest.fixture
def task_log_manager_fixture(monkeypatch) -> _TaskLogManagerFixture:
    return _TaskLogManagerFixture(monkeypatch)


def test_initialize_starts_watchers(task_log_manager_fixture) -> None:
    running = _make_task()
    queued = _make_task(TaskState.QUEUED)
    manager, watchers = task_log_manager_fixture.create_manager([running, queued])

    manager.initialize(UserReference("user"))

    assert running.object_id in manager._watchers_by_task_id
    assert watchers[running.object_id].started
    assert queued.object_id not in manager._watchers_by_task_id


def test_sync_models_updates_watchers(task_log_manager_fixture) -> None:
    task = _make_task()
    manager, watchers = task_log_manager_fixture.create_manager([])

    container = TaskMessageContainer(tasks=(task,), messages=())
    manager.update_watchers_based_on_stream([container])

    assert task.object_id in manager._watchers_by_task_id
    assert watchers[task.object_id].started

    finished = task.model_copy(update={"outcome": TaskState.SUCCEEDED})
    manager.update_watchers_based_on_stream([TaskMessageContainer(tasks=(finished,), messages=())])

    assert task.object_id not in manager._watchers_by_task_id
    assert watchers[task.object_id].stopped


def test_shutdown_stops_all(task_log_manager_fixture) -> None:
    task_a = _make_task()
    task_b = _make_task()
    manager, watchers = task_log_manager_fixture.create_manager([])
    manager.update_watchers_based_on_stream([TaskMessageContainer(tasks=(task_a, task_b), messages=())])

    manager.shutdown()

    assert not manager._watchers_by_task_id
    assert watchers[task_a.object_id].stopped
    assert watchers[task_b.object_id].stopped
