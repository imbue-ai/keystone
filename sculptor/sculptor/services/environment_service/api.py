from abc import ABC
from abc import abstractmethod
from contextlib import contextmanager
from pathlib import Path
from typing import Final
from typing import Generator
from typing import Mapping

from pydantic import BaseModel
from typing_extensions import override

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.secrets_utils import Secret
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import EnvironmentConfig
from sculptor.interfaces.environments.base import Image
from sculptor.interfaces.environments.base import ImageConfig
from sculptor.interfaces.environments.base import ImageTypes
from sculptor.interfaces.environments.base import ProviderTag
from sculptor.interfaces.environments.provider_status import ProviderStatusTypes
from sculptor.primitives.service import Service
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1


class TaskImageCleanupData(BaseModel):
    task_id: TaskID
    last_image_id: str
    is_deleted: bool
    is_archived: bool
    all_image_ids: tuple[str, ...] = ()  # NOTE: all image ids only includes snapshots and not the base image


class TaskSpecificContext(ABC):
    """When invoking environment service methods, sometimes it is valuable to inject task-specific context.

    For example, we may want to be able to emit warnings to the user in the context of a specific task such that they
    can be surfaced in the appropriate location within the UI.
    """

    @abstractmethod
    def emit_warning(self, message: str) -> None:
        """Surface a warning to the user in the appropriate context."""


class _DefaultTaskSpecificContext(TaskSpecificContext):
    """A default implementation of TaskSpecificContext that does nothing."""

    @override
    def emit_warning(self, message: str) -> None:
        """Discard the message."""
        pass


DEFAULT_TASK_SPECIFIC_CONTEXT: Final[TaskSpecificContext] = _DefaultTaskSpecificContext()


# TODO: we need to consider the process for Image and Volume deletion
# TODO: document the exceptions that can be raised by each of these methods
class EnvironmentService(Service, ABC):
    """
    This services enables robust environment creation and destruction via "structured concurrency".

    This means that, when you exit the context manager for a given environment, it will always be cleaned up properly,

    This service will automatically clean up any previous environments when it is started.
    This is required for correctness in the face of hard crashes or unexpected shutdowns.
    """

    @abstractmethod
    def ensure_image(
        self,
        config: ImageConfig,
        project_id: ProjectID,
        secrets: Mapping[str, str | Secret],
        active_repo_path: Path,
        cached_repo_path: Path,
        image_metadata: ImageMetadataV1,
        force_tarball_refresh: bool = False,
        task_specific_context: TaskSpecificContext = DEFAULT_TASK_SPECIFIC_CONTEXT,
        shutdown_event: ReadOnlyEvent | None = None,
        progress_handle: ProgressHandle | None = None,
    ) -> ImageTypes:
        """
        Get a cached image or create an image based on the given configuration and secrets.

        Raises:
            ProviderError: if provider is misconfigured, unavailable, etc.
            ImageConfigError: if image config or Dockerfile is invalid
            CancelledByEventError: if shutdown_event is set during execution
        """

    @abstractmethod
    def remove_stale_images(self, shutdown_event: ReadOnlyEvent | None = None) -> None:
        """
        Remove stale images from each provider.
        """

    @abstractmethod
    @contextmanager
    def generate_environment(
        self,
        image: Image,
        project_id: ProjectID,
        concurrency_group: ConcurrencyGroup,
        task_id: TaskID | None = None,
        config: EnvironmentConfig | None = None,
        name: str | None = None,
    ) -> Generator[Environment, None, None]:
        """
        Generate an environment based on the given image.

        The environment will be cleaned up when the context manager exits.

        Raises:
            ProviderError: if provider is misconfigured, unavailable, etc.
            ImageConfigError: if image config is invalid
            SetupError: if the setup commands fail to run
        """

    @abstractmethod
    def create_environment(
        self,
        source: Image | str,
        project_id: ProjectID,
        concurrency_group: ConcurrencyGroup,
        task_id: TaskID | None = None,
        config: EnvironmentConfig | None = None,
        name: str | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
        container_setup_handle: ProgressHandle | None = None,
    ) -> Environment:
        """
        Create an environment based on the given image or environment ID

        Raises:
            ProviderError: if provider is misconfigured, unavailable, etc.
            ImageConfigError: if image config is invalid
            SetupError: if the setup commands fail to run
        """

    @abstractmethod
    def get_provider_statuses(self) -> dict[ProviderTag, ProviderStatusTypes]:
        """
        Get the status of each provider.
        """
