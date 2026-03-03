"""Load test v2: reproduce Docker Hub rate limiting from a single IP.

Unlike the original approach (multiple sandboxes with different IPs),
this test uses a SINGLE Modal sandbox and launches many concurrent
devcontainer builds simultaneously. Each build targets a unique image
name, so Docker can't share layers between them and they all race to
pull python:3.12-slim from Docker Hub at the same time from the same IP.

Usage:
    cd modal_registry && uv run python load_test_v2.py [--builds 50] [--with-cache]

Prerequisites:
    - Modal configured (modal token set)
    - For --with-cache: registry deployed and secret configured (see README.md)
"""

import argparse
import sys
import textwrap
import threading
import time
from dataclasses import dataclass, field

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

DEVCONTAINER_JSON_TEMPLATE = """\
{{
  "name": "load-test-{index}",
  "build": {{
    "dockerfile": "Dockerfile",
    "context": ".."
  }}
}}
"""

RATE_LIMIT_PHRASES = [
    "429 too many requests",
    "toomanyrequests",
    "rate limit",
    "you have reached your pull rate limit",
    "retry-after",
]


@dataclass
class BuildResult:
    """Result from a single concurrent build."""

    index: int
    exit_code: int
    duration_secs: float
    rate_limited: bool
    output: str = field(repr=False)


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


def _run_one_build(
    sb: modal.Sandbox,
    index: int,
    with_cache: bool,
) -> BuildResult:
    """Run a single devcontainer build inside the sandbox."""
    label = f"build-{index:03d}"
    project_dir = f"/projects/p{index}"

    if with_cache:
        build_cmd = (
            f'CACHE_REF="$DOCKER_BUILD_CACHE_REGISTRY_URL/loadtest-cache:latest" && '
            f"devcontainer build "
            f"--workspace-folder {project_dir} "
            f"--image-name loadtest-{index}:latest "
            f'--cache-from "type=registry,ref=$CACHE_REF" '
            f'--cache-to "type=registry,ref=$CACHE_REF,mode=max" '
            f"2>&1"
        )
    else:
        build_cmd = (
            f"devcontainer build "
            f"--workspace-folder {project_dir} "
            f"--image-name loadtest-{index}:latest "
            f"2>&1"
        )

    start = time.time()
    exit_code, output = _exec_script(sb, build_cmd, label=label)
    duration = time.time() - start

    rate_limited = any(phrase in output.lower() for phrase in RATE_LIMIT_PHRASES)

    return BuildResult(
        index=index,
        exit_code=exit_code,
        duration_secs=round(duration, 1),
        rate_limited=rate_limited,
        output=output,
    )


