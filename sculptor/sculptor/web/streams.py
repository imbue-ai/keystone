import time
from collections import defaultdict
from queue import Empty
from queue import Queue
from typing import Any
from typing import Generator
from typing import TypeVar
from typing import assert_never
from typing import cast

from loguru import logger
from pydantic import Field

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import CompoundEvent
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.sculptor.state.chat_state import ChatMessage
from imbue_core.sculptor.state.messages import Message
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import Notification
from sculptor.database.models import Project
from sculptor.database.models import TaskID
from sculptor.database.models import UserSettings
from sculptor.primitives.ids import RequestID
from sculptor.service_collections.service_collection import CompleteServiceCollection
from sculptor.services.data_model_service.api import CompletedTransaction
from sculptor.services.task_service.api import TaskMessageContainer
from sculptor.web.auth import UserSession
from sculptor.web.data_types import StreamingUpdateSourceTypes
from sculptor.web.data_types import UserUpdateSourceTypes
from sculptor.web.derived import CodingAgentTaskView
from sculptor.web.derived import LocalRepoInfo
from sculptor.web.derived import TaskUpdate
from sculptor.web.derived import UserUpdate
from sculptor.web.derived import create_initial_task_view
from sculptor.web.message_conversion import convert_agent_messages_to_task_update
from sculptor.web.repo_polling_manager import manage_local_repo_info_polling
from sculptor.web.task_log_manager import manage_task_log_watchers

StreamUpdateT = TypeVar("StreamUpdateT", bound=StreamingUpdateSourceTypes)

_KEEPALIVE_SECONDS = 10
_POLL_SECONDS = 1


class ServerStopped(Exception):
    pass


class StreamingUpdate(SerializableModel):
    task_update_by_task_id: dict[TaskID, TaskUpdate] = Field(default_factory=dict)
    task_views_by_task_id: dict[TaskID, CodingAgentTaskView] = Field(default_factory=dict)
    user_update: UserUpdate = Field(default_factory=UserUpdate)
    local_repo_info_by_project_id: dict[ProjectID, LocalRepoInfo | None] = Field(default_factory=dict)
    finished_request_ids: tuple[RequestID, ...] = ()


def stream_everything(
    user_session: UserSession,
    shutdown_event: ReadOnlyEvent,
    services: CompleteServiceCollection,
    concurrency_group: ConcurrencyGroup,
) -> Generator[StreamingUpdate | None, None, None]:
    """Emit unified task/user updates for a user."""
    # Shut down if either a global or local shutdown is requested.
    combined_event = CompoundEvent([concurrency_group.shutdown_event, shutdown_event])
    with services.task_service.subscribe_to_all_tasks_for_user(user_session.user_reference) as updates_queue:
        updates_queue_loosely_typed = cast(Queue[StreamingUpdateSourceTypes], updates_queue)
        with (
            services.data_model_service.observe_user_changes(
                user_reference=user_session.user_reference,
                organization_reference=user_session.organization_reference,
                queue=updates_queue_loosely_typed,
            ),
            manage_local_repo_info_polling(
                services=services,
                queue=updates_queue_loosely_typed,
                concurrency_group=concurrency_group,
            ) as local_repo_info_manager,
            manage_task_log_watchers(
                services=services,
                user_session=user_session,
                queue=updates_queue_loosely_typed,
                concurrency_group=concurrency_group,
            ) as task_log_manager,
        ):
            # Initialize state tracking
            completed_message_by_task_id: dict[TaskID, dict[AgentMessageID, ChatMessage]] = {}
            task_views_by_task_id: dict[TaskID, CodingAgentTaskView] = {}
            task_update_state_by_task_id: dict[TaskID, TaskUpdate] = {}

            # Yield the initial state dump
            initial_data: list[StreamingUpdateSourceTypes] = _empty_update_queue(
                updates_queue=updates_queue_loosely_typed,
                shutdown_event=combined_event,
                is_blocking_allowed=False,
            )
            initial_data.append(services.settings)
            initial_update = StreamingUpdate()
            if initial_data:
                initial_update = _convert_to_streaming_update(
                    all_data=cast(list[StreamingUpdateSourceTypes | None], initial_data),
                    task_views_by_task_id=task_views_by_task_id,
                    task_update_state_by_task_id=task_update_state_by_task_id,
                    processed_message_by_task_id=completed_message_by_task_id,
                    settings=services.settings,
                )

            # We yield the initial state before starting the background watchers to minimize time to first message for the frontend
            yield initial_update

            # Start background watchers after emitting the initial state
            local_repo_info_manager.initialize()
            local_repo_info_manager.update_pollers_based_on_stream(initial_data)
            task_log_manager.initialize(user_session.user_reference)
            task_log_manager.update_watchers_based_on_stream(initial_data)

            # Now continuously yield incremental updates
            while not combined_event.is_set():
                new_data = _empty_update_queue(
                    updates_queue=updates_queue_loosely_typed,
                    shutdown_event=combined_event,
                    is_blocking_allowed=True,
                )
                local_repo_info_manager.update_pollers_based_on_stream(new_data)
                task_log_manager.update_watchers_based_on_stream(new_data)

                if len(new_data) == 0:
                    yield StreamingUpdate()
                else:
                    loosely_typed_new_data = cast(list[StreamingUpdateSourceTypes | None], new_data)
                    incremental_update = _convert_to_streaming_update(
                        all_data=loosely_typed_new_data,
                        task_views_by_task_id=task_views_by_task_id,
                        task_update_state_by_task_id=task_update_state_by_task_id,
                        processed_message_by_task_id=completed_message_by_task_id,
                        settings=services.settings,
                    )
                    yield incremental_update


