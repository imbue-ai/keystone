"""Downloads the Imbue control plane from ghcr.io and makes it available as Docker volumes.

This is very much an optimization to prevent paying a "layer tax" for the control plane.

The idea here is that for content that doesn't change often, we can just fetch it from the image once,
copy it into a volume, and attach it to containers as a read-only volume, rather than a layer.


The alternative would be something like this inside Dockerfile.imbue_addons, which would
copy the control plane directly into the image:
```
ARG SCULPTORBASE_NIX=ghcr.io/imbue-ai/sculptorbase_nix:...
FROM ${SCULPTORBASE_NIX} AS sculptorbase_nix
FROM ubuntu:24.04
COPY --from=sculptorbase_nix /imbue /imbue
```

But the problems with layers are:
1. Changing them invalidates all subsequent layers, and you have to decide an ordering.
2. They're slow to build.

Doing it the "volume mounted" way enables:
* Shorter image build and export times, because the control plane isn't actually a layer in the user's image.
* Theoretically, we can "swap out" the control plane for a newer version without rebuilding the image, but just attaching a different volume.
"""

import json
import os
from enum import Enum
from enum import auto
from importlib.resources.abc import Traversable
from typing import Final
from typing import Literal
from typing import Self

from loguru import logger

from imbue_core.background_setup import BackgroundSetup
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.itertools import only
from imbue_core.processes.local_process import run_blocking
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.progress_tracking.progress_tracking import get_unstarted
from imbue_core.progress_tracking.progress_tracking import start_finish_context
from imbue_core.pydantic_serialization import FrozenModel
from imbue_core.pydantic_serialization import SerializableModel
from imbue_core.subprocess_utils import ProcessError
from sculptor.primitives.constants import CONTROL_PLANE_LOCAL_TAG_PATH
from sculptor.primitives.constants import CONTROL_PLANE_MANIFEST_PATH
from sculptor.primitives.constants import CONTROL_PLANE_TAG_PATH
from sculptor.services.environment_service.providers.docker.image_fetch import ImagePurpose
from sculptor.services.environment_service.providers.docker.image_fetch import fetch_image_from_cdn
from sculptor.services.environment_service.providers.docker.image_fetch import get_cdn_url_for_image
from sculptor.services.environment_service.providers.docker.image_utils import get_platform_architecture
from sculptor.utils.build import is_dev_build
from sculptor.utils.shutdown import globally_cancellable
from sculptor.utils.timeout import log_runtime_decorator

_CDN_PREFIX: Final[str] = "https://d2rpy6crlmjake.cloudfront.net/images/"


def get_control_plane_volume_docker_args() -> tuple[str, ...]:
    """Get the Docker volume mount arguments for control plane volumes.

    Args:
        architecture: The platform architecture (e.g., "amd64", "arm64").
                     If None, uses the current platform's architecture.

    Returns:
        Tuple of Docker CLI arguments for mounting the control plane volume.
    """
    volume_name = ControlPlaneImageNameProvider().get_control_plane_volume_name()
    # Mount the volume as read-only to safely make the same volume available to multiple images.
    return ("-v", f"{volume_name}:/imbue:ro")


class ControlPlaneFetchError(Exception):
    pass


class ControlPlaneRunMode(Enum):
    # Run a control plane version that has already been built locally, e.g. for running tests in CI.
    LOCALLY_BUILT = auto()

    # Run a control plane version that has been built and published, e.g. as in production.
    TAGGED_RELEASE = auto()


