from loguru import logger

from imbue_core.sculptor.telemetry import PosthogEventModel
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry import emit_posthog_event
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.sculptor.telemetry_utils import with_consent
from sculptor.agents.default.constants import AGENT_RESPONSE_TYPE_TO_POSTHOG_EVENT_MAP
from sculptor.agents.default.constants import USER_MESSAGE_TYPE_TO_POSTHOG_EVENT_MAP
from sculptor.database.models import TaskID
from sculptor.interfaces.agents.agent import ParsedAgentResponseType
from sculptor.interfaces.agents.agent import UserMessageUnion


def emit_posthog_event_for_agent_message(task_id: TaskID, message: ParsedAgentResponseType) -> None:
    """Emit PostHog event for agent messages.

    Args:
        task_id: The task ID
        message: The parsed agent response
    """
    if message.object_type not in AGENT_RESPONSE_TYPE_TO_POSTHOG_EVENT_MAP:
        logger.error(
            "Unknown object type '{}' in emit_posthog_event_for_agent_message. If you've added a new message type to ParsedAgentResponseType, please update AGENT_RESPONSE_TYPE_TO_POSTHOG_EVENT_MAP.",
            message.object_type,
        )
        return

    posthog_event = AGENT_RESPONSE_TYPE_TO_POSTHOG_EVENT_MAP[message.object_type]

    emit_posthog_event(
        PosthogEventModel(
            name=posthog_event, component=ProductComponent.CLAUDE_CODE, task_id=str(task_id), payload=message
        )
    )


def emit_posthog_event_for_user_message(task_id: TaskID, message: UserMessageUnion) -> None:
    """Emit PostHog event for user messages.

    Args:
        task_id: The task ID
        message: The user message
    """
    if message.object_type not in USER_MESSAGE_TYPE_TO_POSTHOG_EVENT_MAP:
        logger.error(
            "Unknown object type '{}' in emit_posthog_event_for_user_message. If you've added a new message type to UserMessageUnion, please update USER_MESSAGE_TYPE_TO_POSTHOG_EVENT_MAP.",
            message.object_type,
        )
        return

    posthog_event = USER_MESSAGE_TYPE_TO_POSTHOG_EVENT_MAP[message.object_type]

    emit_posthog_event(
        PosthogEventModel(
            name=posthog_event, component=ProductComponent.CLAUDE_CODE, task_id=str(task_id), payload=message
        )
    )


class AgentCommandLog(PosthogEventPayload):
    """Payload for agent command events (Claude or Codex)."""

    command: list[str] = with_consent(ConsentLevel.LLM_LOGS, default=[])
    system_prompt: str = with_consent(ConsentLevel.LLM_LOGS, default="")
    user_instructions: str = with_consent(ConsentLevel.LLM_LOGS, default="")


def emit_posthog_agent_command_event(
    task_id: TaskID,
    command: list[str],
    system_prompt: str,
    user_instructions: str,
    event_name: SculptorPosthogEvent = SculptorPosthogEvent.CLAUDE_COMMAND,
) -> None:
    """Emit PostHog event for agent command execution.

    Args:
        task_id: The task ID
        command: The command being executed
        system_prompt: The system prompt
        user_instructions: The user instructions
        event_name: The SculptorPosthogEvent name (CLAUDE_CODE or CODEX)
    """
    assert event_name in {SculptorPosthogEvent.CLAUDE_COMMAND, SculptorPosthogEvent.CODEX_COMMAND}
    emit_posthog_event(
        PosthogEventModel(
            name=event_name,
            component=ProductComponent.CLAUDE_CODE,
            task_id=str(task_id),
            payload=AgentCommandLog(command=command, system_prompt=system_prompt, user_instructions=user_instructions),
        )
    )
