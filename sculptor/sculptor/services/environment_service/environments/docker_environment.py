import os
import shlex
import subprocess
import time
from pathlib import Path
from queue import Queue
from shlex import quote
from typing import Callable
from typing import Mapping
from typing import Sequence
from typing import TYPE_CHECKING
from typing import Union

from loguru import logger
from pydantic import AnyUrl
from pydantic import PrivateAttr

from imbue_core.agents.data_types.ids import TaskID
from imbue_core.async_monkey_patches import log_exception
from imbue_core.common import generate_id
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.constants import ExceptionPriority
from imbue_core.event_utils import CancelledByEventError
from imbue_core.event_utils import MutableEvent
from imbue_core.event_utils import ReadOnlyEvent
from imbue_core.nested_evolver import assign
from imbue_core.nested_evolver import chill
from imbue_core.nested_evolver import evolver
from imbue_core.processes.local_process import RunningProcess
from imbue_core.processes.local_process import run_background
from imbue_core.sculptor.user_config import UserConfig
from imbue_core.secrets_utils import Secret
from imbue_core.subprocess_utils import ProcessError
from sculptor.config.settings import SculptorSettings
from sculptor.interfaces.environments.base import Environment
from sculptor.interfaces.environments.base import EnvironmentRestartRequired
from sculptor.interfaces.environments.base import LocalDockerEnvironmentConfig
from sculptor.interfaces.environments.base import LocalDockerImage
from sculptor.interfaces.environments.base import ProviderTag
from sculptor.interfaces.environments.base import SSHD_SERVER_NAME
from sculptor.interfaces.environments.constants import ENVIRONMENT_WORKSPACE_DIRECTORY
from sculptor.interfaces.environments.errors import EnvironmentFailure
from sculptor.interfaces.environments.errors import EnvironmentNotHealthy
from sculptor.interfaces.environments.errors import FileNotFoundEnvironmentError
from sculptor.interfaces.environments.errors import FileOrDirectoryCouldNotBeDeletedError
from sculptor.interfaces.environments.errors import IsADirectoryEnvironmentError
from sculptor.interfaces.environments.errors import ProviderError
from sculptor.interfaces.environments.provider_status import OkStatus
from sculptor.primitives.ids import DockerContainerID
from sculptor.primitives.ids import DockerImageID
from sculptor.services.environment_service.environments.image_tags import DockerImageMetadata
from sculptor.services.environment_service.environments.image_tags import ImageMetadataV0
from sculptor.services.environment_service.environments.image_tags import SNAPSHOT_SUFFIX
from sculptor.services.environment_service.environments.image_tags import get_latest_v1_image_metadata_for_task
from sculptor.services.environment_service.environments.image_tags import get_tagged_reference
from sculptor.services.environment_service.environments.utils import check_provider_health_on_failure
from sculptor.services.environment_service.environments.utils import get_docker_status
from sculptor.services.environment_service.providers.docker.docker_host_config import get_docker_host
from sculptor.services.environment_service.providers.docker.errors import ContainerNotRunningError
from sculptor.services.environment_service.providers.docker.errors import ContainerPausedError
from sculptor.services.environment_service.providers.docker.errors import ProviderIsDownError
from sculptor.services.git_repo_service.git_repos import WritableGitRepo
from sculptor.utils.disk_usage import report_snapshot_to_posthog
from sculptor.utils.shared_exclusive_lock import SharedExclusiveLock
from sculptor.utils.timeout import log_runtime

# https://github.com/python/typeshed/tree/main/stdlib/_typeshed
if TYPE_CHECKING:
    # for proper file mode typing
    from _typeshed import OpenBinaryModeReading
    from _typeshed import OpenTextModeReading