class ControlPlaneImageNameProvider(SerializableModel):
    """Provides the control plane image name based on the current run mode.

    Useful to parametrize some of the constants for testing.
    """

    control_plane_tag_path: Traversable = CONTROL_PLANE_TAG_PATH
    control_plane_local_tag_path: Traversable = CONTROL_PLANE_LOCAL_TAG_PATH
    control_plane_manifest_path: Traversable = CONTROL_PLANE_MANIFEST_PATH
    predetermined_run_mode: ControlPlaneRunMode | None = None
    predetermined_platform_architecture: Literal["amd64", "arm64"] | None = None

    def determine_current_run_mode(self) -> "ControlPlaneRunMode":
        """
        As of this writing, we still pin the control plane version to the tag specified in CONTROL_PLANE_TAG_PATH, and this file
        is tracked by git. By default, we want to use the tag specified here.

        However, if the control plane has been built locally, we write its tag to CONTROL_PLANE_LOCAL_TAG_PATH, which is git-ignored
        so will not get accidentally committed.  We use this tag if the file exists.
        """
        if self.predetermined_run_mode is not None:
            return self.predetermined_run_mode
        if self.control_plane_local_tag_path.is_file():
            return ControlPlaneRunMode.LOCALLY_BUILT
        else:
            return ControlPlaneRunMode.TAGGED_RELEASE

    def _get_control_plane_git_commit_hash(self) -> str:
        run_mode = self.determine_current_run_mode()
        tag_path_to_use = self.control_plane_tag_path
        if run_mode == ControlPlaneRunMode.LOCALLY_BUILT:
            tag_path_to_use = self.control_plane_local_tag_path

        return tag_path_to_use.read_text().strip()

    def _get_control_plane_sha_from_manifest_file(self) -> str:
        manifest_data = json.loads(CONTROL_PLANE_MANIFEST_PATH.read_text().strip())["manifests"]
        our_platform = self.predetermined_platform_architecture or get_platform_architecture()
        control_plane_entry = only(x for x in manifest_data if x["platform"]["architecture"] == our_platform)
        control_plane_sha: Final[str] = control_plane_entry["digest"].split("sha256:")[-1]

        return control_plane_sha

    def _get_control_plane_sha_from_local_docker_image(self, image_name: str) -> str:
        result = run_blocking(
            command=[
                "docker",
                "image",
                "inspect",
                "--format",
                "{{.Id}}",
                image_name,
            ]
        )
        image_id = result.stdout.strip()
        if image_id.startswith("sha256:"):
            return image_id.split("sha256:")[-1]
        else:
            return "unknown_sha256"

    def _get_control_plane_image_sha256(self) -> str:
        run_mode = self.determine_current_run_mode()
        if run_mode == ControlPlaneRunMode.LOCALLY_BUILT:
            # We use this to name the volume; if the image gets rebuilt, we want that to result in a different sha256 and thus a different volume.
            local_image_name = self.determine_control_plane_image_name()
            return self._get_control_plane_sha_from_local_docker_image(local_image_name)
        else:
            # Use the pinned sha256
            return self._get_control_plane_sha_from_manifest_file()

    def determine_control_plane_image_name(self) -> str:
        """Return the docker image name to use for the control plane."""
        # Allow overriding the image name for testing purposes.
        image_name_set_in_env = os.environ.get("SCULPTOR_CONTROL_PLANE_IMAGE")
        if image_name_set_in_env:
            return image_name_set_in_env

        run_mode = self.determine_current_run_mode()
        if run_mode == ControlPlaneRunMode.LOCALLY_BUILT:
            commit_hash = self._get_control_plane_git_commit_hash()
            return f"sculptorbase_nix:local_build_{commit_hash}"
        else:
            commit_hash = self._get_control_plane_git_commit_hash()

            # Pinning to a SHA lets Docker avoid a network call to check with ghcr.io if the tag has been updated.
            # See: https://github.com/orgs/imbue-ai/packages/container/package/sculptorbase_nix.
            control_plane_sha = self._get_control_plane_image_sha256()
            return f"ghcr.io/imbue-ai/sculptorbase_nix:{commit_hash}@sha256:{control_plane_sha}"

    def get_control_plane_volume_name(self) -> str:
        # There is a dev mode where the user can just specify a pre-created control plane volume.
        # We will eventually deprecate this (since we are move to a world where the image is always rebuilt locally), but for now we support it.
        volume_name_set_in_env = os.environ.get("SCULPTOR_CONTROL_PLANE_VOLUME")
        if volume_name_set_in_env:
            return volume_name_set_in_env

        # We keep each version of the control plane in its own volume.
        # It's nice that the same volume can be shared between images; these must be read-only, though.
        commit_hash = self._get_control_plane_git_commit_hash()
        control_plane_sha = self._get_control_plane_image_sha256()

        # Our cleanup logic is slightly different for dev builds vs. tagged releases.
        # For dev builds, we only want to keep one volume around for each commit hash.
        return ControlPlaneVolumeInformation(
            is_dev_build=is_dev_build(), commit_hash=commit_hash, sha256=control_plane_sha
        ).as_volume_name()

    def get_control_plane_cdn_url(self) -> str:
        our_platform = self.predetermined_platform_architecture or get_platform_architecture()
        assert our_platform is not None
        image_name = self.determine_control_plane_image_name()
        return get_cdn_url_for_image(image_name, our_platform)


_NORMAL_PREFIX: Final[str] = "imbue_control_plane"
_DEV_PREFIX: Final[str] = "imbue_dev_control_plane"


