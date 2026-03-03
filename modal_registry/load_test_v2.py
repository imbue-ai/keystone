"""Load test v2: reproduce Docker Hub rate limiting from a single IP.

Unlike the original approach (multiple sandboxes with different IPs),
this test uses a SINGLE Modal sandbox and repeatedly builds a devcontainer
image then prunes all Docker state, forcing fresh pulls every iteration.
This hammers Docker Hub from the same IP to trigger rate limits.

Usage:
    cd modal_registry && uv run python load_test_v2.py [--iterations 20] [--with-cache]

Prerequisites:
    - Modal configured (modal token set)
    - For --with-cache: registry deployed and secret configured (see README.md)
"""

import argparse
import sys
import textwrap
import time

import modal

# ---------------------------------------------------------------------------
# Reuse the keystone Modal image which already has Docker + devcontainer CLI
# ---------------------------------------------------------------------------
sys.path.insert(
    0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "keystone" / "src")
)
from keystone.modal.image import create_modal_image

# A minimal devcontainer project that pulls a base image and runs a few
# layer-creating commands.  This is intentionally NOT cached locally so
# every build must pull from Docker Hub.
DOCKERFILE = textwrap.dedent("""\
    FROM python:3.12-slim

    RUN apt-get update && apt-get install -y --no-install-recommends \\
        curl git jq && \\
        rm -rf /var/lib/apt/lists/*

    RUN pip install --no-cache-dir requests flask

    WORKDIR /workspace
    COPY . .
""")

DEVCONTAINER_JSON = textwrap.dedent("""\
    {
      "name": "load-test",
      "build": {
        "dockerfile": "Dockerfile",
        "context": ".."
      }
    }
""")


def _exec_script(
    sb: modal.Sandbox,
    script: str,
    *,
    label: str = "cmd",
) -> tuple[int, str]:
    """Write a bash script into the sandbox, execute it, and return (exit_code, output)."""
    script_path = "/tmp/_load_test_cmd.sh"
    with sb.open(script_path, "w") as f:
        f.write(script)
    proc = sb.exec("bash", script_path)

    output_lines: list[str] = []
    for line in proc.stdout:
        text = line.strip()
        print(f"  [{label}] {text}", file=sys.stderr)
        output_lines.append(text)
    for line in proc.stderr:
        text = line.strip()
        print(f"  [{label}] {text}", file=sys.stderr)
        output_lines.append(text)

    exit_code = proc.wait()
    return exit_code, "\n".join(output_lines)


