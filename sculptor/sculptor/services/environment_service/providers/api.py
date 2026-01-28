from abc import ABC
from abc import abstractmethod
from pathlib import Path
from typing import Generic
from typing import Mapping

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.pydantic_serialization import MutableModel
from imbue_core.secrets_utils import Secret
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import EnvironmentConfig
from sculptor.interfaces.environments.base import Image
from sculptor.interfaces.environments.base import ImageConfig
from sculptor.interfaces.environments.provider_status import ProviderStatus
from sculptor.primitives.ids import EnvironmentID
from sculptor.services.environment_service.api import DEFAULT_TASK_SPECIFIC_CONTEXT
from sculptor.services.environment_service.api import TaskImageCleanupData
from sculptor.services.environment_service.api import TaskSpecificContext
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.providers.provider_types import ProviderMarkerT


class EnvironmentProvider(MutableModel, ABC, Generic[ProviderMarkerT]):
    @abstractmethod
    def create_image(
        self,
        config: ImageConfig[ProviderMarkerT],
        project_id: ProjectID,
        secrets: Mapping[str, str | Secret],
        cached_repo_tarball_parent_directory: Path,
        environment_prefix: str,
        image_metadata: ImageMetadataV1,
        task_specific_context: TaskSpecificContext = DEFAULT_TASK_SPECIFIC_CONTEXT,
        shutdown_event: ReadOnlyEvent | None = None,
        progress_handle: ProgressHandle | None = None,
    ) -> Image[ProviderMarkerT]:
        """
        Create an image based on the given configuration and secrets.

        Raises:
            ProviderError: if provider is misconfigured, unavailable, etc.
            ImageConfigError: if image config or Dockerfile is invalid
            CancelledByEventError: if the creation is cancelled via the shutdown_event
        """

    @abstractmethod
    def cleanup_stale_resources(
        self,
        task_metadata_by_task_id: dict[TaskID, TaskImageCleanupData],
        settings: SculptorSettings,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> None:
        """
        Clean up stale resources that are no longer associated with active tasks.

        For example, this may involve removing old images or containers that are no longer in use.
        """

    @abstractmethod
    def create_environment(
        self,
        image: Image[ProviderMarkerT],
        config: EnvironmentConfig[ProviderMarkerT],
        concurrency_group: ConcurrencyGroup,
        environment_prefix: str,
        task_id: TaskID | None = None,
        name: str | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
        container_setup_handle: ProgressHandle | None = None,
    ) -> Environment[ProviderMarkerT]:
        """
        Generate an environment based on the given image.

        Raises:
            ProviderError: if provider is misconfigured, unavailable, etc.
            ImageConfigError: if image config is invalid
            SetupError: if the setup commands fail to run
            CancelledByEventError: if the creation is cancelled via the shutdown_event
        """

    @abstractmethod
    def start_environment(
        self,
        environment_id: EnvironmentID[ProviderMarkerT],
        project_id: ProjectID,
        config: EnvironmentConfig[ProviderMarkerT],
        environment_prefix: str,
        name: str,
        concurrency_group: ConcurrencyGroup,
        task_id: TaskID | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> Environment[ProviderMarkerT]:
        """
        Start a previously created Environment based on the given environment ID.

        Raises:
            ProviderError: if provider is misconfigured, unavailable, etc.
            ImageConfigError: if image config is invalid
            SetupError: if the setup commands fail to run
            CancelledByEventError: if the creation is cancelled via the shutdown_event
        """

    @abstractmethod
    def get_default_environment_config(self) -> EnvironmentConfig[ProviderMarkerT]: ...

    @abstractmethod
    def cleanup(self, environment_prefix: str) -> None: ...

    @abstractmethod
    def get_status(self) -> ProviderStatus:
        """
        Get the current status of the provider.

        Returns:
            ProviderStatus: The current status of the provider.
        """
        ...
