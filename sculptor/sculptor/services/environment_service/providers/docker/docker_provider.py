import json
from pathlib import Path
from threading import Lock
from typing import Mapping

from loguru import logger
from pydantic import PrivateAttr

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.file_utils import atomic_writer_to
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.sculptor import telemetry
from imbue_core.secrets_utils import Secret
from imbue_core.subprocess_utils import ProcessError
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.environments.base import EnvironmentConfig
from sculptor.interfaces.environments.base import Image
from sculptor.interfaces.environments.base import ImageConfig
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.interfaces.environments.errors import EnvironmentConfigurationChangedError
from sculptor.interfaces.environments.errors import SetupError
from sculptor.interfaces.environments.provider_status import ProviderStatus
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.primitives.ids import DockerContainerID
from sculptor.primitives.ids import DockerMarker
from sculptor.primitives.ids import EnvironmentID
from sculptor.services.environment_service.api import DEFAULT_TASK_SPECIFIC_CONTEXT
from sculptor.services.environment_service.api import TaskImageCleanupData
from sculptor.services.environment_service.api import TaskSpecificContext
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.environments.image_tags import ImageCreatedFor
from sculptor.services.environment_service.environments.image_tags import ImageInfo
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV0
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.image_tags import get_current_sculptor_images_info
from sculptor.services.environment_service.environments.image_tags import get_environment_prefix
from sculptor.services.environment_service.environments.image_tags import (
    parse_image_info_associated_with_this_sculptor_instance,
)
from sculptor.services.environment_service.environments.utils import get_docker_status
from sculptor.services.environment_service.providers.api import EnvironmentProvider
from sculptor.services.environment_service.providers.docker.control_plane_volume_garbage_collector import (
    ControlPlaneVolumeGarbageCollector,
)
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    build_local_devcontainer_image,
)
from sculptor.services.environment_service.providers.docker.environment_utils import build_docker_environment
from sculptor.services.environment_service.providers.docker.environment_utils import destroy_outdated_docker_containers
from sculptor.services.environment_service.providers.docker.environment_utils import get_base_docker_create_args
from sculptor.services.environment_service.providers.docker.environment_utils import get_external_port_by_name_mapping
from sculptor.services.environment_service.providers.docker.environment_utils import setup_docker_environment
from sculptor.services.environment_service.providers.docker.environment_utils import start_docker_container
from sculptor.services.environment_service.providers.docker.environment_utils import stop_outdated_docker_containers
from sculptor.services.environment_service.providers.docker.environment_utils import (
    upgrade_container_and_read_user_and_home,
)
from sculptor.services.environment_service.providers.docker.errors import DockerError
from sculptor.services.environment_service.providers.docker.image_utils import DeletionTier
from sculptor.services.environment_service.providers.docker.image_utils import calculate_image_ids_to_delete
from sculptor.services.environment_service.providers.docker.image_utils import (
    delete_docker_image_and_any_stopped_containers,
)
from sculptor.services.environment_service.providers.docker.image_utils import extend_image_ids_with_similar_hashes
from sculptor.services.environment_service.providers.docker.image_utils import get_image_ids_with_running_containers
from sculptor.services.environment_service.providers.docker.image_utils import record_images_to_posthog
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    ControlPlaneImageNameProvider,
)
from sculptor.startup_checks import check_docker_installed
from sculptor.startup_checks import check_docker_running
from sculptor.utils.build import get_sculptor_folder

# These commands currently fall out of sync with containers that are restarted due to snapshots.
# We should investigate making this more robust in the future.
#
# Look for invocations of `environment.destroy()` that are missing associated updates to this list.
#
# One possibility is to occasionally reconcile this list with the actual containers (stopped and running)
# on the user's machine.


def _save_container_id_data(previous_create_command_by_environment_id: dict[DockerContainerID, list[str]]) -> None:
    most_recent_data_path = get_sculptor_folder() / "providers" / "docker" / "container_ids.json"
    most_recent_data_path.parent.mkdir(parents=True, exist_ok=True)
    with atomic_writer_to(most_recent_data_path, replace_if_exists=True) as most_recent_data_path_writer:
        most_recent_data_path_writer.write_text(json.dumps(previous_create_command_by_environment_id))
    logger.trace("Wrote docker container id data to {}", most_recent_data_path)


def _load_container_id_data() -> dict[DockerContainerID, list[str]]:
    most_recent_data_path = get_sculptor_folder() / "providers" / "docker" / "container_ids.json"
    try:
        return json.loads(most_recent_data_path.read_text())
    except FileNotFoundError:
        return {}
    except Exception as e:
        log_exception(e, "Failed to load container id data from {dp}", dp=most_recent_data_path)
        return {}


