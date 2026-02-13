import logging
import os
import shlex

import modal

from bootstrap_devcontainer.agent_runner import build_claude_command
from bootstrap_devcontainer.modal.image import create_modal_image
from bootstrap_devcontainer.modal.modal_runner import run_modal_command

# Configure logging to silence noisy third-party libraries
logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s: %(message)s",
)
# Enable DEBUG only for our project
logging.getLogger("bootstrap_devcontainer").setLevel(logging.DEBUG)
# Specifically silence known noisy hpack/http2 logs
logging.getLogger("hpack").setLevel(logging.INFO)
logging.getLogger("httpcore").setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.INFO)

logger = logging.getLogger("bootstrap_devcontainer.modal_test")


def test_run_modal_command_interleaved_streaming():
    """
    Verify that run_modal_command correctly handles interleaved stdout/stderr.
    This uses a real Modal sandbox.
    """
    logger.info("Connecting to Modal...")
    app = modal.App.lookup("bootstrap-devcontainer-test", create_if_missing=True)
    image = create_modal_image()

    sb = modal.Sandbox.create(app=app, image=image, timeout=300)
    try:
        logger.info(f"Sandbox created: {sb.object_id}")

        # Command that produces interleaved output on stdout/stderr
        bash_script = """
        for i in $(seq 1 6); do
          if (( i % 2 == 0 )); then
            echo "OUT: $i"
          else
            echo "ERR: $i" >&2
          fi
          sleep 0.2
        done
        """

        logger.info("\nExecuting command...")
        proc = run_modal_command(
            sb, "bash", "-c", bash_script, pty=False, capture=True, name="test"
        )
        events = list(proc.stream())

        # Log events for inspection
        logger.info("\nCaptured events:")
        for e in events:
            logger.info(f"[{e.stream}] {e.line}")

        # Check that we got the interleaved output
        stdout_lines = [e.line for e in events if e.stream == "stdout" and "OUT:" in e.line]
        stderr_lines = [e.line for e in events if e.stream == "stderr" and "ERR:" in e.line]

        logger.info(
            f"\nFound {len(stdout_lines)} stdout lines and {len(stderr_lines)} stderr lines."
        )

        assert len(stdout_lines) == 3, f"Expected 3 stdout lines, found {len(stdout_lines)}"
        assert len(stderr_lines) == 3, f"Expected 3 stderr lines, found {len(stderr_lines)}"

        # Verify all expected lines are present
        sequence_lines = {e.line for e in events if "OUT:" in e.line or "ERR:" in e.line}
        expected = {"ERR: 1", "OUT: 2", "ERR: 3", "OUT: 4", "ERR: 5", "OUT: 6"}
        assert sequence_lines == expected, (
            f"Line mismatch. Expected {expected}, got {sequence_lines}"
        )
    finally:
        sb.terminate()


def test_docker_readiness_and_run():
    """
    Verify that we can start dockerd, wait for it, and run a container.
    """
    logger.info("\nConnecting to Modal for Docker test...")
    app = modal.App.lookup("bootstrap-devcontainer-test", create_if_missing=True)
    image = create_modal_image()

    logger.info("Creating sandbox with Docker enabled...")
    # NOTE: experimental_options={"enable_docker": True} is required for Docker
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=300,
        experimental_options={"enable_docker": True},
    )
    try:
        logger.info(f"Sandbox created: {sb.object_id}")

        logger.info("Starting Docker daemon...")
        run_modal_command(sb, "/start-dockerd.sh", name="dockerd")

        logger.info("Waiting for Docker readiness...")
        run_modal_command(sb, "/wait_for_docker.sh", name="docker-wait").wait()
        logger.info("Docker is ready!")

        logger.info("Running 'docker run --network host hello-world'...")
        # hello-world is a very small image that prints a message and exits
        # Use --network host to avoid permission issues with creating network interfaces in the sandbox
        proc = run_modal_command(
            sb,
            "docker",
            "run",
            "--network",
            "host",
            "hello-world",
            name="docker-run",
            capture=True,
        )
        events = list(proc.stream())

        logger.info("\nDocker output:")
        for e in events:
            logger.info(f"[{e.stream}] {e.line}")

        # Basic check that it worked
        output_concat = "".join(e.line for e in events)
        assert "Hello from Docker!" in output_concat
        assert proc.wait() == 0

    finally:
        sb.terminate()


def test_claude_streaming():
    """
    Verify that Claude CLI can run and stream its output.
    """
    logger.info("\nConnecting to Modal for Claude test...")
    app = modal.App.lookup("bootstrap-devcontainer-test", create_if_missing=True)
    image = create_modal_image()

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        logger.info("Skipping Claude test: ANTHROPIC_API_KEY not set in environment.")
        return

    logger.info("Creating sandbox with ANTHROPIC_API_KEY...")
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=300,
        env={"ANTHROPIC_API_KEY": api_key},
    )
    try:
        logger.info(f"Sandbox created: {sb.object_id}")

        logger.info("Running 'claude -p ...'...")
        # Run Claude as agent user with explicit env
        # Use a longer timeout as Claude takes time to initialize
        claude_cmd = shlex.join(
            [
                f"ANTHROPIC_API_KEY={shlex.quote(api_key)}",
                "timeout",
                "60",
                *build_claude_command("Figure out what OS you are on and provide evidence.", 0.10),
            ]
        )

        proc = run_modal_command(
            sb,
            "su",
            "agent",
            "-c",
            claude_cmd,
            name="claude",
            capture=True,
            # Claude just plain doesn't work with pty=False
            pty=True,
        )

        logger.info("\nStreaming Claude output:")
        found_content = False
        for e in proc.stream():
            logger.info(f"[{e.stream}] {e.line}")
            if e.line.strip():
                found_content = True

        assert found_content, "Claude produced no output"
        # timeout returns 124 if it killed the process, but we want to see the output
        exit_code = proc.wait()
        logger.info(f"\nClaude finished with exit code {exit_code}")
        assert exit_code == 0
        logger.info("\nClaude test successful!")

    finally:
        sb.terminate()
