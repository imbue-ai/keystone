import os
import subprocess
import sys
from pathlib import Path

import modal

app = modal.App("bootstrap-devcontainer-docker-registry-cache")

REGISTRY_PORT = 5000

# Persistent storage for cached layers
registry_volume = modal.Volume.from_name(
    "bootstrap-devcontainer-docker-registry-cache-volume",
    create_if_missing=True,
)

# Base image: Python + registry binary + apache2-utils for htpasswd generation
registry_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ca-certificates", "wget", "apache2-utils")
    .run_commands(
        # Download and install Docker registry binary
        "wget -O /tmp/registry.tar.gz https://github.com/distribution/distribution/releases/download/v2.8.3/registry_2.8.3_linux_amd64.tar.gz",
        "tar -xzf /tmp/registry.tar.gz -C /usr/local/bin",
        "chmod +x /usr/local/bin/registry",
        "rm /tmp/registry.tar.gz",
    )
    .add_local_file("registry_config.yml", "/etc/docker/registry/config.yml")
)

# This secret must contain:
#   DOCKER_BUILD_CACHE_REGISTRY_URL      - full hostname of the registry
#   DOCKER_BUILD_CACHE_REGISTRY_USERNAME  - username for basic auth
#   DOCKER_BUILD_CACHE_REGISTRY_PASSWORD  - plaintext password for basic auth
# The htpasswd file is derived at runtime from USERNAME + PASSWORD.
auth_secret = modal.Secret.from_name("bootstrap-devcontainer-docker-registry-config")


@app.function(
    image=registry_image,
    volumes={"/var/lib/registry": registry_volume},
    secrets=[auth_secret],
    max_containers=1,  # enforce singleton writer
    min_containers=1,  # keep registry hot (faster builds)
    timeout=60 * 60 * 2,  # allow long pushes (2 hours)
    cpu=1,
    memory=1024,
)
@modal.concurrent(max_inputs=100)
@modal.web_server(REGISTRY_PORT)
def registry() -> None:
    """Start the Docker registry directly on the web server port.

    This registry is used exclusively as a BuildKit cache backend
    (--cache-from / --cache-to). BuildKit's HTTP client handles the
    OCI manifest types natively and uses Content-Length (not chunked
    encoding), so no nginx proxy layer is needed.

    The registry uses relativeurls: true to avoid http:// vs https://
    scheme mismatches (Modal terminates TLS externally).
    """
    # Verify that the URL in the secret matches this app's actual URL.
    # The expected URL is derived from Modal's naming convention for web endpoints.
    expected_url = os.environ.get("DOCKER_BUILD_CACHE_REGISTRY_URL", "")
    if expected_url:
        # Modal web_server URLs follow: <workspace>--<app-name>-<function-name>.modal.run
        # We can't compute it exactly here, but we can check that the secret's URL
        # at least ends with .modal.run and log it for verification.
        print(f"Registry URL from secret: {expected_url}", file=sys.stderr)
        if not expected_url.endswith(".modal.run"):
            print(
                f"WARNING: DOCKER_BUILD_CACHE_REGISTRY_URL ({expected_url}) "
                "does not look like a Modal web endpoint URL",
                file=sys.stderr,
            )
    else:
        print(
            "WARNING: DOCKER_BUILD_CACHE_REGISTRY_URL not set in secret - "
            "clients won't know how to reach this registry",
            file=sys.stderr,
        )

    # Derive htpasswd file from plaintext credentials at startup
    username = os.environ["DOCKER_BUILD_CACHE_REGISTRY_USERNAME"]
    password = os.environ["DOCKER_BUILD_CACHE_REGISTRY_PASSWORD"]
    Path("/auth").mkdir(parents=True, exist_ok=True)
    htpasswd_result = subprocess.run(
        ["htpasswd", "-Bbn", username, password],
        capture_output=True,
        check=True,
        text=True,
    )
    Path("/auth/htpasswd").write_text(htpasswd_result.stdout, encoding="utf-8")

    print(f"Starting registry on :{REGISTRY_PORT}", file=sys.stderr)

    # Start the Docker registry (foreground via Popen)
    subprocess.Popen(
        ["registry", "serve", "/etc/docker/registry/config.yml"],
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
