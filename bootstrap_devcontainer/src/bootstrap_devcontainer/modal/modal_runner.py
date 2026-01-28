"""Modal-based agent runner for running bootstrap agent in cloud sandbox."""

import base64
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
from typing import Any

import modal

from bootstrap_devcontainer.agent_runner import (
    DEFAULT_AGENT_TIMEOUT,
    AgentRunner,
    StreamEvent,
    build_claude_command,
)
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

    def _stream_reader(self, stream: Iterable[str], stream_name: str) -> None:
        logger = logging.getLogger("bootstrap_devcontainer.modal")
        for line in stream:
            clean_line = line.rstrip("\n")
            # Log immediately to Python's logging system
            logger.info(f"{self.prefix}[{stream_name}] {clean_line}")
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
    sb: modal.Sandbox, *args: str, capture: bool = False, prefix: str = "", **kwargs: Any
) -> ManagedProcess:
    """Helper to execute a command and return a ManagedProcess."""
    logger = logging.getLogger("bootstrap_devcontainer.modal")
    logger.info(f"{prefix}Running: {shlex.join(args)}")
    proc = sb.exec(*args, **kwargs)
    return ManagedProcess(proc, prefix=prefix, capture=capture)


_SCRIPT_DIR = Path(__file__).parent


def _create_project_tarball(project_root: Path) -> bytes:
    """Create a tarball of the project directory."""
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(project_root, arcname=".")
    return buf.getvalue()