def _convert_to_streaming_update(
    all_data: list[StreamingUpdateSourceTypes | None],
    task_views_by_task_id: dict[TaskID, CodingAgentTaskView],
    task_update_state_by_task_id: dict[TaskID, TaskUpdate],
    processed_message_by_task_id: dict[TaskID, dict[AgentMessageID, ChatMessage]],
    settings: SculptorSettings,
) -> StreamingUpdate:
    """Converts a list of source updates into a StreamingUpdate.

    This function processes new data and returns an incremental update containing only changes from this batch.
    It maintains internal state in the passed-in dicts for tracking purposes.
    """
    changed_task_ids: set[TaskID] = set()
    finished_request_ids: list[RequestID] = []
    user_update_sources: list[UserUpdateSourceTypes] = []
    updated_local_repo_info_by_project_id: dict[ProjectID, LocalRepoInfo | None] = {}
    messages_by_task: dict[TaskID, list[Message | dict[str, Any]]] = defaultdict(list)

    for model in all_data:
        if model is None:
            continue
        if isinstance(model, TaskMessageContainer):
            _process_task_message_container(
                container=model,
                changed_task_ids=changed_task_ids,
                task_views_by_task_id=task_views_by_task_id,
                messages_by_task=messages_by_task,
                settings=settings,
            )

        elif isinstance(model, CompletedTransaction):
            _process_completed_transaction(
                transaction=model,
                finished_request_ids=finished_request_ids,
                user_update_sources=user_update_sources,
            )

        elif isinstance(model, SculptorSettings):
            user_update_sources.append(model)

        elif isinstance(model, LocalRepoInfo):
            updated_local_repo_info_by_project_id[model.project_id] = model

        elif isinstance(model, dict):
            _process_log_record(
                log_record=model,
                changed_task_ids=changed_task_ids,
                messages_by_task=messages_by_task,
            )
        elif isinstance(model, Message):
            raise TypeError("should not have Message models in streaming update")
        else:
            assert_never(model)

    _apply_message_updates_to_task_state(
        messages_by_task=messages_by_task,
        task_update_state_by_task_id=task_update_state_by_task_id,
        processed_message_by_task_id=processed_message_by_task_id,
    )

    updated_task_views_by_task_id, updated_task_update_by_task_id = _extract_changed_tasks(
        changed_task_ids=changed_task_ids,
        task_views_by_task_id=task_views_by_task_id,
        task_update_state_by_task_id=task_update_state_by_task_id,
    )

    user_update = _convert_to_user_update(all_data=cast(list[UserUpdateSourceTypes | None], user_update_sources))

    return StreamingUpdate(
        task_views_by_task_id=updated_task_views_by_task_id,
        task_update_by_task_id=updated_task_update_by_task_id,
        user_update=user_update,
        local_repo_info_by_project_id=updated_local_repo_info_by_project_id,
        finished_request_ids=tuple(finished_request_ids),
    )


def _convert_to_user_update(all_data: list[UserUpdateSourceTypes | None]) -> UserUpdate:
    """Converts a list of models into a UserUpdate."""
    if len(all_data) == 0:
        return UserUpdate()
    notifications: list[Notification] = []
    projects_by_id: dict[ProjectID, Project] = {}
    user_settings = None
    server_settings = None
    for model in all_data:
        match model:
            case None:
                continue
            case CompletedTransaction():
                completed_transaction = model
                for request_model in completed_transaction.updated_models:
                    match request_model:
                        case Notification():
                            notifications.append(request_model)
                        case Project():
                            projects_by_id[request_model.object_id] = request_model
                        case UserSettings():
                            user_settings = request_model
                        case _ as unreachable:
                            assert_never(unreachable)
            case SculptorSettings():
                server_settings = model
            case LocalRepoInfo():
                raise TypeError("should not have LocalRepoInfo models in user update")
            case _ as also_unreachable:
                assert_never(also_unreachable)
    return UserUpdate(
        user_settings=user_settings,
        projects=tuple(projects_by_id.values()),
        settings=server_settings,
        notifications=tuple(notifications),
    )


