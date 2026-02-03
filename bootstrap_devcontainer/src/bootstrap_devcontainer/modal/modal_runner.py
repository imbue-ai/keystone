"""Modal-based agent runner for running bootstrap agent in cloud sandbox.

The sandbox is created once and reused for both agent execution and verification.
This avoids the 20-30s cold start penalty of creating a new sandbox for verification,
and lets us use Docker's build cache directly instead of Modal's from_dockerfile.
"""

import io
import logging
import os
import queue
import shlex
import sys
import tarfile
import threading
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any, Literal

import modal

from bootstrap_devcontainer.agent_runner import (
    TIMEOUT_EXIT_CODE,
    AgentRunner,
    StreamEvent,
    build_claude_command,
)
from bootstrap_devcontainer.modal.image import create_modal_image
from bootstrap_devcontainer.schema import VerifyResult

logger = logging.getLogger(__name__)


class ManagedProcess:
    """
    Wraps a Modal ContainerProcess to provide consistent logging and optional streaming.

    Immediately starts daemon threads to pipe stdout/stderr to Python's logging.
    """

    def __init__(
        self,
        proc: Any,
        prefix: str = "",
        capture: bool = False,
    ) -> None:
        self.proc = proc
        self.prefix = prefix
        self.capture = capture
        self._queue: queue.Queue[StreamEvent | None] | None = queue.Queue() if capture else None

        self._stdout_thread = threading.Thread(
            target=self._stream_reader,
            args=(proc.stdout, "stdout"),
            name=f"modal-stdout-{prefix}",
            daemon=True,
        )
        self._stderr_thread = threading.Thread(
            target=self._stream_reader,
            args=(proc.stderr, "stderr"),
            name=f"modal-stderr-{prefix}",
            daemon=True,
        )

        self._stdout_thread.start()
        self._stderr_thread.start()

    def _stream_reader(
        self, stream: Iterable[str], stream_name: Literal["stdout", "stderr"]
    ) -> None:
        logger = logging.getLogger("bootstrap_devcontainer.modal")
        try:
            for chunk in stream:
                # Modal may return multiple lines in a single chunk, so split them
                for line in chunk.splitlines():
                    clean_line = line.rstrip()
                    if not clean_line:
                        continue
                    # Log immediately to Python's logging system
                    # Format: [name] STDOUT/STDERR: line
                    if stream_name == "stderr":
                        logger.info(f"[{self.prefix}] STDERR: {clean_line}")
                    else:
                        logger.info(f"[{self.prefix}] STDOUT: {clean_line}")
                    if self._queue is not None:
                        self._queue.put(StreamEvent(stream=stream_name, line=clean_line))
        except Exception:
            # Stream closed due to sandbox termination - this is expected
            pass

        if self._queue is not None:
            self._queue.put(None)  # Signal this stream is done

    def wait(self) -> int:
        """Block until the process and its logging threads finish."""
        self.proc.wait()
        self._stdout_thread.join()
        self._stderr_thread.join()
        return self.proc.returncode or 0

    def stream(self) -> Iterator[StreamEvent]:
        """Yield captured events until the process finishes."""
        if self._queue is None:
            raise RuntimeError("Process was not started with capture=True")

        streams_done = 0
        while streams_done < 2:
            event = self._queue.get()
            if event is None:
                streams_done += 1
            else:
                yield event
        self.wait()

    def terminate(self) -> None:
        """Terminate the underlying process."""
        self.proc.terminate()


def run_modal_command(
    sb: modal.Sandbox, *args: str, capture: bool = False, name: str, **kwargs: Any
) -> ManagedProcess:
    """Helper to execute a command and return a ManagedProcess.

    Args:
        sb: Modal sandbox to run command in
        args: Command and arguments
        capture: Whether to capture output for streaming
        name: Short name for this process (required, used in log prefix)
        **kwargs: Additional arguments passed to sb.exec()
    """

    logger = logging.getLogger("bootstrap_devcontainer.modal")
    logger.info(f"[{name}] Running: {shlex.join(args)}")
    proc = sb.exec(*args, **kwargs)
    return ManagedProcess(proc, prefix=name, capture=capture)


_SCRIPT_DIR = Path(__file__).parent


