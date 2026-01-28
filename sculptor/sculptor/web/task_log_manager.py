import threading
from contextlib import contextmanager
from pathlib import Path
from queue import Queue
from typing import Any
from typing import Callable
from typing import Generator

from loguru import logger

from imbue_core.concurrency_group import ConcurrencyGroup
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import Task
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import TaskState
from sculptor.primitives.constants import USER_FACING_LOG_TYPE
from sculptor.primitives.ids import UserReference
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.data_model_service.api import CompletedTransaction
from sculptor.services.task_service.api import TaskMessageContainer
from sculptor.utils.jsonl_logs import observe_jsonl_log_file
from sculptor.web.auth import UserSession
from sculptor.web.data_types import StreamingUpdateSourceTypes


class _TaskLogWatcherManager:
    def __init__(
        self,
        services: CompleteServiceCollection,
        queue: Queue[StreamingUpdateSourceTypes],
        concurrency_group: ConcurrencyGroup,
        user_session: UserSession,
    ):
        self._services = services
        self._queue = queue
        self._concurrency_group = concurrency_group
        self._watchers_by_task_id: dict[TaskID, LogWatcher] = {}
        self._user_session = user_session

    def initialize(self, user_reference: UserReference) -> None:
        with self._user_session.open_transaction(services=self._services) as transaction:
            # TODO: only TaskAndDataModelTransaction has get_tasks_for_user, not DataModelTransaction
            tasks = transaction.get_tasks_for_user(user_reference)  # pyre-fixme[16]

        for task in tasks:
            if isinstance(task, Task):
                self._update_watcher_for_task(task)

    def update_watchers_based_on_stream(self, models: list[StreamingUpdateSourceTypes]) -> None:
        tasks_by_id: dict[TaskID, Task] = {}
        for model in models:
            if isinstance(model, TaskMessageContainer):
                for task in model.tasks:
                    if isinstance(task, Task):
                        tasks_by_id[task.object_id] = task
            elif isinstance(model, CompletedTransaction):
                for updated_model in model.updated_models:
                    if isinstance(updated_model, Task):
                        tasks_by_id[updated_model.object_id] = updated_model

        for task in tasks_by_id.values():
            self._update_watcher_for_task(task)

    def shutdown(self) -> None:
        for task_id in list(self._watchers_by_task_id.keys()):
            self._stop_watcher(task_id)

    def _update_watcher_for_task(self, task: Task) -> None:
        should_watch = self._should_watch(task)
        is_watching = task.object_id in self._watchers_by_task_id

        if should_watch and not is_watching:
            self._start_watcher(task.object_id)
        elif not should_watch and is_watching:
            self._stop_watcher(task.object_id)

    def _should_watch(self, task: Task) -> bool:
        if task.is_deleted:
            return False
        if task.is_archived:
            return False
        if not isinstance(task.input_data, AgentTaskInputsV1):
            return False
        if task.outcome not in (TaskState.RUNNING, TaskState.FAILED):
            return False
        return True

    def _start_watcher(self, task_id: TaskID) -> None:
        watcher = LogWatcher(
            task_id=task_id,
            queue=self._queue,
            settings=self._services.settings,
            concurrency_group=self._concurrency_group,
        )
        self._watchers_by_task_id[task_id] = watcher

    def _stop_watcher(self, task_id: TaskID) -> None:
        watcher = self._watchers_by_task_id.pop(task_id, None)
        if watcher is not None:
            watcher.stop()


@contextmanager
def manage_task_log_watchers(
    services: CompleteServiceCollection,
    user_session: UserSession,
    queue: Queue[StreamingUpdateSourceTypes],
    concurrency_group: ConcurrencyGroup,
) -> Generator[_TaskLogWatcherManager, None, None]:
    manager = _TaskLogWatcherManager(
        services=services, user_session=user_session, queue=queue, concurrency_group=concurrency_group
    )
    try:
        yield manager
    finally:
        manager.shutdown()


def _log_filter_fn(log_dict: dict[str, Any]) -> bool:
    extra_dict = log_dict.get("record", {}).get("extra", {})
    if extra_dict.get("log_type", "") != USER_FACING_LOG_TYPE:
        return False
    return True


def _log_filter_for_task(task_id: TaskID) -> Callable[[dict[str, Any]], bool]:
    def _filter(log_dict: dict[str, Any]) -> bool:
        if not _log_filter_fn(log_dict):
            return False
        # annotate the log with its task id so the unified stream can route it
        log_dict["task_id"] = str(task_id)
        return True

    return _filter


class LogWatcher:
    def __init__(
        self,
        task_id: TaskID,
        queue: Queue[StreamingUpdateSourceTypes],
        settings: SculptorSettings,
        concurrency_group: ConcurrencyGroup,
    ):
        log_dir = Path(settings.LOG_PATH)
        current_log_file = Path(log_dir) / "tasks" / f"{task_id}.json"

        self._stop_event = threading.Event()
        self._watcher_thread = concurrency_group.start_new_thread(
            target=observe_jsonl_log_file,
            args=(current_log_file, queue, _log_filter_for_task(task_id), self._stop_event),
        )

    def stop(self) -> None:
        self._stop_event.set()
        self._watcher_thread.join(timeout=1.0)
        if self._watcher_thread.is_alive():
            logger.error("File watcher thread did not shut down in time.")
