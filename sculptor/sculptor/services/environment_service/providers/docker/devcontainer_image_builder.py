from enum import StrEnum
from pathlib import Path
from typing import Final
from typing import Literal

import json5
from loguru import logger

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.background_setup import BackgroundSetup
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.nested_evolver import assign
from imbue_core.nested_evolver import chill
from imbue_core.nested_evolver import evolver
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.progress_tracking.progress_tracking import get_unstarted
from imbue_core.progress_tracking.progress_tracking import start_finish_context
from imbue_core.sculptor import telemetry
from imbue_core.sculptor.telemetry import PosthogEventModel
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry import emit_posthog_event
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.sculptor.telemetry_utils import with_consent
from imbue_core.thread_utils import ObservableThread
from sculptor.interfaces.environments.base import LocalDevcontainerImageConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.services.environment_service.api import DEFAULT_TASK_SPECIFIC_CONTEXT
from sculptor.services.environment_service.api import TaskSpecificContext
from sculptor.services.environment_service.environments.image_tags import ImageCreatedFor
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.image_tags import get_tagged_reference
from sculptor.services.environment_service.providers.docker.image_fetch import ImagePurpose
from sculptor.services.environment_service.providers.docker.image_fetch import fetch_image_from_cdn
from sculptor.services.environment_service.providers.docker.image_fetch import get_cdn_url_for_image
from sculptor.services.environment_service.providers.docker.image_utils import build_docker_image
from sculptor.services.environment_service.providers.docker.image_utils import get_platform_architecture
from sculptor.services.environment_service.providers.docker.image_utils import run_initialize_command
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP,
)
from sculptor.utils.shutdown import globally_cancellable
from sculptor.utils.timeout import IntervalTimer
from sculptor.utils.timeout import TimingAttributes
from sculptor.utils.timeout import log_runtime
from sculptor.utils.timeout import log_runtime_decorator

_IMBUE_ADDONS_DOCKERFILE_PATH: Final[Path] = Path(__file__).parent / "imbue_addons" / "Dockerfile.imbue_addons"


class DevcontainerBuildPath(StrEnum):
    """Control flow paths for devcontainer image building."""

    DOCKERFILE_NAME = "dockerfile_name"
    IMAGE_NAME = "image_name"
    FALLBACK_TO_DEFAULT = "fallback_to_default"


class DevcontainerBuildEventData(PosthogEventPayload):
    """PostHog event data for devcontainer build operations."""

    control_flow_path: str = with_consent(ConsentLevel.PRODUCT_ANALYTICS)
    devcontainer_json_path: str = with_consent(ConsentLevel.PRODUCT_ANALYTICS)
    tag: str = with_consent(ConsentLevel.PRODUCT_ANALYTICS)
    fallback_reason: str | None = with_consent(ConsentLevel.PRODUCT_ANALYTICS)


class DevcontainerError(ValueError):
    """Error raised when there's an issue with the DevcontainerError."""

    pass


class DefaultDevcontainerFallbackPayload(PosthogEventPayload):
    """PostHog event data for default devcontainer fallback."""

    reason: str = with_consent(ConsentLevel.ERROR_REPORTING)


def get_default_devcontainer_json_path() -> Path:
    result = Path(__file__).parent / "default_devcontainer" / "devcontainer.json"
    assert result.exists(), f"Default devcontainer.json not found at {result}"
    return result


def get_default_devcontainer_image_reference() -> str:
    """Parse and return the image reference from the default devcontainer.json."""
    default_devcontainer_path = get_default_devcontainer_json_path()
    json_contents = json5.loads(default_devcontainer_path.read_text("utf-8"))
    image_reference = json_contents.get("image")
    assert image_reference, f"No 'image' field found in default devcontainer.json at {default_devcontainer_path}"
    return image_reference


def get_default_devcontainer_cdn_url(architecture: Literal["amd64", "arm64"] | None = None) -> str:
    """Get the CloudFront CDN URL for downloading the default devcontainer image tarball.

    This returns the URL from which the default devcontainer Docker image can be downloaded
    as a tarball from the CloudFront CDN.

    Args:
        architecture: The platform architecture (e.g., "amd64", "arm64").
                     If None, uses the current platform's architecture.

    Returns:
        The CloudFront CDN URL for the default devcontainer image tarball.

    Example:
        >>> get_default_devcontainer_cdn_url("amd64")
        'https://d2rpy6crlmjake.cloudfront.net/images/ghcr.io-imbue-ai-sculptor_default_devcontainer-..._amd64.tar'
    """
    if architecture is None:
        architecture = get_platform_architecture()

    image_reference = get_default_devcontainer_image_reference()
    return get_cdn_url_for_image(image_reference, architecture)