def _read_claude_auth() -> dict[str, str]:
    """Read Claude authentication from ~/.claude.json or environment."""
    auth_env: dict[str, str] = {}

    # Check for API key in environment first
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        auth_env["ANTHROPIC_API_KEY"] = api_key
        return auth_env

    # Try ~/.claude.json
    claude_config = Path.home() / ".claude.json"
    if claude_config.exists():
        auth_env["CLAUDE_CONFIG_JSON"] = claude_config.read_text()

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
        run_modal_command(sb, "/start-dockerd.sh", prefix="dockerd: ")

        # 2. Wait for Docker to be ready
        yield StreamEvent(stream="stderr", line="Waiting for Docker daemon to be ready...")
        run_modal_command(sb, "/wait_for_docker.sh", prefix="docker-wait: ").wait()

        # 2. Upload project
        yield StreamEvent(stream="stderr", line="Uploading project to sandbox...")
        project_tarball = _create_project_tarball(project_root)
        run_modal_command(sb, "mkdir", "-p", "/project").wait()

        # Write tarball via base64 encoding (Modal stdin API uses bytes differently)
        tarball_b64 = base64.b64encode(project_tarball).decode("ascii")
        run_modal_command(
            sb, "sh", "-c", f"echo '{tarball_b64}' | base64 -d | tar -xzf - -C /project"
        ).wait()
        run_modal_command(sb, "chown", "-R", "agent:agent", "/project").wait()

        # 3. Set up Claude auth
        yield StreamEvent(stream="stderr", line="Setting up Claude authentication...")
        auth_env = _read_claude_auth()

        if "CLAUDE_CONFIG_JSON" in auth_env:
            # Write config file
            config_content = auth_env["CLAUDE_CONFIG_JSON"]
            run_modal_command(sb, "mkdir", "-p", "/home/agent").wait()
            run_modal_command(
                sb,
                "sh",
                "-c",
                f"cat > /home/agent/.claude.json << 'EOF'\n{config_content}\nEOF",
            ).wait()
            run_modal_command(sb, "chown", "agent:agent", "/home/agent/.claude.json").wait()

        # 4. Run the agent
        yield StreamEvent(stream="stderr", line="Starting agent...")

        # Debug: check what auth we have
        if "CLAUDE_CONFIG_JSON" in auth_env:
            yield StreamEvent(stream="stderr", line="Using ~/.claude.json for authentication")
        elif "ANTHROPIC_API_KEY" in auth_env:
            yield StreamEvent(stream="stderr", line="Using ANTHROPIC_API_KEY for authentication")
        else:
            yield StreamEvent(stream="stderr", line="WARNING: No Claude authentication found!")

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
        # Upload script
        # encode to base64 to avoid heredoc issues
        script_b64 = base64.b64encode(agent_script_content.encode()).decode()
        run_modal_command(sb, "sh", "-c", f"echo '{script_b64}' | base64 -d > /run_agent.sh").wait()
        run_modal_command(sb, "chmod", "+x", "/run_agent.sh").wait()
        run_modal_command(sb, "chown", "agent:agent", "/run_agent.sh").wait()

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
            prefix="agent: ",
            capture=True,
        )
        yield from agent.stream()
        self._exit_code = agent.wait()

        # 5. Extract .devcontainer directory
        yield StreamEvent(stream="stderr", line="Extracting .devcontainer from sandbox...")
        # Use base64 to handle binary data through text streams
        tar_proc = run_modal_command(
            sb,
            "sh",
            "-c",
            "tar -czf - -C /project .devcontainer | base64",
            prefix="tar: ",
            capture=True,
        )
        tar_lines = []
        for event in tar_proc.stream():
            if event.stream == "stdout":
                tar_lines.append(event.line)
        self._devcontainer_tarball = base64.b64decode("".join(tar_lines))

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def get_devcontainer_tarball(self) -> bytes:
        return self._devcontainer_tarball

    def _ensure_sandbox(self) -> Iterator[StreamEvent]:
        """Ensure the sandbox and Docker are running."""
        if self._sandbox is not None:
            return

        yield StreamEvent(stream="stderr", line="Initializing Modal sandbox for verification...")
        app = modal.App.lookup("bootstrap-devcontainer-sandbox", create_if_missing=True)
        image = create_modal_image()
        self._sandbox = modal.Sandbox.create(
            app=app,
            image=image,
            timeout=self._timeout_seconds,
            region="us-west-2",
            experimental_options={"enable_docker": True},
        )
        sb = self._sandbox

        # Start Docker daemon
        run_modal_command(sb, "/start-dockerd.sh", prefix="dockerd: ")

        # Wait for Docker to be ready
        yield StreamEvent(stream="stderr", line="Waiting for Docker daemon to be ready...")
        run_modal_command(sb, "/wait_for_docker.sh", prefix="docker-wait: ").wait()

    def verify(
        self,
        project_root: Path,
        test_artifacts_dir: Path,
    ) -> Iterator[StreamEvent]:
        """Run verification tests in the Modal sandbox."""
        yield from self._ensure_sandbox()
        assert self._sandbox is not None
        sb = self._sandbox

        # 1. Obliterate old code tree and write the new one
        yield StreamEvent(stream="stderr", line="Updating project tree in sandbox...")
        run_modal_command(sb, "rm", "-rf", "/project").wait()
        run_modal_command(sb, "mkdir", "-p", "/project").wait()

        project_tarball = _create_project_tarball(project_root)
        tarball_b64 = base64.b64encode(project_tarball).decode("ascii")

        run_modal_command(
            sb, "sh", "-c", f"echo '{tarball_b64}' | base64 -d | tar -xzf - -C /project"
        ).wait()
        run_modal_command(sb, "chown", "-R", "agent:agent", "/project").wait()

        # 2. Build the devcontainer image
        yield StreamEvent(stream="stderr", line="Building devcontainer image in sandbox...")
        image_name = f"bootstrap-test-{project_root.name.lower()}"
        # container_name is defined later, moved it to where it's first used.
        # container_name = f"bootstrap-test-{project_root.name.lower()}-container"

        build_cmd = [
            "devcontainer",
            "build",
            "--workspace-folder",
            "/project",
            "--image-name",
            image_name,
        ]
        build_proc = run_modal_command(sb, *build_cmd, prefix="build: ", capture=True)
        yield from build_proc.stream()
        build_exit_code = build_proc.wait()

        if build_exit_code != 0:
            yield StreamEvent(stream="stderr", line="Build failed")
            return

        # 3. Run tests
        container_name = f"bootstrap-test-{project_root.name.lower()}-container"
        test_cmd = [
            "docker",
            "run",
            "--name",
            container_name,
            "--network=host",
            image_name,
            "./.devcontainer/run_all_tests.sh",
        ]
        yield StreamEvent(stream="stderr", line="Running tests in sandbox...")
        test_proc = run_modal_command(sb, *test_cmd, prefix="test: ", capture=True)
        yield from test_proc.stream()
        test_exit_code = test_proc.wait()

        # 4. Extract artifacts
        yield StreamEvent(stream="stderr", line="Extracting test artifacts from sandbox...")
        # Copy from container to sandbox host first
        run_modal_command(sb, "mkdir", "-p", "/tmp/test_artifacts_extraction").wait()
        run_modal_command(
            sb,
            "docker",
            "cp",
            f"{container_name}:/test_artifacts/.",
            "/tmp/test_artifacts_extraction",
        ).wait()

        # Use tar | base64 to download artifacts from sandbox host to local machine
        cp_proc = run_modal_command(
            sb,
            "sh",
            "-c",
            "tar -czf - -C /tmp/test_artifacts_extraction . | base64",
            prefix="cp: ",
            capture=True,
        )

        cp_lines = []
        for event in cp_proc.stream():
            if event.stream == "stdout":
                cp_lines.append(event.line)

        cp_proc.wait()

        if cp_lines:
            try:
                tarball_b64 = "".join(cp_lines)
                tarball = base64.b64decode(tarball_b64)
                test_artifacts_dir.mkdir(parents=True, exist_ok=True)
                with tarfile.open(fileobj=io.BytesIO(tarball), mode="r:gz") as tar:
                    tar.extractall(test_artifacts_dir)
                yield StreamEvent(stream="stderr", line="Test artifacts extracted.")
            except Exception as e:
                yield StreamEvent(stream="stderr", line=f"Error extracting artifacts locally: {e}")
        else:
            yield StreamEvent(stream="stderr", line="No artifacts found to extract.")

        # 5. Clean up container
        run_modal_command(sb, "docker", "rm", container_name).wait()

        if test_exit_code == 0:
            yield StreamEvent(stream="stderr", line="Verification successful!")
        else:
            yield StreamEvent(
                stream="stderr", line=f"Test run failed with return code {test_exit_code}"
            )

    def cleanup(self) -> None:
        """Terminate the Modal sandbox."""
        if self._sandbox:
            print("Terminating Modal sandbox...", file=sys.stderr)
            self._sandbox.terminate()
            self._sandbox = None