def _process_task_message_container(
    container: TaskMessageContainer,
    changed_task_ids: set[TaskID],
    task_views_by_task_id: dict[TaskID, CodingAgentTaskView],
    messages_by_task: dict[TaskID, list[Message | dict[str, Any]]],
    settings: SculptorSettings,
) -> None:
    for task in container.tasks:
        if not isinstance(task.input_data, AgentTaskInputsV1):
            continue
        changed_task_ids.add(task.object_id)

        if task.object_id not in task_views_by_task_id:
            task_view = create_initial_task_view(task, settings)
            assert isinstance(task_view, CodingAgentTaskView), (
                f"should be impossible: {task=} resulted in non-CodingAgentTaskView view {task_view=} "
            )
            task_views_by_task_id[task.object_id] = task_view
        task_views_by_task_id[task.object_id].update_task(task)

    for message, task_id in container.messages:
        changed_task_ids.add(task_id)
        if task_id in task_views_by_task_id and isinstance(message, Message):
            task_views_by_task_id[task_id].add_message(message)
        messages_by_task[task_id].append(message)


def _process_completed_transaction(
    transaction: CompletedTransaction,
    finished_request_ids: list[RequestID],
    user_update_sources: list[UserUpdateSourceTypes],
) -> None:
    if transaction.request_id is not None:
        finished_request_ids.append(transaction.request_id)
    user_update_sources.append(transaction)


def _process_log_record(
    log_record: dict[str, Any],
    changed_task_ids: set[TaskID],
    messages_by_task: dict[TaskID, list[Message | dict[str, Any]]],
) -> None:
    task_id_str = log_record.get("task_id")
    if task_id_str is None:
        return
    task_id = TaskID(task_id_str)
    changed_task_ids.add(task_id)
    messages_by_task[task_id].append(log_record)


def _apply_message_updates_to_task_state(
    messages_by_task: dict[TaskID, list[Message | dict[str, Any]]],
    task_update_state_by_task_id: dict[TaskID, TaskUpdate],
    processed_message_by_task_id: dict[TaskID, dict[AgentMessageID, ChatMessage]],
) -> None:
    for task_id, messages in messages_by_task.items():
        if task_id not in processed_message_by_task_id:
            processed_message_by_task_id[task_id] = {}

        current_task_update = task_update_state_by_task_id.get(task_id)
        new_task_update = convert_agent_messages_to_task_update(
            new_messages=messages,
            task_id=task_id,
            completed_message_by_id=processed_message_by_task_id[task_id],
            current_state=current_task_update,
        )
        task_update_state_by_task_id[task_id] = new_task_update


def _extract_changed_tasks(
    changed_task_ids: set[TaskID],
    task_views_by_task_id: dict[TaskID, CodingAgentTaskView],
    task_update_state_by_task_id: dict[TaskID, TaskUpdate],
) -> tuple[dict[TaskID, CodingAgentTaskView], dict[TaskID, TaskUpdate]]:
    """Extract only the changed tasks from full state to create an incremental update."""
    update_task_views_by_task_id: dict[TaskID, CodingAgentTaskView] = {}
    update_task_update_by_task_id: dict[TaskID, TaskUpdate] = {}

    for task_id in changed_task_ids:
        if task_id in task_views_by_task_id:
            update_task_views_by_task_id[task_id] = task_views_by_task_id[task_id]
        if task_id in task_update_state_by_task_id:
            update_task_update_by_task_id[task_id] = task_update_state_by_task_id[task_id]

    return update_task_views_by_task_id, update_task_update_by_task_id


def _empty_update_queue(
    updates_queue: Queue[StreamUpdateT], shutdown_event: ReadOnlyEvent, is_blocking_allowed: bool
) -> list[StreamUpdateT]:
    """Empties the queue and returns all items in it."""
    all_data: list[StreamUpdateT] = []

    # first get everything that's already in the queue
    while updates_queue.qsize() > 0:
        data = updates_queue.get()
        all_data.append(data)

    # if there was anything at all, we can return it immediately
    if len(all_data) > 0:
        return all_data

    # if we can't block, we're done
    if not is_blocking_allowed:
        return all_data

    # otherwise, if we're allowed to block, we can wait for more data
    start_time = time.monotonic()
    while True:
        try:
            data = updates_queue.get(timeout=_POLL_SECONDS)
        except Empty:
            if shutdown_event.is_set():
                logger.info("Server is stopping, no more updates will be sent.")
                raise ServerStopped("Shutting down because the server is stopping.")
            if time.monotonic() - start_time > _KEEPALIVE_SECONDS:
                return all_data
            else:
                continue
        else:
            # might as well go return the rest of it too
            all_data = [data] + _empty_update_queue(
                updates_queue=updates_queue,
                shutdown_event=shutdown_event,
                is_blocking_allowed=False,
            )
            return all_data

    assert False, "This should never be reached, as we either return or raise an exception in the loop above."
