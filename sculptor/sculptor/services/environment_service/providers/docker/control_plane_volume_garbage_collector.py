"""
Mechanism to prune "old" control plane Docker volumes.

Old volumes are defined as those that:
- are not the current/latest version
- are not in use by any container
- were created more than 7 days ago

For volumes created during development (dev builds), we have more aggressive pruning:
- keep only the most recent volume per commit hash if created within the last 7 days
- always keep volumes that are currently in use
"""

import json
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from typing import Iterable

from loguru import logger

from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.subprocess_utils import ProcessError
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    ControlPlaneVolumeInformation,
)


class ControlPlaneVolumeGarbageCollector:
    def __init__(
        self, latest_volume_name: str, concurrency_group: ConcurrencyGroup, shutdown_event: ReadOnlyEvent | None = None
    ) -> None:
        self.latest_volume_name = latest_volume_name
        self.concurrency_group = concurrency_group
        self.shutdown_event = shutdown_event

    def _volume_is_in_use(self, volume_name: str) -> bool:
        """Check if a Docker volume is currently in use by any container."""
        try:
            ps_result = self.concurrency_group.run_process_to_completion(
                command=[
                    "docker",
                    "ps",
                    "-a",
                    "--filter",
                    f"volume={volume_name}",
                    "--format",
                    "{{.ID}}",
                ],
                shutdown_event=self.shutdown_event,
            )
        except ProcessError as e:
            if self.shutdown_event is not None and self.shutdown_event.is_set():
                raise CancelledByEventError()
            raise
        containers_using_volume = [c for c in ps_result.stdout.strip().split("\n") if c]
        return len(containers_using_volume) > 0

    def _get_volume_creation_date(self, volume_name: str) -> datetime | None:
        """Get the creation date of a Docker volume.

        Returns None if the creation date cannot be determined.
        """
        try:
            inspect_result = self.concurrency_group.run_process_to_completion(
                command=["docker", "volume", "inspect", volume_name, "--format", "{{json .}}"],
                shutdown_event=self.shutdown_event,
            )
            volume_info = json.loads(inspect_result.stdout.strip())

            created_at_str = volume_info.get("CreatedAt", "")
            if not created_at_str:
                logger.debug(f"Could not determine creation date for volume {volume_name}")
                return None

            # Parse the timestamp (format: 2024-01-15T10:30:45Z or 2024-01-15T10:30:45-07:00)
            try:
                created_at = datetime.fromisoformat(created_at_str.replace("Z", "+00:00"))
                return created_at
            except ValueError:
                logger.debug(f"Could not parse creation date for volume {volume_name}: {created_at_str}")
                return None
        except ProcessError as e:
            if self.shutdown_event is not None and self.shutdown_event.is_set():
                raise CancelledByEventError()
            logger.debug(f"Failed to inspect volume {volume_name}: {e}")
            return None

    def _should_prune_prod_volume(self, volume_name: str) -> bool:
        # Skip the current volume
        if volume_name == self.latest_volume_name:
            logger.debug(f"Skipping current volume: {volume_name}")
            return False

        # Check if volume is in use
        if self._volume_is_in_use(volume_name):
            return False

        # Check creation time
        created_at = self._get_volume_creation_date(volume_name)
        if created_at is None:
            return False

        # Only prune volumes created MORE than 7 days ago (created_at < cutoff_date)
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=7)
        if created_at > cutoff_date:
            return False

        return True

    def _get_volumes_to_prune(self) -> list[str]:
        # List all volumes that start with imbue_control_plane_
        try:
            result = self.concurrency_group.run_process_to_completion(
                command=[
                    "docker",
                    "volume",
                    "ls",
                    "--format",
                    "{{.Name}}",
                ],
                shutdown_event=self.shutdown_event,
            )
        except ProcessError as e:
            if self.shutdown_event is not None and self.shutdown_event.is_set():
                raise CancelledByEventError()
            raise

        volume_infos = []
        for line in result.stdout.splitlines():
            info = ControlPlaneVolumeInformation.from_volume_name(line.strip())
            if info is not None:
                volume_infos.append(info)
        logger.debug(f"Found {len(volume_infos)} control plane volumes")

        dev_volumes_to_prune = self._get_dev_volumes_to_prune([info for info in volume_infos if info.is_dev_build])
        logger.debug(f"Identified {len(dev_volumes_to_prune)} dev control plane volumes to prune")
        normal_volumes_to_prune = self._get_prod_volumes_to_prune(
            [info for info in volume_infos if not info.is_dev_build]
        )
        logger.debug(f"Identified {len(normal_volumes_to_prune)} normal control plane volumes to prune")

        return [*dev_volumes_to_prune, *normal_volumes_to_prune]

    def _get_dev_volumes_to_prune(self, volume_infos: Iterable[ControlPlaneVolumeInformation]) -> list[str]:
        # Group dev volumes by commit hash.
        volumes_by_commit: dict[str, list[ControlPlaneVolumeInformation]] = {}
        for info in volume_infos:
            volumes_by_commit.setdefault(info.commit_hash, []).append(info)
        volumes_to_keep: set[str] = set()
        for infos in volumes_by_commit.values():
            infos_with_creation_timestamps = [
                (info, self._get_volume_creation_date(info.as_volume_name())) for info in infos
            ]

            # Only keep volumes created within the last 7 days (or with unknown creation date)
            recent_infos = [
                info
                for info, created_at in infos_with_creation_timestamps
                if created_at is None or created_at > datetime.now(timezone.utc) - timedelta(days=7)
            ]

            # Keep the most recent volume among the recent ones
            if recent_infos:
                most_recent_info = max(
                    recent_infos,
                    key=lambda info: self._get_volume_creation_date(info.as_volume_name())
                    or datetime.min.replace(tzinfo=timezone.utc),
                )
                volumes_to_keep.add(most_recent_info.as_volume_name())

        # Keep volumes that are in use.
        for info in volume_infos:
            if self._volume_is_in_use(info.as_volume_name()):
                volumes_to_keep.add(info.as_volume_name())

        # Prune all other dev volumes.
        volumes_to_prune = {info.as_volume_name() for info in volume_infos} - volumes_to_keep
        return list(volumes_to_prune)

    def _get_prod_volumes_to_prune(self, volume_infos: Iterable[ControlPlaneVolumeInformation]) -> list[str]:
        names = [info.as_volume_name() for info in volume_infos]
        return [name for name in names if self._should_prune_prod_volume(name)]

    def _prune_volumes(self, volumes_to_prune: list[str]) -> None:
        """Delete the specified Docker volumes."""
        if not volumes_to_prune:
            logger.debug("No old control plane volumes to prune")
            return

        logger.debug(f"Pruning {len(volumes_to_prune)} control plane volumes.")
        successfully_pruned = 0
        for volume_name in volumes_to_prune:
            try:
                self.concurrency_group.run_process_to_completion(
                    command=["docker", "volume", "rm", volume_name], shutdown_event=self.shutdown_event
                )
                logger.debug(f"Successfully pruned volume: {volume_name}")
                successfully_pruned += 1
            except ProcessError as e:
                if self.shutdown_event is not None and self.shutdown_event.is_set():
                    raise CancelledByEventError()
                logger.debug(f"Failed to prune volume {volume_name}: {e}")
        logger.info(f"Pruned {successfully_pruned} control plane volumes.")

    def prune_old_control_plane_volumes(self) -> None:
        """Deletes old control plane Docker volumes that match all of the following criteria:
        - name starts with imbue_control_plane_
        - is not the current/latest version
        - is not in use
        - was created >7 days ago (time limit to make it easy to switch between release channels or versions; we may tighten this in the future)
        """
        try:
            volumes_to_prune = self._get_volumes_to_prune()
            self._prune_volumes(volumes_to_prune)
        except Exception as e:
            if isinstance(e, CancelledByEventError):
                raise
            # Don't fail the entire operation if pruning fails, but log a warning
            logger.debug(f"Error during control plane volume pruning: {e}")
