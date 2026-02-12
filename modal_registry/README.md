# Modal-Hosted Docker Buildx Registry Cache

A Docker BuildKit registry cache hosted on Modal for faster builds.

## What This Is

This is **NOT** a production Docker registry. It is a **BuildKit cache backend only** that:

- ✅ Persists cache layers across runs using Modal Volume
- ✅ Guarantees singleton writes (one registry instance)
- ✅ Reachable from public internet (CI) and inside Modal sandboxes
- ✅ Can run for hours but tolerates restarts (cache survives)
- ✅ Has basic authentication (htpasswd)
- ❌ Does NOT have high availability guarantees
- ❌ Does NOT store production artifacts

## Architecture

```
Modal App: docker-registry-cache
    Web endpoint → Docker registry (registry:2)
    Persistent storage → Modal Volume
    Singleton runtime → max_containers=1
```

## Files

- **app.py**: Modal application definition
- **registry_config.yml**: Docker registry configuration
- **README.md**: This file

## Deployment

### Prerequisites

1. Install Modal CLI:
   ```bash
   pip install modal
   ```

2. Authenticate with Modal:
   ```bash
   modal token new
   ```

### Deploy the Registry

From the `modal_registry/` directory:

```bash
modal deploy app.py
```

Modal will print a URL like:
```
https://workspace--bootstrap-devcontainer-docker-registry-cache-registry.modal.run
```

Save this URL as your `REGISTRY_URL` environment variable:
```bash
export BOOTSTRAP_DEVCONTAINER_DOCKER_REGISTRY="https://imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run"
```

## Usage

### BuildKit Cache (Primary Use Case)

Use the registry as a `--cache-from` / `--cache-to` backend for `docker buildx`:

```bash
export REGISTRY="imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run"

# Login (needed once)
docker login "$REGISTRY" -u buildcache -p JabiaJockSapSkelpRathWalt

# Build with registry cache
docker buildx build \
  -t myimage:latest \
  --cache-from "type=registry,ref=$REGISTRY/buildcache:main" \
  --cache-to "type=registry,ref=$REGISTRY/buildcache:main,mode=max" \
  .
```

**Expected behavior:**
- **First build**: Slow (cache warmup, layers pushed to registry)
- **Subsequent builds**: Much faster (cache hit, layers pulled from registry)

### Using from Modal Sandbox

Inside any Modal function, use the same cache flags:

```python
import subprocess

REGISTRY = "imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run"

subprocess.run([
    "docker", "buildx", "build",
    "--cache-from", f"type=registry,ref={REGISTRY}/buildcache:main",
    "--cache-to", f"type=registry,ref={REGISTRY}/buildcache:main,mode=max",
    "-t", "example:latest",
    ".",
], check=True)
```

**Note**: No special networking config required. Modal automatically routes traffic internally.

### Verify the Registry

```bash
curl -s -u buildcache:JabiaJockSapSkelpRathWalt \
  "https://imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run/v2/_catalog"
```

### Known Limitations

> **`docker push` / `docker pull` are not supported.** Modal's HTTP proxy rejects
> `Transfer-Encoding: chunked` requests (used by Docker's push client) and may strip
> the `Accept` header (causing manifest schema issues on pull). BuildKit's own HTTP
> client avoids both problems, so `--cache-from` / `--cache-to` work perfectly.

## Operational Characteristics

### Expected Behavior

| Aspect | Description |
|--------|-------------|
| **Restarts** | May restart occasionally (cache persists via Volume) |
| **Cold start** | ~2–5 seconds |
| **Concurrency** | Singleton means concurrent builds queue briefly |
| **Timeout** | 2 hours per operation |
| **Warmup** | Kept warm (1 instance) for faster response |

### Key Settings

| Setting | Value | Why |
|---------|-------|-----|
| `max_containers` | 1 | Prevent corruption (singleton writer) |
| `keep_warm` | 1 | Faster startup |
| `timeout` | 2 hours | Allow large layer pushes |
| `cpu` | 1 | Adequate for registry |
| `memory` | 1024 MB | Adequate for registry |

## Cost Expectations

**Typical usage:**
- **Idle**: Near $0 (keep_warm=1 has minimal cost)
- **Active builds**: Small compute cost during pushes/pulls
- **Storage**: Modal Volume storage costs

**This is far cheaper than running a dedicated VM.**

## Future Improvements (Optional)

These are **NOT** required for MVP but could be added later:

- [ ] Add periodic volume pruning job
- [ ] Support separate cache tags per branch
- [ ] Add metrics/monitoring

## Troubleshooting

### Registry not responding

Check Modal logs:
```bash
modal app logs bootstrap-devcontainer-docker-registry-cache
```

### Cache not working

Verify you're using the same `ref` for both `--cache-from` and `--cache-to`:
```bash
ref=$REGISTRY_URL/buildcache:main
```

### Push fails with timeout

Increase the timeout in `app.py`:
```python
timeout=60 * 60 * 4,  # 4 hours
```

Then redeploy:
```bash
modal deploy app.py
```

## Definition of Done

Project is complete when:

- ✅ Modal app deploys successfully
- ✅ `--cache-to` pushes layers to registry (tested)
- ✅ `--cache-from` pulls layers and produces cache hits (tested)
- ✅ Cache works from inside a Modal function

## Support

For issues with:
- **Modal platform**: Check [Modal docs](https://modal.com/docs) or support
- **Docker registry**: Check [Docker registry docs](https://docs.docker.com/registry/)
- **BuildKit cache**: Check [BuildKit cache docs](https://docs.docker.com/build/cache/backends/)
