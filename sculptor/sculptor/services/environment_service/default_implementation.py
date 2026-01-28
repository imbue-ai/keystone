import os
import shutil
from collections import deque
from contextlib import contextmanager
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Generator
from typing import Mapping
from typing import Self
from typing import cast

from pydantic import PrivateAttr

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.file_utils import atomic_writer_to
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.sculptor import telemetry
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.secrets_utils import Secret
from imbue_core.subprocess_utils import ProcessError
from sculptor.config.settings import SculptorSettings
from sculptor.database.models import AgentTaskStateV1
from sculptor.database.models import Task
from sculptor.interfaces.agents.agent import AgentSnapshotRunnerMessage
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import EnvironmentConfig
from sculptor.interfaces.environments.base import Image
from sculptor.interfaces.environments.base import ImageConfig
from sculptor.interfaces.environments.base import ImageTypes
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.interfaces.environments.base import LocalEnvironmentConfig
from sculptor.interfaces.environments.base import ModalEnvironmentConfig
from sculptor.interfaces.environments.base import ProviderTag
from sculptor.interfaces.environments.errors import ProviderNotFoundError
from sculptor.interfaces.environments.provider_status import ProviderStatusTypes
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.primitives.ids import DockerContainerID
from sculptor.primitives.ids import EnvironmentIDTypes
from sculptor.primitives.ids import LocalEnvironmentID
from sculptor.primitives.ids import ModalSandboxObjectID
from sculptor.primitives.ids import ProviderMarkerT
from sculptor.primitives.ids import RequestID
from sculptor.services.data_model_service.api import DataModelService
from sculptor.services.environment_service.api import DEFAULT_TASK_SPECIFIC_CONTEXT
from sculptor.services.environment_service.api import EnvironmentService
from sculptor.services.environment_service.api import TaskImageCleanupData
from sculptor.services.environment_service.api import TaskSpecificContext
from sculptor.services.environment_service.environments.image_tags import ImageCreatedFor
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.image_tags import get_non_testing_environment_prefix
from sculptor.services.environment_service.providers.api import EnvironmentProvider
from sculptor.services.environment_service.providers.docker.devcontainer_image_builder import (
    start_control_plane_background_setup,
)
from sculptor.services.environment_service.providers.docker.docker_provider import DockerProvider
from sculptor.services.environment_service.providers.local.local_provider import LocalProvider
from sculptor.services.environment_service.providers.modal.modal_provider import ModalProvider
from sculptor.services.environment_service.providers.provider_types import ProviderMarkerTypes
from sculptor.services.environment_service.providers.provider_union import ProviderUnion
from sculptor.services.git_repo_service.api import GitRepoService
from sculptor.utils.shared_exclusive_lock import SharedExclusiveLock
from sculptor.utils.timeout import log_runtime
from sculptor.utils.type_utils import extract_leaf_types


def create_archived_repo(
    active_repo_path: Path,
    cached_tarball_path: Path,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent | None = None,
) -> None:
    cached_tarball_parent = cached_tarball_path.parent
    if cached_tarball_parent.exists():
        shutil.rmtree(cached_tarball_parent)
    cached_tarball_parent.mkdir(parents=True, exist_ok=True)

    # Get all files that are not gitignored (tracked + untracked)
    try:
        result = concurrency_group.run_process_to_completion(
            ["git", "ls-files", "-z", "--cached", "--exclude-standard"],
            cwd=active_repo_path,
            shutdown_event=shutdown_event,
        )
    except ProcessError as e:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError() from e
        raise
    stdout = result.stdout.strip()
    files_to_include = stdout.split("\0") if stdout else []

    # Add the big things in the .git directory, preserving mtimes. We later use a one-way Mutagen
    # sync to bring the rest of .git up to date quickly.
    files_to_include.append(".git/objects")
    files_to_include.append(".git/refs")
    files_to_include.append(".git/logs")

    # Filter out any files that don't actually exist, in case users have pending deletions
    extant_files: list[Path] = [file for file in files_to_include if (Path(active_repo_path) / file).exists()]

    # Create tarball with all non-gitignored files plus .git directory
    if extant_files:
        with (
            NamedTemporaryFile() as file_listing_temp,
            atomic_writer_to(cached_tarball_path) as cached_tarball_path_writer,
        ):
            file_listing = Path(file_listing_temp.name)
            file_listing.write_text("".join(str(filename) + "\n" for filename in extant_files))
            try:
                concurrency_group.run_process_to_completion(
                    ["tar", "-cf", str(cached_tarball_path_writer), "--files-from", str(file_listing)],
                    cwd=active_repo_path,
                    env={**os.environ, "COPYFILE_DISABLE": "1"},
                    shutdown_event=shutdown_event,
                )
            except ProcessError as e:
                if shutdown_event is not None and shutdown_event.is_set():
                    raise CancelledByEventError() from e
                raise


SHOULD_START_IMAGE_DOWNLOADS_IN_BACKGROUND_DEFAULT = True


