from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Generator
from typing import Sequence

import attr
import pytest
from loguru import logger

from imbue_core.common import generate_id
from imbue_core.concurrency_group import ConcurrencyGroup
from imbue_core.git import get_git_repo_root
from sculptor.agents.default.terminal_manager import TTYD_NGINX_PROXY_DIR
from sculptor.services.environment_service.providers.docker.environment_utils import stop_outdated_docker_containers
from sculptor.testing.caching_utils import save_caches_to_snapshot_directory
from sculptor.testing.container_utils import get_containers_with_tasks
from sculptor.testing.elements.task_list import wait_for_tasks_to_finish
from sculptor.testing.frontend_utils import Frontend
from sculptor.testing.pages.home_page import PlaywrightHomePage
from sculptor.testing.subprocess_utils import Forwarder
from sculptor.testing.subprocess_utils import print_colored_line
from sculptor.utils.build import SCULPTOR_FOLDER_OVERRIDE_ENV_FLAG
from sculptor.utils.file_utils import copy_dir

LOCAL_HOST_URL = "http://127.0.0.1"
READY_MESSAGE_V1 = "Server is ready to accept requests!"


@attr.s(auto_attribs=True, kw_only=True)
class SculptorServer:
    """A sculptor server holds a sculptor process for testing."""

    process: subprocess.Popen
    port: int
    is_unexpected_error_caused_by_test: bool = False

    @property
    def url(self) -> str:
        return f"{LOCAL_HOST_URL}:{self.port}"


