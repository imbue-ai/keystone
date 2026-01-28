"""
Check Claude CLI help for root execution options.
"""

import sys

import modal

from bootstrap_devcontainer.modal_runner import create_modal_image

# Reuse the image definition from the actual runner to ensure we have the same claude version
image = create_modal_image()
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
        print("Running: claude --help")
        proc = sb.exec("claude", "--help", pty=True)

        for line in proc.stdout:
            sys.stdout.write(line)

        proc.wait()
        print(f"\nExit code: {proc.returncode}")

    finally:
        print("\nTerminating...")
        sb.terminate()


if __name__ == "__main__":
    main()
