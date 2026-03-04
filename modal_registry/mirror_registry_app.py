"""Docker Hub pull-through cache (mirror) on Modal.

Runs the official Docker registry in proxy/mirror mode.  When a Docker
daemon is configured with this as a registry-mirror, image pulls check
here first — if cached, the image is served without touching Docker Hub.
This avoids Docker Hub rate limits for base images (the FROM line).

Deploy:
    cd modal_registry && modal deploy mirror_registry_app.py

The resulting URL (printed by modal deploy) is what you pass to
load_test_v2.py via --with-mirror.

NOTE: This mirror has no authentication.  Docker daemon's
"registry-mirrors" sends unauthenticated requests, so the mirror must
be open.  Anyone who knows the URL can pull through it.
"""

import subprocess
import sys

import modal

app = modal.App("keystone-docker-hub-mirror-q7x3p")

REGISTRY_PORT = 5000

# Persistent storage for cached images / layers
mirror_volume = modal.Volume.from_name(
    "keystone-docker-hub-mirror-volume",
    create_if_missing=True,
)

# Same base image as the build-cache registry: registry binary + deps
mirror_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ca-certificates", "wget")
    .run_commands(
        "wget -O /tmp/registry.tar.gz https://github.com/distribution/distribution/releases/download/v2.8.3/registry_2.8.3_linux_amd64.tar.gz",
        "tar -xzf /tmp/registry.tar.gz -C /usr/local/bin",
        "chmod +x /usr/local/bin/registry",
        "rm /tmp/registry.tar.gz",
    )
    .add_local_file("mirror_registry_config.yml", "/etc/docker/registry/config.yml")
)


@app.function(
    image=mirror_image,
    volumes={"/var/lib/registry": mirror_volume},
    max_containers=1,
    min_containers=1,
    timeout=60 * 60 * 2,
    cpu=1,
    memory=1024,
)
@modal.concurrent(max_inputs=100)
@modal.web_server(REGISTRY_PORT)
def registry() -> None:
    """Start the Docker registry as a Docker Hub pull-through cache."""
    print(f"Starting mirror registry on :{REGISTRY_PORT}", file=sys.stderr)
    subprocess.Popen(
        ["registry", "serve", "/etc/docker/registry/config.yml"],
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