def run_load_test(num_builds: int, with_cache: bool) -> None:
    """Launch num_builds concurrent devcontainer builds in a single sandbox."""
    modal.enable_output()

    app = modal.App.lookup("keystone-load-test-v2", create_if_missing=True)
    image = create_modal_image()

    secrets: list[modal.Secret] = []
    if with_cache:
        secrets.append(modal.Secret.from_name("keystone-docker-registry-config"))

    print(
        f"Creating Modal sandbox (builds={num_builds}, with_cache={with_cache})...", file=sys.stderr
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

        # Create N project directories, each with its own devcontainer config.
        # Each gets a unique devcontainer.json name so devcontainer CLI treats
        # them as separate projects.
        print(f"Setting up {num_builds} project directories...", file=sys.stderr)
        for i in range(num_builds):
            project_dir = f"/projects/p{i}"
            dc_dir = f"{project_dir}/.devcontainer"
            _exec_script(sb, f"mkdir -p {dc_dir}", label="setup")
            with sb.open(f"{dc_dir}/Dockerfile", "w") as f:
                f.write(DOCKERFILE)
            with sb.open(f"{dc_dir}/devcontainer.json", "w") as f:
                f.write(DEVCONTAINER_JSON_TEMPLATE.format(index=i))
            with sb.open(f"{project_dir}/README.md", "w") as f:
                f.write(f"# load test project {i}\n")

        # Verify one of them
        exit_code, _ = _exec_script(
            sb,
            "ls /projects/p0/.devcontainer/Dockerfile /projects/p0/.devcontainer/devcontainer.json",
            label="setup",
        )
        if exit_code != 0:
            raise RuntimeError("Project setup failed")

        # Launch all builds concurrently from threads (each calls sb.exec)
        print(f"\n{'=' * 60}", file=sys.stderr)
        print(f"Launching {num_builds} concurrent devcontainer builds...", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)

        overall_start = time.time()
        results: list[BuildResult] = []
        lock = threading.Lock()

        def _build_thread(idx: int) -> None:
            result = _run_one_build(sb, idx, with_cache)
            with lock:
                results.append(result)
                status = (
                    "RATE LIMITED"
                    if result.rate_limited
                    else ("OK" if result.exit_code == 0 else "FAILED")
                )
                print(
                    f"  [{idx:03d}] {status} (exit={result.exit_code}, "
                    f"{result.duration_secs:.1f}s)",
                    file=sys.stderr,
                )

        threads = [threading.Thread(target=_build_thread, args=(i,)) for i in range(num_builds)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        overall_duration = time.time() - overall_start

        # Summary
        results.sort(key=lambda r: r.index)
        print(f"\n{'=' * 60}", file=sys.stderr)
        print("LOAD TEST RESULTS", file=sys.stderr)
        print(f"{'=' * 60}", file=sys.stderr)
        total = len(results)
        rate_limited_count = sum(1 for r in results if r.rate_limited)
        failed_count = sum(1 for r in results if r.exit_code != 0)
        succeeded = total - failed_count
        print(f"Total builds:      {total}", file=sys.stderr)
        print(f"Succeeded:         {succeeded}", file=sys.stderr)
        print(f"Rate limited:      {rate_limited_count}", file=sys.stderr)
        print(f"Other failures:    {failed_count - rate_limited_count}", file=sys.stderr)
        print(f"Overall duration:  {overall_duration:.1f}s", file=sys.stderr)

        if succeeded > 0:
            ok_durations = sorted(r.duration_secs for r in results if r.exit_code == 0)
            print(f"Build time (min):  {ok_durations[0]:.1f}s", file=sys.stderr)
            print(
                f"Build time (med):  {ok_durations[len(ok_durations) // 2]:.1f}s", file=sys.stderr
            )
            print(f"Build time (max):  {ok_durations[-1]:.1f}s", file=sys.stderr)

        print(file=sys.stderr)
        for r in results:
            flag = (
                " ⚠️  RATE LIMITED"
                if r.rate_limited
                else (" ❌ FAILED" if r.exit_code != 0 else " ✅")
            )
            print(
                f"  #{r.index:3d}: exit={r.exit_code} time={r.duration_secs:6.1f}s{flag}",
                file=sys.stderr,
            )

        if rate_limited_count:
            print(
                f"\n🎯 {rate_limited_count}/{total} builds hit Docker Hub rate limits!",
                file=sys.stderr,
            )
        elif failed_count:
            print(f"\n❌ {failed_count}/{total} builds failed (not rate-limited)", file=sys.stderr)
        else:
            print("\n✅ All builds succeeded — no rate limiting observed", file=sys.stderr)

    finally:
        print("\nTerminating sandbox...", file=sys.stderr)
        sb.terminate()


def main() -> None:
    parser = argparse.ArgumentParser(description="Load test: reproduce Docker Hub rate limiting")
    parser.add_argument(
        "--builds",
        type=int,
        default=50,
        help="Number of concurrent builds (default: 50)",
    )
    parser.add_argument(
        "--with-cache",
        action="store_true",
        help="Use the Modal registry cache (to test mitigation)",
    )
    args = parser.parse_args()
    run_load_test(num_builds=args.builds, with_cache=args.with_cache)


if __name__ == "__main__":
    main()
