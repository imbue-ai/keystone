# Deployment Summary

## Mirror URL

```
https://imbue--keystone-docker-hub-mirror-q7x3p-registry.modal.run
```

## Deployment Status

✅ **Successfully deployed and tested**

- Mirror responds to Docker daemon pull requests
- Docker Hub pulls served from cache (no Docker Hub contact when warm)
- Confirmed working in load test (`load_test_v2.py --with-mirror ...`)

## Deploy

```bash
cd modal_registry && modal deploy mirror_registry_app.py
```

## Key Configuration

- **Max containers**: 1
- **Min containers**: 1 (kept warm)
- **Timeout**: 2 hours
- **Concurrent inputs**: 100
- **Storage**: Modal Volume (persistent across restarts)

## Monitoring

```bash
modal app logs keystone-docker-hub-mirror-q7x3p
```

Or visit: https://modal.com/apps/imbue/main/deployed/keystone-docker-hub-mirror-q7x3p
