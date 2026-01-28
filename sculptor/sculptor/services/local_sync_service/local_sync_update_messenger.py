from abc import ABC
from abc import abstractmethod
from pathlib import Path
from typing import Collection
from typing import Mapping
from typing import assert_never

from loguru import logger

from imbue_core.event_utils import ShutdownEvent
from imbue_core.itertools import generate_flattened
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.sculptor.telemetry import PosthogEventModel
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry import emit_posthog_event
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.time_utils import get_current_time
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import LocalSyncDisabledMessage
from sculptor.interfaces.agents.agent import LocalSyncMessageUnion
from sculptor.interfaces.agents.agent import LocalSyncNonPausingNoticeUnion
from sculptor.interfaces.agents.agent import LocalSyncNoticeOfPause
from sculptor.interfaces.agents.agent import LocalSyncSetupAndEnabledMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupProgressMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupStartedMessage
from sculptor.interfaces.agents.agent import LocalSyncSetupStep
from sculptor.interfaces.agents.agent import LocalSyncTeardownProgressMessage
from sculptor.interfaces.agents.agent import LocalSyncTeardownStartedMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdateCompletedMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdateMessageUnion
from sculptor.interfaces.agents.agent import LocalSyncUpdatePausedMessage
from sculptor.interfaces.agents.agent import LocalSyncUpdatePendingMessage
from sculptor.primitives.ids import RequestID
from sculptor.services.data_model_service.api import DataModelService
from sculptor.services.local_sync_service.api import SyncSessionInfo
from sculptor.services.local_sync_service.path_batch_scheduler import BatchLifecycleCallbacks
from sculptor.services.local_sync_service.path_batch_scheduler import LocalSyncPathBatchSchedulerStatus
from sculptor.services.task_service.api import TaskService


def _get_posthog_event_type(message: LocalSyncMessageUnion) -> SculptorPosthogEvent | None:
    match message:
        case LocalSyncSetupStartedMessage():
            return SculptorPosthogEvent.LOCAL_SYNC_SETUP_STARTED
        case LocalSyncSetupProgressMessage():
            return None
        case LocalSyncSetupAndEnabledMessage():
            return SculptorPosthogEvent.LOCAL_SYNC_SETUP_AND_ENABLED
        case LocalSyncUpdatePendingMessage():
            return None
        case LocalSyncUpdateCompletedMessage():
            return SculptorPosthogEvent.LOCAL_SYNC_UPDATE_COMPLETED
        case LocalSyncUpdatePausedMessage():
            return SculptorPosthogEvent.LOCAL_SYNC_UPDATE_PAUSED
        case LocalSyncTeardownStartedMessage():
            return None
        case LocalSyncTeardownProgressMessage():
            return None
        case LocalSyncDisabledMessage():
            return SculptorPosthogEvent.LOCAL_SYNC_DISABLED
        case _ as unreachable:
            assert_never(unreachable)


def emit_local_sync_posthog_event_if_tracked(task_id: TaskID, message: LocalSyncMessageUnion) -> None:
    event_type = _get_posthog_event_type(message)
    if event_type is None:
        return
    assert isinstance(message, PosthogEventPayload), (
        f"All messages inherit PosthogEventPayload, but got {type(message)}"
    )
    event = PosthogEventModel(
        name=event_type, component=ProductComponent.LOCAL_SYNC, task_id=str(task_id), payload=message
    )
    emit_posthog_event(event)


def _log_pause(message: LocalSyncUpdatePausedMessage) -> None:
    notices = tuple(sorted((notice.describe() for notice in message.pause_notices)))
    if len(notices) == 1:
        logger.info("local sync paused due to notice: {notice}", notice=notices[0])
    else:
        logger.info("local sync paused due to notices:\n * {notices}", notices="\n * ".join(notices))


