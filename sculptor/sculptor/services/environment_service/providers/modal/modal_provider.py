import json
from pathlib import Path
from threading import Lock
from typing import Mapping

import modal
from loguru import logger
from pydantic import PrivateAttr

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.common import generate_id
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.secrets_utils import Secret
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.environments.base import EnvironmentConfig
from sculptor.interfaces.environments.base import Image
from sculptor.interfaces.environments.base import ImageConfig
from sculptor.interfaces.environments.base import ModalEnvironmentConfig
from sculptor.interfaces.environments.base import ModalImage
from sculptor.interfaces.environments.base import ModalImageConfig
from sculptor.interfaces.environments.provider_status import OkStatus
from sculptor.interfaces.environments.provider_status import ProviderStatus
from sculptor.primitives.ids import EnvironmentID
from sculptor.primitives.ids import ModalImageObjectID
from sculptor.primitives.ids import ModalSandboxObjectID
from sculptor.services.environment_service.api import DEFAULT_TASK_SPECIFIC_CONTEXT
from sculptor.services.environment_service.api import TaskImageCleanupData
from sculptor.services.environment_service.api import TaskSpecificContext
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.modal_environment import ModalEnvironment
from sculptor.services.environment_service.providers.api import EnvironmentProvider
from sculptor.services.environment_service.providers.modal.app_context import use_modal_app
from sculptor.services.environment_service.providers.modal.environment_utils import build_sandbox_in_app
from sculptor.services.environment_service.providers.modal.new_image_builder import (
    build_modal_image_from_baseline_repo,
)
from sculptor.services.environment_service.providers.provider_types import ModalMarker
from sculptor.utils.build import get_sculptor_folder


def _save_snapshot_data(snapshot_id_by_sandbox_id: dict[ModalSandboxObjectID, ModalImageObjectID]) -> None:
    most_recent_snapshot_data_path = get_sculptor_folder() / "providers" / "modal" / "snapshots.json"
    most_recent_snapshot_data_path.parent.mkdir(parents=True, exist_ok=True)
    most_recent_snapshot_data_path.with_suffix(".tmp").write_text(json.dumps(snapshot_id_by_sandbox_id))
    most_recent_snapshot_data_path.with_suffix(".tmp").rename(most_recent_snapshot_data_path)
    logger.trace("Wrote modal snapshot data to {}", most_recent_snapshot_data_path)


def _load_snapshot_data() -> dict[ModalSandboxObjectID, ModalImageObjectID]:
    most_recent_snapshot_data_path = get_sculptor_folder() / "providers" / "modal" / "snapshots.json"
    if most_recent_snapshot_data_path.exists():
        try:
            return {
                ModalSandboxObjectID(sandbox_id): ModalImageObjectID(image_id)
                for sandbox_id, image_id in json.loads(most_recent_snapshot_data_path.read_text()).items()
            }
        except FileNotFoundError:
            return {}
        except Exception as e:
            log_exception(e, "Failed to load modal snapshot data from {dp}", dp=most_recent_snapshot_data_path)
            return {}
    return {}


