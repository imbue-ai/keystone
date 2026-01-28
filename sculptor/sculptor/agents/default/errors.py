from __future__ import annotations

from imbue_core.errors import ExpectedError


class AgentWrapperError(ExpectedError):
    """
    This error is raised when an agent wrapper hits an expected error
    """


class CommandFailedError(Exception):
    """
    This error is raised when running a user command fails. It gets placed in the message queue on command failures.
    """


class InterruptFailure(ExpectedError):
    """
    This error is raised when the interrupt fails. It gets placed in the message queue on interrupt failures.
    """


class CompactionFailure(AgentWrapperError):
    """
    This error is raised when the compaction fails. It gets placed in the message queue on compaction failures.
    """


class InvalidSlashCommandError(Exception):
    """Raised when a user inputs an invalid / unsupported slash command."""
