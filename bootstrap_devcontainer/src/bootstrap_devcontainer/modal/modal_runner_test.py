import logging

import modal

from bootstrap_devcontainer.modal.image import create_modal_image
from bootstrap_devcontainer.modal.modal_runner import run_modal_command, wait_for_docker

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


def test_run_modal_command_interleaved_streaming():
    """
    Verify that run_modal_command correctly handles interleaved stdout/stderr.
    This uses a real Modal sandbox.
    """
    print("Connecting to Modal...")
    app = modal.App.lookup("bootstrap-devcontainer-test", create_if_missing=True)
    image = create_modal_image()

    sb = modal.Sandbox.create(app=app, image=image, timeout=300)
    try:
        print(f"Sandbox created: {sb.object_id}")

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

        print("\nExecuting command...")
        proc = run_modal_command(sb, "bash", "-c", bash_script, pty=False, capture=True)
        events = list(proc.stream())

        # Print events for inspection
        print("\nCaptured events:")
        for e in events:
            print(f"[{e.stream}] {e.line}")

        # Check that we got the interleaved output
        stdout_lines = [e.line for e in events if e.stream == "stdout" and "OUT:" in e.line]
        stderr_lines = [e.line for e in events if e.stream == "stderr" and "ERR:" in e.line]

        print(f"\nFound {len(stdout_lines)} stdout lines and {len(stderr_lines)} stderr lines.")

        assert len(stdout_lines) == 3, f"Expected 3 stdout lines, found {len(stdout_lines)}"
        assert len(stderr_lines) == 3, f"Expected 3 stderr lines, found {len(stderr_lines)}"

        # Verify interleaving order roughly
        sequence_lines = [e.line for e in events if "OUT:" in e.line or "ERR:" in e.line]
        expected = ["ERR: 1", "OUT: 2", "ERR: 3", "OUT: 4", "ERR: 5", "OUT: 6"]
        assert sequence_lines == expected, (
            f"Order mismatch. Expected {expected}, got {sequence_lines}"
        )
    finally:
        sb.terminate()


def test_docker_readiness_and_run():
    """
    Verify that we can start dockerd, wait for it, and run a container.
    """
    print("\nConnecting to Modal for Docker test...")
    app = modal.App.lookup("bootstrap-devcontainer-test", create_if_missing=True)
    image = create_modal_image()

    print("Creating sandbox with Docker enabled...")
    # NOTE: experimental_options={"enable_docker": True} is required for Docker
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=300,
        experimental_options={"enable_docker": True},
    )
    try:
        print(f"Sandbox created: {sb.object_id}")

        print("Starting Docker daemon...")
        run_modal_command(sb, "/start-dockerd.sh", prefix="dockerd: ")

        print("Waiting for Docker readiness...")
        wait_for_docker(sb)
        print("Docker is ready!")

        print("Running 'docker run --network host hello-world'...")
        # hello-world is a very small image that prints a message and exits
        # Use --network host to avoid permission issues with creating network interfaces in the sandbox
        proc = run_modal_command(
            sb,
            "docker",
            "run",
            "--network",
            "host",
            "hello-world",
            prefix="docker-run: ",
            capture=True,
        )
        events = list(proc.stream())

        print("\nDocker output:")
        for e in events:
            print(f"[{e.stream}] {e.line}")

        # Basic check that it worked
        output_concat = "".join(e.line for e in events)
        assert "Hello from Docker!" in output_concat
        assert proc.wait() == 0

    finally:
        sb.terminate()


if __name__ == "__main__":
    test_run_modal_command_interleaved_streaming()
    test_docker_readiness_and_run()
