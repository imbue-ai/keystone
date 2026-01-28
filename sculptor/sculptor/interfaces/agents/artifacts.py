from __future__ import annotations

from enum import StrEnum

from pydantic import AnyUrl

from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.sculptor.state.chat_state import ImbueCLIToolContent
from sculptor.primitives.numeric import Probability


class TodoStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"


class TodoPriority(StrEnum):
    MEDIUM = "medium"
    HIGH = "high"
    LOW = "low"


class TodoItem(SerializableModel):
    id: str
    content: str
    status: TodoStatus
    priority: TodoPriority


class TodoListArtifact(SerializableModel):
    """Todo list artifact containing all todos."""

    object_type: str = "TodoListArtifact"
    todos: list[TodoItem]


class LogsArtifact(SerializableModel):
    """Logs artifact containing an array of log lines."""

    object_type: str = "LogsArtifact"
    logs: list[str]


class DiffArtifact(SerializableModel):
    """Unified diff artifact containing all diff types."""

    object_type: str = "DiffArtifact"
    committed_diff: str = ""  # Diff from base branch to HEAD
    uncommitted_diff: str = ""  # Uncommitted changes
    complete_diff: str = ""  # Combined view (base to current state)


class SuggestionsArtifact(SerializableModel):
    """Suggestions artifact containing Imbue CLI tool results."""

    object_type: str = "SuggestionsArtifact"
    content: ImbueCLIToolContent


class UsageArtifact(SerializableModel):
    """Usage artifact containing all tool results."""

    object_type: str = "UsageArtifact"
    cost_usd_info: float
    token_info: int


class AgentArtifact(SerializableModel):
    """
    An artifact produced by the agent during its work. Represents the "output" of the agent's work.

    The URL should point to a location where the artifact can be accessed.
    """

    # used to dispatch and discover the type of message
    object_type: str
    # the name of the artifact,
    # can be used to provide some structure to the outputs of an agent.
    # for a file, this is something like "output.txt" or "branch/main" or "whatever/thing.png"
    # for a branch, this is the branch name.
    name: str
    # where the artifact can be found
    url: AnyUrl
    # Probability that this output will be accepted by the user.
    # If this is set, the artifact is considered an "output"
    success: Probability | None = None


class FileAgentArtifact(AgentArtifact):
    object_type: str = "FileAgentArtifact"


ArtifactUnion = DiffArtifact | SuggestionsArtifact | TodoListArtifact | LogsArtifact | UsageArtifact


class ArtifactType(StrEnum):
    """Types of artifacts that agents can produce."""

    DIFF = "DIFF"  # Unified diff artifact with all three diff types
    SUGGESTIONS = "SUGGESTIONS"
    PLAN = "PLAN"
    LOGS = "LOGS"
    USAGE = "USAGE"
    NEW_CHECK_OUTPUTS = "NEW_CHECK_OUTPUTS"
    CHECKS = "CHECKS"