class ModalProvider(EnvironmentProvider[ModalMarker]):
    _snapshot_id_by_sandbox_id: dict[ModalSandboxObjectID, ModalImageObjectID] = PrivateAttr(
        default_factory=_load_snapshot_data
    )
    _snapshot_id_by_sandbox_id_lock: Lock = PrivateAttr(default_factory=Lock)

    def create_image(
        self,
        config: ImageConfig[ModalMarker],
        project_id: ProjectID,
        secrets: Mapping[str, str | Secret],
        cached_repo_tarball_parent_directory: Path,
        environment_prefix: str,
        image_metadata: ImageMetadataV1,
        task_specific_context: TaskSpecificContext = DEFAULT_TASK_SPECIFIC_CONTEXT,
        shutdown_event: ReadOnlyEvent | None = None,
        progress_handle: ProgressHandle | None = None,
    ) -> ModalImage:
        # TODO(millan): actually use the progress handle
        assert isinstance(config, ModalImageConfig)  # the only ImageConfig[ModalMarker] is ModalImageConfig
        app_name = f"{environment_prefix}{generate_id()}"
        image = build_modal_image_from_baseline_repo(
            # TODO: secrets has values that are str | Secret, but build_modal_image_from_baseline_repo expects only str
            Path(config.dockerfile_path),
            cached_repo_tarball_parent_directory,
            app_name,
            project_id,
            secrets,  # pyre-fixme[6]
        )
        return image

    def cleanup_stale_resources(
        self,
        task_metadata_by_task_id: dict[TaskID, TaskImageCleanupData],
        settings: SculptorSettings,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> None:
        # Modal handles resource (e.g. image and sandbox) lifecycle automatically, no manual cleanup needed
        ...

    def create_environment(
        self,
        image: Image[ModalMarker],
        config: EnvironmentConfig[ModalMarker],
        environment_prefix: str,
        concurrency_group: ConcurrencyGroup,  # doesn't actually use this!
        task_id: TaskID | None = None,
        name: str | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
        container_setup_handle: ProgressHandle | None = None,
    ) -> ModalEnvironment:
        # FIXME: figure out whether we want to be detached or not -- it could be unpleasant for the user if they crash
        #  and containers are left running unexpectedly...
        #  likely we want to start out with this being detached=False, and then after we've moved the agent process
        #  into the container, we can give an option to keep the agent running even when sculptor is closed
        #  though that will come with additional complexity -- you'll likely want to have it automatically stop
        #  once it is idle for a while, possibly wake at different times to check conditions, etc.

        assert isinstance(image, ModalImage)  # the only Image[ModalMarker] is ModalImage
        with use_modal_app(image.app_name, is_detached=True) as app:
            modal_image = modal.Image.from_id(image.image_id)
            sandbox = build_sandbox_in_app(app, modal_image, config=config)
            logger.info("Created sandbox with id: {}", sandbox.object_id)
            # TODO: ModalEnvironment doesn't use concurrency group
            return ModalEnvironment(  # pyre-fixme: modal
                config=config,
                environment_id=ModalSandboxObjectID(sandbox.object_id),
                app_name=image.app_name,
                _provider_health_check=self.get_status,
                _on_snapshot=self.on_snapshot,
                project_id=image.project_id,
            )

    def start_environment(
        self,
        environment_id: EnvironmentID[ModalMarker],
        project_id: ProjectID,
        config: EnvironmentConfig[ModalMarker],
        environment_prefix: str,
        name: str,
        concurrency_group: ConcurrencyGroup | None = None,  # this isn't actually used!
        task_id: TaskID | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> ModalEnvironment:
        # FIXME: we'll need to similarly think here about whether the configuration has shifted
        #  in the modal case, that is easier to modify if the container is not already running
        #  but if it is running, that means it needs to be restarted
        #  similarly, we'll need to make sure that we have upgraded control plan Volumes here
        raise NotImplementedError()

    def cleanup(self, environment_prefix: str):
        pass

    def on_snapshot(self, snapshot: ModalImage, is_persisted: bool) -> None:
        with self._snapshot_id_by_sandbox_id_lock:
            # TODO: it looks like ModalEnvironment, not ModalProvider, has the sandbox_id attribute. this is broken
            self._snapshot_id_by_sandbox_id[self.sandbox_id] = snapshot.image_id  # pyre-fixme: modal
            _save_snapshot_data(self._snapshot_id_by_sandbox_id)

    def get_default_environment_config(self) -> ModalEnvironmentConfig:
        return ModalEnvironmentConfig()

    def get_status(self) -> ProviderStatus:
        """
        Get the current status of the Modal provider.

        Returns:
            ProviderStatus: The current status of the Modal provider.
        """
        return OkStatus(message="Modal is available")