class DefaultEnvironmentService(EnvironmentService):
    settings: SculptorSettings
    git_repo_service: GitRepoService
    data_model_service: DataModelService
    should_start_image_downloads_in_background: bool = SHOULD_START_IMAGE_DOWNLOADS_IN_BACKGROUND_DEFAULT
    _is_started: bool = PrivateAttr(default=False)

    _providers: dict[ProviderTag, ProviderUnion] = PrivateAttr()
    # We use a read-write lock here so that we can have multiple images being built at the same time,
    # but all of that behavior is mutually excluded with image cleanup.
    # If we did not use a read-write lock, then sculptor's attempts to eagerly build
    # an image for a project can block the start of a user-initiated task for a project,
    # resulting in no logs and an apparently stalled task.
    # See: https://imbue-ai.slack.com/archives/C0799HVGR7W/p1761851500765439
    _image_lock: SharedExclusiveLock = PrivateAttr(default_factory=SharedExclusiveLock)

    def init_providers(self) -> Self:
        providers: dict[ProviderTag, ProviderUnion] = {}
        if self.settings.DOCKER_PROVIDER_ENABLED:
            providers[ProviderTag.DOCKER] = DockerProvider(concurrency_group=self.concurrency_group)
            if self.should_start_image_downloads_in_background:
                start_control_plane_background_setup(
                    thread_suffix="EnvServiceInit", concurrency_group=self.concurrency_group
                )
        if self.settings.MODAL_PROVIDER_ENABLED:
            providers[ProviderTag.MODAL] = ModalProvider()
        if self.settings.LOCAL_PROVIDER_ENABLED:
            providers[ProviderTag.LOCAL] = LocalProvider()
        self._providers = providers
        return self

    # TODO: consider what should happen when there are errors from the provider during startup
    #  They may be transient or permanent, and it's a bit hard to tell
    #  In one sense, we may want to consider them disabled, but if it's only transient, that will be annoying.
    def start(self) -> None:
        self.init_providers()
        self._is_started = True
        with log_runtime("cleaning up docker containers"):
            self._cleanup(is_starting=True)

    def stop(self) -> None:
        self._cleanup(is_starting=False)
        self._is_started = False

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
        task_id_or_none = image_metadata.identifier if image_metadata.created_for == ImageCreatedFor.TASK else None
        with self._image_lock.shared_lock():  # Allow multiple images to be created in parallel.
            provider: EnvironmentProvider[ProviderMarkerTypes] = self._get_provider(
                environment_tag=config.get_environment_tag()
            )
            cached_tarball_location = cached_repo_path / "repo.tar"
            if force_tarball_refresh or not cached_tarball_location.exists():
                with log_runtime("Creating repo tarball"):
                    create_archived_repo(
                        active_repo_path, cached_tarball_location, self.concurrency_group, shutdown_event
                    )
                telemetry.emit_posthog_event(
                    telemetry.PosthogEventModel(
                        name=SculptorPosthogEvent.ENVIRONMENT_SETUP_REPO_ARCHIVE_CREATED,
                        component=ProductComponent.ENVIRONMENT_SETUP,
                        task_id=task_id_or_none,
                    )
                )
            image: Image[ProviderMarkerTypes] = provider.create_image(
                config=config,
                secrets=secrets,
                cached_repo_tarball_parent_directory=cached_repo_path,
                environment_prefix=self._environment_prefix,
                project_id=project_id,
                task_specific_context=task_specific_context,
                image_metadata=image_metadata,
                shutdown_event=shutdown_event,
                progress_handle=progress_handle,
            )
            telemetry.emit_posthog_event(
                telemetry.PosthogEventModel(
                    name=SculptorPosthogEvent.ENVIRONMENT_SETUP_IMAGE_CREATED,
                    component=ProductComponent.ENVIRONMENT_SETUP,
                    task_id=task_id_or_none,
                )
            )
            assert isinstance(image, extract_leaf_types(ImageTypes))
            # cast is safe now
            return cast(ImageTypes, image)

    def remove_stale_images(self, shutdown_event: ReadOnlyEvent | None = None) -> None:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError()
        with self._image_lock.exclusive_lock():  # Prevent simultaneous cleanup and building.
            task_metadata_by_task_id = _get_task_metadata(self.data_model_service)
            with ObservableThreadPoolExecutor(self.concurrency_group, max_workers=16) as executor:
                # Exhaust the iterator to surface any exceptions raised during cleanup.
                deque(
                    executor.map(
                        lambda environment_provider: environment_provider.cleanup_stale_resources(
                            task_metadata_by_task_id, self.settings, shutdown_event
                        ),
                        self._providers.values(),
                    ),
                    maxlen=0,
                )

    @contextmanager
    def generate_environment(
        self,
        image: Image,
        project_id: ProjectID,
        concurrency_group: ConcurrencyGroup,
        task_id: TaskID | None = None,
        name: str | None = None,
        config: EnvironmentConfig | None = None,
    ) -> Generator[Environment, None, None]:
        environment = self.create_environment(
            source=image,
            name=name,
            config=config,
            concurrency_group=concurrency_group,
            project_id=project_id,
            task_id=task_id,
        )
        try:
            yield environment
        finally:
            environment.close()

    def create_environment(
        self,
        source: Image | str,
        project_id: ProjectID,
        concurrency_group: ConcurrencyGroup,
        task_id: TaskID | None = None,
        config: EnvironmentConfig[ProviderMarkerT] | None = None,
        name: str | None = None,
        shutdown_event: ReadOnlyEvent | None = None,
        container_setup_handle: ProgressHandle | None = None,
    ) -> Environment:
        provider: EnvironmentProvider[ProviderMarkerT]
        if isinstance(source, Image):
            provider = self._get_provider(environment_tag=source.get_environment_tag())
            if config is None:
                config = provider.get_default_environment_config()
            return provider.create_environment(
                image=source,
                name=name,
                config=config,
                concurrency_group=concurrency_group,
                environment_prefix=self._environment_prefix,
                task_id=task_id,
                shutdown_event=shutdown_event,
                container_setup_handle=container_setup_handle,
            )
        else:
            environment_id: EnvironmentIDTypes
            if isinstance(config, ModalEnvironmentConfig):
                provider = self._get_provider(environment_tag=ProviderTag.MODAL)
                environment_id = ModalSandboxObjectID(source)
            elif isinstance(config, LocalDockerEnvironmentConfig):
                provider = self._get_provider(environment_tag=ProviderTag.DOCKER)
                environment_id = DockerContainerID(source)
            elif isinstance(config, LocalEnvironmentConfig) or config is None:
                provider = self._get_provider(environment_tag=ProviderTag.LOCAL)
                environment_id = LocalEnvironmentID(source)
            else:
                raise ProviderNotFoundError(f"Could not find provider for environment config of type '{type(config)}'")
            assert isinstance(provider, ProviderUnion)  # for the type checker
            assert config is not None  # for the type checker
            return provider.start_environment(
                environment_id=environment_id,
                name=name,
                config=config,
                concurrency_group=concurrency_group,
                environment_prefix=self._environment_prefix,
                project_id=project_id,
                task_id=task_id,
                shutdown_event=shutdown_event,
            )

    def _get_provider(self, environment_tag: ProviderTag) -> EnvironmentProvider:
        provider = self._providers.get(environment_tag)
        if provider is None:
            raise ProviderNotFoundError(f"Could not find provider of type '{environment_tag}'")
        return provider

    def _cleanup(self, is_starting: bool) -> None:
        if self._is_started:
            for provider in self._providers.values():
                # FIXME: temporary hack to docker kill containers at startup to get back to faster startup
                #  be sure to remove the is_killing param that was threaded all of the way through too...
                if is_starting and isinstance(provider, DockerProvider):
                    provider.kill_containers_during_startup(environment_prefix=self._environment_prefix)
                else:
                    provider.cleanup(environment_prefix=self._environment_prefix)
            if not is_starting:
                # Do this to terminate the background setup if needed.
                self.concurrency_group.shutdown()

    @property
    def _environment_prefix(self) -> str:
        if self.settings.TESTING.CONTAINER_PREFIX is not None:
            return f"{self.settings.TESTING.CONTAINER_PREFIX}-"
        return f"{get_non_testing_environment_prefix()}"

    def get_provider_statuses(self) -> dict[ProviderTag, ProviderStatusTypes]:
        """
        Get the status of each provider.

        Returns:
            dict[ProviderTag, ProviderStatus]: A mapping of provider tags to their statuses.
        """
        statuses = {}
        for provider_tag, provider in self._providers.items():
            statuses[provider_tag] = provider.get_status()
        return statuses


