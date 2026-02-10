# Deployment Summary

## Registry URL

```
https://imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run
```

## Deployment Status

✅ **Successfully deployed and tested**

- Registry responds to HTTP requests (200 OK)
- BuildKit cache export works
- BuildKit cache import/reuse works
- All layers cached correctly on subsequent builds

## Test Results

### Registry Health Check
```bash
curl https://imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run/v2/
# Returns: {}
# Status: 200 OK
```

### BuildKit Cache Test

**First build (cache warmup):**
- Exported cache successfully to registry
- Cache manifest written
- All layers stored

**Second build (cache reuse):**
- All layers: `CACHED`
- Build time significantly reduced
- Cache hit rate: 100%

## Usage Example

```bash
export REGISTRY_URL="imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run"

# Build with cache
docker buildx build \
  --cache-from type=registry,ref=$REGISTRY_URL/buildcache:main \
  --cache-to type=registry,ref=$REGISTRY_URL/buildcache:main,mode=max \
  -t myimage:latest \
  .
```

## Key Configuration

- **Max containers**: 1 (singleton write protection)
- **Min containers**: 1 (kept warm)
- **Timeout**: 2 hours
- **Concurrent inputs**: 100
- **Storage**: Modal Volume (persistent across restarts)

## Known Limitations

1. **Direct `docker push` may be slow** - The registry is optimized for BuildKit cache, not direct image pushes. Use BuildKit cache commands instead.

2. **No authentication** - Registry is open (as per spec). Suitable for internal cache, not public artifacts.

3. **Cold start time**: ~2-5 seconds when not kept warm.

## Monitoring

View logs and metrics:
```bash
modal app logs bootstrap-devcontainer-docker-registry-cache
```

Or visit: https://modal.com/apps/imbue/main/deployed/bootstrap-devcontainer-docker-registry-cache
