"""
Minimal usage of Modal Sandbox for streaming output.

Usage:
    uv run python sandbox_stream_output.py
"""

import sys
import time

import modal

# Minimal image
image = modal.Image.debian_slim(python_version="3.11")

app = modal.App.lookup("bootstrap-devcontainer-debug", create_if_missing=True)


def main():
    modal.enable_output()
    print("Creating Modal sandbox...")

    sb = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=600,
    )
    print(f"Sandbox created: {sb.object_id}")

    try:
        # Run a simple loop that prints and sleeps
        cmd = ["/bin/bash", "-c", "for i in $(seq 1 10); do echo $i; sleep 1; done"]
        print(f"Running command: {cmd}")

        start_time = time.time()

        # Test normal execution (stdout buffering might happen)
        print("\n--- Standard Execution ---")
        proc = sb.exec(*cmd)

        # Iterate over stdout
        for line in proc.stdout:
            elapsed = time.time() - start_time
            print(f"[{elapsed:.2f}s] {line}", end="")
            sys.stdout.flush()

        proc.wait()
        print(f"\nExit code: {proc.returncode}")

        # Test with PTY execution (should be unbuffered)
        print("\n--- PTY Execution ---")
        start_time = time.time()
        proc_pty = sb.exec(*cmd, pty=True)

        for line in proc_pty.stdout:
            elapsed = time.time() - start_time
            print(f"[{elapsed:.2f}s] {line}", end="")
            sys.stdout.flush()

        proc_pty.wait()
        print(f"\nExit code: {proc_pty.returncode}")

    finally:
        print("\nTerminating...")
        sb.terminate()


if __name__ == "__main__":
    main()