class DockerRunningProcess(RunningProcess):
    def __init__(
        self,
        command: Sequence[str],
        output_queue: Queue[tuple[str, bool]],
        shutdown_event: MutableEvent,
        tag: str,
        container_id: str,
        concurrency_group: ConcurrencyGroup,
        is_checked: bool = False,
        on_output: Callable[[str, bool], None] | None = None,
    ) -> None:
        super().__init__(command, output_queue, shutdown_event, is_checked)
        self.tag = tag
        self.container_id = container_id
        self.concurrency_group = concurrency_group
        self.inner_pid: int | None = None
        self._bad_first_line: str | None = None
        self._on_output = on_output if on_output is not None else lambda line, is_stdout: None

    def wait_until_started(self) -> None:
        while self.inner_pid is None and self._bad_first_line is None:
            if self._completed_process is not None:
                return
            time.sleep(0.01)

        if self.inner_pid is not None:
            return

        line = self._bad_first_line
        # we stay in the while loop until one of _inner_pid and _bad_first_line is set.
        # we just saw that _inner_pid was not set (we're past the if statement that matches if it is set),
        # so _bad_first_line must be set.
        # (this all assumes that, once set, _inner_pid and _bad_first_line cannot be unset back to None)
        assert line is not None

        # 'Error response from daemon: Container 6c68cfb608213a41c64810dda5c019dc57deece848f340546ec7eafffabe294c is paused, unpause the container before exec
        if line.startswith("Error response from daemon:") and line.rstrip().endswith(
            "is paused, unpause the container before exec"
        ):
            raise ContainerPausedError(f"Container {self.container_id} is not running (paused)")
        # Error response from daemon: container b705de75d78f697169502233f9b56f3c4162253e790cbbc71902ebec1aa8b7a3 is not running
        elif line.startswith("Error response from daemon:") and line.rstrip().endswith("is not running"):
            raise ContainerNotRunningError(f"Container {self.container_id} is not running")
        stdout = self.read_stdout()
        stderr = self.read_stderr()
        raise ProviderError(
            f"Unexpected first line from stderr - this usually indicates that something is wrong with docker {self._command}:\nstdout: {stdout}\nstderr: {line.strip()}\n{stderr}",
        )

    def on_line(self, line: str, is_stdout: bool) -> None:
        if not is_stdout and self.inner_pid is None:
            # Parse the PID from the format: "PID:SCULPTOR_PROCESS_TAG=tag"
            if f":SCULPTOR_PROCESS_TAG={self.tag}" in line:
                pid_str = line.strip().split(":")[0]
                self.inner_pid = int(pid_str)
                logger.trace("Discovered PID {} for process with tag {}", self.inner_pid, self.tag)
                return
            else:
                self._bad_first_line = line
                self._on_output(line, is_stdout)
                return
        super().on_line(line, is_stdout)

    def terminate(self, force_kill_seconds: float = 5.0) -> None:
        if self.inner_pid is not None:
            try:
                # Kill the process group (negative PID) to include all child processes
                # To use negative PID, we need to run as root
                self.concurrency_group.run_process_to_completion(
                    command=[
                        "docker",
                        "exec",
                        "--user",
                        "root",
                        self.container_id,
                        "bash",
                        "-c",
                        f"kill -TERM -{self.inner_pid} && if kill -0 -{self.inner_pid} 2>/dev/null; then tail --pid={self.inner_pid} -f /dev/null; fi",
                    ],
                    timeout=force_kill_seconds,
                )
            except ProcessError:
                # Force kill if SIGTERM didn't work
                try:
                    self.concurrency_group.run_process_to_completion(
                        command=[
                            "docker",
                            "exec",
                            "--user",
                            "root",
                            self.container_id,
                            "kill",
                            "-9",
                            f"-{self.inner_pid}",
                        ],
                    )
                except ProcessError as e:
                    error_msg_lower = e.stderr.lower()
                    if "no such process" in error_msg_lower:
                        logger.debug("Process {} already gone", self.inner_pid)
                    elif "cannot connect to the docker daemon" in error_msg_lower and "unix:///" in error_msg_lower:
                        # if we're running docker locally and can't connect to it, it's probably not running and so we have succeeded.
                        # however, if we're running docker remotely, it may be that the connection failed but docker is not down.
                        # so, we want this case to only match local docker. most instances have "unix:///",
                        # but other paths indicative of local docker can be added here.
                        logger.debug("Process {} already gone because Docker daemon is not running", self.inner_pid)
                    elif "no such container" in error_msg_lower:
                        logger.debug("Process {} already gone because container does not exist", self.inner_pid)
                    elif "container" in error_msg_lower and "is not running" in error_msg_lower:
                        logger.debug("Process {} already gone because container is not running", self.inner_pid)
                    else:
                        log_exception(e, "Failed to force kill process", priority=ExceptionPriority.LOW_PRIORITY)

        super().terminate(force_kill_seconds)


