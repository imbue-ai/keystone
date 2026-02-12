"""Manual integration test: verify BuildKit cache-to / cache-from against the Modal registry.

Run with:
    cd modal_registry && uv run pytest build_caching_test.py -v -s

Prerequisites:
    - Docker daemon running with buildx available
    - Registry deployed: cd modal_registry && uv run modal deploy app.py
    - Docker logged in to the registry (see README.md)
"""

import subprocess
import tempfile
import time
from pathlib import Path

import pytest

REGISTRY = "imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run"


def _buildx(
    dockerfile_dir: str,
    *,
    cache_to: str | None = None,
    cache_from: str | None = None,
    no_cache: bool = False,
) -> str:
    """Run docker buildx build and return combined stdout+stderr."""
    cmd = [
        "docker",
        "buildx",
        "build",
        "-t",
        "cache-test-throwaway:latest",
    ]
    if no_cache:
        cmd.append("--no-cache")
    if cache_to:
        cmd.extend(["--cache-to", cache_to])
    if cache_from:
        cmd.extend(["--cache-from", cache_from])
    cmd.append(dockerfile_dir)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    output = result.stdout + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"buildx failed (exit {result.returncode}):\n{output}")
    return output


@pytest.mark.manual
def test_buildkit_cache_roundtrip() -> None:
    """Push cache layers to the registry, then pull them back and verify cache hits."""
    cache_ref = f"{REGISTRY}/test-cache-roundtrip-{int(time.time())}:cache"

    with tempfile.TemporaryDirectory() as tmpdir:
        # Write a Dockerfile with a timestamp so the RUN layer is unique
        dockerfile = Path(tmpdir) / "Dockerfile"
        dockerfile.write_text(
            f"FROM alpine:3.19\n"
            f"RUN echo 'built-at-{int(time.time())}' > /stamp.txt\n"
            f"RUN apk add --no-cache curl\n"
        )

        # Build 1: push cache to registry (--no-cache so layers are fresh)
        output1 = _buildx(
            tmpdir,
            cache_to=f"type=registry,ref={cache_ref},mode=max",
            no_cache=True,
        )
        assert "writing cache image manifest" in output1, (
            f"Expected cache export in build 1 output:\n{output1}"
        )

        # Build 2: pull cache from registry — RUN steps should be CACHED
        output2 = _buildx(
            tmpdir,
            cache_from=f"type=registry,ref={cache_ref}",
        )
        assert "CACHED" in output2, f"Expected CACHED layers in build 2 output:\n{output2}"