class ControlPlaneVolumeInformation(FrozenModel):
    is_dev_build: bool
    commit_hash: str
    sha256: str

    @classmethod
    def from_volume_name(cls, volume_name: str) -> Self | None:
        if volume_name.startswith(_NORMAL_PREFIX):
            is_dev_build = False
        elif volume_name.startswith(_DEV_PREFIX):
            is_dev_build = True
        else:
            return None
        rest = volume_name.removeprefix(f"{_DEV_PREFIX}_").removeprefix(f"{_NORMAL_PREFIX}_")
        try:
            commit_hash, sha256 = rest.rsplit("_", 1)
        except ValueError:
            return None
        return cls(is_dev_build=is_dev_build, commit_hash=commit_hash, sha256=sha256)

    def as_volume_name(self) -> str:
        prefix = _DEV_PREFIX if self.is_dev_build else _NORMAL_PREFIX
        return f"{prefix}_{self.commit_hash}_{self.sha256}"


_VOLUME_READY_OUTPUT = "VOLUME_READY"
_VOLUME_NOT_READY_OUTPUT = "VOLUME_NOT_READY"
VOLUME_READY_SENTINEL = "VOLUME_READY.TXT"


def _is_volume_ready(volume_name: str, concurrency_group: ConcurrencyGroup) -> bool:
    """Check if the control plane volume exists and is ready (contains the sentinel file).

    This check is performed before downloading the Docker image to avoid re-downloading
    when the volume already exists but the image has been cleaned up.

    Returns:
        True if the volume exists and contains VOLUME_READY.TXT.
        False if the sentinel file doesn't exist.

    Raises:
        ProcessError: If Docker fails (e.g., daemon not running, alpine pull failed).
    """
    # We have to use alpine here b/c we don't know if the control plane (or any other) image exists.
    # It's pretty small, so pulling shouldn't be an issue.
    #
    # We use shell output to distinguish between "file exists" vs "file doesn't exist" vs "docker error".
    # The shell command always exits 0, but outputs different strings based on the file check.
    # This way, a ProcessError always indicates a Docker issue, not just a missing file.
    check_command = f"test -f /imbue_volume/{VOLUME_READY_SENTINEL} && echo {_VOLUME_READY_OUTPUT} || echo {_VOLUME_NOT_READY_OUTPUT}"
    result = concurrency_group.run_process_to_completion(
        command=[
            "docker",
            "run",
            "--rm",
            "-v",
            f"{volume_name}:/imbue_volume:ro",
            "alpine:latest",
            "sh",
            "-c",
            check_command,
        ],
    )
    return _VOLUME_READY_OUTPUT in result.stdout


FETCH_CONTROL_PLANE_PROGRESS_HANDLE = get_unstarted(ProgressHandle)


