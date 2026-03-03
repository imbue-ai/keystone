"""Load test v2: reproduce Docker Hub rate limiting from a single IP.

Uses a SINGLE Modal sandbox to run sequential devcontainer builds, each
followed by a full Docker prune. The entire build+prune loop runs as one
bash script inside the sandbox (zero Python↔Modal round-trips in the hot
loop), so the only time spent is actual Docker work.

Usage:
    cd modal_registry && uv run python load_test_v2.py [--iterations 50] [--with-cache]

Prerequisites:
    - Modal configured (modal token set)
    - For --with-cache: registry deployed and secret configured (see README.md)
"""

import argparse
import sys
import textwrap

import modal

# ---------------------------------------------------------------------------
# Reuse the keystone Modal image which already has Docker + devcontainer CLI
# ---------------------------------------------------------------------------
sys.path.insert(
    0, str(__import__("pathlib").Path(__file__).resolve().parent.parent / "keystone" / "src")
)
from keystone.modal.image import create_modal_image

DOCKERFILE = textwrap.dedent("""\
    FROM python:3.12-slim
    RUN echo "built"
""")

DEVCONTAINER_JSON = """\
{
  "name": "load-test",
  "build": {
    "dockerfile": "Dockerfile",
    "context": ".."
  }
}
"""

RATE_LIMIT_PHRASES = [
    "429 too many requests",
    "toomanyrequests",
    "rate limit",
    "you have reached your pull rate limit",
    "retry-after",
]


def _exec_script(
    sb: modal.Sandbox,
    script: str,
    *,
    label: str = "cmd",
) -> tuple[int, str]:
    """Execute a bash command/script in the sandbox and return (exit_code, output)."""
    proc = sb.exec("bash", "-c", script)

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


def _build_loop_script(iterations: int, with_cache: bool) -> str:
    """Generate a bash script that runs the entire build+prune loop.

    The loop runs entirely inside the sandbox so there are no
    Python↔Modal round-trips between iterations.  Each iteration:
      1. devcontainer build (pulls base image from Docker Hub)
      2. docker system prune -af + docker buildx prune -af
    """
    if with_cache:
        build_cmd = (
            'CACHE_REF="$DOCKER_BUILD_CACHE_REGISTRY_URL/loadtest-cache:latest"\n'
            "    devcontainer build "
            "--workspace-folder /project "
            "--image-name loadtest:latest "
            '"--cache-from" "type=registry,ref=$CACHE_REF" '
            '"--cache-to" "type=registry,ref=$CACHE_REF,mode=max" '
            "2>&1"
        )
    else:
        build_cmd = (
            "devcontainer build --workspace-folder /project --image-name loadtest:latest 2>&1"
        )

    return textwrap.dedent(f"""\
        #!/bin/bash
        set -uo pipefail

        ITERATIONS={iterations}
        PASS=0
        FAIL=0

        for i in $(seq 1 $ITERATIONS); do
            echo ""
            echo "=== Iteration $i/$ITERATIONS ==="

            START_NS=$(date +%s%N)
            {build_cmd}
            EXIT_CODE=$?
            END_NS=$(date +%s%N)
            ELAPSED_MS=$(( (END_NS - START_NS) / 1000000 ))

            if [ "$EXIT_CODE" -eq 0 ]; then
                PASS=$((PASS + 1))
                echo "OK (exit=0, ${{ELAPSED_MS}}ms) [pass=$PASS fail=$FAIL]"
            else
                FAIL=$((FAIL + 1))
                echo "FAILED (exit=$EXIT_CODE, ${{ELAPSED_MS}}ms) [pass=$PASS fail=$FAIL]"
            fi

            # Prune everything so next iteration must pull fresh
            docker system prune -af --volumes >/dev/null 2>&1
            docker buildx prune -af >/dev/null 2>&1
        done

        echo ""
        echo "=== SUMMARY ==="
        echo "Total: $ITERATIONS  Pass: $PASS  Fail: $FAIL"
    """)


def run_load_test(iterations: int, with_cache: bool) -> None:
    """Run sequential devcontainer builds in a single sandbox."""
    modal.enable_output()

    app = modal.App.lookup("keystone-load-test-v2", create_if_missing=True)
    image = create_modal_image()

    secrets: list[modal.Secret] = []
    if with_cache:
        secrets.append(modal.Secret.from_name("keystone-docker-registry-config"))

    print(
        f"Creating Modal sandbox (iterations={iterations}, with_cache={with_cache})...",
        file=sys.stderr,
    )
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
            login_cmd = (
                'echo "$DOCKER_BUILD_CACHE_REGISTRY_PASSWORD" | '
                "docker login "
                '--username "$DOCKER_BUILD_CACHE_REGISTRY_USERNAME" '
                "--password-stdin "
                '"$DOCKER_BUILD_CACHE_REGISTRY_URL"'
            )
            exit_code, _ = _exec_script(sb, login_cmd, label="docker-login")
            if exit_code != 0:
                raise RuntimeError("Docker login failed")

        # Set up project directory
        print("Setting up project...", file=sys.stderr)
        _exec_script(sb, "mkdir -p /project/.devcontainer", label="setup")
        with sb.open("/project/.devcontainer/Dockerfile", "w") as f:
            f.write(DOCKERFILE)
        with sb.open("/project/.devcontainer/devcontainer.json", "w") as f:
            f.write(DEVCONTAINER_JSON)
        with sb.open("/project/README.md", "w") as f:
            f.write("# load test project\n")

        # Run the entire loop as a single bash script inside the sandbox
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(
            f"Running {iterations} sequential build+prune cycles...",
            file=sys.stderr,
        )
        print(f"{'=' * 60}\n", file=sys.stderr)

        loop_script = _build_loop_script(iterations, with_cache)
        with sb.open("/tmp/_load_test_loop.sh", "w") as f:
            f.write(loop_script)

        proc = sb.exec("bash", "/tmp/_load_test_loop.sh")

        # Just stream all output directly — the bash script handles formatting
        for line in proc.stdout:
            print(line, end="", file=sys.stderr)
        for line in proc.stderr:
            print(line, end="", file=sys.stderr)

        proc.wait()

    finally:
        print("\nTerminating sandbox...", file=sys.stderr)
        sb.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test: reproduce Docker Hub rate limiting")
    parser.add_argument(
        "--iterations",
        type=int,
        default=50,
        help="Number of build+prune cycles (default: 50)",
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
