import os
import re
import threading
import time
from datetime import datetime
from datetime import timedelta
from enum import StrEnum
from pathlib import Path
from typing import Final
from typing import Literal
from typing import Optional
from urllib.error import ContentTooShortError
from urllib.request import urlopen
from uuid import uuid4

from loguru import logger
from pydantic import AnyUrl

from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.progress_tracking.progress_tracking import start_finish_context
from imbue_core.subprocess_utils import ProcessError
from sculptor.interfaces.environments.errors import ExpectedError
from sculptor.interfaces.environments.errors import ProviderError
from sculptor.interfaces.environments.provider_status import OkStatus
from sculptor.services.environment_service.environments.utils import get_docker_status
from sculptor.services.environment_service.providers.docker.image_utils import get_platform_architecture
from sculptor.utils.build import get_sculptor_folder
from sculptor.utils.timeout import log_runtime


class ImagePurpose(StrEnum):
    CONTROL_PLANE = "CONTROL_PLANE"
    DEFAULT_DEVCONTAINER = "DEFAULT_DEVCONTAINER"


_CDN_PREFIX: Final[str] = "https://d2rpy6crlmjake.cloudfront.net/images/"

# Global dictionary to store locks by image URL
_image_url_locks: dict[str, threading.Lock] = {}
_image_url_locks_lock = threading.Lock()


def _get_or_create_image_url_lock(image_url: str) -> threading.Lock:
    """Get or create a lock for the given image URL."""
    with _image_url_locks_lock:
        if image_url not in _image_url_locks:
            _image_url_locks[image_url] = threading.Lock()
        return _image_url_locks[image_url]


def _extract_sha_from_image_url(image_url: str) -> str:
    """Extract the SHA256 hash from a Docker image URL.

    Args:
        image_url: Docker image URL (e.g., "ghcr.io/imbue-ai/sculptor:tag@sha256:abc123...")

    Returns:
        The SHA256 hash portion

    Raises:
        ValueError: If no SHA256 hash is found in the URL
    """
    match = re.search(r"sha256:([a-f0-9]+)", image_url)
    if not match:
        raise ValueError(f"No SHA256 hash found in image URL: {image_url}")
    return match.group(1)


BASE_IMAGE_CACHE_DIR: Final[Path] = Path("image_cache")


def _get_image_cache_dir(image_purpose: ImagePurpose) -> Path:
    """Get the directory for caching downloaded Docker images."""
    cache_dir = get_sculptor_folder() / BASE_IMAGE_CACHE_DIR / image_purpose
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir


_PARTIAL_FILE_NAME_EXTENSION_SUBSTRING: Final[str] = ".part."


def _cleanup_stale_image_tarballs(current_sha: str, cache_dir: Path) -> None:
    """Remove old image tarballs and partial downloads, keeping only the current version and things newer than 1 day, to avoid races.

    Args:
        current_sha: The SHA256 hash of the current image to keep
    """

    # Clean up all .part files (partial downloads that may have failed)
    for part_file in cache_dir.glob(f"*{_PARTIAL_FILE_NAME_EXTENSION_SUBSTRING}*"):
        if datetime.fromtimestamp(part_file.stat().st_mtime) < datetime.now() - timedelta(days=1):
            logger.info("Removing partial download: {}", part_file)
            part_file.unlink(missing_ok=True)

    # Clean up tarballs for different SHAs
    for tar_file in cache_dir.glob("*.tar"):
        if datetime.fromtimestamp(tar_file.stat().st_mtime) < datetime.now() - timedelta(days=1):
            if tar_file.stem != current_sha:
                logger.info("Removing stale image tarball: {}", tar_file)
                tar_file.unlink(missing_ok=True)


DISABLE_DOCKER_IMAGE_DOWNLOADS_ENV_VAR = "IMBUE_SCULPTOR_DISABLE_DOCKER_IMAGE_DOWNLOADS"


def get_cdn_url_for_image(image_url: str, platform_architecture: Literal["amd64", "arm64"]) -> str:
    safe_name = docker_image_url_to_s3_safe_name(image_url, platform_architecture)
    return f"{_CDN_PREFIX}{safe_name}.tar"


