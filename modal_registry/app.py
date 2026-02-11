import os
import subprocess
import sys
from pathlib import Path

import modal

app = modal.App("bootstrap-devcontainer-docker-registry-cache")

# Modal exposes this port; nginx listens here and proxies to the registry.
EXTERNAL_PORT = 5000
# The Docker registry listens on this internal port.
INTERNAL_REGISTRY_PORT = 5001

# Persistent storage for cached layers
registry_volume = modal.Volume.from_name(
    "bootstrap-devcontainer-docker-registry-cache-volume",
    create_if_missing=True,
)

# nginx config: proxy from EXTERNAL_PORT -> INTERNAL_REGISTRY_PORT.
# Modal's @web_server proxy strips the Accept header, so we inject a default
# that includes all modern manifest types. Without this, the Docker registry
# returns schema v1 manifests which Docker >= 20 can't handle.
NGINX_CONF = f"""\
worker_processes 1;
error_log /dev/stderr info;
pid /tmp/nginx.pid;

events {{
    worker_connections 1024;
}}

http {{
    access_log /dev/stderr;
    client_max_body_size 0;  # unlimited upload size for layer blobs

    server {{
        listen {EXTERNAL_PORT};

        # For manifest requests, always set Accept to include modern types.
        # Modal's proxy may strip or replace the Accept header, causing the
        # registry to return deprecated v1 schema manifests.
        location ~ ^/v2/.*/manifests/ {{
            proxy_pass http://127.0.0.1:{INTERNAL_REGISTRY_PORT};
            proxy_set_header Host $http_host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_set_header Accept "application/vnd.docker.distribution.manifest.v2+json, application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.manifest.v1+json, application/vnd.oci.image.index.v1+json, */*";
            proxy_buffering off;
            proxy_request_buffering off;
        }}

        location / {{
            proxy_pass http://127.0.0.1:{INTERNAL_REGISTRY_PORT};
            proxy_set_header Host $http_host;
            proxy_set_header X-Real-IP $remote_addr;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header X-Forwarded-Proto https;
            proxy_buffering off;
            proxy_request_buffering off;
        }}
    }}
}}
"""

# Base image: Python + registry binary + nginx
registry_image = (
    modal.Image.debian_slim(python_version="3.12")
    .apt_install("ca-certificates", "wget", "nginx")
    .run_commands(
        # Download and install Docker registry binary
        "wget -O /tmp/registry.tar.gz https://github.com/distribution/distribution/releases/download/v2.8.3/registry_2.8.3_linux_amd64.tar.gz",
        "tar -xzf /tmp/registry.tar.gz -C /usr/local/bin",
        "chmod +x /usr/local/bin/registry",
        "rm /tmp/registry.tar.gz",
    )
    .add_local_file("registry_config.yml", "/etc/docker/registry/config.yml")
)

auth_secret = modal.Secret.from_name("bootstrap-devcontainer-docker-registry-auth")


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
@modal.web_server(EXTERNAL_PORT)
def registry() -> None:
    """Start nginx (front) -> Docker registry (back).

    nginx is needed because Modal's @web_server proxy strips the Accept header.
    Without the correct Accept header, the registry returns deprecated v1 schema
    manifests that modern Docker clients reject with "manifest unknown".
    """
    # Write htpasswd file from secret
    Path("/auth").mkdir(parents=True, exist_ok=True)
    Path("/auth/htpasswd").write_text(os.environ["HT_PASSWD"], encoding="utf-8")

    # Write nginx config
    Path("/etc/nginx/nginx.conf").write_text(NGINX_CONF, encoding="utf-8")

    print(
        f"Starting registry on :{INTERNAL_REGISTRY_PORT}, nginx on :{EXTERNAL_PORT}",
        file=sys.stderr,
    )

    # Start the Docker registry on the internal port
    subprocess.Popen(
        ["registry", "serve", "/etc/docker/registry/config.yml"],
        stdout=sys.stderr,
        stderr=sys.stderr,
    )

    # Start nginx on the external port (foreground-ish via Popen)
    subprocess.Popen(
        ["nginx", "-g", "daemon off;"],
        stdout=sys.stderr,
        stderr=sys.stderr,
    )
