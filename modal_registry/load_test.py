"""Load test: 200 concurrent Modal sandboxes building with cache from/to our registry.

This verifies that our build cache registry shields us from Docker Hub rate limits
when many sandboxes pull the same base image concurrently.

Run with:
    cd modal_registry && uv run python load_test.py

Prerequisites:
    - Registry deployed: cd modal_registry && uv run modal deploy app.py
    - Modal secret 'keystone-docker-registry-config' exists with registry credentials
"""

import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass

import modal

from keystone.modal.image import create_modal_image

SANDBOX_COUNT = 200
SANDBOX_TIMEOUT_SECS = 600  # 10 minutes per sandbox

# Registry URL (from secret, but also hardcoded as fallback)
REGISTRY = "imbue--keystone-docker-registry-cache-registry.modal.run"

# Dockerfile that pulls python:3.12-slim from Docker Hub.
# All 200 builds use the same Dockerfile + same cache ref, so after the first
# build pushes cache layers, the remaining 199 should get cache hits and never
# touch Docker Hub for the base image layers.
DOCKERFILE_CONTENT = """\
FROM python:3.12-slim
RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts
COPY .devcontainer/run_all_tests.sh /run_all_tests.sh
RUN chmod +x /run_all_tests.sh
"""

DEVCONTAINER_JSON = """\
{{
    "build": {{
        "dockerfile": "Dockerfile",
        "context": "..",
        "cacheFrom": "type=registry,ref={registry}/load-test-cache:latest",
        "cacheTo": "type=registry,ref={registry}/load-test-cache:latest,mode=max"
    }}
}}
"""

RUN_ALL_TESTS_SH = """\
#!/bin/bash
echo "load-test-sentinel"
mkdir -p /test_artifacts/junit
echo '<?xml version="1.0" ?><testsuites><testsuite tests="1"><testcase name="t"/></testsuite></testsuites>' > /test_artifacts/junit/results.xml
echo '{"success": true}' > /test_artifacts/final_result.json
"""

# Script that runs inside each sandbox: starts Docker, logs in, runs devcontainer build.
SANDBOX_SCRIPT_TEMPLATE = """\
#!/bin/bash
set -euo pipefail

REGISTRY="{registry}"

# Set up project with .devcontainer
mkdir -p /tmp/project/.devcontainer
cat > /tmp/project/.devcontainer/Dockerfile << 'DOCKERFILE'
{dockerfile}
DOCKERFILE

cat > /tmp/project/.devcontainer/devcontainer.json << 'DEVCONTAINER_JSON'
{devcontainer_json}
DEVCONTAINER_JSON

cat > /tmp/project/.devcontainer/run_all_tests.sh << 'RUN_ALL_TESTS'
{run_all_tests}
RUN_ALL_TESTS
chmod +x /tmp/project/.devcontainer/run_all_tests.sh

# Log in to our cache registry
echo "$DOCKER_BUILD_CACHE_REGISTRY_PASSWORD" | \
    docker login "$REGISTRY" \
        -u "$DOCKER_BUILD_CACHE_REGISTRY_USERNAME" \
        --password-stdin

echo "=== Starting devcontainer build (sandbox {sandbox_id}) ==="
START_TS=$(date +%s%N)

devcontainer build \
    --workspace-folder /tmp/project \
    --image-name "load-test-throwaway-{sandbox_id}:latest" 2>&1

END_TS=$(date +%s%N)
ELAPSED_MS=$(( (END_TS - START_TS) / 1000000 ))
echo "=== Build completed in ${{ELAPSED_MS}}ms (sandbox {sandbox_id}) ==="
"""


@dataclass
class SandboxResult:
    """Result from a single sandbox build."""

    sandbox_index: int
    sandbox_id: str
    success: bool
    duration_secs: float
    output: str
    error: str


