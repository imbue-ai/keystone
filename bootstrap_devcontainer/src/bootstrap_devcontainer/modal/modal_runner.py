"""Modal-based agent runner for running bootstrap agent in cloud sandbox."""

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
    DEFAULT_AGENT_TIMEOUT,
    AgentRunner,
    StreamEvent,
    build_claude_command,
)
from bootstrap_devcontainer.git_utils import create_git_archive_bytes
from bootstrap_devcontainer.modal.image import create_modal_image

# Script directory for bundled files


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
        for line in stream:
            clean_line = line.rstrip("\n")
            # Log immediately to Python's logging system
            # Format: [prefix] stream: line (or just [prefix] line for stdout)
            if stream_name == "stderr":
                logger.info(f"[{self.prefix}] STDERR: {clean_line}")
            else:
                logger.info(f"[{self.prefix}] STDOUT: {clean_line}")
            if self._queue is not None:
                self._queue.put(StreamEvent(stream=stream_name, line=clean_line))

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
    """Run agent in a Modal sandbox with Docker support."""

    def __init__(self, timeout_seconds: int = 3600) -> None:
        self._timeout_seconds = timeout_seconds
        self._exit_code: int = 1
        self._devcontainer_tarball: bytes = b""
        self._sandbox: modal.Sandbox | None = None

    def run(
        self,
        prompt: str,
        project_root: Path,
        max_budget_usd: float,
        agent_cmd: str,
    ) -> Iterator[StreamEvent]:
        """Run the agent in a Modal sandbox."""
        modal.enable_output()

        print("Creating Modal sandbox with Docker...", file=sys.stderr)

        # Get or create app
        app = modal.App.lookup("bootstrap-devcontainer-sandbox", create_if_missing=True)

        # Create image
        image = create_modal_image()

        # Create sandbox with Docker enabled
        self._sandbox = modal.Sandbox.create(
            app=app,
            image=image,
            timeout=self._timeout_seconds,
            region="us-west-2",
            experimental_options={"enable_docker": True},
        )

        # Print sandbox info for debugging
        sandbox_id = self._sandbox.object_id
        print(f"Modal sandbox created: {sandbox_id}", file=sys.stderr)
        print("  Dashboard: https://modal.com/apps/bootstrap-devcontainer-sandbox", file=sys.stderr)
        print(f"  Shell:     modal shell {sandbox_id}", file=sys.stderr)

        try:
            yield from self._run_in_sandbox(prompt, project_root, max_budget_usd, agent_cmd)
        except Exception:
            if self._sandbox:
                self._sandbox.terminate()
                self._sandbox = None
            raise

    def _run_in_sandbox(
        self,
        prompt: str,
        project_root: Path,
        max_budget_usd: float,
        agent_cmd: str,
    ) -> Iterator[StreamEvent]:
        """Execute agent workflow inside the sandbox."""
        assert self._sandbox is not None
        sb = self._sandbox

        # 1. Start Docker daemon
        # We start it in the background by not calling .wait() here
        run_modal_command(sb, "/start-dockerd.sh", name="dockerd")

        # 2. Wait for Docker to be ready
        yield StreamEvent(stream="stderr", line="Waiting for Docker daemon to be ready...")
        run_modal_command(sb, "/wait_for_docker.sh", name="docker-wait").wait()

        # 2. Upload project (using git archive for clean, reproducible content)
        yield StreamEvent(stream="stderr", line="Uploading project to sandbox via git archive...")
        project_tarball = create_git_archive_bytes(project_root)
        run_modal_command(sb, "mkdir", "-p", "/project", name="upload").wait()

        # Write tarball using Modal's native filesystem API
        with sb.open("/tmp/project.tar.gz", "wb") as f:
            f.write(project_tarball)
        run_modal_command(
            sb,
            "tar",
            "-xzf",
            "/tmp/project.tar.gz",
            "-C",
            "/project",
            name="upload",
        ).wait()
        run_modal_command(sb, "chown", "-R", "agent:agent", "/project", name="upload").wait()

        # 3. Set up Claude auth
        yield StreamEvent(stream="stderr", line="Setting up Claude authentication...")
        auth_env = _read_claude_auth()

        # 4. Run the agent
        yield StreamEvent(stream="stderr", line="Starting agent...")

        # Debug: check what auth we have
        if "ANTHROPIC_API_KEY" in auth_env:
            yield StreamEvent(stream="stderr", line="Using ANTHROPIC_API_KEY for authentication")
        else:
            yield StreamEvent(stream="stderr", line="WARNING: No ANTHROPIC_API_KEY found!")

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
exec timeout {DEFAULT_AGENT_TIMEOUT} {shlex.join(cmd_parts)}
"""
        # Upload script using Modal's native filesystem API
        with sb.open("/run_agent.sh", "w") as f:
            f.write(agent_script_content)
        run_modal_command(sb, "chmod", "+x", "/run_agent.sh", name="setup").wait()
        run_modal_command(sb, "chown", "agent:agent", "/run_agent.sh", name="setup").wait()

        yield StreamEvent(
            stream="stderr",
            line="Executing: su agent -c /run_agent.sh",
        )
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
        yield StreamEvent(stream="stderr", line="Extracting .devcontainer from sandbox...")
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
        project_root: Path,
        test_artifacts_dir: Path,
    ) -> Iterator[StreamEvent]:
        """Run verification tests using Modal's from_dockerfile (cached image builds)."""
        dockerfile_path = project_root / ".devcontainer" / "Dockerfile"

        if not dockerfile_path.exists():
            yield StreamEvent(stream="stderr", line=f"Dockerfile not found at {dockerfile_path}")
            return

        yield StreamEvent(stream="stderr", line="Building devcontainer image via Modal...")

        # Build image using Modal's cached from_dockerfile
        image = modal.Image.from_dockerfile(
            path=dockerfile_path,
            context_dir=project_root,
        )

        app = modal.App.lookup("bootstrap-devcontainer-verify", create_if_missing=True)

        yield StreamEvent(stream="stderr", line="Running tests in Modal sandbox...")

        sandbox = modal.Sandbox.create(
            app=app,
            image=image,
            timeout=self._timeout_seconds,
        )

        try:
            # Run the test script
            proc = sandbox.exec("bash", "/project_src/.devcontainer/run_all_tests.sh")

            for line in proc.stdout:
                yield StreamEvent(stream="stdout", line=line.rstrip("\n"))
            for line in proc.stderr:
                yield StreamEvent(stream="stderr", line=line.rstrip("\n"))

            proc.wait()
            test_exit_code = proc.returncode

            # Extract test artifacts using Modal's native filesystem API
            yield StreamEvent(stream="stderr", line="Extracting test artifacts...")
            tar_proc = sandbox.exec(
                "tar", "-czf", "/tmp/test_artifacts.tar.gz", "-C", "/test_artifacts", "."
            )
            tar_proc.wait()

            try:
                with sandbox.open("/tmp/test_artifacts.tar.gz", "rb") as f:
                    tarball = f.read()
                test_artifacts_dir.mkdir(parents=True, exist_ok=True)
                with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
                    tar.extractall(test_artifacts_dir)
                yield StreamEvent(stream="stderr", line="Test artifacts extracted.")
            except Exception as e:
                yield StreamEvent(stream="stderr", line=f"Error extracting artifacts: {e}")

            if test_exit_code == 0:
                yield StreamEvent(stream="stderr", line="Verification successful!")
            else:
                yield StreamEvent(
                    stream="stderr", line=f"Test run failed with return code {test_exit_code}"
                )
        finally:
            sandbox.terminate()

    def cleanup(self) -> None:
        """Terminate the Modal sandbox."""
        if self._sandbox:
            print("Terminating Modal sandbox...", file=sys.stderr)
            self._sandbox.terminate()
            self._sandbox = None
