# Modal-Hosted Docker Buildx Registry Cache

A Docker BuildKit registry cache hosted on Modal for faster builds.

## What This Is

This is **NOT** a production Docker registry. It is a **BuildKit cache backend only** that:

- ✅ Persists cache layers across runs using Modal Volume
- ✅ Guarantees singleton writes (one registry instance)
- ✅ Reachable from public internet (CI) and inside Modal sandboxes
- ✅ Can run for hours but tolerates restarts (cache survives)
- ❌ Does NOT have authentication
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
export REGISTRY_URL="https://workspace--bootstrap-devcontainer-docker-registry-cache-registry.modal.run"
```

## Usage

### Basic Registry Test

Verify the registry works:

```bash
# Pull a test image
docker pull alpine

# Tag and push to the registry
docker tag alpine $REGISTRY_URL/test/alpine:latest
docker push $REGISTRY_URL/test/alpine:latest
```

If successful, your registry is functional! 🎉

### Using as BuildKit Cache

Use the registry as a cache backend for `docker buildx`:

```bash
docker buildx build \
  -t myimage:latest \
  --cache-from type=registry,ref=$REGISTRY_URL/buildcache:main \
  --cache-to type=registry,ref=$REGISTRY_URL/buildcache:main,mode=max \
  .
```

**Expected behavior:**
- **First build**: Slow (cache warmup)
- **Subsequent builds**: Much faster (cache hit)

### Using from Modal Sandbox

Inside any Modal function, use the same cache flags:

```python
import subprocess

REGISTRY = "https://workspace--bootstrap-devcontainer-docker-registry-cache-registry.modal.run"

subprocess.run([
    "docker", "buildx", "build",
    "--cache-from", f"type=registry,ref={REGISTRY}/buildcache:main",
    "--cache-to", f"type=registry,ref={REGISTRY}/buildcache:main,mode=max",
    "-t", "example:latest",
    ".",
], check=True)
```

**Note**: No special networking config required. Modal automatically routes traffic internally.

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

- [ ] Add basic authentication
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
- ✅ `docker push` works to registry URL
- ✅ buildx cache reduces build time on second run
- ✅ Cache works from inside a Modal function

## Support

For issues with:
- **Modal platform**: Check [Modal docs](https://modal.com/docs) or support
- **Docker registry**: Check [Docker registry docs](https://docs.docker.com/registry/)
- **BuildKit cache**: Check [BuildKit cache docs](https://docs.docker.com/build/cache/backends/)
