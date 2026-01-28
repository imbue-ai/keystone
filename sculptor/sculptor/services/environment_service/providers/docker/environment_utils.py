import time
from concurrent.futures import Future
from pathlib import Path
from typing import Callable

from loguru import logger

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.common import generate_id
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.itertools import flatten
from imbue_core.progress_tracking.progress_tracking import ProgressHandle
from imbue_core.progress_tracking.progress_tracking import start_finish_context
from imbue_core.sculptor import telemetry
from imbue_core.sculptor.telemetry_constants import ProductComponent
from imbue_core.sculptor.telemetry_constants import SculptorPosthogEvent
from imbue_core.subprocess_utils import ProcessError
from imbue_core.subprocess_utils import ProcessSetupError
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.interfaces.environments.base import SSHD_SERVER_NAME
from sculptor.interfaces.environments.constants import AGENT_DATA_PATH
from sculptor.interfaces.environments.errors import EnvironmentAlreadyExistsError
from sculptor.interfaces.environments.errors import EnvironmentNotFoundError
from sculptor.interfaces.environments.errors import ImageNotFoundError
from sculptor.interfaces.environments.errors import ProviderError
from sculptor.interfaces.environments.errors import SetupError
from sculptor.interfaces.environments.provider_status import ProviderStatus
from sculptor.primitives.constants import USER_FACING_LOG_TYPE
from sculptor.primitives.executor import ObservableThreadPoolExecutor
from sculptor.primitives.ids import DockerContainerID
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.environments.docker_environment import remove_docker_container
from sculptor.services.environment_service.environments.docker_environment import stop_docker_container
from sculptor.services.environment_service.providers.docker.docker_host_config import get_docker_host
from sculptor.services.environment_service.providers.docker.errors import DockerError
from sculptor.services.environment_service.providers.docker.errors import DockerNotInstalledError
from sculptor.services.environment_service.providers.docker.errors import NoServerPortBoundError
from sculptor.services.environment_service.providers.docker.errors import ProviderIsDownError
from sculptor.services.environment_service.providers.docker.image_utils import read_on_create_command_from_container
from sculptor.services.environment_service.providers.docker.image_utils import (
    read_update_content_command_from_container,
)
from sculptor.services.environment_service.providers.docker.image_utils import run_on_create_command
from sculptor.services.environment_service.providers.docker.image_utils import run_update_content_command
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP,
)
from sculptor.services.environment_service.providers.docker.volume_mounted_nix_control_plane import (
    get_control_plane_volume_docker_args,
)
from sculptor.utils.build import get_sculptor_folder
from sculptor.utils.timeout import log_runtime_decorator