class DockerEnvironment(Environment):
    object_type: str = "DockerEnvironment"
    environment_id: DockerContainerID
    server_port_by_name: dict[str, int]
    config: LocalDockerEnvironmentConfig

    # TODO: Document what this prefix is used for.  Is it for container names?
    environment_prefix: str = ""

    # See: https://containers.dev/implementors/spec/#users
    # TODO: it's unclear to me whether these need to actually be serialized.
    # And specifically, if we'd ever deserialize an object that was missing these and need to provide these defaults.
    # The defaults provided here are leftover from the days when Sculptor added this user into the container and always ran as it.
    container_user: str
    container_user_home: Path

    _snapshot_guard: SharedExclusiveLock = PrivateAttr(default_factory=SharedExclusiveLock)

    @property
    def container_id(self) -> DockerContainerID:
        return self.environment_id

    def get_container_user(self) -> str:
        """
        Get the user to use for operations inside the container.

        See: https://containers.dev/implementors/spec/#users
        """
        return self.container_user

    def get_container_user_home_directory(self) -> Path:
        """
        Get the home directory of the container user.

        Reads from /imbue_addons/container_user_home.txt which is created during image build
        by imbue_image_build.sh and contains the home directory path from /etc/passwd.
        """
        return self.container_user_home

    def get_repo_url(self) -> AnyUrl:
        remote_repo_host = get_docker_host()
        return AnyUrl(
            f"ssh://{self.get_container_user()}@{remote_repo_host}:{self.server_port_by_name[SSHD_SERVER_NAME]}{ENVIRONMENT_WORKSPACE_DIRECTORY}"
        )

    def get_snapshot_guard(self) -> SharedExclusiveLock:
        return self._snapshot_guard

    def push_into_environment_repo(
        self, user_repo: WritableGitRepo, src_branch_name: str, dst_branch_name: str
    ) -> None:
        with self._snapshot_guard.shared_lock():
            user_repo.push_ref_to_remote(
                remote=str(self.get_repo_url()),
                local_ref=f"refs/heads/{src_branch_name}",
                remote_ref=f"refs/heads/{dst_branch_name}",
                is_forced=True,
            )

    def get_repo_url_for_mutagen(self) -> str:
        remote_repo_host = get_docker_host()
        return f"{self.get_container_user()}@{remote_repo_host}:{self.server_port_by_name[SSHD_SERVER_NAME]}:{ENVIRONMENT_WORKSPACE_DIRECTORY}"

    def get_config(self) -> LocalDockerEnvironmentConfig:
        return self.config

    def get_file_mtime(self, path: str) -> float:
        with self._snapshot_guard.shared_lock():
            try:
                # Performing read operation as root.
                result = self.concurrency_group.run_process_to_completion(
                    command=["docker", "exec", "--user", "root", self.container_id, "stat", "-c", "%Y", path],
                )
            except ProcessError as e:
                if "no such file or directory" in e.stderr.lower():
                    raise FileNotFoundEnvironmentError(f"Failed to get mtime for file {path}: {e.stderr}") from e
                else:
                    raise
        return float(result.stdout.strip())

    def get_extra_logger_context(self) -> Mapping[str, str | float | int | bool | None]:
        return {"container_id": self.container_id, "provider": ProviderTag.DOCKER}

    def _assemble_docker_exec_args(
        self,
        command: Sequence[str],
        cwd: str | None,
        secrets: Mapping[str, str | Secret],
        is_interactive: bool,
        run_as_root: bool,
        run_with_sudo_privileges: bool,
    ) -> tuple[list[str], str]:
        # TODO: Thad thinks run_with_sudo_privileges should just be a synonym for run_as_root, and we give the user the option to drop those privileges.
        # note -- we used to have -it here instead of -i, but it seems to be working fine with just -i
        #  and -t ends up causing issues with logging (the lines don't properly flush)
        docker_command = [
            "docker",
            "exec",
            # When running with sudo privileges, we need -u root in order to run setpriv later
            *("-u", "root" if run_with_sudo_privileges or run_as_root else self.get_container_user()),
            *(["-i"] if is_interactive else []),
        ]
        for key in secrets:
            docker_command.extend(["-e", f"{key}"])
        if cwd:
            docker_command.extend(["-w", cwd])
        else:
            docker_command.extend(["-w", str(self.get_workspace_path())])
        tag = generate_id()
        # Wrap command to echo PID with tag to stderr first
        wrapped_command = (
            ["setpriv", f"--reuid={os.getuid()}", f"--regid={os.getgid()}", "--groups", "sculptoradmin"]
            if run_with_sudo_privileges
            else []
        ) + [
            *("sh", "-c", f'echo "$$:SCULPTOR_PROCESS_TAG={tag}" >&2 && exec "$@"'),
            "--",  # This is $0 for the shell
            *command,
        ]
        docker_command.extend([self.container_id, *wrapped_command])
        return docker_command, tag

    @check_provider_health_on_failure
    def _run_process_in_background(
        self,
        command: Sequence[str],
        secrets: Mapping[str, str | Secret],
        cwd: str | None = None,
        is_interactive: bool = False,
        run_with_sudo_privileges: bool = False,
        run_as_root: bool = False,
        shutdown_event: MutableEvent | None = None,
        timeout: float | None = None,
        is_checked: bool = False,
        on_output: Callable[[str, bool], None] | None = None,
    ) -> RunningProcess:
        docker_command, tag = self._assemble_docker_exec_args(
            command, cwd, secrets, is_interactive, run_as_root, run_with_sudo_privileges
        )
        with self._snapshot_guard.shared_lock():
            process = run_background(
                docker_command,
                process_class=DockerRunningProcess,
                shutdown_event=shutdown_event,
                is_checked=is_checked,
                timeout=timeout,
                process_class_kwargs=dict(
                    tag=tag,
                    container_id=self.container_id,
                    concurrency_group=self.concurrency_group,
                    on_output=on_output,
                ),
                env={**os.environ, **{k: v.unwrap() if isinstance(v, Secret) else v for k, v in secrets.items()}},
            )
            process.wait_until_started()
            return process

    @check_provider_health_on_failure
    def snapshot(self, user_config: UserConfig, task_id: TaskID, settings: SculptorSettings) -> LocalDockerImage:
        assert user_config.max_snapshot_size_bytes > 0, "Snapshot size must be positive"
        with log_runtime("Snapshotting docker image") as timing_details_for_posthog:
            latest_v1_image_metadata = get_latest_v1_image_metadata_for_task(task_id, self.concurrency_group, settings)
            if latest_v1_image_metadata is None:
                new_image_metadata = ImageMetadataV0.from_docker_metadata(
                    DockerImageMetadata(tag=generate_id(), labels={})
                )
            else:
                image_metadata_evolver = evolver(latest_v1_image_metadata)
                assign(image_metadata_evolver.sequence_number, lambda: latest_v1_image_metadata.sequence_number + 1)
                new_image_metadata = chill(image_metadata_evolver)

            image_name_and_tag = get_tagged_reference(
                f"{self.environment_prefix}{self.project_id}{SNAPSHOT_SUFFIX}", new_image_metadata
            )
            logger.info("Snapshotting Docker container {} into {}", self.container_id, image_name_and_tag)
            try:
                with (
                    self._snapshot_time_awareness(),
                    self._snapshot_guard.exclusive_lock(),
                ):
                    result = self.concurrency_group.run_process_to_completion(
                        command=["docker", "commit", self.container_id, image_name_and_tag],
                    )
            except ProcessError as e:
                raise EnvironmentFailure(
                    f"Failed to snapshot Docker container {self.container_id} to image {image_name_and_tag}: returncode={e.returncode}\nstderr={e.stderr}\nstdout={e.stdout}"
                ) from e

            image_id = DockerImageID(result.stdout.strip())

            image_obj = LocalDockerImage(image_id=image_id, project_id=self.project_id)
            if self._on_snapshot is not None:
                self._on_snapshot(image_obj, False)

            # now we need to check whether the image was too large
            try:
                size_bytes = get_unique_snapshot_size_bytes(self.concurrency_group, image_id)
                timing_details_for_posthog.set_attribute("snapshot_size_bytes", size_bytes)
            except ProviderError as e:
                raise EnvironmentFailure("Unable to find snapshot size") from e
            is_restart_required = size_bytes > user_config.max_snapshot_size_bytes
            logger.trace(
                "is_restart_required: {}, size: {}, max: {}",
                is_restart_required,
                size_bytes,
                user_config.max_snapshot_size_bytes,
            )
            report_snapshot_to_posthog(size_bytes, is_restart_required=is_restart_required)
            if is_restart_required:
                logger.info("Restarting container {}. size_bytes: {}", self.environment_id, size_bytes)
                # ugh, fine, raise an exception to signal this so that we can restart
                raise EnvironmentRestartRequired(image_obj)
            else:
                return image_obj

    def persist(self, user_config: UserConfig, task_id: TaskID, settings: SculptorSettings) -> None:
        pass

    @check_provider_health_on_failure
    def is_alive(self) -> bool:
        # Check if container is running
        with self._snapshot_guard.shared_lock():
            result = self.concurrency_group.run_process_to_completion(
                command=["docker", "inspect", "-f", "{{.State.Running}}", self.container_id],
                is_checked_after=False,
            )
        return result.returncode == 0 and result.stdout.strip() == "true"

    @check_provider_health_on_failure
    def exists(self, path: str) -> bool:
        # TODO (from maciek): Hmm, on a side note, environment.exists should probably be capable of accepting pathlib.Path
        try:
            with self._snapshot_guard.shared_lock():
                # Performing read operation as root.
                result = self.concurrency_group.run_process_to_completion(
                    command=[
                        *("docker", "exec"),
                        *("--user", "root"),
                        self.container_id,
                        *("test", "-e", path),
                    ],
                    is_checked_after=False,
                )
            return result.returncode == 0
        except ProcessError as e:
            raise EnvironmentFailure("Failed to check if path exists because docker exec failed") from e

    # TODO: output typing should discriminate based on mode literals, or better yet we should have `read_binary_file` and `read_text_file` methods
    @check_provider_health_on_failure
    def read_file(self, path: str, mode: Union["OpenTextModeReading", "OpenBinaryModeReading"] = "r") -> str | bytes:
        try:
            with self._snapshot_guard.shared_lock():
                # Performing read operation as root.
                result = self.concurrency_group.run_process_to_completion(
                    command=["docker", "exec", "--user", "root", self.container_id, "cat", path],
                )
        except ProcessError as e:
            raise FileNotFoundEnvironmentError(f"Failed to read file {path}: {e.stderr}") from e
        if "b" in mode:
            return result.stdout.encode("utf-8")
        return result.stdout

    @check_provider_health_on_failure
    def write_file(
        self,
        path: str,
        content: str | bytes,
        mode: str = "w",  # "w" or "a"; no binary support for now
        run_as_root: bool = False,
    ) -> None:
        assert mode in ("w", "a"), "w and a are the only supported modes"

        # Normalize to bytes
        data = content.encode("utf-8") if isinstance(content, str) else content

        parent_dir = str(Path(path).parent)
        q_parent = shlex.quote(parent_dir if parent_dir not in ("/", ".") else "/")
        q_path = shlex.quote(path)

        # Single exec: ensure dir exists, then stream to file
        redirector = ">" if mode == "w" else ">>"
        shell = f"mkdir -p {q_parent} && cat {redirector} {q_path}"

        cmd = (
            *("docker", "exec", "-i"),
            *("--user", "root" if run_as_root else self.get_container_user()),
            self.container_id,
            *("sh", "-c", shell),
        )

        with self._snapshot_guard.shared_lock():
            # Note: run_blocking doesn't support stdin input, so we keep subprocess.run for this specific case
            result = subprocess.run(cmd, input=data, capture_output=True)
        if result.returncode != 0:
            raise EnvironmentFailure(
                f"Failed to write file {path} in container: returncode={result.returncode}\n"
                + f"stderr={result.stderr}\nstdout={result.stdout}"
            )

    def move_file(
        self,
        original_path: str,
        new_path: str,
        run_as_root: bool = False,
    ) -> None:
        cmd = ["docker", "exec"]
        if run_as_root:
            cmd += ["--user", "root"]
        else:
            cmd += ["--user", self.get_container_user()]
        parent = str(Path(new_path).parent)
        cmd += [
            self.container_id,
            "bash",
            "-c",
            # We tried using `install` instead of `mv` here but it couldn't deal with an already existing file.
            f"mkdir -p {quote(parent)} && mv -f {quote(original_path)} {quote(new_path)}",
        ]
        try:
            with self._snapshot_guard.shared_lock():
                self.concurrency_group.run_process_to_completion(
                    command=cmd,
                )
        except ProcessError as e:
            if not self.is_alive():
                raise EnvironmentFailure(
                    f"Failed to move file from {original_path} to {new_path}: container {self.container_id} is not running"
                ) from e
            raise FileNotFoundEnvironmentError(
                f"Failed to move file from {original_path} to {new_path}: {e.stderr}"
            ) from e

    def delete_file_or_directory(self, path: str) -> None:
        try:
            with self._snapshot_guard.shared_lock():
                self.concurrency_group.run_process_to_completion(
                    command=[
                        *("docker", "exec"),
                        *("--user", "root"),
                        self.container_id,
                        *("rm", "-rf", path),
                    ],
                )
        except ProcessError as e:
            if not self.is_alive():
                raise EnvironmentFailure(
                    f"Failed to delete file or directory {path}: container {self.container_id} is not running"
                ) from e
            raise FileOrDirectoryCouldNotBeDeletedError(
                f"Failed to delete file or directory {path}: {e.stderr}"
            ) from e

    def get_server_url(self, name: str) -> AnyUrl:
        server_port = self.server_port_by_name[name]
        remote_repo_host = get_docker_host()
        return AnyUrl(f"http://{remote_repo_host}:{server_port}")

    def close(self) -> None:
        """Stop a Docker container."""
        logger.info("DockerEnvironment.close(), {}", self.container_id)
        pids_to_kill = [
            str(process.inner_pid)
            for process in self.concurrency_group.unfinished_processes
            if isinstance(process, DockerRunningProcess)
        ]
        if len(pids_to_kill) > 0:
            self.run_process_to_completion(
                ["kill", "-TERM", *pids_to_kill], secrets={}, run_as_root=True, timeout=8.0, is_checked_after=False
            )
        try:
            stop_docker_container(container_id=self.container_id, concurrency_group=self.concurrency_group)
        except ProviderIsDownError:
            pass

    def destroy(self, is_killing: bool = False) -> None:
        logger.info("Destroying Docker container {} (is_killing={})", self.container_id, is_killing)
        if is_killing:
            try:
                stop_docker_container(
                    container_id=self.container_id, concurrency_group=self.concurrency_group, is_killing=True
                )
            except ProviderIsDownError:
                pass
        remove_docker_container(container_id=self.container_id, concurrency_group=self.concurrency_group)

    @check_provider_health_on_failure
    def copy_from_local(self, local_path: Path, env_path: str, recursive: bool = True) -> None:
        if not local_path.exists():
            raise FileNotFoundEnvironmentError(f"Local path {local_path} does not exist")

        if local_path.is_dir() and not recursive:
            raise IsADirectoryEnvironmentError(f"{local_path} is a directory but recursive=False")

        with self._snapshot_guard.shared_lock():
            # Ensure parent directory exists in container
            parent_dir = str(Path(env_path).parent)
            if parent_dir != "/" and parent_dir != ".":
                try:
                    self.concurrency_group.run_process_to_completion(
                        command=[
                            *("docker", "exec"),
                            *("--user", self.get_container_user()),
                            self.container_id,
                            *("mkdir", "-p", parent_dir),
                        ],
                    )
                except ProcessError as e:
                    raise EnvironmentFailure(
                        f"Failed to create parent directory {parent_dir} in container: returncode={e.returncode}\nstderr={e.stderr}\nstdout={e.stdout}"
                    ) from e

            # Use docker cp to copy the file/directory
            logger.info("Copying {} to {}:{}", local_path, self.container_id, env_path)
            try:
                cp_process = self.concurrency_group.run_process_to_completion(
                    command=["docker", "cp", str(local_path), f"{self.container_id}:{env_path}"],
                )
                chown_process = self.concurrency_group.run_process_to_completion(
                    command=[
                        *("docker", "exec"),
                        *("--user", "root"),
                        self.container_id,
                        *("chown", "-R", f"{self.get_container_user()}"),
                        env_path,
                    ],
                )
            except ProcessError as e:
                raise EnvironmentFailure(
                    f"Failed to copy {local_path} to container: returncode={e.returncode}\nstderr={e.stderr}\nstdout={e.stdout}"
                ) from e

    @check_provider_health_on_failure
    def copy_to_local(self, env_path: str, local_path: Path, recursive: bool = True) -> None:
        if not self.exists(env_path):
            raise FileNotFoundEnvironmentError(f"Path {env_path} does not exist in container")

        # Check if it's a directory
        is_dir_result = self.concurrency_group.run_process_to_completion(
            command=["docker", "exec", "--user", "root", self.container_id, "test", "-d", env_path],
            is_checked_after=False,
        )
        is_directory = is_dir_result.returncode == 0

        if is_directory and not recursive:
            raise IsADirectoryEnvironmentError(f"{env_path} is a directory but recursive=False")

        # Ensure parent directory exists locally.
        local_path.parent.mkdir(parents=True, exist_ok=True)

        # Use docker cp to copy from container.
        logger.info("Copying {}:{} to {}", self.container_id, env_path, local_path)
        try:
            with self._snapshot_guard.shared_lock():
                # It doesn't seem possible to specify a container user for this command.
                # https://docs.docker.com/reference/cli/docker/container/cp/
                process = self.concurrency_group.run_process_to_completion(
                    command=["docker", "cp", f"{self.container_id}:{env_path}", str(local_path)],
                )
        except ProcessError as e:
            raise EnvironmentFailure(
                f"Failed to copy {env_path} from container: returncode={e.returncode}\nstderr={e.stderr}\nstdout={e.stdout}"
            ) from e

    def raise_if_not_healthy(self) -> None:
        super().raise_if_not_healthy()
        try:
            self.concurrency_group.raise_if_any_strands_or_ancestors_failed_or_is_shutting_down()
        except Exception as e:
            raise EnvironmentNotHealthy("Environment concurrency group is not healthy") from e


