from __future__ import annotations

from enum import StrEnum

from imbue_core.agents.data_types.ids import ObjectID


class RunID(ObjectID):
    tag: str = "run"


class TaskState(StrEnum):
    """The possible states of a server task."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    DELETED = "DELETED"
    ARCHIVED = "ARCHIVED"
    SUCCEEDED = "SUCCEEDED"