def download_image_tarball_if_needed(
    image_url: str,
    cache_dir: Path,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> Path:
    """Download Docker image tarball to persistent cache if not already present.

    This function uses a lock for each image URL to prevent concurrent downloads of the same image.
    This prevents multiple threads within a single process from downloading the same image simultaneously.

    However, multiple processes may call this function concurrently; we defend against concurrency issues by:
    1. Downloading to a uniquely named .part file
    2. Renaming the .part file to the final name atomically once download is complete

    Args:
        image_url: Docker image URL (e.g., "ghcr.io/imbue-ai/sculptor:tag@sha256:...")

    Returns:
        Path to the downloaded tarball

    Raises:
        ValueError: If the image URL doesn't contain a SHA256 hash
        Exception: If download fails
    """
    if progress_handle is None:
        progress_handle = ProgressHandle()

    sha = _extract_sha_from_image_url(image_url)
    tarball_path = cache_dir / f"{sha}.tar"

    with _get_or_create_image_url_lock(image_url):
        if tarball_path.exists():
            logger.info("Image tarball already exists at {}", tarball_path)
            return tarball_path

        # If we are running in a test environment (and we are not actively testing this function),
        # some test setup code should have already downloaded the tarball.
        if DISABLE_DOCKER_IMAGE_DOWNLOADS_ENV_VAR in os.environ and not image_url.startswith("test://"):
            raise FileNotFoundError(
                "".join(
                    (
                        f"Unable to find cached tarball at {tarball_path} in test environment. ",
                        "The image should have been downloaded as part of test setup. ",
                        "Pass --prefetch-docker-control-plane as a pytest argument to enable this.",
                    )
                )
            )

        # Download to .part file
        part_path = cache_dir / f"{sha}.tar{_PARTIAL_FILE_NAME_EXTENSION_SUBSTRING}{uuid4()}"
        platform_name = get_platform_architecture()
        safe_name = docker_image_url_to_s3_safe_name(image_url, platform_name)
        cdn_url = f"{_CDN_PREFIX}{safe_name}.tar"

        try:
            with (
                log_runtime(f"DownloadImageTarball:{sha}"),
                start_finish_context(
                    progress_handle.track_download(AnyUrl(cdn_url), "Fetching image tarball")
                ) as download_handle,
            ):
                logger.info("Downloading image tarball from {} to {}", cdn_url, part_path)
                timeout_tracker = _UrlRetrieveTimeoutTracker(timeout_seconds=float("inf"))
                with urlopen(cdn_url) as response, open(part_path, "wb") as out_file:
                    headers = response.info()
                    content_length = headers.get("Content-Length")
                    download_handle.report_size(content_length)
                    received_bytes = 0
                    while True:
                        if shutdown_event is not None and shutdown_event.is_set():
                            raise CancelledByEventError("Download cancelled due to shutdown event.")
                        chunk = response.read(8192)
                        received_bytes += len(chunk)
                        download_handle.report_progress(received_bytes)
                        if not chunk:
                            break
                        out_file.write(chunk)
                    if content_length is not None and received_bytes < int(content_length):
                        raise ContentTooShortError(
                            f"Retrieval incomplete: got only {received_bytes} out of {content_length} bytes",
                            (str(part_path), headers),
                        )

                # Atomic rename when complete
                try:
                    part_path.rename(tarball_path)
                except FileNotFoundError:
                    # This can happen if there is a race condition with another process.
                    pass
                logger.info("Successfully downloaded image tarball to {}", tarball_path)
        finally:
            # Clean up stale tarballs (best effort)
            try:
                _cleanup_stale_image_tarballs(sha, cache_dir)
            except Exception as e:
                log_exception(e, "Failed to cleanup stale tarballs", priority=ExceptionPriority.LOW_PRIORITY)

    return tarball_path


def _get_cached_tarball_path(image_url: str, cache_dir: Path) -> Path:
    """
    Get the path to a cached image tarball.

    This function assumes the tarball has already been downloaded to the cache directory.

    Args:
        image_url: Docker image URL (e.g., "ghcr.io/imbue-ai/sculptor:tag@sha256:...")

    Returns:
        Path to the cached tarball

    Raises:
        ValueError: If the image URL doesn't contain a SHA256 hash
        FileNotFoundError: If the cached tarball does not exist
    """
    sha = _extract_sha_from_image_url(image_url)
    tarball_path = cache_dir / f"{sha}.tar"

    if not tarball_path.exists():
        raise FileNotFoundError(
            "".join(
                (
                    f"Cached tarball not found at {tarball_path}. ",
                    "The image should have been downloaded earlier via background setup. ",
                    "This indicates a temporal dependency issue in the code.",
                )
            )
        )

    return tarball_path


def _load_image_from_cached_tarball(
    image_url: str, cache_dir: Path, concurrency_group: ConcurrencyGroup, shutdown_event: ReadOnlyEvent | None = None
) -> None:
    """
    Load a Docker image from cached tarball into Docker.

    This function has a temporal dependency: it assumes the tarball has already been
    downloaded to the cache directory (typically via CONTROL_PLANE_DOWNLOAD_BACKGROUND_SETUP
    or similar mechanism). If the tarball doesn't exist, this will raise an error.

    Args:
        image_url: Docker image URL (e.g., "ghcr.io/imbue-ai/sculptor:tag@sha256:...")

    Raises:
        FileNotFoundError: If the cached tarball does not exist
        Exception: Any exception that occurs during loading
    """
    tarball_path = _get_cached_tarball_path(image_url, cache_dir)

    with log_runtime(f"DockerLoad:{image_url}"):
        logger.info(f"Loading image from cached tarball: {tarball_path}")
        try:
            load_result = concurrency_group.run_process_to_completion(
                command=["docker", "load", "-i", str(tarball_path)],
                on_output=lambda line, is_stderr: logger.debug(line.strip()),
                timeout=float("inf"),
                shutdown_event=shutdown_event,
            )
        except ProcessError as e:
            if shutdown_event is not None and shutdown_event.is_set():
                raise CancelledByEventError("Operation cancelled due to shutdown event.") from e
            health_status = get_docker_status(concurrency_group)
            # If Docker is running but the load failed, this may be a real issue.
            if isinstance(health_status, OkStatus):
                raise
            logger.debug("Docker seems to be down, cannot load image")
            details_message = f" (details: {health_status.details})" if health_status.details else ""
            raise ProviderError(f"Provider is unavailable: {health_status.message}{details_message}") from e
        logger.info("Loaded image from {}, load result: {}", tarball_path, load_result)


def docker_image_url_to_s3_safe_name(image_url: str, target_platform: str) -> str:
    """
    Convert a Docker image URL and platform to an S3-safe path component.

    Replaces unsafe characters in the image URL and platform to make them S3-compatible.

    Args:
        image_url: Docker image URL (e.g., "ubuntu:20.04", "gcr.io/project/image:v1.0")
        platform: Platform architecture (e.g., "amd64", "arm64")

    Returns:
        S3-safe string combining image URL and platform

    Examples:
        >>> docker_image_url_to_s3_safe_name("ubuntu:20.04", "amd64")
        'ubuntu-20.04_amd64'

        >>> docker_image_url_to_s3_safe_name("gcr.io/my-project/my-image:v1.2.3", "arm64")
        'gcr.io/my-project/my-image-v1.2.3_arm64'

        >>> docker_image_url_to_s3_safe_name("nginx@sha256:abc123def456", "amd64")
        'nginx-sha256-abc123def456_amd64'
    """
    # Replace unsafe characters with safe ones
    # S3 keys can contain: letters, numbers, hyphens, underscores, periods
    # Replace problematic characters: / : @ . with safe alternatives
    result = f"{image_url}_{target_platform}"
    result = re.sub(r"[^-_/.a-zA-Z0-9]", "-", result)
    return result


def _ensure_image_available(
    image_url: str, cache_dir: Path, concurrency_group: ConcurrencyGroup, shutdown_event: ReadOnlyEvent | None = None
) -> None:
    """
    Ensure a Docker image is available in the local Docker daemon.

    First checks if the image exists locally using `docker inspect`. If not,
    attempts to load from cached tarball, then registers the image with Docker via `docker pull`.

    This method is locked per image URL to prevent concurrent operations on the same image.

    IMPORTANT: This function has a temporal dependency - the image tarball must have been
    downloaded earlier (typically via CONTROL_PLANE_DOWNLOAD_BACKGROUND_SETUP or similar).
    If the cached tarball doesn't exist, this function will raise FileNotFoundError.

    Args:
        image_url: Docker image URL (e.g., "ghcr.io/imbue-ai/sculptor:tag@sha256:...")

    Raises:
        FileNotFoundError: If the cached tarball does not exist
    """
    image_lock = _get_or_create_image_url_lock(image_url)

    with image_lock:
        with log_runtime(f"EnsureImageAvailable:{image_url}"):
            logger.info("Checking if image {} is available locally", image_url)

            # Check if image exists locally in Docker
            try:
                inspect_result = concurrency_group.run_process_to_completion(
                    command=["docker", "inspect", image_url],
                    is_checked_after=False,
                    shutdown_event=shutdown_event,
                )
            except ProcessError as e:
                if shutdown_event is not None and shutdown_event.is_set():
                    raise CancelledByEventError("Operation cancelled due to shutdown event.") from e
                raise
            if inspect_result.returncode == 0:
                logger.trace("Image {} already available locally", image_url)
                return

            logger.info("Image {} not found locally, loading from cached tarball.", image_url)

            # Load from cached tarball into Docker
            try:
                _load_image_from_cached_tarball(image_url, cache_dir, concurrency_group, shutdown_event)
                logger.info("Successfully loaded image from cached tarball, {}", image_url)
            except ExpectedError:
                raise
            except Exception as e:
                log_exception(
                    e,
                    "Failed to load image {image_url} from cached tarball, will fallback to docker pull",
                    image_url=image_url,
                )

            # We have to do this `docker pull`, even if the docker load above succeeded.
            # It has the effect of registering "image_url" with docker, and that's what we check for
            # above to decide if we need to re-run this method or if docker already knows about "image_url".
            # If we didn't `docker pull` here, the next call to this method would do the load again.
            # This should not actually fetch many bytes, but does talk to the registry.
            with log_runtime(f"DockerPull:{image_url}"):
                try:
                    concurrency_group.run_process_to_completion(
                        command=["docker", "pull", image_url],
                        on_output=lambda line, is_stderr: logger.debug(line.strip()),
                        shutdown_event=shutdown_event,
                    )
                except ProcessError as e:
                    if shutdown_event is not None and shutdown_event.is_set():
                        raise CancelledByEventError("Operation cancelled due to shutdown event.") from e
                    health_status = get_docker_status(concurrency_group)
                    # If Docker is running but the pull failed, this may be a real issue.
                    if isinstance(health_status, OkStatus):
                        raise

                    logger.debug("Docker seems to be down, cannot pull image")
                    details_message = f" (details: {health_status.details})" if health_status.details else ""
                    raise ProviderError(f"Provider is unavailable: {health_status.message}{details_message}") from e

        logger.success("Successfully ensured image {} is available.", image_url)


def fetch_image_from_cdn(
    image_url: str,
    image_purpose: ImagePurpose,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> None:
    """
    Fetch a Docker image from CDN if it's not already available locally. Once the image has been fetched, attempt to load it into Docker.

    Images are cached in a directory specific to their purpose to speed up cold starts while allowing for cleanup of stale images.

    Args:
        image_url: Docker image URL (e.g., "ghcr.io/imbue-ai/sculptor:tag@sha256:...")
        image_purpose: Description of the image's purpose for managing cleanup; should be a valid directory name (e.g., "control_plane").
    """
    image_cache_dir = _get_image_cache_dir(image_purpose)

    logger.info("Downloading {} image from {}", image_purpose, image_url)
    # TODO this call needs to respect the concurrency_group
    download_image_tarball_if_needed(image_url, image_cache_dir, shutdown_event, progress_handle)

    logger.info("Ensuring {} image is loaded into Docker: {}", image_purpose, image_url)
    _ensure_image_available(image_url, image_cache_dir, concurrency_group, shutdown_event)


def get_image_purpose_from_url(image_url: str) -> Optional[ImagePurpose]:
    if "sculptorbase_nix" in image_url:
        return ImagePurpose.CONTROL_PLANE
    if "sculptor_default_devcontainer" in image_url:
        return ImagePurpose.DEFAULT_DEVCONTAINER
    return None


class _UrlRetrieveTimeoutTracker:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = timeout_seconds
        self.start_time = time.monotonic()

    def report_hook(self, block_num: int, block_size: int, total_size: int) -> None:
        elapsed_time = time.monotonic() - self.start_time
        if elapsed_time > self.timeout_seconds:
            downloaded_bytes = block_num * block_size
            raise TimeoutError(
                f"Download timed out after {elapsed_time:.1f} seconds. {downloaded_bytes=}, {total_size=}."
            )
