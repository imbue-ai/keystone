"""
Modal Sandbox with SSH access for Docker builds.

This uses Modal's Sandbox feature which provides an interactive container
you can SSH into.

Usage:
    uv run python sandbox_ssh.py
"""

import time

import modal

TEST_DOCKERFILE = """\
FROM alpine:latest
COPY hello.sh /hello.sh
RUN chmod +x /hello.sh
CMD ["/hello.sh"]
"""

HELLO_SCRIPT = """\
#!/bin/sh
echo "Hello World from Docker!"
echo "Build succeeded at $(date)"
"""

# Image with Docker installed
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install(
        "ca-certificates",
        "curl",
        "gnupg",
        "lsb-release",
        "git",
        "vim",
    )
    # Install Docker
    .run_commands(
        "install -m 0755 -d /etc/apt/keyrings",
        "curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg",
        "chmod a+r /etc/apt/keyrings/docker.gpg",
        'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list',
    )
    .apt_install(
        "docker-ce",
        "docker-ce-cli",
        "containerd.io",
        "docker-buildx-plugin",
    )
)

app = modal.App("docker-sandbox-ssh", image=image)


def main():
    """Create a sandbox and provide SSH access."""
    print("Creating Modal sandbox with Docker...")

    # Initialize app lazily
    app = modal.App.lookup("bootstrap-devcontainer-sandbox", create_if_missing=True)

    # Create a sandbox - this gives us an interactive container
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=3600,  # 1 hour
        encrypted_ports=[22],  # For SSH access
        experimental_options={"enable_docker": True},  # Enable Docker-in-Docker!
    )

    print(f"Sandbox created! ID: {sb.object_id}")

    # Start Docker daemon in the sandbox
    print("Starting Docker daemon...")
    sb.exec("dockerd", "--host=unix:///var/run/docker.sock")

    # Give it a moment to start
    time.sleep(5)

    # Create test project directory with Dockerfile
    print("Setting up test Docker project in /root/test-build...")
    sb.exec("mkdir", "-p", "/root/test-build").wait()
    sb.exec("sh", "-c", f"cat > /root/test-build/Dockerfile << 'EOF'\n{TEST_DOCKERFILE}EOF").wait()
    sb.exec("sh", "-c", f"cat > /root/test-build/hello.sh << 'EOF'\n{HELLO_SCRIPT}EOF").wait()

    # Test Docker
    print("\nTesting Docker...")
    test_proc = sb.exec("docker", "version")
    test_proc.wait()
    print(test_proc.stdout.read())

    # Get tunnel URL for SSH-like access
    tunnel = sb.tunnels()
    print(f"\nSandbox tunnels: {tunnel}")

    print("\n" + "=" * 60)
    print("Sandbox is running! You can interact via Modal CLI:")
    print(f"  modal sandbox exec {sb.object_id} bash")
    print("\nOr run commands directly:")
    print(f"  modal sandbox exec {sb.object_id} docker build .")
    print("\nTo terminate:")
    print(f"  modal sandbox terminate {sb.object_id}")
    print("=" * 60)

    # Keep running - user can Ctrl+C to exit
    print("\nPress Ctrl+C to terminate sandbox...")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        print("\nTerminating sandbox...")
        sb.terminate()
        print("Done!")


if __name__ == "__main__":
    main()
