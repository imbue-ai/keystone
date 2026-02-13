# Modal-Hosted Docker BuildKit Registry Cache

A Docker registry hosted on Modal, used exclusively as a BuildKit cache backend
(`--cache-from` / `--cache-to`) to speed up `docker build` and `devcontainer build`.

## What This Is

This is **NOT** a general-purpose Docker registry. It is a **BuildKit cache backend only**:

- ✅ Persists cache layers across runs using Modal Volume
- ✅ Guarantees singleton writes (one registry instance)
- ✅ Reachable from public internet (CI) and inside Modal sandboxes
- ✅ Has basic authentication (htpasswd, derived at runtime from plaintext creds)
- ❌ Does NOT support `docker push` / `docker pull` (see [Why BuildKit Only?](#why-buildkit-only))
- ❌ Does NOT store production artifacts

## Why BuildKit Only?

Modal's HTTP proxy sits between the internet and the registry container. It has two
behaviours that break the standard Docker push/pull client:

1. **Rejects `Transfer-Encoding: chunked` requests.** Docker's push client uploads
   layers using chunked encoding, which Modal's proxy does not forward.
2. **May strip or rewrite the `Accept` header.** Docker's pull client uses specific
   `Accept` values to negotiate manifest schemas; when these are missing or wrong,
   the registry returns the wrong manifest type and the pull fails.

BuildKit's own HTTP client (used by `--cache-from` / `--cache-to`) avoids both
problems: it sends `Content-Length` headers (not chunked) and handles OCI manifest
types natively. This is why registry-based BuildKit caching works perfectly while
plain `docker push`/`docker pull` does not.

## Architecture

```
BuildKit (HTTPS) → Modal proxy (TLS termination) → Docker registry (:5000)
                                                    ↕ Modal Volume (persistent storage)
```

Single container, no nginx — the registry listens directly on the web server port.

## Configuration — Modal Secret

All credentials live in a single Modal secret named
**`keystone-docker-registry-config`** with three environment variables:

| Variable | Example | Description |
|----------|---------|-------------|
| `DOCKER_BUILD_CACHE_REGISTRY_URL` | `imbue--keystone-docker-registry-cache-registry.modal.run` | Registry hostname (no `https://` prefix) |
| `DOCKER_BUILD_CACHE_REGISTRY_USERNAME` | `buildcache` | Basic-auth username |
| `DOCKER_BUILD_CACHE_REGISTRY_PASSWORD` | `hunter2` | Plaintext password |

The registry app derives the htpasswd file at startup from USERNAME + PASSWORD
(via `htpasswd -Bbn`), so there is no redundant pre-hashed secret to maintain.

The same secret is used in two places:

1. **`modal_registry/app.py`** — attached to the registry function so it can
   generate the htpasswd file and start the authenticated registry.
2. **`keystone_cli.py --docker_cache_secret`** — passed by name to
   `ModalAgentRunner`, which attaches it to the sandbox so `docker login` and
   build-cache flags work inside the sandbox.

### Creating the Secret

```bash
modal secret create keystone-docker-registry-config \
  DOCKER_BUILD_CACHE_REGISTRY_URL="imbue--keystone-docker-registry-cache-registry.modal.run" \
  DOCKER_BUILD_CACHE_REGISTRY_USERNAME="buildcache" \
  DOCKER_BUILD_CACHE_REGISTRY_PASSWORD="your-password-here"
```

## Files

- **app.py** — Modal application definition (registry function)
- **registry_config.yml** — Docker Distribution registry configuration
- **README.md** — This file

## Deployment

```bash
# From the modal_registry/ directory
modal deploy app.py
```

Modal will print a URL like:
```
https://imbue--keystone-docker-registry-cache-registry.modal.run
```

The hostname (without `https://`) is the value for `DOCKER_BUILD_CACHE_REGISTRY_URL`.

## Usage

### With keystone CLI

```bash
uv run keystone bootstrap \
  --project_root /path/to/project \
  --test_artifacts_dir /tmp/artifacts \
  --agent_in_modal \
  --docker_cache_secret keystone-docker-registry-config
```

The CLI will:
1. Attach the secret to the Modal sandbox
2. Run `docker login` inside the sandbox (for both root and agent users)
3. Generate a `devcontainer.json` with `--cache-from` / `--cache-to` flags
4. Use the same cache flags for verification builds

### Manual BuildKit Cache Usage

```bash
REGISTRY="imbue--keystone-docker-registry-cache-registry.modal.run"

docker login "$REGISTRY" -u buildcache -p your-password-here

docker buildx build \
  -t myimage:latest \
  --cache-from "type=registry,ref=$REGISTRY/buildcache:latest" \
  --cache-to "type=registry,ref=$REGISTRY/buildcache:latest,mode=max" \
  .
```

### Verify the Registry

```bash
curl -s -u buildcache:your-password-here \
  "https://imbue--keystone-docker-registry-cache-registry.modal.run/v2/_catalog"
```

## Operational Characteristics

| Aspect | Description |
|--------|-------------|
| **Restarts** | May restart occasionally (cache persists via Volume) |
| **Cold start** | ~2–5 seconds |
| **Concurrency** | Singleton means concurrent builds queue briefly |
| **Timeout** | 2 hours per operation |
| **Warmup** | Kept warm (1 instance) for faster response |

## Cost Expectations

- **Idle**: Near $0 (min_containers=1 has minimal cost)
- **Active builds**: Small compute cost during pushes/pulls
- **Storage**: Modal Volume storage costs

Far cheaper than running a dedicated VM.

## Troubleshooting

### Registry not responding

```bash
modal app logs keystone-docker-registry-cache
```

### Cache not working

Verify you're using the same `ref` for both `--cache-from` and `--cache-to`.

### Push fails with timeout

Increase `timeout` in `app.py` and redeploy.
