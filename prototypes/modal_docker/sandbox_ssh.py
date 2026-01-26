"""
Modal Sandbox with SSH access for Docker builds.

This uses Modal's Sandbox feature which provides an interactive container
you can SSH into.

Usage:
    uv run python sandbox_ssh.py
"""

import time
from pathlib import Path

import modal

TEST_DOCKERFILE = """\
FROM alpine:latest
COPY --chmod=755 hello.sh /hello.sh
CMD ["/hello.sh"]
"""

HELLO_SCRIPT = """\
#!/bin/sh
echo "Hello World from Docker!"
echo "Build succeeded at $(date)"
"""

SCRIPT_DIR = Path(__file__).parent

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
        "iptables",
        "iproute2",
        "wget",
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
        "docker-compose-plugin",
    )
    # Fix runc for Modal/gVisor compatibility
    .run_commands(
        "rm -f $(which runc) || true",
        "wget https://github.com/opencontainers/runc/releases/download/v1.3.0/runc.amd64",
        "chmod +x runc.amd64",
        "mv runc.amd64 /usr/local/bin/runc",
    )
    # Add start-dockerd script
    .add_local_file(
        str(SCRIPT_DIR / "start-dockerd.sh"),
        "/start-dockerd.sh",
    )
    .run_commands("chmod +x /start-dockerd.sh")
)

app = modal.App("docker-sandbox-ssh", image=image)


def main():
    """Create a sandbox and provide SSH access."""
    modal.enable_output()
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
    sb.exec("/start-dockerd.sh")

    # Give it a moment to start
    time.sleep(10)

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
    print("Sandbox is running!")
    print("\nIn another terminal, run:")
    print(f"  modal shell {sb.object_id}")
    print("\nThen test Docker with:")
    print("  cd /root/test-build && docker build -t hello-test . && docker run hello-test")
    print("=" * 60)

    # Keep running - user can Ctrl+C to exit
    print("\nPress Ctrl+C to terminate sandbox...")
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        pass

    print("\nTerminating sandbox...")
    sb.terminate()
    print("Done!")


if __name__ == "__main__":
    main()