def _read_claude_auth() -> dict[str, str]:
    """Read Claude authentication from environment."""
    auth_env: dict[str, str] = {}

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        auth_env["ANTHROPIC_API_KEY"] = api_key

    return auth_env


class ModalAgentRunner(AgentRunner):
    """Run agent in a Modal sandbox with Docker support.

    The sandbox is created once via ensure_sandbox() and reused for both
    agent execution and verification. This saves the 20-30s cold start
    and benefits from Docker's build cache.
    """

    def __init__(self, timeout_seconds: int = 3600) -> None:
        self._timeout_seconds = timeout_seconds
        self._exit_code: int = 1
        self._devcontainer_tarball: bytes = b""
        self._sandbox: modal.Sandbox | None = None

    def ensure_sandbox(self) -> modal.Sandbox:
        """Create sandbox if not already created. Returns the sandbox."""
        if self._sandbox is not None:
            return self._sandbox

        modal.enable_output()
        print("Creating Modal sandbox with Docker...", file=sys.stderr)

        app = modal.App.lookup("bootstrap-devcontainer-sandbox", create_if_missing=True)
        image = create_modal_image()

        self._sandbox = modal.Sandbox.create(
            app=app,
            image=image,
            timeout=self._timeout_seconds,
            region="us-west-2",
            experimental_options={"enable_docker": True},
        )

        sandbox_id = self._sandbox.object_id
        print(f"Modal sandbox created: {sandbox_id}", file=sys.stderr)
        print("  Dashboard: https://modal.com/apps/bootstrap-devcontainer-sandbox", file=sys.stderr)
        print(f"  Shell:     modal shell {sandbox_id}", file=sys.stderr)

        # Start Docker daemon
        run_modal_command(self._sandbox, "/start-dockerd.sh", name="dockerd")
        logger.info("Waiting for Docker daemon to be ready...")
        run_modal_command(self._sandbox, "/wait_for_docker.sh", name="docker-wait").wait()

        return self._sandbox

    def upload_project(self, project_archive: bytes) -> None:
        """Upload project archive to sandbox."""
        sb = self.ensure_sandbox()
        logger.info("Uploading project to sandbox...")
        run_modal_command(sb, "rm", "-rf", "/project", name="upload").wait()
        run_modal_command(sb, "mkdir", "-p", "/project", name="upload").wait()

        with sb.open("/tmp/project.tar.gz", "wb") as f:
            f.write(project_archive)
        run_modal_command(
            sb, "tar", "-xzf", "/tmp/project.tar.gz", "-C", "/project", name="upload"
        ).wait()
        run_modal_command(sb, "chown", "-R", "agent:agent", "/project", name="upload").wait()

    def run(
        self,
        prompt: str,
        project_archive: bytes,
        max_budget_usd: float,
        agent_cmd: str,
        time_limit_secs: int,
    ) -> Iterator[StreamEvent]:
        """Run the agent in the Modal sandbox."""
        self.ensure_sandbox()
        self.upload_project(project_archive)

        try:
            yield from self._run_agent(prompt, max_budget_usd, agent_cmd, time_limit_secs)
        except Exception:
            if self._sandbox:
                self._sandbox.terminate()
                self._sandbox = None
            raise

    def _run_agent(
        self,
        prompt: str,
        max_budget_usd: float,
        agent_cmd: str,
        time_limit_secs: int,
    ) -> Iterator[StreamEvent]:
        """Execute the agent inside the sandbox (sandbox and project already set up)."""
        assert self._sandbox is not None
        sb = self._sandbox

        # Set up Claude auth
        logger.info("Setting up Claude authentication...")
        auth_env = _read_claude_auth()

        # 4. Run the agent
        logger.info("Starting agent...")

        # Debug: check what auth we have
        if "ANTHROPIC_API_KEY" in auth_env:
            logger.info("Using ANTHROPIC_API_KEY for authentication")
        else:
            logger.warning("No ANTHROPIC_API_KEY found!")

        env_vars = {}
        if "ANTHROPIC_API_KEY" in auth_env:
            env_vars["ANTHROPIC_API_KEY"] = auth_env["ANTHROPIC_API_KEY"]

        # Build agent command
        # Note: agent_cmd might be "claude" or a full path
        cmd_parts = build_claude_command(prompt, max_budget_usd, agent_cmd)

        # Run agent in project directory
        # We write a wrapper script to avoid quoting hell with 'su -c'
        agent_script_content = f"""#!/bin/bash
set -e
cd /project
{f"export ANTHROPIC_API_KEY={shlex.quote(env_vars['ANTHROPIC_API_KEY'])}" if "ANTHROPIC_API_KEY" in env_vars else ""}
exec timeout {time_limit_secs} {shlex.join(cmd_parts)}
"""
        # Upload script using Modal's native filesystem API
        with sb.open("/run_agent.sh", "w") as f:
            f.write(agent_script_content)
        run_modal_command(sb, "chmod", "+x", "/run_agent.sh", name="setup").wait()
        run_modal_command(sb, "chown", "agent:agent", "/run_agent.sh", name="setup").wait()

        logger.info("Executing: su agent -c /run_agent.sh")
        agent = run_modal_command(
            sb,
            "su",
            "agent",
            "-c",
            "/run_agent.sh",
            env=None,
            pty=True,
            name="agent",
            capture=True,
        )
        yield from agent.stream()
        self._exit_code = agent.wait()

        # 5. Extract .devcontainer directory
        logger.info("Extracting .devcontainer from sandbox...")
        # Create tarball in sandbox, then read it using Modal's native filesystem API
        run_modal_command(
            sb,
            "tar",
            "-czf",
            "/tmp/devcontainer.tar.gz",
            "-C",
            "/project",
            ".devcontainer",
            name="extract",
        ).wait()
        with sb.open("/tmp/devcontainer.tar.gz", "rb") as f:
            self._devcontainer_tarball = f.read()

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def get_devcontainer_tarball(self) -> bytes:
        return self._devcontainer_tarball

    def verify(
        self,
        project_archive: bytes,
        devcontainer_tarball: bytes,
        test_artifacts_dir: Path,
        image_build_timeout_secs: int,
        test_timeout_secs: int,
    ) -> VerifyResult:
        """Run verification by building and running docker in the existing sandbox.

        Uses docker commands directly instead of Modal's from_dockerfile, which:
        - Avoids 20-30s cold start for a new sandbox
        - Benefits from Docker's build cache already in this sandbox

        Note: Timeouts are enforced via the timeout command wrapper.
        """
        sb = self.ensure_sandbox()

        # Upload fresh project source
        logger.info("Uploading project source for verification...")
        run_modal_command(sb, "rm", "-rf", "/project", name="verify-setup").wait()
        run_modal_command(sb, "mkdir", "-p", "/project", name="verify-setup").wait()
        with sb.open("/tmp/project.tar.gz", "wb") as f:
            f.write(project_archive)
        run_modal_command(
            sb, "tar", "-xzf", "/tmp/project.tar.gz", "-C", "/project", name="verify-setup"
        ).wait()

        # Overlay devcontainer
        logger.info("Uploading .devcontainer for verification...")
        with sb.open("/tmp/devcontainer.tar.gz", "wb") as f:
            f.write(devcontainer_tarball)
        run_modal_command(
            sb, "tar", "-xzf", "/tmp/devcontainer.tar.gz", "-C", "/project", name="verify-setup"
        ).wait()

        # Check Dockerfile exists
        check_proc = run_modal_command(
            sb, "test", "-f", "/project/.devcontainer/Dockerfile", name="verify"
        )
        if check_proc.wait() != 0:
            return VerifyResult(
                success=False,
                error_message="Build failed: .devcontainer/Dockerfile not found.",
            )

        image_name = "bootstrap-verify"
        container_name = "bootstrap-verify-container"

        # 1. Build the image
        # NOTE: Registry-based caching was attempted but Modal's serverless architecture
        # doesn't support the stateful upload sessions that Docker registries require.
        # See scripts/modal_registry.py for the attempted implementation.
        logger.info("Building devcontainer image with docker...")
        import time

        build_start = time.time()
        build_proc = run_modal_command(
            sb,
            "timeout",
            str(image_build_timeout_secs),
            "docker",
            "build",
            "--network=host",
            "-t",
            image_name,
            "-f",
            "/project/.devcontainer/Dockerfile",
            "/project",
            name="docker-build",
        )
        build_exit = build_proc.wait()
        image_build_seconds = time.time() - build_start
        if build_exit == TIMEOUT_EXIT_CODE:
            return VerifyResult(
                success=False,
                error_message=f"Image build timed out after {image_build_timeout_secs} seconds",
                image_build_seconds=image_build_seconds,
            )
        if build_exit != 0:
            return VerifyResult(
                success=False,
                error_message=f"Build failed with exit code {build_exit}",
                image_build_seconds=image_build_seconds,
            )

        # 2. Run tests
        logger.info("Running tests in container...")
        test_start = time.time()
        run_modal_command(sb, "docker", "rm", "-f", container_name, name="cleanup").wait()
        test_proc = run_modal_command(
            sb,
            "timeout",
            str(test_timeout_secs),
            "docker",
            "run",
            "--network=host",
            "--name",
            container_name,
            image_name,
            "/run_all_tests.sh",
            name="docker-test",
        )
        test_exit_code = test_proc.wait()
        test_execution_seconds = time.time() - test_start

        # 3. Extract test artifacts
        logger.info("Extracting test artifacts...")
        run_modal_command(
            sb,
            "docker",
            "cp",
            f"{container_name}:/test_artifacts",
            "/tmp/test_artifacts",
            name="extract",
        ).wait()
        run_modal_command(
            sb,
            "tar",
            "-czf",
            "/tmp/test_artifacts.tar.gz",
            "-C",
            "/tmp/test_artifacts",
            ".",
            name="extract",
        ).wait()

        try:
            with sb.open("/tmp/test_artifacts.tar.gz", "rb") as f:
                tarball = f.read()
            test_artifacts_dir.mkdir(parents=True, exist_ok=True)
            with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
                tar.extractall(test_artifacts_dir)
            logger.info(f"Test artifacts extracted to {test_artifacts_dir}")
        except Exception as e:
            logger.exception("Error extracting artifacts: %s", e)

        # 4. Clean up container
        run_modal_command(sb, "docker", "rm", container_name, name="cleanup").wait()

        if test_exit_code == TIMEOUT_EXIT_CODE:
            return VerifyResult(
                success=False,
                error_message=f"Test execution timed out after {test_timeout_secs} seconds",
                image_build_seconds=image_build_seconds,
                test_execution_seconds=test_execution_seconds,
            )
        if test_exit_code == 0:
            logger.info("Verification successful!")
            return VerifyResult(
                success=True,
                image_build_seconds=image_build_seconds,
                test_execution_seconds=test_execution_seconds,
            )
        else:
            logger.error(f"Test run failed with return code {test_exit_code}")
            return VerifyResult(
                success=False,
                error_message=f"Test run failed with return code {test_exit_code}",
                image_build_seconds=image_build_seconds,
                test_execution_seconds=test_execution_seconds,
            )

    def get_claude_dir_tarball(self) -> bytes | None:
        """Extract tarball of ~/.claude directory from the sandbox.

        This captures Claude's full state including conversation logs,
        settings, and any other data stored during the run.

        Returns:
            Gzipped tarball of ~/.claude, or None if not available.
        """
        if self._sandbox is None:
            return None

        sb = self._sandbox
        try:
            # Check if ~/.claude exists
            check_proc = run_modal_command(
                sb, "test", "-d", "/home/agent/.claude", name="check-claude-dir"
            )
            if check_proc.wait() != 0:
                logger.info("No ~/.claude directory found in sandbox")
                return None

            # Create tarball
            run_modal_command(
                sb,
                "tar",
                "-czf",
                "/tmp/claude_dir.tar.gz",
                "-C",
                "/home/agent",
                ".claude",
                name="tar-claude-dir",
            ).wait()

            # Read tarball
            with sb.open("/tmp/claude_dir.tar.gz", "rb") as f:
                return f.read()
        except Exception as e:
            logger.error(f"Error extracting ~/.claude tarball: {e}")
            return None

    def cleanup(self) -> None:
        """Terminate the Modal sandbox."""
        if self._sandbox:
            print("Terminating Modal sandbox...", file=sys.stderr)
            self._sandbox.terminate()
            self._sandbox = None