class LocalSyncUpdateMessengerAPI(BatchLifecycleCallbacks, ABC):
    """Adapts the LocalSyncPathBatchScheduler's lifecycle event hooks to sending LocalSyncUpdateMessageUnion.

    LocalSyncUpdateMessenger below is the runtime version.

    TODO: The only reason this interface is referenced and not a private implementation detail is to sidestep pydantic validation in testing
    """

    session_level_shutdown_event: ShutdownEvent

    @abstractmethod
    def send_update_message(self, message: LocalSyncUpdateMessageUnion) -> None: ...

    def on_new_batch_pending(self, path_batch_by_tag: Mapping[str, Collection[Path]]) -> None:
        changed_path_count = len({*generate_flattened(path_batch_by_tag.values())})
        description = f"New batch pending ({changed_path_count=})"
        self.send_update_message(LocalSyncUpdatePendingMessage(event_description=description))

    def on_batch_complete(
        self,
        path_batch_by_tag: Mapping[str, Collection[Path]],
        nonpause_notices: tuple[LocalSyncNonPausingNoticeUnion, ...],
        prior_status: LocalSyncPathBatchSchedulerStatus,
    ) -> None:
        changed_path_count = len({*generate_flattened(path_batch_by_tag.values())})

        if prior_status == LocalSyncPathBatchSchedulerStatus.PAUSED_ON_KNOWN_NOTICE:
            description = f"Resuming after resolving known notices ({changed_path_count=})"
            logger.info(description)
            continue_message = LocalSyncUpdateCompletedMessage(
                event_description=description, nonpause_notices=tuple(nonpause_notices), is_resumption=True
            )

        elif prior_status == LocalSyncPathBatchSchedulerStatus.PAUSED_ON_UNEXPECTED_EXCEPTION:
            description = f"Resuming after resolving unexpected exceptions ({changed_path_count=})"
            logger.info(description)
            continue_message = LocalSyncUpdateCompletedMessage(
                event_description=description, nonpause_notices=tuple(nonpause_notices), is_resumption=True
            )

        else:
            description = f"Sending update local sync message ({changed_path_count=})"
            logger.info(description)
            continue_message = LocalSyncUpdateCompletedMessage(
                event_description=description, nonpause_notices=nonpause_notices
            )
        self.send_update_message(continue_message)

    def on_handling_paused(
        self,
        pending_reconciler_tags: tuple[str, ...],
        nonpause_notices: tuple[LocalSyncNonPausingNoticeUnion, ...],
        pause_notices: tuple[LocalSyncNoticeOfPause, ...],
    ) -> None:
        """Called when handling is paused due to notices (all_notices can include NONBLOCKING notices)."""
        pause_message = LocalSyncUpdatePausedMessage(
            event_description=f"Paused due to notices ({pending_reconciler_tags=})",
            nonpause_notices=nonpause_notices,
            pause_notices=pause_notices,
        )
        self.send_update_message(pause_message)

    @abstractmethod
    def on_setup_update(self, next_step: LocalSyncSetupStep) -> None: ...

    @abstractmethod
    def on_setup_complete(self) -> None: ...

    @property
    @abstractmethod
    def last_sent_message(self) -> LocalSyncUpdateMessageUnion | None: ...


class LocalSyncUpdateMessenger(MutableModel, LocalSyncUpdateMessengerAPI):
    session_level_shutdown_event: ShutdownEvent

    info: SyncSessionInfo
    data_model_service: DataModelService
    task_service: TaskService

    _last_sent_message: LocalSyncMessageUnion | None = None
    _pause_log_frequency_seconds: float = 15.0

    def _sec_since_last_message(self) -> float:
        last = self._last_sent_message
        if last is None:
            return float("inf")
        return (get_current_time() - last.approximate_creation_time).total_seconds()

    # Would rather be more fine-grained but notice causes aren't super well taxonimized atm.
    # At least this will collect some data on if multiple reconcilers or healthchecks toggle on and off
    def _is_fairly_different_pause_from_last_message(self, message: LocalSyncUpdatePausedMessage) -> bool:
        if not isinstance(self._last_sent_message, LocalSyncUpdatePausedMessage):
            return True
        prev_source_tags = {n.source_tag for n in self._last_sent_message.pause_notices}
        new_source_tags = {n.source_tag for n in message.pause_notices}
        return prev_source_tags != new_source_tags

    def _is_posthog_worthy(self, message: LocalSyncMessageUnion) -> bool:
        if isinstance(message, LocalSyncUpdatePausedMessage):
            return self._is_fairly_different_pause_from_last_message(message)
        return True

    # throttle redundant pause message sends
    def _is_task_service_worthy(self, message: LocalSyncMessageUnion) -> bool:
        if isinstance(message, LocalSyncUpdatePausedMessage):
            if (
                self._is_fairly_different_pause_from_last_message(message)
                or self._sec_since_last_message() > self._pause_log_frequency_seconds
            ):
                _log_pause(message)
                return True
            return False
        return True

    def send_message(self, message: LocalSyncMessageUnion) -> None:
        if self.session_level_shutdown_event.is_set():
            logger.info("Not sending update message, sync session is stopped: {}", message)
            return

        # throttle redundant messages if it hasn't been long and no state has meaningfully changed
        if not self._is_task_service_worthy(message):
            return

        with self.data_model_service.open_transaction(request_id=RequestID()) as transaction:
            self.task_service.create_message(message, task_id=self.info.task_id, transaction=transaction)
        self._last_sent_message = message

        if self._is_posthog_worthy(message):
            emit_local_sync_posthog_event_if_tracked(self.info.task_id, message)

    def send_update_message(self, message: LocalSyncUpdateMessageUnion) -> None:
        self.send_message(message)

    def on_setup_update(self, next_step: LocalSyncSetupStep) -> None:
        self.send_message(
            LocalSyncSetupProgressMessage(
                next_step=next_step, sync_branch=self.info.sync_branch, original_branch=self.info.original_branch
            )
        )

    def on_setup_complete(self) -> None:
        self.send_message(LocalSyncSetupAndEnabledMessage())

    @property
    def last_sent_message(self) -> LocalSyncMessageUnion | None:
        return self._last_sent_message