def stop_docker_container(
    container_id: str,
    concurrency_group: ConcurrencyGroup,
    is_killing: bool = False,
    shutdown_event: ReadOnlyEvent | None = None,
) -> None:
    try:
        concurrency_group.run_process_to_completion(
            command=["docker", "kill" if is_killing else "stop", container_id],
            is_checked_after=True,
            shutdown_event=shutdown_event,
        )
    except ProcessError as e:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError() from e
        if "No such container" in e.stderr:
            logger.debug("Docker container {} already gone to stop it", container_id)
            return
        elif "container" in e.stderr.lower() and "is not running" in e.stderr.lower():
            logger.debug("Docker container {} already stopped", container_id)
            return
        else:
            health_status = get_docker_status(concurrency_group)
            if not isinstance(health_status, OkStatus):
                logger.debug("Docker seems to be down, cannot stop container {}", container_id)
                details_msg = f" (details: {health_status.details})" if health_status.details else ""
                raise ProviderIsDownError(f"Provider is unavailable: {health_status.message}{details_msg}") from e
            else:
                log_exception(
                    e,
                    "Failed to stop Docker container, but docker seems to be running...",
                    priority=ExceptionPriority.LOW_PRIORITY,
                    extra=dict(container_id=container_id),
                )


def remove_docker_container(
    container_id: str, concurrency_group: ConcurrencyGroup, shutdown_event: ReadOnlyEvent | None = None
) -> None:
    logger.info("Removing outdated Docker container {}", container_id)
    try:
        concurrency_group.run_process_to_completion(
            command=["docker", "rm", "-f", container_id],
            timeout=30.0,
            shutdown_event=shutdown_event,
        )
    except ProcessError as e:
        if shutdown_event is not None and shutdown_event.is_set():
            raise CancelledByEventError() from e
        # Error response from daemon: removal of container edcd0b869be5ed55902c9b6d45513e4a40e92ef6db4746ac53a554c0f10910dd is already in progress
        if e.stderr.strip().startswith(
            "Error response from daemon: removal of container"
        ) and e.stderr.strip().endswith("is already in progress"):
            logger.warning("Docker container {} is already being removed", container_id)
            return
        else:
            health_status = get_docker_status(concurrency_group)
            if not isinstance(health_status, OkStatus):
                logger.debug("Docker seems to be down, cannot remove container {}", container_id)
                details_msg = f" (details: {health_status.details})" if health_status.details else ""
                raise ProviderIsDownError(f"Provider is unavailable: {health_status.message}{details_msg}") from e
            else:
                log_exception(
                    e,
                    "Failed to remove outdated Docker container",
                    priority=ExceptionPriority.LOW_PRIORITY,
                    extra=dict(container_id=container_id),
                )


def get_unique_snapshot_size_bytes(concurrency_group: ConcurrencyGroup, image_id: str) -> int:
    try:
        image_history = concurrency_group.run_process_to_completion(
            ["docker", "history", "--human=false", "--no-trunc", "--format", "{{.ID}}: {{.Size}}", image_id]
        )
    except ProcessError as e:
        raise ProviderError(
            f"Failed to understand snapshot size for snapshot {image_id}: returncode={e.returncode}\nstderr={e.stderr}\nstdout={e.stdout}"
        ) from e
    # find our image
    for row in image_history.stdout.strip().splitlines(keepends=False):
        row_image_id, size_str = row.rsplit(":", maxsplit=1)
        if row_image_id == image_id:
            return int(size_str.strip())
    raise ProviderError(f"Failed to understand snapshot size {image_id}: could not find snapshot size from history")
