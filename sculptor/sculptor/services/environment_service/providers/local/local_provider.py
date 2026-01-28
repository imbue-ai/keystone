import shutil
from pathlib import Path
from typing import Mapping

from loguru import logger

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.secrets_utils import Secret
from imbue_core.subprocess_utils import ProcessError
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.environments.base import EnvironmentConfig
from sculptor.interfaces.environments.base import Image
from sculptor.interfaces.environments.base import ImageConfig
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.interfaces.environments.base import LocalImage
from sculptor.interfaces.environments.base import LocalImageConfig
from sculptor.interfaces.environments.provider_status import OkStatus
from sculptor.interfaces.environments.provider_status import ProviderStatus
from sculptor.primitives.ids import EnvironmentID
from sculptor.primitives.ids import LocalEnvironmentID
from sculptor.primitives.ids import LocalMarker
from sculptor.services.environment_service.api import DEFAULT_TASK_SPECIFIC_CONTEXT
from sculptor.services.environment_service.api import TaskImageCleanupData
from sculptor.services.environment_service.api import TaskSpecificContext
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.local_environment import LocalEnvironment
from sculptor.services.environment_service.providers.api import EnvironmentProvider
from sculptor.services.environment_service.providers.local.environment_utils import build_local_environment
from sculptor.services.environment_service.providers.local.environment_utils import (
    get_local_environment_sandbox_directory,
)
from sculptor.services.environment_service.providers.local.image_utils import build_local_image


class LocalProvider(EnvironmentProvider[LocalMarker]):
    def create_image(
        self,
        config: ImageConfig[LocalMarker],
        project_id: ProjectID,
        secrets: Mapping[str, str | Secret],
        cached_repo_tarball_parent_directory: Path,
        environment_prefix: str,
        image_metadata: ImageMetadataV1,
        task_specific_context: TaskSpecificContext = DEFAULT_TASK_SPECIFIC_CONTEXT,
        shutdown_event: ReadOnlyEvent | None = None,
        progress_handle: ProgressHandle | None = None,
    ) -> LocalImage:
        # TODO(millan): actually use the progress handle
        assert isinstance(config, LocalImageConfig)  # the only ImageConfig[LocalMarker] is LocalImageConfig
        return build_local_image(code_directory=config.code_directory, project_id=project_id)

    def cleanup_stale_resources(
        self,
        task_metadata_by_task_id: dict[TaskID, TaskImageCleanupData],
        settings: SculptorSettings,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> None:
        # No cleanup needed for local provider
        ...

    def create_environment(
        self,
        image: Image[LocalMarker],
        config: EnvironmentConfig[LocalMarker],
        concurrency_group: ConcurrencyGroup,
        environment_prefix: str,
        task_id: TaskID | None = None,
        name: str | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
        container_setup_handle: ProgressHandle | None = None,
    ) -> LocalEnvironment:
        assert isinstance(image, LocalImage)  # the only Image[LocalMarker] is LocalImage
        assert isinstance(
            config, LocalEnvironmentConfig
        )  # the only EnvironmentConfig[LocalMarker] is LocalEnvironmentConfig
        return build_local_environment(
            local_image=image,
            config=config,
            concurrency_group=concurrency_group,
            environment_prefix=environment_prefix,
            provider_health_check=self.get_status,
        )

    def start_environment(
        self,
        environment_id: EnvironmentID[LocalMarker],
        project_id: ProjectID,
        config: EnvironmentConfig[LocalMarker],
        environment_prefix: str,
        name: str,
        concurrency_group: ConcurrencyGroup,
        task_id: TaskID | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> LocalEnvironment:
        # the only EnvironmentID[LocalMarker] is LocalEnvironmentID
        assert isinstance(environment_id, LocalEnvironmentID)
        # the only EnvironmentConfig[LocalMarker] is LocalEnvironmentConfig
        assert isinstance(config, LocalEnvironmentConfig)
        return LocalEnvironment(
            environment_id=environment_id,
            project_id=project_id,
            config=config,
            concurrency_group=concurrency_group,
            _provider_health_check=self.get_status,
        )

    def get_default_environment_config(self) -> LocalEnvironmentConfig:
        return LocalEnvironmentConfig()

    def cleanup(self, environment_prefix: str):
        try:
            cleanup_outdated_local_sandboxes(environment_prefix)
        except ProcessError as e:
            log_exception(e, "Failed to clean up local sandboxes", priority=ExceptionPriority.LOW_PRIORITY)

    def get_status(self) -> ProviderStatus:
        """
        Get the current status of the Local provider.

        Returns:
            ProviderStatus: The current status of the Local provider.
        """
        return OkStatus(message="Local is available")


def cleanup_outdated_local_sandboxes(environment_prefix: str) -> None:
    environment_sandbox_directory = get_local_environment_sandbox_directory(environment_prefix)
    if not environment_sandbox_directory.exists():
        return
    for sandbox in environment_sandbox_directory.iterdir():
        if sandbox.is_dir() and not sandbox.name.startswith("."):
            logger.info("Cleaning up outdated local sandbox: {}", sandbox)
            shutil.rmtree(sandbox, ignore_errors=True)