FETCH_DEFAULT_DEVCONTAINER_PROGRESS_HANDLE = get_unstarted(ProgressHandle)


@log_runtime_decorator()
@globally_cancellable
def docker_pull_default_devcontainer(shutdown_event: ReadOnlyEvent, concurrency_group: ConcurrencyGroup) -> None:
    """Download and ensure default devcontainer image is available.

    This function downloads the image tarball to cache, then ensures it's loaded into Docker.
    """
    with start_finish_context(FETCH_DEFAULT_DEVCONTAINER_PROGRESS_HANDLE) as progress_handle:
        image_reference = get_default_devcontainer_image_reference()
        # Try to fetch the devcontainer image from CDN first
        logger.info("Starting download and load of default devcontainer image: {}", image_reference)
        with start_finish_context(
            progress_handle.track_subtask("Downloading default devcontainer image")
        ) as default_devcontainer_download_handle:
            fetch_image_from_cdn(
                image_reference,
                ImagePurpose.DEFAULT_DEVCONTAINER,
                concurrency_group,
                shutdown_event,
                default_devcontainer_download_handle,
            )


PULL_DEFAULT_DEVCONTAINER_BACKGROUND_SETUP: Final[BackgroundSetup] = BackgroundSetup(
    "DockerPullDefaultDevcontainerBackgroundSetup",
    docker_pull_default_devcontainer,
)


def start_control_plane_background_setup(
    thread_suffix: str, concurrency_group: ConcurrencyGroup
) -> list[ObservableThread]:
    """Starting control plane background setup tasks.  Does not block, just starts background threads."""
    logger.info("Starting background setup tasks for devcontainers.")
    return [
        PULL_DEFAULT_DEVCONTAINER_BACKGROUND_SETUP.start_run_in_background(
            thread_name=f"DockerPullDefaultDevcontainerBackgroundSetup_{thread_suffix}",
            concurrency_group=concurrency_group,
        ),
        CONTROL_PLANE_FETCH_BACKGROUND_SETUP.start_run_in_background(
            thread_name=f"ControlPlaneFetchBackgroundSetup_{thread_suffix}", concurrency_group=concurrency_group
        ),
    ]


def get_devcontainer_json_path_from_repo_or_default(repo_path: Path) -> Path:
    """Find the user's devcontainer.json file, or use our default one so they don't have to specify it."""
    paths = [
        ".devcontainer/devcontainer.json",
        "devcontainer.json",
    ]
    for p in paths:
        if (repo_path / p).exists():
            logger.info("Found devcontainer.json at {}", p)
            return repo_path / p
    result = get_default_devcontainer_json_path()
    logger.info("No devcontainer.json found, using the Sculptor default at {}", result)
    return result


