import datetime
from typing import Any
from typing import Callable
from typing import assert_never

from imbue_core.common import is_running_within_a_pytest_tree
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import ReadOnlyEvent
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskInputsV1
from sculptor.database.models import CacheReposInputsV1
from sculptor.database.models import CleanupImagesInputsV1
from sculptor.database.models import MustBeShutDownTaskInputsV1
from sculptor.database.models import SendEmailTaskInputsV1
from sculptor.database.models import Task
from sculptor.services.data_model_service.data_types import DataModelTransaction
from sculptor.services.task_service.data_types import ServiceCollectionForTask
from sculptor.tasks.handlers.cache_repos.v1 import cache_repos_task_v1
from sculptor.tasks.handlers.cleanup_images.v1 import run_cleanup_images_task_v1
from sculptor.tasks.handlers.run_agent.v1 import run_agent_task_v1
from sculptor.tasks.handlers.send_email.v1 import run_send_email_task_v1


def run_task(
    task: Task,
    services: ServiceCollectionForTask,
    task_deadline: datetime.datetime | None,
    settings: SculptorSettings,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent,
    on_started: Callable[[], None] | None = None,
) -> Callable[[DataModelTransaction], Any] | None:
    """
    Calls the correct task function based on the type of the input_data.

    When `on_started` is provided, it will be called once the task has started processing and (in case of agents) is ready to accept messages.

    """
    data = task.input_data
    match data:
        case AgentTaskInputsV1():
            return run_agent_task_v1(
                data, task, services, task_deadline, settings, concurrency_group, shutdown_event, on_started
            )
        case SendEmailTaskInputsV1():
            return run_send_email_task_v1(data, task, services, task_deadline)
        case MustBeShutDownTaskInputsV1():
            assert is_running_within_a_pytest_tree(), "MustBeShutDownTaskInputsV1 should only be used in testing"
            with services.task_service.subscribe_to_user_and_sculptor_system_messages(
                task.object_id
            ) as input_message_queue:
                if on_started is not None:
                    on_started()
                shutdown_event.wait()
            return None
        case CleanupImagesInputsV1():
            return run_cleanup_images_task_v1(services, shutdown_event, on_started)
        case CacheReposInputsV1():
            return cache_repos_task_v1(services, shutdown_event, on_started)

        case _ as unreachable:
            assert_never(unreachable)