class SculptorFactory:
    """A factory for creating sculptor instances."""

    def __init__(
        self,
        command: tuple[str, ...],
        environment: dict[str, str | None],
        snapshot_path: Path | None,
        container_prefix: str,
        port: int,
        database_url: str,
        update_snapshots: bool,
        frontend: Frontend,
        request: pytest.FixtureRequest,
    ) -> None:
        self.command = command
        self.environment = environment
        self.snapshot_path = snapshot_path
        self.database_url = database_url
        self.port = port
        self.frontend = frontend
        self.request = request
        self.container_prefix = container_prefix
        self.update_snapshots = update_snapshots
        self._sculptor_cache_id = 0
        self._tmp_snapshot_path = Path(tempfile.mkdtemp())
        self._tmp_artifacts_path = Path(tempfile.mkdtemp())

    @contextmanager
    def spawn_sculptor_instance(self) -> Generator[tuple[SculptorServer, PlaywrightHomePage], None, None]:
        """Only for use in fixtures! Test errors won't be handled otherwise"""
        environment = self.environment.copy()
        snapshot_parent = f"sculptor_snapshot_{self._sculptor_cache_id}"
        artifacts_parent = f"sculptor_artifacts_{self._sculptor_cache_id}"

        tmp_artifacts_path = self._tmp_artifacts_path / artifacts_parent

        if self.snapshot_path is not None:
            assert not self.update_snapshots, (
                "error in test: We can't update snapshot and provide them at the same time"
            )
            snapshot_path = self.snapshot_path / snapshot_parent
            environment["TESTING__SNAPSHOT_PATH"] = str(snapshot_path.absolute())

        logger.info("Starting sculptor server with command: {}", self.command)
        logger.info("Setting environment to: {}", environment)

        env = {k: str(v) for k, v in {**os.environ, **environment}.items() if v is not None}
        server = _start_server_process_and_validate_readiness(self.command, env)
        forwarder = Forwarder(server)
        forwarder.start()
        specimen_server = SculptorServer(process=server, port=self.port)

        with self.frontend.get_fresh_page(self.request) as sculptor_page:
            self._sculptor_cache_id += 1
            # adding this here so that we can note when we expect things to fail
            sculptor_page._imbue_server = specimen_server
            yield specimen_server, sculptor_page

        if self.update_snapshots:
            logger.debug("Snapshotting, waiting for tasks to complete")
            # TODO: This should really work by reading DB
            wait_for_tasks_to_finish(
                task_list=sculptor_page.get_task_list(),
                is_unexpected_error_caused_by_test=specimen_server.is_unexpected_error_caused_by_test,
            )
        logger.debug("Test server fixture finished")

        # normal case -- we do NOT expect any containers to be missing
        if not specimen_server.is_unexpected_error_caused_by_test or self.update_snapshots:
            with ConcurrencyGroup(name="adhoc_testing_concurrency_group") as concurrency_group:
                containers_with_tasks = get_containers_with_tasks(self.database_url, concurrency_group)
            if self.update_snapshots:
                if forwarder.first_failure_line is not None:
                    logger.info("Not updating snapshots due to earlier error: {}", forwarder.first_failure_line)
                else:
                    logger.info("Preserving snapshots to a temporary directory")
                    save_caches_to_snapshot_directory(
                        local_path=self._tmp_snapshot_path / snapshot_parent,
                        containers_with_tasks=containers_with_tasks,
                    )
        else:
            containers_with_tasks = ()

        logger.info("Preserving files from the tasks and sculptor to a temporary directory: {}", tmp_artifacts_path)
        diagnostics_output = "/tmp/diagnostics.txt"
        files_to_extract = {
            "/tmp/proxy_logs.txt": "proxy_logs.txt",
            "/tmp/imbue-cli.log": "imbue-cli.log",
            str(Path(TTYD_NGINX_PROXY_DIR) / "nginx.access.log"): "nginx.access.log",
            str(Path(TTYD_NGINX_PROXY_DIR) / "nginx.error.log"): "nginx.error.log",
            diagnostics_output: "diagnostic.txt",
        }

        tmp_artifacts_path.mkdir(parents=True, exist_ok=True)
        for i, (container_id, task_id) in enumerate(containers_with_tasks):
            try:
                # these have to be shell-safe!
                commands = [
                    "set -x",
                    "/bin/ps auxwf",
                    "du -s",
                    "ulimit -a",
                    "git --version",
                    "env",
                    "claude --version",
                    "uname -a",
                ]
                # re-directing to `cat` to hide exit code of the commands but have `docker exec` fail
                # if bash is not found or container is dead.
                bash_command = ["bash", "-c", f"({';'.join(commands)}) 2>&1 | cat >{shlex.quote(diagnostics_output)}"]

                subprocess.run(
                    ["docker", "exec", "--user", "root", container_id, *bash_command],
                    timeout=60,
                    capture_output=True,
                    check=True,
                )
            except Exception as e:
                logger.info("Container not available, or diagnostics command failed. Ignoring. Reason: {}", str(e))

            for source, destination in files_to_extract.items():
                output_file = tmp_artifacts_path / f"task.{i}.{task_id}-{destination}"
                try:
                    subprocess.run(
                        ["docker", "cp", f"{container_id}:{source}", output_file],
                        timeout=60,
                        capture_output=True,
                        check=True,
                    )
                except Exception as e:
                    logger.info("Could not extract {} from the container. Ignoring. Reason: {}", source, str(e))

        logger.info("Terminating sculptor server")
        server.terminate()
        try:
            # TODO: we need to think a little deeply about what we want the timeout to be here, bumped to make sure we try and wait for a clean shutdown
            if os.environ.get("IMBUE_MODAL_TEST"):
                # it can take a really long time for things to shut down on modal because we can be waiting for some containers to go away...
                server.wait(timeout=60 * 6)
            else:
                server.wait(timeout=60)
        except subprocess.TimeoutExpired:
            # logging as error so our tests will fail if we don't shut down cleanly
            logger.error("Sculptor server did not terminate gracefully, killing it.")
            server.kill()
            server.wait(2)

        with ConcurrencyGroup(name="adhoc_testing_concurrency_group") as concurrency_group:
            stop_outdated_docker_containers(
                container_name_predicate=lambda x: x.startswith(self.container_prefix),
                concurrency_group=concurrency_group,
            )

        # if there was an error in the logs, that is sufficient for us to mark this task as failed
        # (even if the test didn't realize it failed)
        if specimen_server.is_unexpected_error_caused_by_test:
            pass
        else:
            if forwarder.first_failure_line is not None:
                raise Exception(
                    f"Sculptor server encountered emitted a line with ERROR: {forwarder.first_failure_line}"
                )

    def copy_snapshots(self, new_snapshot_path: Path) -> None:
        if new_snapshot_path.exists():
            shutil.rmtree(new_snapshot_path)

        new_snapshot_path.parent.mkdir(parents=True, exist_ok=True)
        copy_dir(self._tmp_snapshot_path, new_snapshot_path)

    def copy_artifacts(self, new_artifacts_path: Path) -> None:
        new_artifacts_path.parent.mkdir(parents=True, exist_ok=True)
        # the directory may have already been created by Playwright's fixtures
        copy_dir(self._tmp_artifacts_path, new_artifacts_path, dirs_exist_ok=True)


TEST_ENVIRONMENT_PREFIX = "sculptortesting"


