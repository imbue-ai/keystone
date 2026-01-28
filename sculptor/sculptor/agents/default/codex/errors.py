from sculptor.agents.default.errors import AgentWrapperError
from sculptor.interfaces.agents.errors import AgentOutputDecodeError


class CodexJsonDecodeError(AgentOutputDecodeError):
    """
    This error is raised when we fail to decode Codex's output JSON.
    """


class InconsistentSessionError(AgentWrapperError):
    """
    This error is raised when an inconsistent session ID is detected between the state file and Codex output.
    """