@log_runtime_decorator()
def build_docker_environment(
    docker_image: LocalDockerImage,
    config: LocalDockerEnvironmentConfig,
    concurrency_group: ConcurrencyGroup,
    task_id: TaskID | None = None,
    name: str | None = None,
    environment_prefix: str = "",
    provider_health_check: Callable[[], ProviderStatus] | None = None,
    shutdown_event: ReadOnlyEvent | None = None,
    container_setup_handle: ProgressHandle | None = None,
) -> tuple[DockerEnvironment, list[str]]:
    """Create a Docker container from an image.

    The container user will be detected by reading /imbue_addons/container_user.txt from the container,
    which is populated during image build from devcontainer.json's containerUser field or the base
    image's default user.
    """

    control_plane_already_downloaded = CONTROL_PLANE_FETCH_BACKGROUND_SETUP.is_finished()
    # This needs to happen whether we are building a new image or starting an existing one.
    CONTROL_PLANE_FETCH_BACKGROUND_SETUP.ensure_finished(concurrency_group)

    event_name = (
        SculptorPosthogEvent.ENVIRONMENT_SETUP_DOCKER_CONTROL_PLANE_ALREADY_DOWNLOADED
        if control_plane_already_downloaded
        else SculptorPosthogEvent.ENVIRONMENT_SETUP_DOCKER_CONTROL_PLANE_DOWNLOAD_FINISHED
    )
    if task_id is not None:
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=event_name,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=str(task_id),
            )
        )

    # Generate container name if not provided
    if name is None:
        name = generate_id()
    name = environment_prefix + name

    create_command = get_base_docker_create_args(name, config.server_port_by_name, docker_image.forward_ports)

    if container_setup_handle is None:
        container_setup_handle = ProgressHandle()

    with start_finish_context(
        container_setup_handle.track_subtask("Creating docker container")
    ) as create_container_handle:
        container_id = create_docker_container(
            create_command + [docker_image.image_id],
            docker_image,
            name,
            concurrency_group,
            shutdown_event,
            create_container_handle,
        )
    if task_id is not None:
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_DOCKER_CONTAINER_CREATED,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=str(task_id),
            )
        )

    # It's possible this image was created by an older version of Sculptor.
    with start_finish_context(
        container_setup_handle.track_subtask("Upgrading docker container")
    ) as upgrade_container_handle:
        container_user, container_user_home = upgrade_container_and_read_user_and_home(
            container_id, concurrency_group, shutdown_event, upgrade_container_handle
        )

    # Run onCreateCommand if present in the image
    # See: https://containers.dev/implementors/json_reference/#lifecycle-scripts
    with start_finish_context(
        container_setup_handle.track_subtask("Running onCreateCommand")
    ) as on_create_command_handle:
        on_create_command = read_on_create_command_from_container(container_id, concurrency_group)
        if on_create_command:
            logger.info("Running onCreateCommand from devcontainer.json")
            run_on_create_command(
                on_create_command=on_create_command,
                container_id=container_id,
                concurrency_group=concurrency_group,
                container_user=container_user,
                workspace_path="/code",  # TODO: Update to use workspace_folder from devcontainer.json
                shutdown_event=shutdown_event,
                progress_handle=on_create_command_handle,
            )

    # Run updateContentCommand after onCreateCommand
    # See: https://containers.dev/implementors/json_reference/#lifecycle-scripts
    # Although the spec formally says that we should only run updateContentCommand after content
    # has been added, we intentionally run updateContentCommand after onCreateCommand.
    with start_finish_context(
        container_setup_handle.track_subtask("Running updateContentCommand")
    ) as update_content_command_handle:
        update_content_command = read_update_content_command_from_container(container_id, concurrency_group)
        if update_content_command:
            logger.info("Running updateContentCommand from devcontainer.json")
            run_update_content_command(
                update_content_command=update_content_command,
                container_id=container_id,
                concurrency_group=concurrency_group,
                container_user=container_user,
                workspace_path="/code",  # TODO: Update to use workspace_folder from devcontainer.json
                shutdown_event=shutdown_event,
                progress_handle=update_content_command_handle,
            )

    # Now retrieve the port that each server is mapped to
    with start_finish_context(container_setup_handle.track_subtask("Finding ports")) as port_finding_handle:
        external_port_by_name = get_external_port_by_name_mapping(
            container_id, config.server_port_by_name, concurrency_group, port_finding_handle
        )

    environment = DockerEnvironment(
        config=config,
        environment_id=DockerContainerID(container_id),
        server_port_by_name=external_port_by_name,
        concurrency_group=concurrency_group,
        _provider_health_check=provider_health_check,
        environment_prefix=environment_prefix,
        project_id=docker_image.project_id,
        container_user=container_user,
        container_user_home=container_user_home,
    )

    try:
        with start_finish_context(
            container_setup_handle.track_subtask("Setting up container for Sculptor")
        ) as setup_container_handle:
            setup_docker_environment(environment, shutdown_event, setup_container_handle)
    except (SetupError, CancelledByEventError):
        environment.close()
        raise
    if task_id is not None:
        telemetry.emit_posthog_event(
            telemetry.PosthogEventModel(
                name=SculptorPosthogEvent.ENVIRONMENT_SETUP_DOCKER_CONTAINER_FINISHED_SETUP,
                component=ProductComponent.ENVIRONMENT_SETUP,
                task_id=str(task_id),
            )
        )
    return environment, create_command