def _validate_forward_ports(
    forward_ports_raw: list | None,
    task_specific_context: TaskSpecificContext,
) -> list[int]:
    """Validate and filter forwardPorts from devcontainer.json.

    Args:
        forward_ports_raw: Raw forwardPorts value from devcontainer.json
        task_specific_context: Context for emitting warnings and notifications

    Returns:
        List of validated port numbers (integers in range 1-65535)

    Raises:
        DevcontainerError: If forwardPorts is not a list
    """
    if not forward_ports_raw:
        return []

    logger.info("Found forwardPorts in devcontainer.json: {}", forward_ports_raw)

    # Validate ports at parse-time with lenient error handling (skip invalid ports with warnings)
    if not isinstance(forward_ports_raw, list):
        raise DevcontainerError(
            f"forwardPorts must be a list, got {type(forward_ports_raw).__name__}: {forward_ports_raw!r}"
        )

    validation_warnings = []
    forward_ports: list[int] = []
    seen_ports: set[int] = set()

    for i, port in enumerate(forward_ports_raw):
        # Check if port is an integer
        if not isinstance(port, int):
            validation_warnings.append(
                f"Skipping invalid forwardPorts[{i}] ({port!r}): Port must be an integer, got {type(port).__name__}"
            )
            continue

        # Check if port is in valid range (1-65535)
        if port < 1 or port > 65535:
            validation_warnings.append(
                f"Skipping invalid forwardPorts[{i}] ({port!r}): Port number {port} is outside the valid range [1-65535]"
            )
            continue

        # Check for duplicate ports
        if port in seen_ports:
            validation_warnings.append(
                f"Skipping duplicate forwardPorts[{i}] ({port}): Port {port} was already specified earlier"
            )
            continue

        seen_ports.add(port)
        forward_ports.append(port)

    # Log and emit warnings
    for warning in validation_warnings:
        logger.info("Port validation warning: {}", warning)
        task_specific_context.emit_warning(f"Port validation: {warning}")

    # Emit notification if any ports failed validation
    if validation_warnings:
        if len(validation_warnings) == 1:
            # For a single failure, include the specific error
            notification_message = f"Port validation failed: {validation_warnings[0]}"
        else:
            # For multiple failures, provide a summary
            notification_message = (
                f"Port validation failed for {len(validation_warnings)} port(s). Check the logs for details."
            )
        task_specific_context.emit_warning(notification_message)

    if forward_ports:
        logger.info("Validated forwardPorts: {}", forward_ports)

    return forward_ports


class DockerBuildCacheStatusPayload(PosthogEventPayload):
    """PostHog event data for docker build cache status."""

    cache_missed: bool = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Whether the docker build cache was missed"
    )


