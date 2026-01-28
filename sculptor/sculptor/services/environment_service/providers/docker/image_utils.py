import json
import os
import platform
import re
import shlex
import types
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Callable
from typing import Final
from typing import Generator
from typing import Literal
from typing import Mapping
from typing import Self
from typing import Sequence

import humanfriendly
from humanfriendly import InvalidSize
from loguru import logger

from imbue_core.agents.data_types.ids import ProjectID
from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.processes.local_process import run_blocking
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.sculptor.telemetry import PosthogEventModel
from imbue_core.sculptor.telemetry import PosthogEventPayload
from imbue_core.sculptor.telemetry import emit_posthog_event
from imbue_core.sculptor.telemetry_constants import ConsentLevel
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.sculptor.telemetry_utils import with_consent
from imbue_core.subprocess_utils import ProcessError
from sculptor import version
from sculptor.cli.sculptor_instance_utils import get_or_create_sculptor_instance_id
from sculptor.cli.ssh_utils import ensure_local_sculptor_ssh_configured
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.interfaces.environments.errors import ImageConfigError
from sculptor.interfaces.environments.errors import ProviderError
from sculptor.interfaces.environments.provider_status import OkStatus
from sculptor.primitives.constants import USER_FACING_LOG_TYPE
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.primitives.ids import DockerContainerID
from sculptor.primitives.ids import DockerImageID
from sculptor.services.environment_service.api import TaskImageCleanupData
from sculptor.services.environment_service.environments.docker_environment import get_unique_snapshot_size_bytes
from sculptor.services.environment_service.environments.image_tags import ImageInfo
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV1
from sculptor.services.environment_service.environments.image_tags import get_tagged_reference
from sculptor.services.environment_service.environments.utils import get_docker_status
from sculptor.utils.timeout import log_runtime_decorator

# Match build step headers like: "#5 [2/6] WORKDIR /app"
_STEP_PATTERN: Final[re.Pattern] = re.compile(r"^#\d+\s+\[.*\d+/\d+\]\s+(?P<directive>[A-Z]+)( .*)?$")

# Combined pattern for step completion lines:
# Matches "#N CACHED" or "#N DONE 0.5s" with a capture group to distinguish them
# Group 'status' will be either 'CACHED' or 'DONE'
_STEP_STATUS_PATTERN: Final[re.Pattern] = re.compile(r"^#\d+\s+(?P<status>CACHED|DONE\s+[\d.]+s)\s*$")

# Match FROM directive resolution lines: "#N resolve ... done"
_FROM_RESOLVE_PATTERN: Final[re.Pattern] = re.compile(r"^#\d+\s+resolve\s+.*\s+done\s*$")


class UncachedBuildDetector:
    """Detects cache misses in `docker buildx build` output.

    Use as a context manager to ensure final cache status is checked:
        with UncachedBuildDetector(on_cache_miss) as detector:
            # process output lines
            detector.process_output_line(line)
        # on_cache_miss called here if build wasn't fully cached

    Or use directly and call finalize() manually after all output is processed.

    Detection heuristic:
    - Build steps start with `#N [stage M/N] DIRECTIVE`
    - Cache hits show "CACHED" on the following line
    - FROM directives show "resolve ... done" and "DONE" when cached
    - Non-cached steps show other output (downloads, extraction, etc.)
    - Final export step always shows "DONE"

    See test file for examples including multi-stage builds and ADD directives.
    """

    def __init__(self, on_cache_miss: Callable[[], None]) -> None:
        self._on_cache_miss = on_cache_miss
        self._cache_miss_detected: bool = False
        self._current_directive: str | None = None
        self._last_step_status: str | None = None
        self._finalized: bool = False

    def __enter__(self) -> Self:
        return self

    def __exit__(
        self, exc_type: type | None, exc_val: BaseException | None, exc_tb: types.TracebackType | None
    ) -> None:
        self.finalize()
        return None

    def process_output_line(self, line: str) -> None:
        if self._cache_miss_detected:
            return

        stripped_line = line.strip()

        step_match = _STEP_PATTERN.match(stripped_line)
        if step_match:
            self._current_directive = step_match.group("directive")
            return

        if not stripped_line:
            self._current_directive = None
            return

        # These should be the lines that follow a step header.
        if self._current_directive is not None:
            if self._is_cache_miss(stripped_line):
                self._cache_miss_detected = True
                logger.debug("Detected uncached build step: {}", stripped_line)
                self._on_cache_miss()

    def _is_cache_miss(self, line: str) -> bool:
        status_match = _STEP_STATUS_PATTERN.match(line)
        if status_match:
            self._last_step_status = status_match.group("status")
            return False

        if self._current_directive == "FROM":
            if _FROM_RESOLVE_PATTERN.match(line):
                return False
            return True

        return True

    def finalize(self) -> None:
        if self._finalized or self._cache_miss_detected:
            return

        self._finalized = True

        # The build is fully cached only if the last meaningful line was "#N CACHED"
        # TODO(sam): In fact, I believe that we expect the final output line for _every_ step to be
        # "#N CACHED" but that necessitates more complex state tracking.
        #
        # An even _better_ mechanism to detect cache misses more conclusively would also include image ID comparison.
        # For now, we'll start with this heuristic.
        if self._last_step_status != "CACHED":
            self._cache_miss_detected = True
            logger.debug(
                "Build completed without full cache hit: final step status={}",
                self._last_step_status,
            )
            self._on_cache_miss()


