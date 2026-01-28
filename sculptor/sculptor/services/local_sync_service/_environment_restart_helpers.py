import queue
from pathlib import Path
from typing import Callable
from typing import Collection
from typing import Final

from loguru import logger

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.concurrency_group import ConcurrencyGroupState
from imbue_core.event_utils import ShutdownEvent
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.thread_utils import ObservableThread
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import EnvironmentCreatedRunnerMessage
from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
from sculptor.interfaces.environments.base import Environment
from sculptor.services.local_sync_service.path_batch_scheduler import NoticeBasedHealthCheck
from sculptor.services.local_sync_service.path_batch_scheduler import NoticeTuple
from sculptor.services.local_sync_service.path_batch_scheduler import WatchedEventType
from sculptor.services.task_service.api import TaskService

AGENT_ENVIRONMENT_TAG: Final = "agent_environment_healthcheck"
CONTAINER_STOP_NOTICE: Final = LocalSyncNoticeOfPause(
    source_tag=AGENT_ENVIRONMENT_TAG,
    reason="The agent environment is stopped or restarting. Pairing will resume when a new environment is started.",
)


# More of an intercepter really, as doesn't "check-in" unless an event comes through
class EnvironmentAliveHealthCheck(NoticeBasedHealthCheck):
    tag: str = AGENT_ENVIRONMENT_TAG
    environment_concurrency_group: ConcurrencyGroup

    @property
    def local_dirs_to_watch(self) -> tuple[Path, ...]:
        return ()

    @property
    def environment_dirs_to_watch(self) -> tuple[Path, ...]:
        return ()

    @property
    def is_environment_dead(self) -> bool:
        state = self.environment_concurrency_group.state
        return state in (ConcurrencyGroupState.EXITED, ConcurrencyGroupState.EXITING)

    @property
    def is_current_state_fatal(self) -> bool:
        return self.is_environment_dead

    def get_notices(self) -> NoticeTuple:
        return (CONTAINER_STOP_NOTICE,) if self.is_environment_dead else ()

    def maybe_intercept_event(self, event: WatchedEventType, paths: Collection[Path]) -> tuple[NoticeTuple, bool]:
        "all other healthchecks are rendered moot here and their internal state will be discardded on restart"
        notices = self.get_notices()
        return notices, notices != ()


class EnvironmentRestartHandler(MutableModel):
    task_id: TaskID
    task_service: TaskService
    queue_poll_interval_seconds: float = 1.0

    def _log(self, message: str) -> None:
        logger.info("LOCAL_SYNC Restart Watcher for task {}: {}", self.task_id, message)

    def _watch_for_environment_restarts(
        self, session_level_shutdown_event: ShutdownEvent, on_new_environment: Callable[[Environment], None]
    ) -> None:
        """
        Watch for environment messages from which to trigger Session rebuilds via on_new_environment
        """
        self._log("Starting")
        with self.task_service.subscribe_to_environment_messages(self.task_id, is_history_included=False) as listener:
            self._log("Listening")
            while not session_level_shutdown_event.is_set():
                try:
                    message = listener.get(timeout=self.queue_poll_interval_seconds)
                    self._log(f"Received {type(message).__name__}")
                except queue.Empty:
                    continue

                if not isinstance(message, EnvironmentCreatedRunnerMessage):
                    continue

                env_label = message.environment.concurrency_group.name
                self._log(f"Restarting with env {env_label}")
                on_new_environment(message.environment)
        self._log("Exiting")

    def create_background_thread(
        self, session_level_shutdown_event: ShutdownEvent, on_new_environment: Callable[[Environment], None]
    ) -> ObservableThread:
        return ObservableThread(
            target=self._watch_for_environment_restarts,
            name=f"EnvironmentRestartHandler-{self.task_id}",
            args=(session_level_shutdown_event, on_new_environment),
        )