def run_sandbox_build(
    app: modal.App,
    image: modal.Image,
    secret: modal.Secret,
    index: int,
) -> SandboxResult:
    """Create a sandbox, start Docker, run the cached build, and collect results."""
    start = time.monotonic()
    sandbox_id = f"load-test-{index}"
    output_lines: list[str] = []
    error_lines: list[str] = []

    try:
        sandbox = modal.Sandbox.create(
            app=app,
            image=image,
            timeout=SANDBOX_TIMEOUT_SECS,
            secrets=[secret],
            experimental_options={"enable_docker": True},
        )
        actual_id = sandbox.object_id or sandbox_id
        print(f"[{index:03d}] Sandbox created: {actual_id}", file=sys.stderr)

        # Start Docker daemon
        sandbox.exec("/start-dockerd.sh")
        # Don't wait for it — it runs in background. Wait for readiness instead.
        wait_proc = sandbox.exec("/wait_for_docker.sh")
        wait_proc.wait()
        if wait_proc.returncode != 0:
            raise RuntimeError("Docker daemon failed to start")

        # Run the build script
        script = SANDBOX_SCRIPT_TEMPLATE.format(
            registry=REGISTRY,
            dockerfile=DOCKERFILE_CONTENT,
            devcontainer_json=DEVCONTAINER_JSON.format(registry=REGISTRY),
            run_all_tests=RUN_ALL_TESTS_SH,
            sandbox_id=index,
        )
        build_proc = sandbox.exec("bash", "-c", script)

        # Stream output
        for line in build_proc.stdout:
            output_lines.append(line)
            print(f"[{index:03d}] {line}", end="", file=sys.stderr)
        for line in build_proc.stderr:
            error_lines.append(line)
            # Only print errors/warnings, skip noisy docker output
            if any(kw in line.lower() for kw in ("error", "warn", "rate limit", "pull rate")):
                print(f"[{index:03d}] ERR: {line}", end="", file=sys.stderr)

        build_proc.wait()
        success = build_proc.returncode == 0

        # Clean up
        sandbox.terminate()

        return SandboxResult(
            sandbox_index=index,
            sandbox_id=actual_id,
            success=success,
            duration_secs=time.monotonic() - start,
            output="".join(output_lines),
            error="".join(error_lines),
        )

    except Exception as e:
        return SandboxResult(
            sandbox_index=index,
            sandbox_id=sandbox_id,
            success=False,
            duration_secs=time.monotonic() - start,
            output="".join(output_lines),
            error=f"Exception: {e}\n" + "".join(error_lines),
        )


def run_load_test() -> None:
    """Spawn SANDBOX_COUNT sandboxes and run concurrent builds."""
    print(f"Starting load test with {SANDBOX_COUNT} sandboxes", file=sys.stderr)
    print(f"Registry: {REGISTRY}", file=sys.stderr)

    app = modal.App.lookup("keystone-sandbox", create_if_missing=True)
    image = create_modal_image()
    secret = modal.Secret.from_name("keystone-docker-registry-config")

    # First, seed the cache with one build so that the remaining 199 can use it.
    print("=== Phase 1: Seeding cache with initial build ===", file=sys.stderr)
    seed_result = run_sandbox_build(app, image, secret, index=0)
    if not seed_result.success:
        print(
            f"FATAL: Seed build failed — cannot proceed.\n{seed_result.error}",
            file=sys.stderr,
        )
        sys.exit(1)
    print(
        f"Seed build completed in {seed_result.duration_secs:.1f}s",
        file=sys.stderr,
    )

    # Now launch ALL remaining builds concurrently using threads.
    print(
        f"=== Phase 2: Launching {SANDBOX_COUNT - 1} concurrent builds ===",
        file=sys.stderr,
    )
    overall_start = time.monotonic()

    all_results: list[SandboxResult] = [seed_result]
    with ThreadPoolExecutor(max_workers=SANDBOX_COUNT - 1) as executor:
        futures = {
            executor.submit(run_sandbox_build, app, image, secret, i): i
            for i in range(1, SANDBOX_COUNT)
        }
        for future in as_completed(futures):
            result = future.result()
            all_results.append(result)
            status = "✓" if result.success else "✗"
            print(
                f"  {status} sandbox {result.sandbox_index:03d} "
                f"finished in {result.duration_secs:.1f}s",
                file=sys.stderr,
            )

    overall_duration = time.monotonic() - overall_start

    # Print summary
    print("\n" + "=" * 80, file=sys.stderr)
    print("LOAD TEST RESULTS", file=sys.stderr)
    print("=" * 80, file=sys.stderr)

    successes = [r for r in all_results if r.success]
    failures = [r for r in all_results if not r.success]

    print(f"Total sandboxes:  {len(all_results)}", file=sys.stderr)
    print(f"Succeeded:        {len(successes)}", file=sys.stderr)
    print(f"Failed:           {len(failures)}", file=sys.stderr)
    print(f"Overall duration: {overall_duration:.1f}s", file=sys.stderr)

    if successes:
        durations = [r.duration_secs for r in successes]
        durations.sort()
        print(f"Build time (min):  {durations[0]:.1f}s", file=sys.stderr)
        print(f"Build time (med):  {durations[len(durations) // 2]:.1f}s", file=sys.stderr)
        print(f"Build time (max):  {durations[-1]:.1f}s", file=sys.stderr)

    if failures:
        print("\nFailed sandboxes:", file=sys.stderr)
        for r in failures:
            err_preview = r.error[:500] if r.error else "(no error)"
            print(
                f"  [{r.sandbox_index:03d}] {r.sandbox_id}: {err_preview}",
                file=sys.stderr,
            )
        print(
            f"\n❌ {len(failures)}/{len(all_results)} builds failed",
            file=sys.stderr,
        )
        sys.exit(1)
    else:
        print(
            "\n✅ All builds succeeded — cache is shielding us from Docker Hub rate limits!",
            file=sys.stderr,
        )


if __name__ == "__main__":
    # Allow overriding sandbox count via env var
    count_override = os.environ.get("LOAD_TEST_SANDBOX_COUNT")
    if count_override:
        SANDBOX_COUNT = int(count_override)

    run_load_test()
