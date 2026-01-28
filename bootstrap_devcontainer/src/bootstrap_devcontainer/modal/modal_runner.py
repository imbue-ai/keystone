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
import time
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import modal

from bootstrap_devcontainer.agent_runner import AgentRunner, StreamEvent
from bootstrap_devcontainer.modal.image import create_modal_image

# Script directory for bundled files


def _stream_reader(
    stream: Iterable[str],
    stream_name: str,
    output_queue: "queue.Queue[StreamEvent | None] | None",
    prefix: str = "",
) -> None:
    """Read lines from stream, log them, and optionally put them on the queue."""
    logger = logging.getLogger("bootstrap_devcontainer.modal")
    for line in stream:
        clean_line = line.rstrip("\n")
        # Log immediately to the Python logging system
        logger.info(f"{prefix}[{stream_name}] {clean_line}")
        if output_queue is not None:
            output_queue.put(StreamEvent(stream=stream_name, line=clean_line))
    if output_queue is not None:
        output_queue.put(None)  # Signal this stream is done


def stream_modal_process(
    proc: Any,
    output_queue: "queue.Queue[StreamEvent | None] | None" = None,
    prefix: str = "",
) -> Iterator[StreamEvent]:
    """
    Stream stdout and stderr from a Modal process using threads.

    If output_queue is provided, it will also put events there.
    Returns an iterator of StreamEvents if output_queue is None.
    """
    # If no queue provided, we use a local one to drive the iterator
    q = output_queue if output_queue is not None else queue.Queue()

    stdout_thread = threading.Thread(
        target=_stream_reader,
        args=(proc.stdout, "stdout", q, prefix),
        name=f"modal-stdout-reader-{prefix}",
        daemon=True,
    )
    stderr_thread = threading.Thread(
        target=_stream_reader,
        args=(proc.stderr, "stderr", q, prefix),
        name=f"modal-stderr-reader-{prefix}",
        daemon=True,
    )

    stdout_thread.start()
    stderr_thread.start()

    if output_queue is None:
        # We are the consumer of the local queue
        streams_done = 0
        while streams_done < 2:
            event = q.get()
            if event is None:
                streams_done += 1
            else:
                yield event
        stdout_thread.join()
        stderr_thread.join()
        proc.wait()
    else:
        # Caller will handle the queue or we are in background
        pass


def run_modal_command(
    sb: modal.Sandbox, *args: str, background: bool = False, prefix: str = "", **kwargs: Any
) -> Iterator[StreamEvent]:
    """
    Execute a command in a Modal sandbox and stream its output.

    Args:
        sb: The Modal sandbox instance.
        *args: Command and arguments to execute.
        background: If True, start streaming in background threads and return immediately.
        prefix: Prefix for log messages.
        **kwargs: Additional arguments for sb.exec() (e.g., pty, env).

    Yields:
        StreamEvent objects if background=False.
    """
    proc = sb.exec(*args, **kwargs)
    if background:
        # Start background streaming to logs (None queue means just log)
        stream_modal_process(proc, output_queue=None, prefix=prefix)
        return
    else:
        yield from stream_modal_process(proc, prefix=prefix)


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
        finally:
            if self._sandbox:
                self._sandbox.terminate()
                self._sandbox = None

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
        yield from run_modal_command(sb, "/start-dockerd.sh", background=True, prefix="dockerd: ")
        time.sleep(10)  # Give Docker time to start

        # 2. Upload project
        yield StreamEvent(stream="stderr", line="Uploading project to sandbox...")
        project_tarball = _create_project_tarball(project_root)
        yield from run_modal_command(sb, "mkdir", "-p", "/project")

        # Write tarball via base64 encoding (Modal stdin API uses bytes differently)
        tarball_b64 = base64.b64encode(project_tarball).decode("ascii")
        yield from run_modal_command(
            sb, "sh", "-c", f"echo '{tarball_b64}' | base64 -d | tar -xzf - -C /project"
        )
        yield from run_modal_command(sb, "chown", "-R", "agent:agent", "/project")

        # 3. Set up Claude auth
        yield StreamEvent(stream="stderr", line="Setting up Claude authentication...")
        auth_env = _read_claude_auth()

        if "CLAUDE_CONFIG_JSON" in auth_env:
            # Write config file
            config_content = auth_env["CLAUDE_CONFIG_JSON"]
            yield from run_modal_command(sb, "mkdir", "-p", "/home/agent")
            yield from run_modal_command(
                sb,
                "sh",
                "-c",
                f"cat > /home/agent/.claude.json << 'EOF'\n{config_content}\nEOF",
            )
            yield from run_modal_command(sb, "chown", "agent:agent", "/home/agent/.claude.json")

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
        cmd_parts = [
            agent_cmd,
            "--dangerously-skip-permissions",
            "-p",
            prompt,
            "--output-format",
            "stream-json",
            "--verbose",
            "--max-budget-usd",
            str(max_budget_usd),
        ]

        # Run agent in project directory
        # Run agent in project directory
        # We write a wrapper script to avoid quoting hell with 'su -c'
        agent_script_content = f"""#!/bin/bash
set -e
cd /project
{f"export ANTHROPIC_API_KEY={shlex.quote(env_vars['ANTHROPIC_API_KEY'])}" if "ANTHROPIC_API_KEY" in env_vars else ""}
exec {shlex.join(cmd_parts)}
"""
        # Upload script
        # encode to base64 to avoid heredoc issues
        script_b64 = base64.b64encode(agent_script_content.encode()).decode()
        yield from run_modal_command(
            sb, "sh", "-c", f"echo '{script_b64}' | base64 -d > /run_agent.sh"
        )
        yield from run_modal_command(sb, "chmod", "+x", "/run_agent.sh")
        yield from run_modal_command(sb, "chown", "agent:agent", "/run_agent.sh")

        yield StreamEvent(
            stream="stderr",
            line="Executing: su agent -c /run_agent.sh",
        )
        agent_proc = sb.exec(
            "su",
            "agent",
            "-c",
            "/run_agent.sh",
            env=None,
            pty=False,
        )
        yield from stream_modal_process(agent_proc, prefix="agent: ")
        self._exit_code = agent_proc.returncode or 0

        # 5. Extract .devcontainer directory
        yield StreamEvent(stream="stderr", line="Extracting .devcontainer from sandbox...")
        # Use base64 to handle binary data through text streams
        tar_proc = sb.exec("sh", "-c", "tar -czf - -C /project .devcontainer | base64")
        tar_lines = []
        for event in stream_modal_process(tar_proc, prefix="tar: "):
            if event.stream == "stdout":
                tar_lines.append(event.line)
        self._devcontainer_tarball = base64.b64decode("".join(tar_lines))

    @property
    def exit_code(self) -> int:
        return self._exit_code

    def get_devcontainer_tarball(self) -> bytes:
        return self._devcontainer_tarball
