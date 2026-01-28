from sculptor.interfaces.agents.errors import AgentClientError
from sculptor.interfaces.agents.errors import AgentOutputDecodeError


class ClaudeAPIError(AgentClientError):
    """
    This error is raised when the Claude client encounters an API error.
    https://docs.anthropic.com/en/api/errors#http-errors
    """


class ClaudeJsonDecodeError(AgentOutputDecodeError):
    """
    This error is raised when we fail to decode Claude Code's output JSON.
    """
