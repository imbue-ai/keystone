import traceback

from loguru import logger

from imbue_core.agents.data_types.ids import AgentMessageID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.sculptor.telemetry import PosthogEventModel
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry import emit_posthog_event
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.sculptor.telemetry_utils import with_consent
from imbue_core.serialization import SerializedException
from sculptor.interfaces.agents.agent import WarningAgentMessage
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.errors import EnvironmentFailure


class PosthogWarningPayload(PosthogEventPayload):
    warning_message: str = with_consent(ConsentLevel.ERROR_REPORTING, description="The warning message.")
    exception_name: str | None = with_consent(
        ConsentLevel.ERROR_REPORTING, description="The name of the raised exception."
    )
    exception_value: str | None = with_consent(
        ConsentLevel.ERROR_REPORTING, description="The value of the raised exception."
    )
    exception_traceback: str | None = with_consent(
        ConsentLevel.ERROR_REPORTING, description="Formatted traceback of the raised exception."
    )


def _get_warning_payload(message: str, error: BaseException | None) -> PosthogWarningPayload:
    formatted_traceback = (
        "".join(traceback.format_exception(type(error), error, error.__traceback__)) if error else None
    )
    return PosthogWarningPayload(
        warning_message=message,
        exception_name=type(error).__name__ if error else None,
        exception_value=str(error) if error else None,
        exception_traceback=formatted_traceback,
    )


def get_warning_message(message: str, error: BaseException | None, task_id: TaskID) -> WarningAgentMessage:
    logger.bind(exc_info=error).warning(message)
    emit_posthog_event(
        PosthogEventModel(
            name=SculptorPosthogEvent.WARNING_AGENT_MESSAGE,
            component=ProductComponent.CLAUDE_CODE,
            payload=_get_warning_payload(message, error),
            task_id=str(task_id),
        )
    )
    warning_message = WarningAgentMessage(
        message_id=AgentMessageID(),
        message=message,
        error=SerializedException.build(error) if error is not None else None,
    )
    return warning_message


def get_state_file_contents(environment: Environment, relative_path: str) -> str | None:
    try:
        contents = environment.read_file(str(environment.get_state_path() / relative_path))
    except FileNotFoundError:
        return None
    except EnvironmentFailure as e:
        logger.debug("Failed to read state file {}: {}", relative_path, e)
        return None
    else:
        if isinstance(contents, str):
            return contents.strip()
        else:
            assert isinstance(contents, bytes)
            return contents.decode("utf-8").strip()
