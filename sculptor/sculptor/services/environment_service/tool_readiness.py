"""
Tool readiness management for blocking tool execution until environment is ready.

This module provides infrastructure to block Claude Code tool execution via PreToolUse hooks
until critical setup events have completed (e.g., repo sync, build verification).
"""

from enum import Enum
from pathlib import Path
from typing import Final

from loguru import logger

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.sculptor import telemetry
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from sculptor.interfaces.environments.base import Environment


class ToolReadinessBlocker(Enum):
    """Blockers that must be cleared before tools can execute.

    The application tracks these blockers internally. Once all blockers are cleared,
    a single ready file is written to the environment.
    """

    REPO_SYNCED = "repo_synced"
    """Repository has been synced into the container"""

    BUILD_VERIFIED = "build_verified"
    """Docker build has been verified (for parallel build optimization)"""

    @property
    def description(self) -> str:
        """Human-readable description of what this blocker represents."""
        descriptions = {
            ToolReadinessBlocker.REPO_SYNCED: "Repository synchronization",
            ToolReadinessBlocker.BUILD_VERIFIED: "Build verification",
        }
        return descriptions.get(self, self.value)


class ToolReadinessCompletePayload(telemetry.PosthogEventPayload):
    """Payload for tool readiness completion telemetry event."""

    event_name: str = telemetry.with_consent(ConsentLevel.PRODUCT_ANALYTICS)
    event_description: str = telemetry.with_consent(ConsentLevel.PRODUCT_ANALYTICS)


READY_FILE: Final[Path] = Path("/imbue_addons/.tools_ready")


class ToolReadinessManager:
    """Manages tool readiness blockers for an environment.

    This class tracks blockers internally and writes a single ready file to the
    environment once all blockers are cleared. The PreToolUse hook simply waits
    for this single file.
    """

    def __init__(self, environment: Environment, task_id: TaskID | None = None):
        self.environment = environment
        self.task_id = task_id
        self._blockers: set[ToolReadinessBlocker] = set()

    def add_blockers(self, *blockers: ToolReadinessBlocker) -> None:
        """Add one or more blockers that must be cleared before tools can execute.

        Args:
            blockers: Blockers to add
        """
        self._blockers.update(blockers)
        logger.info("Added tool readiness blockers: {}", [b.value for b in blockers])

    def clear_blocker(self, blocker: ToolReadinessBlocker) -> None:
        """Clear a blocker and write ready file if all blockers are cleared.

        Args:
            blocker: The blocker to clear
        """
        self._blockers.discard(blocker)
        logger.info("Cleared tool readiness blocker: {} ({})", blocker.value, blocker.description)

        # Emit telemetry
        if self.task_id is not None:
            telemetry.emit_posthog_event(
                telemetry.PosthogEventModel(
                    name=SculptorPosthogEvent.TOOL_READINESS_EVENT_COMPLETED,
                    component=ProductComponent.ENVIRONMENT_SETUP,
                    task_id=str(self.task_id),
                    payload=ToolReadinessCompletePayload(
                        event_name=blocker.value, event_description=blocker.description
                    ),
                )
            )

        # If all blockers cleared, write ready file
        if not self._blockers:
            self._write_ready_file()

    def _write_ready_file(self) -> None:
        """Write the ready file to signal that all blockers are cleared."""
        self.environment.write_file(str(READY_FILE), "")
        logger.info("All tool readiness blockers cleared, tools are now ready")

    def mark_ready(self) -> None:
        """Mark tools as ready without requiring blockers to be cleared.

        This is useful when reusing environments that are already set up.
        Calling this when the environment is already ready is a no-op.
        """
        if not self.is_ready():
            self._write_ready_file()

    def remove_ready_marker(self) -> None:
        """Remove the ready marker file if it exists.

        This is useful when reusing environments to start from a clean state.
        """
        self.environment.run_process_to_completion(["rm", "-f", str(READY_FILE)], is_checked_after=True, secrets={})
        logger.debug("Removed tool readiness marker")

    def is_ready(self) -> bool:
        """Check if tools are ready (ready file exists).

        Useful for debugging, monitoring, and observability.

        Returns:
            True if the ready file exists, False otherwise
        """
        return self.environment.exists(str(READY_FILE))

    def get_pending_blockers(self) -> set[ToolReadinessBlocker]:
        """Get the set of blockers that haven't been cleared yet.

        Useful for debugging and observability.

        Returns:
            Set of pending blockers
        """
        return self._blockers.copy()