class DockerProvider(EnvironmentProvider[DockerMarker]):
    concurrency_group: ConcurrencyGroup
    _previous_create_command_by_environment_id: dict[DockerContainerID, list[str]] = PrivateAttr(
        default_factory=_load_container_id_data
    )
    _previous_create_command_by_environment_id_lock: Lock = PrivateAttr(default_factory=Lock)

    def create_image(
        self,
        config: ImageConfig,
        project_id: ProjectID,
        secrets: Mapping[str, str | Secret],
        cached_repo_tarball_parent_directory: Path,
        environment_prefix: str,
        image_metadata: ImageMetadataV1,
        task_specific_context: TaskSpecificContext = DEFAULT_TASK_SPECIFIC_CONTEXT,
        shutdown_event: ReadOnlyEvent | None = None,
        progress_handle: ProgressHandle | None = None,
    ) -> LocalDockerImage:
        if not isinstance(config, LocalDevcontainerImageConfig):
            raise ValueError(f"Invalid config type: {type(config)}")

        image_name = f"{environment_prefix}{project_id}"
        image = build_local_devcontainer_image(
            config,
            cached_repo_tarball_parent_directory,
            project_id=project_id,
            image_repo=image_name,
            task_specific_context=task_specific_context,
            concurrency_group=self.concurrency_group,
            image_metadata=image_metadata,
            shutdown_event=shutdown_event,
            progress_handle=progress_handle,
        )
        return image

    def cleanup_stale_resources(
        self,
        task_metadata_by_task_id: dict[TaskID, TaskImageCleanupData],
        settings: SculptorSettings,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> None:
        self._clean_up_stale_containers(task_metadata_by_task_id, settings, shutdown_event)
        self._clean_up_stale_images(task_metadata_by_task_id, settings, shutdown_event)
        self._clean_up_stale_control_plane_volumes(shutdown_event)

    def _clean_up_stale_containers(
        self,
        task_metadata_by_task: dict[TaskID, TaskImageCleanupData],
        settings: SculptorSettings,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> None:
        environment_prefix = get_environment_prefix(settings)

        # For now, we restrict ourselves to only deleting containers for tasks that have been deleted.
        # This may be conservative but it's more aggressive than what we are already doing.
        deleted_task_container_names = {
            # TODO(sam): Container name encoding should be centralized.
            f"{environment_prefix}{task_id}"
            for task_id, data in task_metadata_by_task.items()
            if data.is_deleted
        }
        removed_container_ids = destroy_outdated_docker_containers(
            container_name_predicate=lambda name: name in deleted_task_container_names,
            concurrency_group=self.concurrency_group,
            shutdown_event=shutdown_event,
        )
        with self._previous_create_command_by_environment_id_lock:
            for container_id in removed_container_ids:
                self._previous_create_command_by_environment_id.pop(container_id, None)
            _save_container_id_data(self._previous_create_command_by_environment_id)

    def _clean_up_stale_images(
        self,
        task_metadata_by_task_id: dict[TaskID, TaskImageCleanupData],
        settings: SculptorSettings,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> None:
        image_infos = get_current_sculptor_images_info(
            self.concurrency_group, get_environment_prefix(settings), shutdown_event=shutdown_event
        )
        record_images_to_posthog(self.concurrency_group, image_infos)
        with ObservableThreadPoolExecutor(self.concurrency_group, max_workers=16) as executor:
            v0_future = executor.submit(
                self._remove_stale_images_v0, image_infos, task_metadata_by_task_id, shutdown_event
            )
            v1_future = executor.submit(
                self._remove_stale_images_v1, task_metadata_by_task_id, settings, executor, shutdown_event
            )
            v0_future.result()
            v1_future.result()
        logger.debug("Docker image cleanup completed")

    def _remove_stale_images_v0(
        self,
        image_infos: tuple[ImageInfo, ...],
        task_metadata_by_task_id: dict[TaskID, TaskImageCleanupData],
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> None:
        # TODO(sam): The task metadata pulls the image IDs from agent logs which means we may need to
        # support image IDs without the "sha256:" prefix.
        # Right now, we try to handle both cases. But we should have better hash-matching logic.
        v0_image_infos = [
            info
            for info in image_infos
            if isinstance(parse_image_info_associated_with_this_sculptor_instance(info), ImageMetadataV0)
        ]
        existing_image_ids = extend_image_ids_with_similar_hashes([info.id for info in v0_image_infos])
        active_image_ids = extend_image_ids_with_similar_hashes(
            get_image_ids_with_running_containers(self.concurrency_group, shutdown_event)
        )
        image_ids_to_delete = calculate_image_ids_to_delete(
            task_metadata_by_task_id, active_image_ids, existing_image_ids, DeletionTier.RARELY_DELETE
        )
        deleted_image_ids = []
        failed_image_ids = []
        deleted_container_ids: list[DockerContainerID] = []

        for image_id in image_ids_to_delete:
            is_deleted, new_deleted_image_ids = delete_docker_image_and_any_stopped_containers(
                image_id, self.concurrency_group, shutdown_event
            )
            deleted_container_ids.extend(new_deleted_image_ids)
            if is_deleted:
                deleted_image_ids.append(image_id)
            else:
                failed_image_ids.append(image_id)

        logger.debug("Successfully deleted the following Docker images: {}", deleted_image_ids)
        if len(failed_image_ids) > 0:
            logger.debug("{} images failed to delete", failed_image_ids)

        # finally, adjust our saved state to remove any containers that were deleted
        with self._previous_create_command_by_environment_id_lock:
            for container_id in deleted_container_ids:
                if container_id in self._previous_create_command_by_environment_id:
                    del self._previous_create_command_by_environment_id[container_id]
            _save_container_id_data(self._previous_create_command_by_environment_id)

    def _remove_stale_images_v1(
        self,
        task_metadata_by_task_id: dict[TaskID, TaskImageCleanupData],
        settings: SculptorSettings,
        executor: ObservableThreadPoolExecutor,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> None:
        image_infos = get_current_sculptor_images_info(
            self.concurrency_group,
            get_environment_prefix(settings),
            filter_to_instance_id=True,
            shutdown_event=shutdown_event,
        )
        image_repos_and_metadatas = [
            (info.repository, parse_image_info_associated_with_this_sculptor_instance(info)) for info in image_infos
        ]
        v1_image_metadatas = [
            (repo, metadata) for repo, metadata in image_repos_and_metadatas if isinstance(metadata, ImageMetadataV1)
        ]

        # These might also be archived, but that's okay.
        active_task_ids = {str(task_id) for task_id, data in task_metadata_by_task_id.items() if not data.is_deleted}

        # First, we find the `ImageCreatedFor.TASK` image tags that are not associated with an active task.
        # Later, we'll need to add the logic for `ImageCreatedFor.DAILY_CACHE`.

        image_tags_to_delete: set[str] = set()
        for repo, metadata in v1_image_metadatas:
            if metadata.created_for != ImageCreatedFor.TASK:
                continue
            if metadata.identifier not in active_task_ids:
                image_tag = metadata.to_docker_metadata().tag
                image_tags_to_delete.add(f"{repo}:{image_tag}")

        logger.info(f"Deleting the following docker image tags: {image_tags_to_delete}")
        image_tag_removal_futures = tuple(
            (image_tag, executor.submit(self._remove_image_by_tag, image_tag, shutdown_event))
            for image_tag in image_tags_to_delete
        )
        image_tags_removed = {
            image_tag
            for image_tag, removal_success_future in image_tag_removal_futures
            if removal_success_future.result()
        }

        logger.debug("Successfully deleted the following Docker image tags: {}", image_tags_removed)

    def _remove_image_by_tag(self, image_tag: str, shutdown_event: ReadOnlyEvent | None = None) -> bool:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError()
        try:
            self.concurrency_group.run_process_to_completion(
                ["docker", "rmi", image_tag], shutdown_event=shutdown_event
            )
        except ProcessError as e:
            if shutdown_event is not None and shutdown_event.is_set():
                raise CancelledByEventError() from e
            log_exception(
                e,
                "Failed to remove Docker image with tag {image_tag}; this likely means there are still containers using it",
                image_tag=image_tag,
                priority=ExceptionPriority.LOW_PRIORITY,
            )
            return False
        else:
            return True

    def _clean_up_stale_control_plane_volumes(self, shutdown_event: ReadOnlyEvent | None = None) -> None:
        control_plane_volume_name = ControlPlaneImageNameProvider().get_control_plane_volume_name()
        ControlPlaneVolumeGarbageCollector(
            latest_volume_name=control_plane_volume_name,
            concurrency_group=self.concurrency_group,
            shutdown_event=shutdown_event,
        ).prune_old_control_plane_volumes()

    def get_default_environment_config(self) -> LocalDockerEnvironmentConfig:
        return LocalDockerEnvironmentConfig()

    def cleanup(self, environment_prefix: str) -> None:
        try:
            stop_outdated_docker_containers(
                container_name_predicate=lambda x: x.startswith(environment_prefix),
                concurrency_group=self.concurrency_group,
            )
        except DockerError as e:
            # only log the error if docker is installed and running
            if check_docker_installed() and check_docker_running(self.concurrency_group):
                log_exception(e, "Failed to clean up docker containers", priority=ExceptionPriority.LOW_PRIORITY)

    def kill_containers_during_startup(self, environment_prefix: str) -> None:
        try:
            stop_outdated_docker_containers(
                container_name_predicate=lambda x: x.startswith(environment_prefix),
                concurrency_group=self.concurrency_group,
                is_killing=True,
            )
        except DockerError as e:
            # only log the error if docker is installed and running
            if check_docker_installed() and check_docker_running(self.concurrency_group):
                log_exception(e, "Failed to clean up docker containers", priority=ExceptionPriority.LOW_PRIORITY)

    def create_environment(
        self,
        image: Image[DockerMarker],
        config: EnvironmentConfig[DockerMarker],
        concurrency_group: ConcurrencyGroup,
        environment_prefix: str,
        task_id: TaskID | None = None,
        name: str | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
        container_setup_handle: ProgressHandle | None = None,
    ) -> DockerEnvironment:
        assert isinstance(image, LocalDockerImage)  # the only Image[DockerMarker] is LocalDockerImage
        assert isinstance(
            config, LocalDockerEnvironmentConfig
        )  # the only EnvironmentConfig[DockerMarker] is LocalDockerEnvironmentConfig
        environment, create_command = build_docker_environment(
            docker_image=image,
            config=config,
            environment_prefix=environment_prefix,
            concurrency_group=concurrency_group,
            name=name,
            provider_health_check=self.get_status,
            task_id=task_id,
            shutdown_event=shutdown_event,
            container_setup_handle=container_setup_handle,
        )
        with self._previous_create_command_by_environment_id_lock:
            self._previous_create_command_by_environment_id[environment.environment_id] = create_command
            _save_container_id_data(self._previous_create_command_by_environment_id)
        return environment

    def start_environment(
        self,
        environment_id: EnvironmentID[DockerMarker],
        project_id: ProjectID,
        config: EnvironmentConfig[DockerMarker],
        environment_prefix: str,
        name: str,
        concurrency_group: ConcurrencyGroup,
        task_id: TaskID | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
    ) -> DockerEnvironment:
        """This is for starting an environment that was already created by create_environment()."""
        assert isinstance(
            environment_id, DockerContainerID
        )  # the only EnvironmentID[DockerMarker] is DockerContainerID
        assert isinstance(
            config, LocalDockerEnvironmentConfig
        )  # the only EnvironmentConfig[DockerMarker] is LocalDockerEnvironmentConfig
        create_command = get_base_docker_create_args(
            environment_prefix + name,
            config.server_port_by_name,
        )
        with self._previous_create_command_by_environment_id_lock:
            previous_create_command = self._previous_create_command_by_environment_id.get(environment_id, None)
        if create_command != previous_create_command:
            raise EnvironmentConfigurationChangedError(
                f"The configuration has changed to {create_command} from {previous_create_command}"
            )
        start_docker_container(environment_id, concurrency_group)
        if task_id is not None:
            telemetry.emit_posthog_event(
                telemetry.PosthogEventModel(
                    name=telemetry.SculptorPosthogEvent.ENVIRONMENT_SETUP_DOCKER_STARTED_EXISTING_CONTAINER,
                    component=telemetry.ProductComponent.ENVIRONMENT_SETUP,
                    task_id=str(task_id),
                )
            )

        # We need to run the upgrade script since this container might have been created by an old version of Sculptor.
        container_user, container_user_home = upgrade_container_and_read_user_and_home(
            environment_id, concurrency_group
        )

        external_port_by_name = get_external_port_by_name_mapping(
            environment_id, config.server_port_by_name, concurrency_group
        )
        environment = DockerEnvironment(
            config=config,
            project_id=project_id,
            environment_id=environment_id,
            server_port_by_name=external_port_by_name,
            concurrency_group=concurrency_group,
            _provider_health_check=self.get_status,
            environment_prefix=environment_prefix,
            container_user=container_user,
            container_user_home=container_user_home,
        )
        try:
            setup_docker_environment(environment, shutdown_event)
        except (SetupError, CancelledByEventError):
            environment.close()
            raise
        if task_id is not None:
            telemetry.emit_posthog_event(
                telemetry.PosthogEventModel(
                    name=telemetry.SculptorPosthogEvent.ENVIRONMENT_SETUP_DOCKER_CONTAINER_FINISHED_SETUP,
                    component=telemetry.ProductComponent.ENVIRONMENT_SETUP,
                    task_id=str(task_id),
                )
            )
        return environment

    def get_status(self) -> ProviderStatus:
        """
        Get the current status of the Docker provider.

        Returns:
            ProviderStatus: The current status of the Docker provider.
        """
        return get_docker_status(self.concurrency_group)
