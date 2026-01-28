from typing import Callable

from loguru import logger

from imbue_core.errors import ExpectedError
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.sculptor.telemetry import send_exception_to_posthog
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from sculptor.services.task_service.data_types import ServiceCollectionForTask
from sculptor.services.task_service.errors import UserPausedTaskError


def run_cleanup_images_task_v1(
    services: ServiceCollectionForTask, shutdown_event: ReadOnlyEvent, on_started: Callable[[], None] | None = None
) -> None:
    """Run the cleanup images task."""
    if on_started is not None:
        on_started()
    logger.debug("Starting Docker image cleanup process")
    try:
        services.environment_service.remove_stale_images(shutdown_event)
    except CancelledByEventError:
        logger.info("Docker image cleanup task was cancelled")
        raise UserPausedTaskError()
    except ExpectedError as e:
        if "Docker daemon is not running" in str(e):
            send_exception_to_posthog(
                SculptorPosthogEvent.TASK_FAILED_WITH_EXPECTED_ERROR,
                e,
                include_traceback=True,
            )
            logger.info("Docker daemon is not running, skipping image cleanup")
            return
        raise
