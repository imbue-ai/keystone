#!/bin/bash
set -e

TIMEOUT=${1:-30}
echo "Waiting for Docker daemon to be ready (timeout: ${TIMEOUT}s)..."

for i in $(seq 1 "$TIMEOUT"); do
    if docker info >/dev/null 2>&1; then
        echo "Docker daemon is ready after $i seconds."
        exit 0
    fi
    if [ $((i % 5)) -eq 0 ]; then
        echo "Still waiting for Docker ($i/${TIMEOUT}s)..."
    fi
    sleep 1
done

echo "Error: Docker daemon failed to become ready within ${TIMEOUT} seconds."
docker info || true
exit 1
