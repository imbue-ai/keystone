#!/usr/bin/env python3
"""Docker build cache using Modal Volumes.

Instead of running a separate registry, this approach:
1. Mounts a shared Modal Volume to each sandbox
2. Uses BuildKit's local cache type pointing to the volume
3. Cache persists across sandbox runs

This is simpler and doesn't require a separate registry service.

Usage in modal_runner.py:
    # Create sandbox with cache volume
    cache_vol = modal.Volume.from_name("docker-build-cache", create_if_missing=True)

    sandbox = modal.Sandbox.create(
        ...
        volumes={"/docker-cache": cache_vol},
        ...
    )

    # Docker build with cache
    docker buildx build \
        --cache-to type=local,dest=/docker-cache \
        --cache-from type=local,src=/docker-cache \
        -t myimage .

Note: BuildKit local cache requires the docker-container driver, which
has networking issues inside Modal. Alternative approaches:
1. Use DOCKER_BUILDKIT=1 with inline cache
2. Pre-pull base images to a volume
3. Use Modal's image caching for the base environment
"""

import subprocess
import tempfile
import time
from pathlib import Path

import modal

app = modal.App("docker-cache-test")

# Shared cache volume
cache_volume = modal.Volume.from_name("docker-build-cache", create_if_missing=True)

# Image with Docker installed
image = (
    modal.Image.debian_slim(python_version="3.11")
    .apt_install("ca-certificates", "curl", "gnupg", "lsb-release", "iptables", "iproute2")
    .run_commands(
        "install -m 0755 -d /etc/apt/keyrings",
        "curl -fsSL https://download.docker.com/linux/debian/gpg | gpg --dearmor -o /etc/apt/keyrings/docker.gpg",
        "chmod a+r /etc/apt/keyrings/docker.gpg",
        'echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/debian $(lsb_release -cs) stable" > /etc/apt/sources.list.d/docker.list',
    )
    .apt_install("docker-ce", "docker-ce-cli", "containerd.io", "docker-buildx-plugin")
    .run_commands(
        "rm -f $(which runc) || true",
        "curl -L https://github.com/opencontainers/runc/releases/download/v1.3.0/runc.amd64 -o /usr/local/bin/runc",
        "chmod +x /usr/local/bin/runc",
    )
)


@app.function(
    image=image,
    volumes={"/docker-cache": cache_volume},
    timeout=600,
    experimental_options={"enable_docker": True},
)
def test_cached_build(use_cache: bool = True) -> dict:  # noqa: ARG001
    """Test docker build with volume-based caching."""
    results = {"docker_started": False, "build_time": 0, "cache_used": False}

    # Start Docker daemon
    subprocess.run(["rm", "-f", "/var/run/docker.pid"], capture_output=True)
    Path("/proc/sys/net/ipv4/ip_forward").write_text("1")

    subprocess.Popen(
        ["dockerd", "--iptables=false", "--ip6tables=false"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # Wait for Docker
    for _ in range(30):
        if subprocess.run(["docker", "info"], capture_output=True).returncode == 0:
            results["docker_started"] = True
            break
        time.sleep(1)

    if not results["docker_started"]:
        return results

    # Create test Dockerfile
    with tempfile.TemporaryDirectory() as tmpdir:
        dockerfile = Path(tmpdir) / "Dockerfile"
        dockerfile.write_text("""
FROM debian:bookworm-slim
RUN echo "layer 1 - $(date +%s)"
RUN echo "layer 2"
""")

        # Build
        start = time.time()
        cmd = ["docker", "build", "-t", "test:v1", "-f", str(dockerfile), tmpdir]

        ret = subprocess.run(cmd, capture_output=True, text=True)
        results["build_time"] = round(time.time() - start, 2)

        if ret.returncode == 0:
            results["cache_used"] = "CACHED" in ret.stderr or "Using cache" in ret.stderr

    # Check cache volume contents
    cache_path = Path("/docker-cache")
    if cache_path.exists():
        results["cache_files"] = len(list(cache_path.rglob("*")))

    return results


@app.local_entrypoint()
def main():
    """Test the caching approach."""
    print("Testing Docker build caching with Modal Volumes...")
    print("=" * 60)

    print("\nFirst build (cold):")
    r1 = test_cached_build.remote(use_cache=True)
    print(f"  Docker started: {r1['docker_started']}")
    print(f"  Build time: {r1['build_time']}s")
    print(f"  Cache files in volume: {r1.get('cache_files', 0)}")

    print("\nSecond build (should use cache):")
    r2 = test_cached_build.remote(use_cache=True)
    print(f"  Docker started: {r2['docker_started']}")
    print(f"  Build time: {r2['build_time']}s")
    print(f"  Cache used: {r2['cache_used']}")

    if r2["build_time"] < r1["build_time"] * 0.5:
        print("\n✓ Second build was significantly faster!")
    else:
        print("\n⚠ Cache may not be working as expected")