def _get_task_metadata(sql_service: DataModelService) -> dict[TaskID, TaskImageCleanupData]:
    with sql_service.open_transaction(RequestID()) as transaction:
        # TODO: get_all_tasks is only implemented by TaskAndDataModelTransaction
        all_tasks: tuple[Task, ...] = transaction.get_all_tasks()  # pyre-fixme[16]

        task_metadata_by_task_id: dict[TaskID, TaskImageCleanupData] = dict()
        for task in all_tasks:
            if isinstance(task.current_state, AgentTaskStateV1):
                # TODO: get_messages_for_task is only implemented by TaskAndDataModelTransaction
                saved_agent_messages = transaction.get_messages_for_task(task.object_id)  # pyre-fixme[16]
                snapshot_messages = [
                    message.message
                    for message in saved_agent_messages
                    if isinstance(message.message, AgentSnapshotRunnerMessage)
                ]
                all_image_ids = tuple(
                    message.image.image_id
                    for message in snapshot_messages
                    if isinstance(message.image, LocalDockerImage)
                )
                task_metadata = TaskImageCleanupData(
                    task_id=task.object_id,
                    # TODO: task.current_state.image can be None
                    last_image_id=task.current_state.image.image_id,  # pyre-fixme[16]
                    is_deleted=task.is_deleted or task.is_deleting,
                    is_archived=task.is_archived,
                    all_image_ids=all_image_ids,
                )
                task_metadata_by_task_id[task.object_id] = task_metadata

        return task_metadata_by_task_id
