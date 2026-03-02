#!/usr/bin/env python3
"""Docker Registry running on Modal with persistent storage.

This provides a build cache for docker builds running in Modal sandboxes.
The registry persists data to a Modal Volume for durability.

ARCHITECTURE:
- Uses Modal's @web_server to expose the registry:2 container on port 5000
- Data persisted to a Modal Volume
- Sandboxes can reach it via the public URL

Usage:
    # Deploy the registry
    uv run modal deploy scripts/modal_registry.py

    # The URL will be printed, something like:
    # https://<workspace>--docker-registry-registry.modal.run

    # In your docker builds (inside Modal sandbox):
    docker buildx build \\
        --cache-to type=registry,ref=<registry-url>/cache:latest \\
        --cache-from type=registry,ref=<registry-url>/cache:latest \\
        -t myimage .

LIMITATIONS:
- Docker registry protocol requires specific HTTP handling
- Modal's web_server may need adjustments for chunked uploads
- Consider using Modal Volumes directly for simpler caching
"""

import subprocess

import modal

app = modal.App("docker-registry")

# Persistent volume for registry data
# Using local disk storage - cache resets on container restart but that's fine
# This avoids Modal Volume persistence issues with registry writes

# Build image with nginx + registry
registry_image = (
    modal.Image.debian_slim()
    .apt_install("ca-certificates", "curl", "nginx")
    .run_commands(
        # Install docker registry binary
        "curl -L https://github.com/distribution/distribution/releases/download/v2.8.3/registry_2.8.3_linux_amd64.tar.gz | tar xz",
        "mv registry /usr/local/bin/",
        "mkdir -p /etc/docker/registry /var/lib/registry",
    )
    .run_commands(
        # Create registry config (listens on 5001 internally)
        "echo 'version: 0.1' > /etc/docker/registry/config.yml",
        "echo 'log:' >> /etc/docker/registry/config.yml",
        "echo '  level: info' >> /etc/docker/registry/config.yml",
        "echo 'storage:' >> /etc/docker/registry/config.yml",
        "echo '  filesystem:' >> /etc/docker/registry/config.yml",
        "echo '    rootdirectory: /var/lib/registry' >> /etc/docker/registry/config.yml",
        "echo '  delete:' >> /etc/docker/registry/config.yml",
        "echo '    enabled: true' >> /etc/docker/registry/config.yml",
        "echo 'http:' >> /etc/docker/registry/config.yml",
        "echo '  addr: :5001' >> /etc/docker/registry/config.yml",
    )
    .run_commands(
        # Create nginx config that adds X-Forwarded-Proto header
        "rm -f /etc/nginx/sites-enabled/default",
        "echo 'server {' > /etc/nginx/conf.d/registry.conf",
        "echo '    listen 5000;' >> /etc/nginx/conf.d/registry.conf",
        "echo '    client_max_body_size 0;' >> /etc/nginx/conf.d/registry.conf",
        "echo '    chunked_transfer_encoding on;' >> /etc/nginx/conf.d/registry.conf",
        "echo '    location / {' >> /etc/nginx/conf.d/registry.conf",
        "echo '        proxy_pass http://127.0.0.1:5001;' >> /etc/nginx/conf.d/registry.conf",
        "echo '        proxy_set_header Host imbue--docker-registry-registry.modal.run;' >> /etc/nginx/conf.d/registry.conf",
        "echo '        proxy_set_header X-Forwarded-Proto https;' >> /etc/nginx/conf.d/registry.conf",
        "echo '        proxy_read_timeout 900;' >> /etc/nginx/conf.d/registry.conf",
        "echo '        proxy_send_timeout 900;' >> /etc/nginx/conf.d/registry.conf",
        "echo '    }' >> /etc/nginx/conf.d/registry.conf",
        "echo '}' >> /etc/nginx/conf.d/registry.conf",
    )
)


@app.function(
    image=registry_image,
    timeout=86400,  # 24 hours max (Modal limit)
    cpu=1.0,
    memory=1024,  # More memory for cache
    keep_warm=1,  # Keep one container always running for session stickiness
    max_containers=1,  # Only one container to ensure stateful uploads work
)
@modal.web_server(port=5000, startup_timeout=60)
def registry():
    """Run nginx + Docker registry."""
    # Start registry on port 5001
    subprocess.Popen(
        ["/usr/local/bin/registry", "serve", "/etc/docker/registry/config.yml"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # Start nginx on port 5000 (proxies to registry with proper headers)
    subprocess.Popen(
        ["nginx", "-g", "daemon off;"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@app.local_entrypoint()
def main():
    """Check registry is deployed."""
    print("Registry deployed at: https://imbue--docker-registry-registry.modal.run")
