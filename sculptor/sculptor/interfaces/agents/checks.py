from __future__ import annotations

from enum import StrEnum

from pydantic import Field

from imbue_core.pydantic_serialization import SerializableModel
from sculptor.interfaces.agents.constants import DEFAULT_CHECK_TIMEOUT_SECONDS


class CheckSource(StrEnum):
    USER = "USER"
    SYSTEM = "SYSTEM"


class CheckTrigger(StrEnum):
    MANUAL = "MANUAL"
    AGENT_MESSAGE = "AGENT_MESSAGE"
    FILE_CHANGE = "FILE_CHANGE"
    # TODO: would be nice to implement this!  It would be useful to check that user messages are sufficiently clear before wasting a bunch of time
    #  realistically we'd want to at least suggest a better, clearer, longer message (in response to a bad user message)
    #  and we'd probably want to be fairly careful about how often this happened
    #  we could also give really small suggestions, which could actually be useful (ex: about things that are potentially unclear)
    #  when we're enabling this, we'll need to call _load_checks_from_environment twice -- once when we start, and once when the turn is complete
    #  otherwise, if we loaded only at the beginning, telling the agent to fix your config wouldn't work well
    # USER_MESSAGE = "USER_MESSAGE"


class Check(SerializableModel):
    # the shell (bash) command to run for this check.
    # may *not* end with "&" -- only blocking commands are allowed.
    # should not redirect stdout or stderr, as this will be done automatically.
    # this should only be None for the built-in check, which is just there to raise a fixed set of Suggestions
    command: str | None = Field(pattern=r"^.*[^&]$")
    # the name of the check, which is used to identify it in the system. Everything in the UI is keyed off of this.
    name: str = Field(min_length=1, pattern=r"^[A-Za-z0-9_\-]+$")
    # a description of the check, which is used to provide context to the user / remember why you created this check.
    description: str = ""
    # default timeout of 10 minutes because you really don't want checks going for too long --
    # they will cause noticeable spending on containers
    timeout_seconds: float = Field(
        default=DEFAULT_CHECK_TIMEOUT_SECONDS,
        gt=0.0,  # pyre-ignore[6]
        description="Timeout for the check in seconds",
    )
    # (if sculptor speaks true) pyre doesn't like float values for gt/ge/le because... it's looking for def __gt__(self: T, __other: T) -> bool and float has def __gt__(self, value: float, /) -> bool
    # severity for the Suggestion that results if this command returns a non-zero exit code.
    failure_severity: float = Field(ge=0.0, le=1.0, default=1.0)  # pyre-ignore[6]
    # if True, this check will be run in a separate container. If False, it will be within the agent's environment.
    # TODO: switch this to True as soon as we can
    is_forked: bool = False
    # if True, this check can be run concurrently with other checks in the same container, otherwise is killed when a new message is detected
    is_local_concurrency_allowed: bool = False
    # is set if and only if there is an error parsing this specific check
    config_error: str | None = None
    # set to AGENT_MESSAGE if you want to run it automatically when the agent message is complete (default)
    # set to MANUAL if you want to avoid running this check automatically,
    # set to USER_MESSAGE if you want to run it automatically after the user message is sent (useful for checking that the message makes sense)
    trigger: CheckTrigger = CheckTrigger.AGENT_MESSAGE
    # use this to specifically disable a check, for example, a built-in system check, or one that is enabled only by some users
    is_enabled: bool = True
    # whether this is shown in the row of checks after a conversation turn
    # this can be disabled in case users don't like seeing the system-level checks
    is_visible: bool = True
    # this is non-empty when a check fails to fully load because it is an outdated value from an earlier run
    outdated_reason: str = ""
    # where this check came from, either USER or SYSTEM.
    # it is an error for the user to set this to anything other than USER
    source: CheckSource = CheckSource.USER


class CheckFinishedReason(StrEnum):
    # the command actually exited and we observed an exit code
    # there is no guarantee that the exit code is 0 though!
    FINISHED = "FINISHED"
    # took too long to run, was stopped by us
    TIMEOUT = "TIMEOUT"
    # manually stopped by the user
    STOPPED = "STOPPED"
    # stopped when the agent started the next message. This only matters for non-forked tasks
    INTERRUPTED = "INTERRUPTED"
    # effectively stopped because it was running in our parent, but we are a forked task
    FORKED = "FORKED"
    # the case where sculptor was shut down while the check was running
    SHUTDOWN = "SHUTDOWN"
    # the case where the task exited while the check was running
    TASK_EXIT = "TASK_EXIT"
    # if sculptor itself crashed while the check was running
    SCULPTOR_CRASHED = "SCULPTOR_CRASHED"
    # if the environment crashed while the check was running
    ENVIRONMENT_CRASHED = "ENVIRONMENT_CRASHED"