def run_load_test(iterations: int, with_cache: bool) -> None:
    """Run the load test: build + prune in a loop from a single sandbox."""
    modal.enable_output()

    app = modal.App.lookup("keystone-load-test-v2", create_if_missing=True)
    image = create_modal_image()

    secrets: list[modal.Secret] = []
    if with_cache:
        secrets.append(modal.Secret.from_name("keystone-docker-registry-config"))

    print(f"Creating Modal sandbox (with_cache={with_cache})...", file=sys.stderr)
    sb = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=60 * 60 * 2,  # 2 hours
        region="us-west-2",
        secrets=secrets,
        experimental_options={"enable_docker": True},
    )
    sandbox_id = sb.object_id
    print(f"Sandbox created: {sandbox_id}", file=sys.stderr)
    print(f"  Shell: modal shell {sandbox_id}", file=sys.stderr)

    try:
        # Start Docker daemon
        print("Starting Docker daemon...", file=sys.stderr)
        sb.exec("/start-dockerd.sh")
        exit_code, _ = _exec_script(sb, "/wait_for_docker.sh", label="docker-wait")
        if exit_code != 0:
            raise RuntimeError("Docker daemon failed to start")

        # Docker login for cache registry (if enabled)
        if with_cache:
            print("Logging into cache registry...", file=sys.stderr)
            login_script = textwrap.dedent("""\
                #!/bin/bash
                set -euo pipefail
                echo "$DOCKER_BUILD_CACHE_REGISTRY_PASSWORD" | \\
                    docker login \\
                        --username "$DOCKER_BUILD_CACHE_REGISTRY_USERNAME" \\
                        --password-stdin \\
                        "$DOCKER_BUILD_CACHE_REGISTRY_URL"
            """)
            exit_code, _ = _exec_script(sb, login_script, label="docker-login")
            if exit_code != 0:
                raise RuntimeError("Docker login failed")

        # Upload the minimal project
        print("Uploading test project...", file=sys.stderr)
        setup_script = textwrap.dedent(f"""\
            #!/bin/bash
            set -euo pipefail
            mkdir -p /project/.devcontainer

            cat > /project/.devcontainer/Dockerfile << 'DOCKERFILE_EOF'
            {DOCKERFILE}
            DOCKERFILE_EOF

            cat > /project/.devcontainer/devcontainer.json << 'JSON_EOF'
            {DEVCONTAINER_JSON}
            JSON_EOF

            echo "# load test project" > /project/README.md
        """)
        exit_code, _ = _exec_script(sb, setup_script, label="setup")
        if exit_code != 0:
            raise RuntimeError("Project setup failed")

        # Build loop
        results: list[dict[str, object]] = []
        for i in range(1, iterations + 1):
            print(f"\n{'=' * 60}", file=sys.stderr)
            print(f"Iteration {i}/{iterations}", file=sys.stderr)
            print(f"{'=' * 60}", file=sys.stderr)

            # Build the devcontainer image
            if with_cache:
                build_script = textwrap.dedent("""\
                    #!/bin/bash
                    set -euo pipefail
                    CACHE_REF="$DOCKER_BUILD_CACHE_REGISTRY_URL/loadtest-cache:latest"
                    devcontainer build \
                        --workspace-folder /project \
                        --image-name loadtest:latest \
                        --cache-from "type=registry,ref=$CACHE_REF" \
                        --cache-to "type=registry,ref=$CACHE_REF,mode=max" \
                        2>&1
                """)
            else:
                build_script = textwrap.dedent("""\
                    #!/bin/bash
                    set -euo pipefail
                    devcontainer build \
                        --workspace-folder /project \
                        --image-name loadtest:latest \
                        2>&1
                """)

            build_start = time.time()
            exit_code, output = _exec_script(sb, build_script, label=f"build-{i}")
            build_secs = time.time() - build_start

            rate_limited = any(
                phrase in output.lower()
                for phrase in [
                    "429 too many requests",
                    "toomanyrequests",
                    "rate limit",
                    "you have reached your pull rate limit",
                    "retry-after",
                ]
            )

            result = {
                "iteration": i,
                "exit_code": exit_code,
                "build_seconds": round(build_secs, 1),
                "rate_limited": rate_limited,
            }
            results.append(result)

            status = "RATE LIMITED" if rate_limited else ("OK" if exit_code == 0 else "FAILED")
            print(
                f"  -> {status} (exit={exit_code}, {build_secs:.1f}s)",
                file=sys.stderr,
            )

            if rate_limited:
                print(
                    f"\n🎯 Rate limiting detected on iteration {i}!",
                    file=sys.stderr,
                )

            # Prune everything to force fresh pulls next iteration
            print("  Pruning all Docker state...", file=sys.stderr)
            prune_script = textwrap.dedent("""\
                #!/bin/bash
                set -euo pipefail
                docker system prune -af --volumes 2>&1
                docker buildx prune -af 2>&1
            """)
            _exec_script(sb, prune_script, label=f"prune-{i}")

        # Summary
        print(f"\n{'=' * 60}", file=sys.stderr)
        print("LOAD TEST RESULTS", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        total = len(results)
        rate_limited_count = sum(1 for r in results if r["rate_limited"])
        failed_count = sum(1 for r in results if r["exit_code"] != 0)
        print(f"Total iterations: {total}", file=sys.stderr)
        print(f"Rate limited:     {rate_limited_count}", file=sys.stderr)
        print(f"Other failures:   {failed_count - rate_limited_count}", file=sys.stderr)
        print(f"Successful:       {total - failed_count}", file=sys.stderr)
        for r in results:
            flag = (
                " ⚠️  RATE LIMITED"
                if r["rate_limited"]
                else (" ❌ FAILED" if r["exit_code"] != 0 else " ✅")
            )
            print(
                f"  #{r['iteration']:3d}: exit={r['exit_code']} "
                f"time={r['build_seconds']:6.1f}s{flag}",
                file=sys.stderr,
            )

    finally:
        print("\nTerminating sandbox...", file=sys.stderr)
        sb.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test: reproduce Docker Hub rate limiting")
    parser.add_argument(
        "--iterations",
        type=int,
        default=20,
        help="Number of build+prune cycles (default: 20)",
    )
    parser.add_argument(
        "--with-cache",
        action="store_true",
        help="Use the Modal registry cache (to test mitigation)",
    )
    args = parser.parse_args()
    run_load_test(iterations=args.iterations, with_cache=args.with_cache)


if __name__ == "__main__":
    main()