@log_runtime_decorator()
@globally_cancellable
def _fetch_control_plane_volume(shutdown_event: ReadOnlyEvent, concurrency_group: ConcurrencyGroup) -> None:
    """Fetches /imbue from the control plane image into a single volume.

    There's a race condition here.
    To summarize:
    * Two processes can start populating the volume at the same time, and copy all the same files into it.
    * But once one of them writes the VOLUME_READY.TXT file, all the files should have been written at least once.
    * However, the second process can still be copying files into the volume, and would "overwrite" with the same contents.
    * I talked this through with ChatGPT and convinced myself this is OK: https://chatgpt.com/share/68b090b9-b354-8004-a487-8a6f003d6dee
    * I've looked at Docker's volume auto-initialization and it doesn't handle the race well: https://imbue-ai.slack.com/archives/C06MFB87T4P/p1757356166569579?thread_ts=1757349096.985299&cid=C06MFB87T4P
    """
    with start_finish_context(FETCH_CONTROL_PLANE_PROGRESS_HANDLE) as progress_handle:
        name_provider = ControlPlaneImageNameProvider()
        control_plane_volume_name = name_provider.get_control_plane_volume_name()

        # Check if the volume already exists and is ready before downloading the image.
        # This avoids re-downloading the control plane image when the volume is already populated
        # but the image has been cleaned up (which we now do after copying files to the volume).
        try:
            if _is_volume_ready(control_plane_volume_name, concurrency_group):
                logger.info(
                    "Control plane volume {} already exists and is ready, skipping image fetch.",
                    control_plane_volume_name,
                )
                return
        except ProcessError as e:
            # Docker failed during the volume check (e.g., daemon not running, alpine pull failed).
            # Log and proceed with normal flow - if Docker is broken, we'll get a clearer error later.
            logger.info(
                "Failed to check if volume {} is ready, proceeding with image fetch: {}", control_plane_volume_name, e
            )

        # Use the current platform's architecture for fetching
        # Try to fetch the control plane image from CDN first.
        image_to_use = name_provider.determine_control_plane_image_name()
        if name_provider.determine_current_run_mode() == ControlPlaneRunMode.TAGGED_RELEASE:
            with start_finish_context(
                progress_handle.track_subtask("Fetching control plane volume")
            ) as control_plane_fetch_handle:
                fetch_image_from_cdn(
                    image_to_use,
                    ImagePurpose.CONTROL_PLANE,
                    concurrency_group,
                    shutdown_event,
                    control_plane_fetch_handle,
                )

        logger.info("Creating control plane volume {}.", control_plane_volume_name)

        command = f"""
        set -e
        if [ -f /imbue_volume/{VOLUME_READY_SENTINEL} ]; then
            echo "_fetch_control_plane_volume: {control_plane_volume_name} already exists and is ready."
        else
            echo "_fetch_control_plane_volume: Initializing {control_plane_volume_name} volume, copying from /imbue to /imbue_volume..."

            # Copy /imbue contents to /imbue_volume/
            # /imbue/. means everything in the directory, including the ".venv" directory, which wouldn't match a * glob.
            rsync -a /imbue/. /imbue_volume/

            touch /imbue_volume/{VOLUME_READY_SENTINEL}
            echo "_fetch_control_plane_volume: {control_plane_volume_name} finished rsync'ing from image into volume."
        fi
        """

        try:
            finished_process = concurrency_group.run_process_to_completion(
                command=[
                    *("docker", "run", "--rm"),
                    *("-v", f"{control_plane_volume_name}:/imbue_volume"),
                    image_to_use,
                    *("sh", "-c", command),
                ],
                on_output=lambda line, is_stderr: logger.debug(line.strip()),
                shutdown_event=shutdown_event,
                progress_handle=progress_handle,
            )
            logger.info(
                "Finished process to fetch volume_name={}: stdout={}, stderr={}",
                control_plane_volume_name,
                finished_process.stdout,
                finished_process.stderr,
            )
        except ProcessError as e:
            if shutdown_event.is_set():
                raise CancelledByEventError() from e
            raise ControlPlaneFetchError(
                f"Failed to fetch control plane volume {control_plane_volume_name} from image {image_to_use}"
            ) from e

        # Clean up the Docker image now that we've copied files to the volume.
        # This might fail if another thread is using the image, which is fine - we'll ignore that error.
        # The image will be cleaned up when the last thread finishes.
        if name_provider.determine_current_run_mode() == ControlPlaneRunMode.TAGGED_RELEASE:
            try:
                logger.debug(
                    "Attempting to remove Docker image for control plane volume now that it's in a volume: {}",
                    image_to_use,
                )
                concurrency_group.run_process_to_completion(
                    command=["docker", "image", "rm", image_to_use],
                    is_checked_after=True,
                )
                logger.info("Successfully removed Docker image for control plane volume: {}", image_to_use)
            except ProcessError as e:
                # Expected to fail if another thread is still using the image
                logger.info(
                    "Could not remove Docker image for control plane volume {} (may still be in use): {}",
                    image_to_use,
                    e,
                )
        else:
            logger.debug("Skipping cleanup of Docker image {} because it's not a tagged release", image_to_use)


class _ControlPlaneVolumeBackgroundSetup(BackgroundSetup):
    """BackgroundSetup subclass that verifies the volume still exists before returning cached result.

    The standard BackgroundSetup caches success and returns immediately on subsequent calls.
    This subclass checks if the volume is actually still ready, and re-runs setup if the
    volume was deleted after the initial setup completed.
    """

    # It'd be better if we didn't have to subclass here
    # TODO - maybe we could have BackgroundSetup take a callable for repeated is_finished checks?
    def ensure_finished(self, concurrency_group: ConcurrencyGroup) -> None:
        # If we think setup is finished, verify the volume is actually still ready.
        # The volume might have been manually deleted after the initial setup.
        if self.is_finished():
            volume_name = ControlPlaneImageNameProvider().get_control_plane_volume_name()
            try:
                if _is_volume_ready(volume_name, concurrency_group):
                    # Volume is still ready, nothing to do
                    return
                # Volume was deleted, reset state so parent re-runs setup
                logger.info("Control plane volume {} was deleted after initial setup, re-creating it.", volume_name)
                self._is_finished = False
            except ProcessError as e:
                # Docker failed during the check, reset state and let parent handle it
                logger.info("Failed to check if volume {} is ready, will re-run setup: {}", volume_name, e)
                self._is_finished = False

        super().ensure_finished(concurrency_group)


CONTROL_PLANE_FETCH_BACKGROUND_SETUP: Final[BackgroundSetup] = _ControlPlaneVolumeBackgroundSetup(
    "SculptorControlPlaneVolumeFetchBackgroundSetup",
    _fetch_control_plane_volume,
)