class BuildOutputProcessor:
    def __init__(self, detector: UncachedBuildDetector) -> None:
        self._detector = detector

    def on_output(self, line: str, is_stderr: bool) -> None:
        # We really only expect stderr.
        self._detector.process_output_line(line)
        # These debug logs are surfaced to the user in the "Logs" tab of the artifact panel.
        logger.debug(line.strip())


@contextmanager
def get_cache_miss_output_callback(
    on_cache_miss: Callable[[], None] | None,
) -> Generator[Callable[[str, bool], None], None, None]:
    if on_cache_miss is None:
        yield lambda line, is_stderr: logger.debug(line.strip())
        return
    with UncachedBuildDetector(on_cache_miss) as detector:
        yield BuildOutputProcessor(detector).on_output


@log_runtime_decorator()
def build_docker_image(
    dockerfile_path: Path,
    project_id: ProjectID,
    concurrency_group: ConcurrencyGroup,
    image_repo: str,
    image_metadata: ImageMetadataV1,
    cached_repo_tarball_parent_directory: Path | None = None,
    disable_cache: bool = False,
    secrets: Mapping[str, str] | None = None,
    build_path: Path | None = None,
    base_image_tag: str | None = None,
    forward_ports: list[int] | None = None,
    container_user: str | None = None,
    on_create_command: str | list[str] | Mapping[str, str | list[str]] | None = None,
    update_content_command: str | list[str] | Mapping[str, str | list[str]] | None = None,
    on_cache_miss: Callable[[], None] | None = None,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> LocalDockerImage:
    """Build a Docker image from a Dockerfile.

    build_path is a synonym for Docker's build context, which is an unnamed argument to docker build.
    container_user is the user from devcontainer.json's containerUser field, if any.
    on_create_command is the onCreateCommand from devcontainer.json, if any.
    update_content_command is the updateContentCommand from devcontainer.json, if any.
    """
    if progress_handle is None:
        progress_handle = ProgressHandle()

    tagged_reference = get_tagged_reference(image_repo, image_metadata)
    if not dockerfile_path.exists():
        raise FileNotFoundError(f"Dockerfile not found at {dockerfile_path}")

    if secrets is None:
        secrets = {}

    # Build the Docker image
    build_command = [
        *("docker", "buildx", "build"),
        "--progress=plain",
        "--output=type=docker,compression=uncompressed",
        *("-f", str(dockerfile_path)),
        *("-t", tagged_reference),
        *("--build-arg", f"BUILT_FROM_SCULPTOR_VERSION={version.__version__}"),
        *("--build-arg", f"BUILT_FROM_SCULPTOR_GIT_HASH={version.__git_sha__}"),
    ]
    if cached_repo_tarball_parent_directory:
        build_command.extend(("--build-context", f"imbue_user_repo={cached_repo_tarball_parent_directory}"))

    ssh_keypair_dir = ensure_local_sculptor_ssh_configured()
    build_command.extend(("--build-context", f"ssh_keypair_dir={ssh_keypair_dir}"))

    sculptor_instance_id = get_or_create_sculptor_instance_id()
    build_command.extend(("--label", f"instance_id={sculptor_instance_id}"))

    if base_image_tag:
        build_command.extend(("--build-arg", f"BASE_IMAGE={base_image_tag}"))

    if forward_ports:
        # Serialize the forward_ports to JSON and pass as build arg
        forward_ports_json = json.dumps(forward_ports)
        build_command.extend(("--build-arg", f"FORWARD_PORTS={forward_ports_json}"))

    if container_user:
        build_command.extend(("--build-arg", f"CONTAINER_USER={container_user}"))

    if on_create_command:
        # Serialize the on_create_command to JSON and pass as build arg
        on_create_command_json = json.dumps(on_create_command)
        build_command.extend(("--build-arg", f"ON_CREATE_COMMAND={on_create_command_json}"))

    if update_content_command:
        # Serialize the update_content_command to JSON and pass as build arg
        update_content_command_json = json.dumps(update_content_command)
        build_command.extend(("--build-arg", f"UPDATE_CONTENT_COMMAND={update_content_command_json}"))

    if disable_cache:
        build_command.append("--no-cache")

    build_path = build_path or dockerfile_path.parent
    build_command.append(str(build_path))

    logger.info("Building Docker image with tag {}", tagged_reference)

    build_command_string = " ".join(shlex.quote(arg) for arg in build_command)
    logger.debug("Building Docker image with build_path={}:\n{}", build_path, build_command_string)

    # Use the detector as a context manager to ensure finalize() is called
    with (
        get_cache_miss_output_callback(on_cache_miss) as cache_miss_output_callback,
    ):
        try:
            result = concurrency_group.run_process_to_completion(
                command=build_command,
                on_output=cache_miss_output_callback,
                cwd=build_path,
                trace_log_context={
                    "sandbox_path": str(build_path),
                    "log_type": USER_FACING_LOG_TYPE,
                },
                env={**os.environ, **secrets},
                shutdown_event=shutdown_event,
                progress_handle=progress_handle,
            )

        except ProcessError as e:
            if shutdown_event and shutdown_event.is_set():
                raise CancelledByEventError() from e
            error_msg = f"Docker build failed - is your Docker up-to-date? Exit code {e.returncode}: {build_command_string}\nstdout=\n{e.stdout}\nstderr=\n{e.stderr}"
            if "ERROR: failed to solve" in e.stderr:
                # NOTE: this might not be the best way to distinguish between image config errors and other errors
                raise ImageConfigError(error_msg) from e
            raise ProviderError(error_msg) from e

    # Get the image ID
    inspect_result = concurrency_group.run_process_to_completion(
        command=["docker", "inspect", "-f", "{{.Id}}", tagged_reference],
        is_checked_after=False,
    )

    if inspect_result.returncode != 0:
        raise ProviderError(f"Failed to inspect built image: {inspect_result.stderr}")

    docker_image_id = inspect_result.stdout.strip()

    # Save to database
    full_id = DockerImageID(docker_image_id)

    logger.info("Built Docker image {} with tag {}", full_id, tagged_reference)
    return LocalDockerImage(image_id=full_id, project_id=project_id, forward_ports=forward_ports)


@log_runtime_decorator("initializeCommands")
def run_initialize_command(
    initialize_command: str | list[str] | Mapping[str, str | list[str]],
    devcontainer_path: Path,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent | None = None,
) -> None:
    """Run `initializeCommand` spec from a devcontainer.json.

    initialize_command is the command(s) to run.
    devcontainer_path is the path to the devcontainer.json file.
    concurrency_group is the concurrency group to run the commands in.
    """

    commands = _preprocess_lifecycle_command(initialize_command, command_property="initializeCommand")
    cwd = _repository_root_from_devcontainer_path(devcontainer_path)
    try:
        with ObservableThreadPoolExecutor(concurrency_group=concurrency_group, max_workers=10) as executor:
            executor.map(
                lambda command_name_and_command: concurrency_group.run_process_to_completion(
                    command=command_name_and_command[1],
                    on_output=lambda line, is_stderr, name=command_name_and_command[0]: logger.debug(
                        f"#[{name:>15}]: {line.strip()}"
                    ),
                    cwd=cwd,
                    trace_log_context={
                        "sandbox_path": str(cwd),
                        "log_type": USER_FACING_LOG_TYPE,
                    },
                    env={**os.environ},
                    shutdown_event=shutdown_event,
                ),
                commands,
            )
    except ProcessError as e:
        if shutdown_event and shutdown_event.is_set():
            raise CancelledByEventError() from e
        error_msg = f"running `initializeCommand` hooks failed? Path {cwd}, Exit code {e.returncode}: {commands}\nstdout=\n{e.stdout}\nstderr=\n{e.stderr}"
        raise ImageConfigError(error_msg) from e


def read_on_create_command_from_container(
    container_id: DockerContainerID, concurrency_group: ConcurrencyGroup
) -> str | list[str] | Mapping[str, str | list[str]] | None:
    """Read the onCreateCommand from /imbue_addons/on_create_command.json in the container.

    Returns None if no onCreateCommand was stored in the image.
    """
    try:
        result = concurrency_group.run_process_to_completion(
            command=["docker", "exec", container_id, "cat", "/imbue_addons/on_create_command.json"],
            is_checked_after=False,
        )
        if result.returncode != 0:
            logger.debug("No onCreateCommand found in container {}", container_id)
            return None

        on_create_command_json = result.stdout.strip()
        if not on_create_command_json or on_create_command_json == "null":
            return None

        return json.loads(on_create_command_json)
    except Exception as e:
        logger.debug("Failed to read onCreateCommand from container {}: {}", container_id, e)
        return None


def read_update_content_command_from_container(
    container_id: DockerContainerID, concurrency_group: ConcurrencyGroup
) -> str | list[str] | Mapping[str, str | list[str]] | None:
    """Read the updateContentCommand from /imbue_addons/update_content_command.json in the container.

    Returns None if no updateContentCommand was stored in the image.
    """
    try:
        result = concurrency_group.run_process_to_completion(
            command=["docker", "exec", container_id, "cat", "/imbue_addons/update_content_command.json"],
            is_checked_after=False,
        )
        if result.returncode != 0:
            logger.debug("No updateContentCommand found in container {}", container_id)
            return None

        update_content_command_json = result.stdout.strip()
        if not update_content_command_json or update_content_command_json == "null":
            return None

        return json.loads(update_content_command_json)
    except Exception as e:
        logger.debug("Failed to read updateContentCommand from container {}: {}", container_id, e)
        return None


@log_runtime_decorator("onCreateCommands")
def run_on_create_command(
    on_create_command: str | list[str] | Mapping[str, str | list[str]],
    container_id: DockerContainerID,
    concurrency_group: ConcurrencyGroup,
    container_user: str,
    workspace_path: str,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> None:
    """Run `onCreateCommand` spec from a devcontainer.json inside a container.

    on_create_command is the command(s) to run.
    container_id is the ID of the container to run commands in.
    concurrency_group is the concurrency group to run the commands in.
    container_user is the user to run the commands as.
    workspace_path is the working directory for the commands. Currently "/code" where
                   the repository is extracted (per Dockerfile.imbue_addons).
                   TODO: Update to use workspace_folder from devcontainer.json.
    """
    if progress_handle is None:
        progress_handle = ProgressHandle()

    commands = _preprocess_lifecycle_command(on_create_command, command_property="onCreateCommand")
    try:
        with ObservableThreadPoolExecutor(concurrency_group=concurrency_group, max_workers=10) as executor:
            executor.map(
                lambda command_name_and_command: concurrency_group.run_process_to_completion(
                    command=[
                        "docker",
                        "exec",
                        "-u",
                        container_user,
                        "-w",
                        workspace_path,
                        container_id,
                        "bash",
                        "-c",
                        " ".join(shlex.quote(arg) for arg in command_name_and_command[1]),
                    ],
                    on_output=lambda line, is_stderr, name=command_name_and_command[0]: logger.debug(
                        f"#[{name:>15}]: {line.strip()}"
                    ),
                    trace_log_context={
                        "container_id": str(container_id),
                        "log_type": USER_FACING_LOG_TYPE,
                    },
                    env={**os.environ},
                    shutdown_event=shutdown_event,
                    progress_handle=progress_handle,
                ),
                commands,
            )

    except ProcessError as e:
        if shutdown_event and shutdown_event.is_set():
            raise CancelledByEventError() from e
        error_msg = f"running `onCreateCommand` hooks failed in container {container_id}, Exit code {e.returncode}: {commands}\nstdout=\n{e.stdout}\nstderr=\n{e.stderr}"
        raise ImageConfigError(error_msg) from e


@log_runtime_decorator("updateContentCommands")
def run_update_content_command(
    update_content_command: str | list[str] | Mapping[str, str | list[str]],
    container_id: DockerContainerID,
    concurrency_group: ConcurrencyGroup,
    container_user: str,
    workspace_path: str,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> None:
    """Run `updateContentCommand` spec from a devcontainer.json inside a container.

    update_content_command is the command(s) to run.
    container_id is the ID of the container to run commands in.
    concurrency_group is the concurrency group to run the commands in.
    container_user is the user to run the commands as.
    workspace_path is the working directory for the commands. Currently "/code" where
                   the repository is extracted (per Dockerfile.imbue_addons).
                   TODO: Update to use workspace_folder from devcontainer.json.
    """
    if progress_handle is None:
        progress_handle = ProgressHandle()

    commands = _preprocess_lifecycle_command(update_content_command, command_property="updateContentCommand")
    try:
        with ObservableThreadPoolExecutor(concurrency_group=concurrency_group, max_workers=10) as executor:
            executor.map(
                lambda command_name_and_command: concurrency_group.run_process_to_completion(
                    # Build the docker exec command to run as the container user
                    command=[
                        "docker",
                        "exec",
                        "-u",
                        container_user,
                        "-w",
                        workspace_path,
                        container_id,
                        "bash",
                        "-c",
                        " ".join(shlex.quote(arg) for arg in command_name_and_command[1]),
                    ],
                    on_output=lambda line, is_stderr, name=command_name_and_command[0]: logger.debug(
                        f"#[{name:>15}]: {line.strip()}"
                    ),
                    trace_log_context={
                        "container_id": str(container_id),
                        "log_type": USER_FACING_LOG_TYPE,
                    },
                    env={**os.environ},
                    shutdown_event=shutdown_event,
                    progress_handle=progress_handle,
                ),
                commands,
            )
    except ProcessError as e:
        if shutdown_event and shutdown_event.is_set():
            raise CancelledByEventError() from e
        error_msg = f"running `updateContentCommand` hooks failed in container {container_id}, Exit code {e.returncode}: {commands}\nstdout=\n{e.stdout}\nstderr=\n{e.stderr}"
        raise ImageConfigError(error_msg) from e


def _cmd_as_list(cmd) -> list[str]:
    """Convert a command string or list into a list of arguments.

    Args:
        cmd: Command as a string (will be shell-parsed) or list of strings

    Returns:
        List of command arguments

    Raises:
        TypeError: If cmd is not a string or list of strings
    """
    if isinstance(cmd, str):
        return shlex.split(cmd)
    if isinstance(cmd, list) and all(isinstance(i, str) for i in cmd):
        return cmd
    raise TypeError(f"Command {cmd} is not a list or string")


def _preprocess_lifecycle_command(
    lifecycle_command: str | list[str] | Mapping[str, str | list[str]],
    command_property: str,
) -> list[tuple[str, Sequence[str]]]:
    """Preprocess devcontainer lifecycle command spec into a normalized format.

    This is used for all devcontainer lifecycle commands (initializeCommand, onCreateCommand,
    postCreateCommand, etc.) which all support the same three formats:
    - String: "npm install" -> shell-parsed into ["npm", "install"]
    - Array: ["npm", "install"] -> used directly
    - Object: {"install": "npm install", "build": "npm run build"} -> multiple named commands

    Args:
        lifecycle_command: The command(s) from devcontainer.json
        command_property: The devcontainer.json property name (e.g., "initializeCommand", "onCreateCommand").
                         This is used as the command name for single string/array commands, and appears
                         in logs to identify which lifecycle hook is being processed.

    Returns:
        List of (name, command_args) tuples ready for execution

    Example:
        >>> _preprocess_lifecycle_command("npm install", command_property="initializeCommand")
        [("initializeCommand", ["npm", "install"])]

        >>> _preprocess_lifecycle_command({"install": "npm install", "build": "npm run build"}, command_property="onCreateCommand")
        [("install", ["npm", "install"]), ("build", ["npm", "run", "build"])]
    """
    commands: list[tuple[str, Sequence[str]]] = []

    if isinstance(lifecycle_command, dict):
        for command_name, command in lifecycle_command.items():
            commands.append((command_name, _cmd_as_list(command)))
    else:
        commands.append((command_property, _cmd_as_list(lifecycle_command)))

    logger.debug(
        "Preprocessed {} lifecycle command: input_type={}, num_commands={}, commands={}",
        command_property,
        "object" if isinstance(lifecycle_command, dict) else "string/array",
        len(commands),
        commands,
    )

    return commands


def _repository_root_from_devcontainer_path(devcontainer_path: Path) -> Path:
    """
    devcontainer_path has one of these forms:.
       .devcontainer/devcontainer.json
       .devcontainer.json
       .devcontainer/<folder>/devcontainer.json (where <folder> is a sub-folder, one level deep)

    we want to find out the folder containing each of these forms.
    """
    # If the file is devcontainer.json, go up until we're above .devcontainer
    if devcontainer_path.name == "devcontainer.json":
        if devcontainer_path.parent.name == ".devcontainer":
            return devcontainer_path.parent.parent
            # Cases: <root>/.devcontainer/devcontainer.json
            # or <root>/.devcontainer/<folder>/devcontainer.json
        if devcontainer_path.parent.parent.name == ".devcontainer":
            return devcontainer_path.parent.parent.parent

    if devcontainer_path.name == ".devcontainer.json":
        # Case: <root>/.devcontainer.json
        return devcontainer_path.parent

    raise ImageConfigError(f"devcontainer.json path is invalid {devcontainer_path}")


def delete_docker_image_and_any_stopped_containers(
    image_id: str, concurrency_group: ConcurrencyGroup, shutdown_event: ReadOnlyEvent | None = None
) -> tuple[bool, list[DockerContainerID]]:
    """Delete a Docker image by image ID."""
    deleted_container_ids: list[DockerContainerID] = []
    # first delete all *stopped* docker containers that were created from this image
    try:
        container_ids = (
            concurrency_group.run_process_to_completion(
                command=["docker", "ps", "-a", "-q", "-f", "status=exited", "-f", f"ancestor={image_id}"],
                shutdown_event=shutdown_event,
            )
            .stdout.strip()
            .splitlines(keepends=False)
        )
    # TODO: probably need some better error handling here
    except ProcessError as e:
        if shutdown_event and shutdown_event.is_set():
            raise CancelledByEventError() from e
        log_exception(
            e, "Failed to list containers for {image_id}", priority=ExceptionPriority.LOW_PRIORITY, image_id=image_id
        )
        return False, deleted_container_ids

    for container_id in container_ids:
        try:
            concurrency_group.run_process_to_completion(
                command=["docker", "rm", container_id], shutdown_event=shutdown_event
            )
            deleted_container_ids.append(DockerContainerID(container_id))
            logger.debug("Successfully deleted stopped container {} for image {}", container_id, image_id)
        except ProcessError as e:
            if shutdown_event and shutdown_event.is_set():
                raise CancelledByEventError() from e
            log_exception(
                e,
                "Failed to delete stopped containers for image {image_id}",
                priority=ExceptionPriority.LOW_PRIORITY,
                image_id=image_id,
            )
            return False, deleted_container_ids

    try:
        # The only time we want to delete an image is when it is genuinely unused; i.e.
        # not being used by a current running container. The docker rmi command fails when
        # it is asked to delete an image used by a currently running container, while allowing
        # you to delete outdated snapshots for currently running containers.

        concurrency_group.run_process_to_completion(command=["docker", "rmi", image_id], shutdown_event=shutdown_event)
        logger.debug("Successfully deleted Docker image: {}", image_id)
        return True, deleted_container_ids
    except ProcessError as e:
        if shutdown_event and shutdown_event.is_set():
            raise CancelledByEventError() from e
        image_still_exists_against_our_wishes = concurrency_group.run_process_to_completion(
            command=["docker", "inspect", image_id], is_checked_after=False
        )
        if image_still_exists_against_our_wishes.returncode != 0:
            return True, deleted_container_ids
        else:
            if "image is being used by running container" in e.stderr:
                pass
            else:
                log_exception(e, "Failed to delete Docker image {image_id}", image_id=image_id)
            return False, deleted_container_ids
    except Exception as e:
        log_exception(e, "Error deleting Docker image {image_id}", image_id=image_id)
        return False, deleted_container_ids


def get_image_ids_with_running_containers(
    concurrency_group: ConcurrencyGroup, shutdown_event: ReadOnlyEvent | None = None
) -> tuple[str, ...]:
    try:
        container_ids_result = concurrency_group.run_process_to_completion(
            command=("docker", "ps", "--quiet"), shutdown_event=shutdown_event
        )
        container_ids = container_ids_result.stdout.strip().splitlines()
        if len(container_ids) == 0:
            return ()
        image_ids_result = concurrency_group.run_process_to_completion(
            command=(
                "docker",
                "inspect",
                "--format={{.Image}}",
                *container_ids,
            ),
            shutdown_event=shutdown_event,
        )
    except ProcessError as e:
        if shutdown_event and shutdown_event.is_set():
            raise CancelledByEventError() from e
        health_status = get_docker_status(concurrency_group)
        if not isinstance(health_status, OkStatus):
            logger.debug("Docker seems to be down, cannot list running containers")
            details_msg = f" (details: {health_status.details})" if health_status.details else ""
            raise ProviderError(f"Provider is unavailable: {health_status.message}{details_msg}") from e
        else:
            log_exception(
                e, "Error getting image IDs with running containers", priority=ExceptionPriority.LOW_PRIORITY
            )
            return ()

    active_image_ids: set[str] = set()
    for line in image_ids_result.stdout.strip().splitlines():
        line = line.strip()
        if line:
            active_image_ids.add(line)
    return tuple(active_image_ids)


class DeletionTier(Enum):
    # if an image is being used in multiple tasks, we take the lowest tier of the tasks

    # never delete: images on running containers or the latest image of a task
    NEVER_DELETE = 0
    # rarely delete: historical images on active tasks that are not being used by a running container
    RARELY_DELETE = 1
    # sometimes delete: historical images on archived tasks that are not being used by a running container
    SOMETIMES_DELETE = 2
    # always delete: images for deleted tasks
    ALWAYS_DELETE = 3


def _classify_image_tier(image_id: str, associated_task_metadata: TaskImageCleanupData) -> DeletionTier:
    if associated_task_metadata.is_deleted:
        return DeletionTier.ALWAYS_DELETE
    if image_id == associated_task_metadata.last_image_id:
        return DeletionTier.NEVER_DELETE
    if associated_task_metadata.is_archived:
        return DeletionTier.SOMETIMES_DELETE
    return DeletionTier.RARELY_DELETE


def _get_task_ids_by_image_id(
    task_metadata_by_task_id: Mapping[TaskID, TaskImageCleanupData],
) -> dict[str, list[TaskID]]:
    task_ids_by_image_id: dict[str, list[TaskID]] = dict()
    for task_id, task_metadata in task_metadata_by_task_id.items():
        for image_id in task_metadata.all_image_ids:
            task_ids_by_image_id.setdefault(image_id, []).append(task_id)
    return task_ids_by_image_id


def _get_tier_by_image_id(
    task_metadata_by_task_id: Mapping[TaskID, TaskImageCleanupData],
    active_image_ids: tuple[str, ...],
) -> dict[str, DeletionTier]:
    tier_by_image_id: dict[str, DeletionTier] = dict()
    task_ids_by_image_id = _get_task_ids_by_image_id(task_metadata_by_task_id)

    for image_id, task_ids in task_ids_by_image_id.items():
        if image_id in active_image_ids:
            logger.debug("Image {} is in active image IDs - never delete", image_id)
            tier_by_image_id[image_id] = DeletionTier.NEVER_DELETE
        else:
            tiers = []
            for task_id in task_ids:
                task_metadata = task_metadata_by_task_id[task_id]
                tiers.append(_classify_image_tier(image_id=image_id, associated_task_metadata=task_metadata))
            if any(tier == DeletionTier.NEVER_DELETE for tier in tiers):
                tier_by_image_id[image_id] = DeletionTier.NEVER_DELETE
            elif any(tier == DeletionTier.RARELY_DELETE for tier in tiers):
                tier_by_image_id[image_id] = DeletionTier.RARELY_DELETE
            elif any(tier == DeletionTier.SOMETIMES_DELETE for tier in tiers):
                tier_by_image_id[image_id] = DeletionTier.SOMETIMES_DELETE
            else:
                tier_by_image_id[image_id] = DeletionTier.ALWAYS_DELETE
            logger.debug("Image {} has been assigned tier {}", image_id, tier_by_image_id[image_id])
    return tier_by_image_id


def get_images_disk_usage_bytes(concurrency_group: ConcurrencyGroup) -> int | None:
    try:
        result = concurrency_group.run_process_to_completion(
            ["docker", "system", "df", "--format={{.Type}} {{.Size}}"],
        )
    except ProcessError:
        raise ProviderError("Failed to run docker system df")

    for line in result.stdout.strip().splitlines():
        if line.startswith("Images "):
            try:
                return humanfriendly.parse_size(line.split()[1])
            except InvalidSize:
                return None

    return None


def extend_image_ids_with_similar_hashes(image_ids: Sequence[str]) -> tuple[str, ...]:
    return tuple({*image_ids, *(image_id.split(":", 1)[-1] for image_id in image_ids if ":" in image_id)})


class ImageInfoPayload(PosthogEventPayload):
    snapshot_count: int = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Number of sculptor-created snapshot images"
    )
    total_snapshot_bytes: int = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS, description="Space used by sculptor-created snapshot images"
    )
    # TODO: add dangling_image_count, which uses garbage collection logic to find images not associatd with active tasks
    total_image_bytes: int | None = with_consent(
        ConsentLevel.PRODUCT_ANALYTICS,
        description="Space used by all docker images, not just sculptor-created images",
    )


def record_images_to_posthog(concurrency_group: ConcurrencyGroup, image_infos: Sequence[ImageInfo]) -> None:
    snapshot_image_infos = tuple(image_info for image_info in image_infos if image_info.category == "SNAPSHOT")
    with ObservableThreadPoolExecutor(
        concurrency_group, max_workers=16, thread_name_prefix="ImageInspector"
    ) as executor:
        snapshot_sizes = executor.map(
            lambda image_info: get_unique_snapshot_size_bytes(concurrency_group, image_info.id), snapshot_image_infos
        )
        total_image_bytes_future = executor.submit(get_images_disk_usage_bytes, concurrency_group)
        payload = ImageInfoPayload(
            total_snapshot_bytes=sum(snapshot_sizes),
            snapshot_count=len(snapshot_image_infos),
            total_image_bytes=total_image_bytes_future.result(),
        )
        emit_posthog_event(
            PosthogEventModel(
                name=SculptorPosthogEvent.IMAGE_INFORMATION,
                component=ProductComponent.CROSS_COMPONENT,
                payload=payload,
            )
        )


def calculate_image_ids_to_delete(
    task_metadata_by_task_id: Mapping[TaskID, TaskImageCleanupData],
    active_image_ids: tuple[str, ...],
    existing_image_ids: tuple[str, ...],
    minimum_deletion_tier: DeletionTier,
) -> tuple[str, ...]:
    tier_by_image_id = _get_tier_by_image_id(task_metadata_by_task_id, active_image_ids)
    image_ids = set()
    for image_id, tier in tier_by_image_id.items():
        if tier.value > minimum_deletion_tier.value and image_id in existing_image_ids:
            # only attempt to delete images that are above the minimum deletion tier and still exist in the system
            logger.debug("Adding image {} to deletion list", image_id)
            image_ids.add(image_id)
    return tuple(image_ids)


def get_platform_architecture() -> Literal["amd64", "arm64"]:
    """
    Determine the platform architecture for Docker images.

    Returns:
        Platform name ("amd64" or "arm64")

    Examples:
        >>> get_platform_architecture() in ["amd64", "arm64"]
        True
    """
    # TODO: Add unit test that exercises the docker info command path when docker is available
    # NOTE(bowei): use the docker info, in case somehow it's different from the host
    # Fall back to platform.machine() if docker is not available
    arch = platform.machine().lower()
    try:
        arch = run_blocking(["docker", "info", "--format", "{{.Architecture}}"]).stdout.strip() or arch
    except ProcessError:
        # Docker not available or command failed, use fallback
        pass

    if arch == "x86_64":
        return "amd64"
    elif arch == "aarch64" or arch == "arm64":
        return "arm64"
    else:
        logger.info(f"Unknown architecture {arch}, defaulting to amd64")
        return "amd64"
