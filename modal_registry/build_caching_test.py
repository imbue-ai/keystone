"""Manual integration test: verify BuildKit cache-to / cache-from against the Modal registry.

Run with:
    cd modal_registry && uv run pytest build_caching_test.py -v -s

Prerequisites:
    - Docker daemon running with buildx available
    - Registry deployed: cd modal_registry && uv run modal deploy app.py
    - Docker logged in to the registry (see README.md)
"""

import tempfile
import time
from pathlib import Path

import pytest

from bootstrap_devcontainer.process_runner import run_process

REGISTRY = "imbue--bootstrap-devcontainer-docker-registry-cache-registry.modal.run"


def _buildx(
    dockerfile_dir: str,
    *,
    cache_to: str | None = None,
    cache_from: str | None = None,
    no_cache: bool = False,
    build_args: dict[str, str] | None = None,
) -> str:
    """Run docker buildx build and return combined stdout+stderr, streaming logs live."""
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
    for key, val in (build_args or {}).items():
        cmd.extend(["--build-arg", f"{key}={val}"])
    cmd.append(dockerfile_dir)

    result = run_process(cmd, log_prefix="[buildx]")
    output = result.stdout + "\n" + result.stderr
    if result.returncode != 0:
        raise RuntimeError(f"buildx failed (exit {result.returncode}):\n{output}")
    return output


@pytest.mark.manual
def test_buildkit_cache_roundtrip() -> None:
    """Push cache layers to the registry, then pull them back and verify cache hits."""
    ts = str(int(time.time()))
    cache_ref = f"{REGISTRY}/test-cache-roundtrip-{ts}:cache"

    with tempfile.TemporaryDirectory() as tmpdir:
        # Use a build arg so the RUN layer is unique per test run.
        # The ARG before RUN ensures the layer fingerprint changes each time.
        dockerfile = Path(tmpdir) / "Dockerfile"
        dockerfile.write_text(
            "FROM alpine:3.19\n"
            "ARG CACHE_BUST\n"
            'RUN echo "built-at-${CACHE_BUST}" > /stamp.txt\n'
            "RUN apk add --no-cache curl\n"
        )

        # Build 1: push cache to registry (--no-cache so layers are fresh)
        output1 = _buildx(
            tmpdir,
            cache_to=f"type=registry,ref={cache_ref},mode=max",
            no_cache=True,
            build_args={"CACHE_BUST": ts},
        )
        assert "writing cache image manifest" in output1, (
            f"Expected cache export in build 1 output:\n{output1}"
        )

        # Prune local buildx cache so build 2 MUST fetch from the registry
        prune_result = run_process(["docker", "buildx", "prune", "-af"], log_prefix="[prune]")
        if prune_result.returncode != 0:
            raise RuntimeError(
                f"buildx prune failed (exit {prune_result.returncode}):\n{prune_result.stderr}"
            )

        # Build 2: pull cache from registry — RUN steps should be CACHED
        output2 = _buildx(
            tmpdir,
            cache_from=f"type=registry,ref={cache_ref}",
            build_args={"CACHE_BUST": ts},
        )
        assert "importing cache manifest from" in output2, (
            f"Expected registry cache import in build 2 output:\n{output2}"
        )
        assert "CACHED" in output2, f"Expected CACHED layers in build 2 output:\n{output2}"
