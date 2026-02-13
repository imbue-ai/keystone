"""
Minimal usage of Modal Sandbox for streaming output.

Usage:
    uv run python prototypes/modal_docker/sandbox_stream_output.py
"""

import sys
import time

import modal

# Minimal image
image = modal.Image.debian_slim(python_version="3.11")

app = modal.App.lookup("keystone-debug", create_if_missing=True)


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
        # Run a simple loop that alternates between stdout and stderr
        # Even to stdout, Odd to stderr
        bash_script = """
        for i in $(seq 1 10); do
          if (( i % 2 == 0 )); then
            echo "OUT: $i"
          else
            echo "ERR: $i" >&2
          fi
          sleep 0.5
        done
        """
        cmd = ["/bin/bash", "-c", bash_script]
        print(f"Running command: {cmd}")

        import threading

        def consume_stream(stream, label, start_time):
            for line in stream:
                elapsed = time.time() - start_time
                print(f"[{elapsed:.2f}s] [{label}] {line}", end="")
                sys.stdout.flush()

        # Test normal execution (stdout and stderr are separate)
        print("\n--- Standard Execution (Simultaneous Streams) ---")
        start_time = time.time()
        proc = sb.exec(*cmd)

        t1 = threading.Thread(target=consume_stream, args=(proc.stdout, "STDOUT", start_time))
        t2 = threading.Thread(target=consume_stream, args=(proc.stderr, "STDERR", start_time))
        t1.start()
        t2.start()

        t1.join()
        t2.join()

        proc.wait()
        print(f"\nExit code: {proc.returncode}")

        # Test with PTY execution (Modal merges stderr into stdout when pty=True)
        print("\n--- PTY Execution (Merged Streams) ---")
        print("Note: With pty=True, Modal typically merges stderr into stdout at the source.")
        start_time = time.time()
        proc_pty = sb.exec(*cmd, pty=True)

        for line in proc_pty.stdout:
            elapsed = time.time() - start_time
            print(f"[{elapsed:.2f}s] [PTY-OUT] {line}", end="")
            sys.stdout.flush()

        proc_pty.wait()
        print(f"\nExit code: {proc_pty.returncode}")

    finally:
        print("\nTerminating...")
        sb.terminate()


if __name__ == "__main__":
    main()
