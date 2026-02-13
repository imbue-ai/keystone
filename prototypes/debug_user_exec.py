"""
Debug user execution in Modal sandbox.
"""

import sys

import modal

from keystone.modal_runner import create_modal_image

# Reuse the image definition from the actual runner to ensure we have the same configuration
image = create_modal_image()
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
        # 1. Check if user exists
        print("\n--- Checking correct user ---")
        proc = sb.exec("id", "agent")
        print(proc.stdout.read())
        proc.wait()
        print(f"Exit code: {proc.returncode}")

        # 2. Try running as user
        print("\n--- Running whoami as agent ---")
        full_cmd = "su agent -c 'whoami'"
        proc = sb.exec("sh", "-c", full_cmd, pty=True)
        for line in proc.stdout:
            sys.stdout.write(line)
        proc.wait()
        print(f"Exit code: {proc.returncode}")

        # 3. Try running claude help as user
        print("\n--- Running claude --help as agent ---")
        full_cmd = "su agent -c 'claude --help'"
        proc = sb.exec("sh", "-c", full_cmd, pty=True)
        for line in proc.stdout:
            sys.stdout.write(line)
        proc.wait()
        print(f"Exit code: {proc.returncode}")

    finally:
        print("\nTerminating...")
        sb.terminate()


if __name__ == "__main__":
    main()