def get_testing_container_prefix() -> str:
    return f"{TEST_ENVIRONMENT_PREFIX}-{generate_id()}"


def get_v1_frontend_path() -> Path:
    """Returns the path to the frontend directory in v1"""
    return get_git_repo_root() / "sculptor" / "frontend"


def get_testing_environment(
    container_prefix: str,
    database_url: str,
    sculptor_folder: Path,
    tmp_path: Path,
    static_files_path: Path | None = None,
    hide_keys: bool = True,
    is_checks_enabled: bool = False,
    port: int | None = None,
    frontend_port: int | None = None,
    local_sync_debounce_seconds: float | None = None,
) -> dict[str, str | None]:
    environment = {}

    if static_files_path is not None:
        environment["STATIC_FILES_PATH"] = str(static_files_path.absolute())

    environment[SCULPTOR_FOLDER_OVERRIDE_ENV_FLAG] = sculptor_folder
    environment["DATABASE_URL"] = database_url
    environment["TESTING__INTEGRATION_ENABLED"] = "true"
    environment["TESTING__CONTAINER_PREFIX"] = container_prefix
    environment["SENTRY_DSN"] = None
    environment["IS_CHECKS_ENABLED"] = "true" if is_checks_enabled else "false"
    environment["IS_FORKING_ENABLED"] = "true"
    environment["LOCAL_PROVIDER_ENABLED"] = "true"
    environment["CONFIG_HOME"] = str(tmp_path)

    if hide_keys:
        environment["ANTHROPIC_API_KEY"] = "sk-HIDDEN-FOR-TESTING"
        environment["OPENAI_API_KEY"] = "sk-HIDDEN-FOR-TESTING"

    environment["GITLAB_DEFAULT_TOKEN"] = "test-gitlab-token-for-integration-tests"
    if port is not None:
        environment["SCULPTOR_API_PORT"] = str(port)
    if frontend_port is not None:
        environment["SCULPTOR_FRONTEND_PORT"] = str(frontend_port)

    if local_sync_debounce_seconds is not None:
        environment["_OVERRIDE_LOCAL_SYNC_CHANGE_DEBOUNCE_SECONDS"] = str(local_sync_debounce_seconds)

    return environment


def get_sculptor_command_backend_only(
    repo_path: Path | None,
    port: int,
) -> tuple[str, ...]:
    command = [
        "python",
        "-m",
        "sculptor.cli.main",
        "--no-open-browser",
        f"--port={port}",
    ]
    if repo_path is not None:
        command.append(str(repo_path))
    return tuple(command)


def get_sculptor_command_electron(
    repo_path: Path | None,
    port: int,
    cdp_port: int,
) -> tuple[str, ...]:
    """Creates the command to invoke Sculptor from the folder."""
    artifact_dir = get_git_repo_root() / "dist"
    if sys.platform == "darwin":
        executable_path = artifact_dir / "Sculptor.app" / "Contents" / "MacOS" / "Sculptor"
    elif sys.platform == "linux":
        executable_path = artifact_dir / "Sculptor-linux-x64" / "Sculptor"
    else:
        raise Exception(f"Unsupported platform: {sys.platform}")
    if not executable_path.exists():
        raise FileNotFoundError(f"Electron executable not found at {executable_path}")
    backend_args = [f"--port={port}"]
    if repo_path is not None:
        backend_args.append(str(repo_path))
    return (
        str(executable_path),
        f"--remote-debugging-port={cdp_port}",
        # The Electron executable requires this for arguments to pass to the Sculptor backend.
        *("--sculptor=" + arg for arg in backend_args),
    )


def _start_server_process_and_validate_readiness(command: Sequence[str], env: dict[str, str]) -> subprocess.Popen[str]:
    server = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=env)
    assert server.stdout, "Sculptor server stdout is always available in PIPE mode"
    server_output_lines = []
    for line in server.stdout:
        server_output_lines.append(line.rstrip())
        print_colored_line(line.rstrip())
        if READY_MESSAGE_V1 in line:
            logger.info("Sculptor server is ready")
            return server

    # Server failed to start properly - provide detailed error information
    error_msg = "Sculptor server failed to start and never outputted ready message.\n"
    error_msg += f"Expected message containing: '{READY_MESSAGE_V1}'\n"
    error_msg += f"Command: {' '.join(command)}\n"
    error_msg += "Server output:\n" + "\n".join(server_output_lines)

    logger.error(error_msg)
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(2)

    raise RuntimeError(error_msg)