def setup_docker_environment(
    environment: DockerEnvironment,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> None:
    """Starts processes that we need to be running inside the container."""
    if progress_handle is None:
        progress_handle = ProgressHandle()

    with logger.contextualize(log_type=USER_FACING_LOG_TYPE):
        logger.info("Created Docker sandbox {container_id}", container_id=environment.container_id)
        run_script_in_container(
            environment.container_id,
            environment.concurrency_group,
            "/imbue_addons/imbue_post_container_build.sh",
            shutdown_event,
            progress_handle,
        )

    sshd_log_file = "/tmp/sshd_log.txt"
    sshd_process = environment.run_process_in_background(
        ["/imbue/nix_bin/sshd", "-f", "/sshd_config/sshd_config", "-D", "-E", sshd_log_file], {}, run_as_root=True
    )
    # TODO: Maybe don't wait forever?
    logger.info("Waiting for sshd to start...")
    with start_finish_context(progress_handle.track_subtask("Waiting for sshd to start")):
        while True:
            result = environment.concurrency_group.run_process_to_completion(
                command=[
                    *("docker", "exec"),
                    *("--user", "root"),
                    environment.container_id,
                    *("test", "-e", sshd_log_file),
                ],
                is_checked_after=False,
            )
            if result.returncode == 0:
                break
            if shutdown_event is not None and shutdown_event.is_set():
                raise CancelledByEventError()
            time.sleep(0.1)

    result = environment.concurrency_group.run_process_to_completion(
        command=[
            *("docker", "exec"),
            *("--user", "root"),
            environment.container_id,
            *("cat", sshd_log_file),
        ],
    )
    sshd_log = result.stdout
    sshd_stderr = sshd_process.read_stderr()
    if ("Server listening on" not in sshd_log) or sshd_stderr != "":
        raise SetupError(f"Immediate sshd startup check failed! log: {sshd_log} stderr: {sshd_stderr}")
    environment.register_healthcheck(
        lambda: None if not sshd_process.is_finished() else f"sshd process exited: {sshd_process.returncode}"
    )

    assert _wait_for_ssh_connectivity_or_error(environment)


def _wait_for_ssh_connectivity_or_error(
    environment: DockerEnvironment, delay_seconds: float = 1.0, timeout_seconds: float = 10
) -> bool:
    """
    Wait for actual SSH connectivity.

    Some container environments observed in the wild (like Colima) may introduce a delay before the SSH port is properly forwarded.

    Return True if connectivity is verified, raises SetupError otherwise.

    """
    ssh_port = environment.server_port_by_name.get(SSHD_SERVER_NAME)
    sshd_hostname = get_docker_host()

    if ssh_port is None:
        logger.info("No SSH port found in server_port_by_name, by {}", SSHD_SERVER_NAME)
        return False
    ssh_path = get_sculptor_folder() / "ssh" / "ssh"
    container_user = environment.get_container_user()
    start_time = time.monotonic()
    attempt = 0
    command = None
    while time.monotonic() - start_time < timeout_seconds and not environment.concurrency_group.is_shutting_down():
        attempt += 1
        logger.info("Verifying that SSH port forwarding is ready (attempt {})...", attempt)
        result = environment.concurrency_group.run_process_to_completion(
            command=[
                str(ssh_path),
                "-p",
                str(ssh_port),
                "-o",
                "ConnectTimeout=2",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "UserKnownHostsFile=/dev/null",
                "-o",
                "BatchMode=yes",
                f"{container_user}@{sshd_hostname}",
                "true",  # Simple command that returns success
            ],
            is_checked_after=False,
            timeout=4,
        )
        if result.returncode == 0:
            logger.info("SSH connectivity verified.")
            return True
        time.sleep(delay_seconds)

    logger.info("Failed to establish SSH connectivity after {} attempts. command={}", attempt, command)

    raise SetupError(
        f"Could not verify SSH connectivity to Docker container after multiple attempts. command={command}"
    )


def get_external_port_by_name_mapping(
    container_id: DockerContainerID,
    internal_port_by_server_name: dict[str, int],
    concurrency_group: ConcurrencyGroup,
    progress_handle: ProgressHandle | None = None,
) -> dict[str, int]:
    external_port_by_name = {}
    for server_name, internal_port in internal_port_by_server_name.items():
        try:
            external_port = _attempt_to_get_mapped_port(
                server_name, internal_port, container_id, concurrency_group, progress_handle
            )
        except NoServerPortBoundError as e:
            log_exception(
                e,
                "Failed to get mapped port for server",
                priority=ExceptionPriority.MEDIUM_PRIORITY,
                extra=dict(server_name=server_name, internal_port=internal_port, container_id=container_id),
            )
            # note that we simply continue in this case, per note c70ca82b-f7b2-4beb-b2b4-0db777ad369b
            # the container will be brought online without the requested port.
        else:
            external_port_by_name[server_name] = external_port

    return external_port_by_name


def create_docker_container(
    create_command: list[str],
    docker_image: LocalDockerImage,
    name: str,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> DockerContainerID:
    if progress_handle is None:
        progress_handle = ProgressHandle()

    with logger.contextualize(log_type=USER_FACING_LOG_TYPE):
        logger.info("Creating Docker container {}", name)
        logger.info("create_command: {}", create_command)
        try:
            try:
                create_container_result = concurrency_group.run_process_to_completion(
                    command=create_command, shutdown_event=shutdown_event
                )
            except ProcessError as e:
                if shutdown_event is not None and shutdown_event.is_set():
                    raise CancelledByEventError() from e
                # sigh, have to handle the case where we try to start something with the same name
                # we have to be careful about how we detect this
                # because some strings are different in different docker versions and on different operating systems
                if "is already in use by container" in e.stderr and name in e.stderr:
                    logger.debug("Container name conflict, removing existing container and retrying: {}", name)
                    concurrency_group.run_process_to_completion(("docker", "rm", "-f", name))
                    try:
                        concurrency_group.run_process_to_completion(
                            ("docker", "rm", "-f", name),
                            shutdown_event=shutdown_event,
                            progress_handle=progress_handle,
                        )
                        create_container_result = concurrency_group.run_process_to_completion(
                            command=create_command,
                            shutdown_event=shutdown_event,
                            progress_handle=progress_handle,
                        )
                    except ProcessError as e:
                        if shutdown_event is not None and shutdown_event.is_set():
                            raise CancelledByEventError() from e
                        raise
                else:
                    raise
        except ProcessError as e:
            stdout = e.stdout
            stderr = e.stderr
            if "Unable to find image" in stderr:
                raise ImageNotFoundError(
                    f"Docker image {docker_image.image_id} not found - exit code {e.returncode}: {stderr} {stdout}"
                ) from e
            if "Error response from daemon: Conflict. The container name " in stderr:
                # this should almost never happen anymore, since we are deleting on conflict above
                raise EnvironmentAlreadyExistsError(
                    f"Docker container {name} already exists - exit code {e.returncode}: {stderr} {stdout}"
                ) from e
            raise ProviderError(f"Docker run failed with exit code {e.returncode}: {e.stderr} {e.stdout}") from e
    return DockerContainerID(create_container_result.stdout.strip())


def start_docker_container(container_id: DockerContainerID, concurrency_group: ConcurrencyGroup) -> None:
    with logger.contextualize(log_type=USER_FACING_LOG_TYPE):
        logger.info("Starting Docker container {}", container_id)
        try:
            concurrency_group.run_process_to_completion(command=["docker", "start", str(container_id)])
        except ProcessError as e:
            stdout = e.stdout
            stderr = e.stderr
            if stderr.startswith("Error response from daemon: No such container:"):
                raise EnvironmentNotFoundError(
                    f"Docker container {container_id} not found - exit code {e.returncode}: {stderr} {stdout}"
                ) from e
            raise ProviderError(f"Docker start failed with exit code {e.returncode}: {e.stderr} {e.stdout}") from e


def get_base_docker_create_args(
    name: str, internal_port_by_server_name: dict[str, int], forward_ports: list[int] | None = None
) -> list[str]:
    docker_daemon_host = get_docker_host()

    # Validate server ports
    for server_name, port in internal_port_by_server_name.items():
        if not isinstance(port, int) or port < 1 or port > 65535:
            logger.error("Invalid server port for {}: {}", server_name, port)
            raise ValueError(f"Port {port} for {server_name} must be an integer in range [1-65535]")

    port_args = flatten([("--publish", f"{docker_daemon_host}::{x}") for x in internal_port_by_server_name.values()])

    # Add forward_ports from devcontainer.json if available
    # Ports are validated in devcontainer_image_builder.py before being passed here
    if forward_ports:
        logger.info("Adding forwardPorts from devcontainer.json: {}", forward_ports)
        forward_port_args = flatten([("--publish", f"{docker_daemon_host}::{port}") for port in forward_ports])
        port_args.extend(forward_port_args)

    # Create and start the container
    create_command = [
        *("docker", "run", "-td"),  # Detached mode
        *("--name", name),
        *("-v", f"checks_volume:{AGENT_DATA_PATH}"),
        # Don't specify --user here - let the container boot with its default user
        # (from Dockerfile USER directive or devcontainer containerUser).
        # We'll detect the actual user after the container starts using whoami.
        # NOTE(bowei): sourced from https://github.com/anthropics/claude-code/blob/main/.devcontainer/Dockerfile
        *("-e", "NODE_OPTIONS=--max-old-space-size=4096"),
        *("-e", "POWERLEVEL9K_DISABLE_GITSTATUS=true"),
        # Let docker find an available ports for anything we want mapped
        *port_args,
        # Mounts the imbue control plane as RO volumes.
        # TODO: What is the right place to put this concern?
        *get_control_plane_volume_docker_args(),
    ]
    return create_command


def destroy_outdated_docker_containers(
    container_name_predicate: Callable[[str], bool],
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent | None = None,
) -> tuple[DockerContainerID, ...]:
    return _handle_outdated_docker_containers(
        container_name_predicate=container_name_predicate,
        is_stopped=False,
        concurrency_group=concurrency_group,
        shutdown_event=shutdown_event,
    )


def destroy_outdated_docker_images(
    repository_and_tag_predicate: Callable[[str], bool], concurrency_group: ConcurrencyGroup
) -> None:
    images = concurrency_group.run_process_to_completion(
        ["docker", "images", "--format", "{{.Repository}}:{{.Tag}}"], is_checked_after=False
    ).stdout.splitlines()

    targets = [img for img in images if repository_and_tag_predicate(img)]

    if targets:
        concurrency_group.run_process_to_completion(command=["docker", "rmi", *targets], is_checked_after=False)


def stop_outdated_docker_containers(
    container_name_predicate: Callable[[str], bool], concurrency_group: ConcurrencyGroup, is_killing: bool = False
) -> None:
    _handle_outdated_docker_containers(
        container_name_predicate=container_name_predicate,
        is_stopped=True,
        concurrency_group=concurrency_group,
        is_killing=is_killing,
    )


def _handle_outdated_docker_containers(
    container_name_predicate: Callable[[str], bool],
    is_stopped: bool,
    concurrency_group: ConcurrencyGroup,
    is_killing: bool = False,
    shutdown_event: ReadOnlyEvent | None = None,
) -> tuple[DockerContainerID, ...]:
    try:
        result = concurrency_group.run_process_to_completion(
            # when we are just stopping containers, don't bother trying to stop those that aren't even running!
            command=["docker", "ps", *([] if is_stopped else ["-a"]), "--format", "{{.ID}} {{.Names}}"],
            timeout=30.0,
            shutdown_event=shutdown_event,
        )
    except ProcessSetupError as e:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError() from e
        if e.__cause__ and "No such file or directory: 'docker'" in str(e.__cause__):
            raise DockerNotInstalledError("Docker does not exist or is not installed.") from e
        else:
            raise DockerError("Docker failed to list existing containers before even running") from e
    except ProcessError as e:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError() from e
        raise DockerError("Docker failed to list existing containers") from e
    else:
        with concurrency_group.make_concurrency_group("docker_container_stopper") as cg:
            with ObservableThreadPoolExecutor(
                cg, max_workers=10, thread_name_prefix="DockerContainerStopper"
            ) as executor:
                futures_with_container_ids: list[tuple[Future, DockerContainerID]] = []
                for line in result.stdout.splitlines():
                    if shutdown_event is not None and shutdown_event.is_set():
                        raise CancelledByEventError()
                    container_id, container_name = line.strip().split(maxsplit=1)
                    if container_name_predicate(container_name):
                        if is_stopped:
                            futures_with_container_ids.append(
                                (
                                    executor.submit(
                                        _stop_docker_container_and_ignore_if_docker_is_down,
                                        container_id=container_id,
                                        concurrency_group=cg,
                                        is_killing=is_killing,
                                    ),
                                    DockerContainerID(container_id),
                                )
                            )
                        else:
                            futures_with_container_ids.append(
                                (
                                    executor.submit(
                                        remove_docker_container,
                                        container_id=container_id,
                                        concurrency_group=cg,
                                        shutdown_event=shutdown_event,
                                    ),
                                    DockerContainerID(container_id),
                                )
                            )
                exceptions = []
                for future, container_id in futures_with_container_ids:
                    maybe_exception = future.exception()
                    if maybe_exception is not None:
                        exceptions.append((container_id, maybe_exception))
                if exceptions:
                    raise exceptions[0]
        return tuple(container_id for _, container_id in futures_with_container_ids)


def _stop_docker_container_and_ignore_if_docker_is_down(
    container_id: str,
    concurrency_group: ConcurrencyGroup,
    is_killing: bool = False,
    shutdown_event: ReadOnlyEvent | None = None,
) -> None:
    try:
        stop_docker_container(
            container_id=container_id,
            concurrency_group=concurrency_group,
            is_killing=is_killing,
            shutdown_event=shutdown_event,
        )
    except ProviderIsDownError:
        pass


def _attempt_to_get_mapped_port(
    server_name: str,
    internal_port: int,
    container_id: str,
    concurrency_group: ConcurrencyGroup,
    progress_handle: ProgressHandle | None = None,
) -> int:
    """
    Returns the external port mapped to the internal port of a Docker container.

    Raises
        NoServerPortBoundError: If the port is not bound after several retries.
    """
    if progress_handle is None:
        progress_handle = ProgressHandle()

    max_retries = 10
    for _ in range(max_retries):
        # Retry to handle race condition: Docker may need time to establish port mapping
        # after container creation before NetworkSettings.Ports is populated
        try:
            result = concurrency_group.run_process_to_completion(
                command=[
                    "docker",
                    "inspect",
                    "-f",
                    (
                        '{{ if index .NetworkSettings.Ports "'
                        + str(internal_port)
                        + '/tcp" }}{{ if index (index .NetworkSettings.Ports "'
                        + str(internal_port)
                        + '/tcp") 0 }}{{ (index (index .NetworkSettings.Ports "'
                        + str(internal_port)
                        + '/tcp") 0).HostPort }}{{ end }}{{ end }}'
                    ),
                    container_id,
                ],
                progress_handle=progress_handle,
            )
        except ProcessError:
            # Docker inspect failed, treat as port not available
            result = None

        stdout = result.stdout.strip() if result is not None else ""
        if result and stdout:
            external_port = int(stdout)
            logger.info("{} port for container {} is {}", server_name, container_id, external_port)
            return external_port
        time.sleep(0.1)
    raise NoServerPortBoundError(
        "Failed to get mapped port for {}, port {}, container ID {} after {} retries".format(
            server_name, internal_port, container_id, max_retries
        )
    )


def read_container_file(
    concurrency_group: ConcurrencyGroup,
    container_id: str,
    file_path: str,
    default: str,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> str:
    """
    Read a file from the container and return its contents, or a default value on failure.

    Args:
        file_path: Absolute path to the file inside the container
        default: Value to return if the file cannot be read

    Returns:
        The stripped contents of the file, or the default value
    """
    if progress_handle is None:
        progress_handle = ProgressHandle()
    try:
        command = ["docker", "exec", container_id, "cat", file_path]
        result = concurrency_group.run_process_to_completion(
            command=command, shutdown_event=shutdown_event, progress_handle=progress_handle
        )
        return result.stdout.strip()
    except ProcessError as e:
        logger.warning(
            "Could not read {} from container {}, defaulting to {}. Error: {}",
            file_path,
            container_id,
            default,
            e.stderr,
        )
        return default


def upgrade_container_and_read_user_and_home(
    container_id: DockerContainerID,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> tuple[str, Path]:
    """
    Upgrade container and read user and home directory information.

    Returns:
        Tuple of (container_user, container_user_home_path)
    """
    copy_and_run_upgrade_script_in_container(container_id, concurrency_group, shutdown_event, progress_handle)

    container_user = read_container_file(
        concurrency_group,
        container_id,
        "/imbue_addons/container_user.txt",
        "sculptoruser",
        shutdown_event,
        progress_handle,
    )
    container_user_home = read_container_file(
        concurrency_group,
        container_id,
        "/imbue_addons/container_user_home.txt",
        "/home/sculptoruser",
        shutdown_event,
        progress_handle,
    )

    return container_user, Path(container_user_home)


def copy_and_run_upgrade_script_in_container(
    container_id: DockerContainerID,
    concurrency_group: ConcurrencyGroup,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> None:
    """Copy the upgrade script into the container and run it."""
    upgrade_script_path = Path(__file__).parent / "imbue_addons" / "imbue_upgrade_container.sh"
    docker_cp_command = [
        *("docker", "cp", str(upgrade_script_path)),
        f"{container_id}:/imbue_addons/imbue_upgrade_container.sh",
    ]
    if progress_handle is None:
        progress_handle = ProgressHandle()
    try:
        concurrency_group.run_process_to_completion(
            command=docker_cp_command,
            shutdown_event=shutdown_event,
            progress_handle=progress_handle,
        )
    except ProcessError as e:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError() from e
        raise SetupError(
            f"Failed to copy upgrade script into container:\nstderr:\n{e.stderr}\nstdout:\n{e.stdout}"
        ) from e

    run_script_in_container(
        container_id, concurrency_group, "/imbue_addons/imbue_upgrade_container.sh", shutdown_event, progress_handle
    )


def run_script_in_container(
    container_id: DockerContainerID,
    concurrency_group: ConcurrencyGroup,
    script_command: str,
    shutdown_event: ReadOnlyEvent | None = None,
    progress_handle: ProgressHandle | None = None,
) -> None:
    """Run a script command in the container as root."""
    docker_exec_command = [
        *("docker", "exec"),
        *("--user", "root"),
        container_id,
        *("bash", "-c"),
        script_command,
    ]
    try:
        if progress_handle is None:
            progress_handle = ProgressHandle()
        concurrency_group.run_process_to_completion(
            command=docker_exec_command,
            on_output=lambda line, is_stderr: logger.debug(line.strip()),
            trace_log_context={
                "log_type": USER_FACING_LOG_TYPE,
            },
            shutdown_event=shutdown_event,
        )
    except ProcessError as e:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError() from e
        raise SetupError(f"Failed to run script in container:\nstderr:\n{e.stderr}\nstdout:\n{e.stdout}") from e