def build_local_devcontainer_image(
    config: LocalDevcontainerImageConfig,
    cached_repo_tarball_parent_directory: Path,
    project_id: ProjectID,
    image_repo: str,
    concurrency_group: ConcurrencyGroup,
    image_metadata: ImageMetadataV1,
    secrets: dict[str, str] | None = None,
    task_specific_context: TaskSpecificContext = DEFAULT_TASK_SPECIFIC_CONTEXT,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> LocalDockerImage:
    """Build a Docker image from a devcontainer.json configuration."""
    with log_runtime("build_local_devcontainer_image") as timing_attributes_for_posthog:
        return _build_local_devcontainer_image(
            timing_attributes_for_posthog,
            config,
            cached_repo_tarball_parent_directory,
            project_id,
            image_repo,
            concurrency_group,
            image_metadata,
            secrets,
            task_specific_context,
            shutdown_event,
            progress_handle,
        )


class _CacheMissSignal:
    def __init__(self) -> None:
        self._detected = False

    def mark_miss(self) -> None:
        self._detected = True

    def was_miss_detected(self) -> bool:
        return self._detected


def _build_local_devcontainer_image(
    timing_attributes_for_posthog: TimingAttributes,
    config: LocalDevcontainerImageConfig,
    cached_repo_tarball_parent_directory: Path,
    project_id: ProjectID,
    image_repo: str,
    concurrency_group: ConcurrencyGroup,
    image_metadata: ImageMetadataV1,
    secrets: dict[str, str] | None = None,
    task_specific_context: TaskSpecificContext = DEFAULT_TASK_SPECIFIC_CONTEXT,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> LocalDockerImage:
    logger.info(
        "Building local devcontainer image from {} with image_name {}", config.devcontainer_json_path, image_repo
    )
    interval_timer = IntervalTimer()

    # Start control plane volume setup in background thread
    control_plane_thread = CONTROL_PLANE_FETCH_BACKGROUND_SETUP.start_run_in_background(
        thread_name="ControlPlaneFetchJoinedThread", concurrency_group=concurrency_group
    )

    devcontainer_path = Path(config.devcontainer_json_path)
    if not devcontainer_path.exists():
        raise FileNotFoundError(f"devcontainer.json not found at {devcontainer_path}")

    # Initialize variables so they're always defined
    forward_ports: list[int] = []
    container_user: str | None = None
    on_create_command: str | list[str] | dict[str, str | list[str]] | None = None
    update_content_command: str | list[str] | dict[str, str | list[str]] | None = None

    task_id_or_none = image_metadata.identifier if image_metadata.created_for == ImageCreatedFor.TASK else None

    try:
        json_contents = json5.loads(devcontainer_path.read_text("utf-8"))
        # TODO: Consider somehow invoking the reference implementation via:
        # devcontainer build --workspace-folder devcontainer_path.parent.
        # For now, we are just supporting a very limited amount of the devcontainer.json format.

        # Extract and validate forwardPorts from devcontainer.json
        forward_ports_raw = json_contents.get("forwardPorts", [])
        forward_ports = _validate_forward_ports(forward_ports_raw, task_specific_context)

        # Parse the containerUser field if present
        # See: https://containers.dev/implementors/spec/#users
        container_user = json_contents.get("containerUser")

        # Pull out the `initializeCommand` payload if present
        # See: https://containers.dev/implementors/json_reference/#lifecycle-scripts
        initialize_command = json_contents.get("initializeCommand")
        timing_attributes_for_posthog.set_attribute(
            "up_to_initialize_command_seconds", interval_timer.get_and_restart()
        )
        if initialize_command:
            timing_attributes_for_posthog.set_attribute("has_initialize_command", True)
            run_initialize_command(
                initialize_command=initialize_command,
                concurrency_group=concurrency_group,
                devcontainer_path=devcontainer_path,
                shutdown_event=shutdown_event,
            )
        timing_attributes_for_posthog.set_attribute("run_initialize_command_seconds", interval_timer.get_and_restart())

        # Pull out the `onCreateCommand` payload if present
        # See: https://containers.dev/implementors/json_reference/#lifecycle-scripts
        # This will be stored in the image and executed when the container is first created
        on_create_command = json_contents.get("onCreateCommand")

        # Pull out the `updateContentCommand` payload if present
        # See: https://containers.dev/implementors/json_reference/#lifecycle-scripts
        # This will be stored in the image and executed when content is updated
        update_content_command = json_contents.get("updateContentCommand")

        # We support two different ways to build a devcontainer image:
        # 1. From a Dockerfile: devcontainer.json's build.dockerfile field
        # 2. From an image: devcontainer.json's image field
        # Exactly one of these must be specified, and we check this.
        devcontainer_dockerfile_name = json_contents.get("build", {}).get("dockerfile")
        devcontainer_image_reference = json_contents.get("image")
        if not devcontainer_dockerfile_name and not devcontainer_image_reference:
            raise DevcontainerError(
                f"devcontainer.json must contain a 'build.dockerfile' field or an 'image' field, {json_contents=}"
            )
        elif devcontainer_dockerfile_name and devcontainer_image_reference:
            raise DevcontainerError(
                f"devcontainer.json cannot contain both a 'build.dockerfile' field and an 'image' field, {json_contents=}"
            )
        # Initialize PostHog event data - control_flow_path and fallback_reason will be set in the branches
        # TODO: Consider deleting these variables in favor of the annotations on the timing_attributes_for_posthog.
        control_flow_path: DevcontainerBuildPath
        fallback_reason: str | None = None

        if devcontainer_dockerfile_name:
            timing_attributes_for_posthog.set_attribute("has_dockerfile_name", True)
            build_context = json_contents.get("build", {}).get("context", ".")
            build_context_path = devcontainer_path.parent / build_context
            # Build from a Dockerfile
            dockerfile_path = devcontainer_path.parent / devcontainer_dockerfile_name
            if not dockerfile_path.exists():
                raise DevcontainerError(f"Dockerfile not found at {dockerfile_path}")

            image_metadata_evolver = evolver(image_metadata)
            assign(image_metadata_evolver.is_user_image, lambda: True)
            user_devcontainer_base_image_metadata = chill(image_metadata_evolver)

            logger.info(
                "Building user image from Dockerfile at {}, with build context at {}",
                dockerfile_path,
                build_context_path,
            )
            timing_attributes_for_posthog.set_attribute(
                "up_to_build_user_docker_image_seconds", interval_timer.get_and_restart()
            )
            cache_miss_signal = _CacheMissSignal()
            user_image: LocalDockerImage = build_docker_image(
                dockerfile_path,
                project_id=project_id,
                concurrency_group=concurrency_group,
                image_repo=image_repo,
                image_metadata=user_devcontainer_base_image_metadata,
                build_path=build_context_path,
                secrets=secrets,
                on_cache_miss=cache_miss_signal.mark_miss,
                shutdown_event=shutdown_event,
                progress_handle=progress_handle,
            )
            timing_attributes_for_posthog.set_attribute(
                "build_user_docker_image_seconds", interval_timer.get_and_restart()
            )
            timing_attributes_for_posthog.set_attribute(
                "build_user_docker_image_cache_miss", cache_miss_signal.was_miss_detected()
            )

            telemetry.emit_posthog_event(
                PosthogEventModel(
                    name=SculptorPosthogEvent.ENVIRONMENT_SETUP_LOCAL_DOCKERFILE_BUILT,
                    component=ProductComponent.ENVIRONMENT_SETUP,
                    task_id=task_id_or_none,
                    payload=DockerBuildCacheStatusPayload(
                        cache_missed=cache_miss_signal.was_miss_detected(),
                    ),
                )
            )
            logger.info(
                "Built user image tag with tag={}, id={}", user_devcontainer_base_image_metadata, user_image.image_id
            )
            control_flow_path = DevcontainerBuildPath.DOCKERFILE_NAME
            user_devcontainer_base_image_name_and_tag = get_tagged_reference(
                image_repo, user_devcontainer_base_image_metadata
            )
        else:
            timing_attributes_for_posthog.set_attribute("has_image_name", True)
            # Use the pre-existing image.
            # The great thing about this path is that it skips an entire docker build step.
            assert devcontainer_image_reference is not None
            user_devcontainer_base_image_name_and_tag = devcontainer_image_reference
            control_flow_path = DevcontainerBuildPath.IMAGE_NAME
    except Exception as e:
        timing_attributes_for_posthog.set_attribute("build_user_docker_image_failed", True)
        # TODO: Somehow get a message into Sculptor's message queue with the logs from the failure.
        log_exception(e, "Failed to build user Dockerfile, falling back to default devcontainer image")
        fallback_reason = f"Dockerfile build failed: {type(e).__name__}"

        task_specific_context.emit_warning(
            "Failed to build devcontainer image from Dockerfile, falling back to default devcontainer image. Check the logs tab for additional details."
        )
        telemetry.emit_posthog_event(
            PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_FELL_BACK_TO_DEFAULT_DEVCONTAINER,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=task_id_or_none,
                payload=DefaultDevcontainerFallbackPayload(reason=fallback_reason),
            )
        )

        # Fall back to using the default devcontainer image
        user_devcontainer_base_image_name_and_tag = get_default_devcontainer_image_reference()
        control_flow_path = DevcontainerBuildPath.FALLBACK_TO_DEFAULT

    logger.info("Building Imbue's wrapper image around user_image_tag={}", user_devcontainer_base_image_name_and_tag)

    try:
        timing_attributes_for_posthog.set_attribute(
            "up_to_build_imbue_wrapper_image_seconds", interval_timer.get_and_restart()
        )
        cache_miss_signal = _CacheMissSignal()
        wrapped_image: LocalDockerImage = build_docker_image(
            _IMBUE_ADDONS_DOCKERFILE_PATH,
            project_id=project_id,
            concurrency_group=concurrency_group,
            cached_repo_tarball_parent_directory=cached_repo_tarball_parent_directory,
            image_repo=image_repo,
            image_metadata=image_metadata,
            secrets=secrets,
            base_image_tag=user_devcontainer_base_image_name_and_tag,
            forward_ports=forward_ports,
            container_user=container_user,
            on_create_command=on_create_command,
            update_content_command=update_content_command,
            on_cache_miss=cache_miss_signal.mark_miss,
            shutdown_event=shutdown_event,
        )
        timing_attributes_for_posthog.set_attribute(
            "build_imbue_wrapper_image_seconds", interval_timer.get_and_restart()
        )
        timing_attributes_for_posthog.set_attribute(
            "wrapper_dockerfile_cache_miss", cache_miss_signal.was_miss_detected()
        )

        telemetry.emit_posthog_event(
            PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_WRAPPER_DOCKERFILE_BUILT,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=task_id_or_none,
                payload=DockerBuildCacheStatusPayload(
                    cache_missed=cache_miss_signal.was_miss_detected(),
                ),
            )
        )
        logger.info("Built Imbue's wrapper image with tag={}", image_repo)
    except Exception as e:
        timing_attributes_for_posthog.set_attribute("build_imbue_wrapper_image_failed_first_time", True)
        log_exception(
            e,
            "Failed to build Imbue's wrapper around user_image_tag={user_image_tag}, falling back to default devcontainer image.",
            user_image_tag=user_devcontainer_base_image_name_and_tag,
        )

        task_specific_context.emit_warning(
            "Failed to build Imbue's wrapper around your devcontainer image, falling back to default devcontainer image. Check the logs tab for additional details."
        )

        telemetry.emit_posthog_event(
            PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_FELL_BACK_TO_DEFAULT_DEVCONTAINER,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=task_id_or_none,
                payload=DefaultDevcontainerFallbackPayload(reason=f"Imbue wrapper build failed: {repr(e)[:200]}"),
            )
        )
        # The reason this is almost repeated is to handle the case where devcontainer.json specifies an image,
        # but the image is not valid.  In that case, there's no build step for the user image, but the
        # build_docker_image above for _IMBUE_ADDONS_DOCKERFILE_PATH will fail, and we fall back to using
        # the default devcontainer image.
        timing_attributes_for_posthog.set_attribute(
            "up_to_build_second_imbue_wrapper_image_seconds", interval_timer.get_and_restart()
        )

        # Create a new detector for the fallback wrapper build
        cache_miss_signal = _CacheMissSignal()

        wrapped_image: LocalDockerImage = build_docker_image(
            _IMBUE_ADDONS_DOCKERFILE_PATH,
            project_id=project_id,
            concurrency_group=concurrency_group,
            cached_repo_tarball_parent_directory=cached_repo_tarball_parent_directory,
            image_repo=image_repo,
            image_metadata=image_metadata,
            secrets=secrets,
            base_image_tag=get_default_devcontainer_image_reference(),
            forward_ports=forward_ports,
            container_user=container_user,
            update_content_command=update_content_command,
            on_cache_miss=cache_miss_signal.mark_miss,
            shutdown_event=shutdown_event,
        )
        timing_attributes_for_posthog.set_attribute(
            "build_second_imbue_wrapper_image_seconds", interval_timer.get_and_restart()
        )
        timing_attributes_for_posthog.set_attribute(
            "wrapper_dockerfile_cache_miss", cache_miss_signal.was_miss_detected()
        )

        telemetry.emit_posthog_event(
            PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_WRAPPER_DOCKERFILE_BUILT,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=task_id_or_none,
                payload=DockerBuildCacheStatusPayload(
                    cache_missed=cache_miss_signal.was_miss_detected(),
                ),
            )
        )
        logger.info("As a fallback, built Imbue's wrapper image with tag={}", image_repo)
        control_flow_path = DevcontainerBuildPath.FALLBACK_TO_DEFAULT

    timing_attributes_for_posthog.set_attribute("control_flow_path", control_flow_path)

    # Emit PostHog telemetry event
    try:
        event_data = DevcontainerBuildEventData(
            control_flow_path=control_flow_path,
            devcontainer_json_path=str(devcontainer_path),
            tag=get_tagged_reference(image_repo, image_metadata),
            fallback_reason=fallback_reason,
        )
        posthog_event = PosthogEventModel[
            DevcontainerBuildEventData
        ](
            name=SculptorPosthogEvent.TASK_START_MESSAGE,  # Using existing event - could add DEVCONTAINER_BUILD if needed
            component=ProductComponent.TASK,
            payload=event_data,
        )
        emit_posthog_event(posthog_event)
    except Exception as e:
        logger.info("Failed to emit devcontainer build telemetry: {}", e)

    telemetry.emit_posthog_event(
        PosthogEventModel(
            name=SculptorPosthogEvent.ENVIRONMENT_SETUP_WAITING_FOR_CONTROL_PLANE_SETUP,
            component=ProductComponent.ENVIRONMENT_SETUP,
            task_id=task_id_or_none,
        )
    )
    # Wait for control plane thread to complete and raise any errors
    timing_attributes_for_posthog.set_attribute(
        "up_to_join_control_plane_thread_seconds", interval_timer.get_and_restart()
    )
    control_plane_thread.join()  # This will raise any exception from the background thread
    timing_attributes_for_posthog.set_attribute("join_control_plane_thread_seconds", interval_timer.get_and_restart())

    # The container_user has been baked into the image at /imbue_addons/container_user.txt
    # during the docker build process, so we don't need to return it separately.
    return wrapped_image
