import sys

import modal


def main():
    if len(sys.argv) < 2:
        print("Usage: python inspect_sandbox.py <sandbox_id>")
        sys.exit(1)

    sandbox_id = sys.argv[1]
    print(f"Connecting to sandbox {sandbox_id}...")

    try:
        sb = modal.Sandbox.from_id(sandbox_id)

        print("--- Running simple claude command WITH PTY ---")
        p = sb.exec(
            "claude",
            "--dangerously-skip-permissions",
            "-p",
            "hello",
            "--verbose",
            pty_info=modal.PtyInfo(options=modal.PtyInfo.Options(rows=24, cols=80)),
        )

        # Iterate output (stdout acts as merged stream with pty usually? or just stdout)
        print("Reading output...")

        # For pty, stderr might be merged into stdout or handled differently.
        # But let's just read stdout.

        found_output = False
        for chunk in p.stdout:
            sys.stdout.write(chunk)
            found_output = True

        print("\nFinished stdout iterator.")
        if not found_output:
            print("No output received.")

        p.wait()
        print(f"Exit code: {p.returncode}")

    except Exception as e:
        print(f"Error: {e}")


if __name__ == "__main__":
    main()
