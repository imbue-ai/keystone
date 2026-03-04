# Modal-Hosted Docker Hub Pull-Through Mirror

A Docker Hub pull-through cache registry hosted on Modal. When the Docker
daemon is configured to use this as a `registry-mirror`, every Docker Hub
pull (metadata **and** layers) goes through the mirror first — if cached,
Docker Hub is never contacted at all. This eliminates Docker Hub rate
limiting for base images.

## Why a Mirror Instead of BuildKit Cache?

`--cache-from`/`--cache-to` (BuildKit registry cache) does **not** prevent
Docker Hub rate limiting: BuildKit still fetches `FROM` image metadata
(manifests) from Docker Hub on every build, even when all layers are cached.

A pull-through mirror (`registry-mirrors` in `daemon.json`) solves the
problem completely because Docker resolves the `FROM` image through the
mirror — Docker Hub is never contacted when the image is cached.

## Architecture

```
Docker daemon (registry-mirrors) → Modal mirror → Docker Hub
                                                ↕ Modal Volume (cached layers)
```

The mirror is the official CNCF distribution/distribution v3.0.0 registry
in pull-through proxy mode. The Docker daemon routes all Docker Hub pulls
through it transparently.

## Files

- **mirror_registry_app.py** — Modal application definition
- **mirror_registry_config.yml** — Registry proxy configuration
- **load_test_v2.py** — Load test to reproduce and test Docker Hub rate limiting
- **README.md** — This file

## Deployment

```bash
# From the modal_registry/ directory
cd modal_registry && modal deploy mirror_registry_app.py
```

Modal will print a URL like:
```
https://imbue--keystone-docker-hub-mirror-q7x3p-registry.modal.run
```

This URL is what you pass to `--docker_registry_mirror` (or `--with-mirror`
in the load test).

## Usage

### With keystone CLI

```bash
uv run keystone bootstrap \
  --project_root /path/to/project \
  --test_artifacts_dir /tmp/artifacts \
  --agent_in_modal \
  --docker_registry_mirror https://imbue--keystone-docker-hub-mirror-q7x3p-registry.modal.run
```

The CLI writes `/etc/docker/daemon.json` with `registry-mirrors` set to the
mirror URL before starting dockerd inside the Modal sandbox.

The default mirror is Google's public mirror (`https://mirror.gcr.io`), which
works without any deployment step.

### With load test

```bash
# Test baseline (hits Docker Hub directly)
cd modal_registry && uv run python load_test_v2.py --iterations 50

# Test with mirror (Docker Hub never contacted for cached images)
cd modal_registry && uv run python load_test_v2.py --iterations 50 \
    --with-mirror https://imbue--keystone-docker-hub-mirror-q7x3p-registry.modal.run
```

## Security Note

The mirror has no authentication. Docker daemon's `registry-mirrors`
mechanism sends unauthenticated requests, so the mirror must be open. The
Modal app name includes random characters (`q7x3p`) for URL obscurity — the
endpoint is not publicly advertised.

## Operational Characteristics

| Aspect | Description |
|--------|-------------|
| **Restarts** | May restart occasionally (cache persists via Volume) |
| **Cold start** | ~2–5 seconds |
| **Concurrency** | Up to 100 concurrent inputs |
| **Timeout** | 2 hours per operation |
| **Warmup** | Kept warm (1 instance) for faster response |

## Monitoring

```bash
modal app logs keystone-docker-hub-mirror-q7x3p
```
