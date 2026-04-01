"""Reproduce and re-run verification locally from an S3 eval result.

Given an S3 output directory for a single repo trial (e.g.
``s3://…/claude-opus/flask/trial_0/``), this tool downloads the devcontainer
tarball and repo tarball, builds the image locally, runs the clean verification,
and optionally runs broken-branch verifications — all without Modal or an agent.

Usage::

    uv run repro-verify s3://int8-datasets/keystone/evals/2026-04-01_thad_eval_v1/claude-opus/libjwt/trial_0/
    uv run repro-verify s3://…/trial_0/ --broken-only   # skip clean, just run broken branches
    uv run repro-verify s3://…/trial_0/ --branches broken-1,broken-2  # specific branches
"""

import argparse
import json
import shutil
import subprocess
import sys
import tarfile
import tempfile
import time
from pathlib import Path

from keystone.junit_report_parser import enrich_verification_with_junit
from keystone.schema import VerificationResult

TIMEOUT_EXIT_CODE = 124


def _download_s3(s3_path: str, local_path: Path) -> None:
    """Download a file from S3."""
    result = subprocess.run(
        ["aws", "s3", "cp", s3_path, str(local_path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(f"Failed to download {s3_path}: {result.stderr}")


def _run_tests_in_container(
    container_name: str,
    test_timeout_seconds: int,
    test_artifacts_dir: Path,
    *,
    use_docker_exec: bool = False,
    image_name: str | None = None,
    image_build_seconds: float | None = None,
) -> VerificationResult:
    """Run /run_all_tests.sh, extract artifacts, parse JUnit.

    Shared between clean verification (docker run) and broken-branch
    verification (docker exec on persistent container).
    """
    # Clear stale JUnit artifacts
    if use_docker_exec:
        subprocess.run(
            [
                "docker",
                "exec",
                container_name,
                "sh",
                "-c",
                "rm -rf /test_artifacts/junit && mkdir -p /test_artifacts/junit",
            ],
            capture_output=True,
        )

    # Run tests
    test_start = time.time()
    if use_docker_exec:
        test_cmd = [
            "docker",
            "exec",
            container_name,
            "timeout",
            str(test_timeout_seconds),
            "bash",
            "/run_all_tests.sh",
        ]
    else:
        if image_name is None:
            raise ValueError("image_name required for docker-run mode")
        test_cmd = [
            "timeout",
            str(test_timeout_seconds),
            "docker",
            "run",
            "--name",
            container_name,
            image_name,
            "bash",
            "/run_all_tests.sh",
        ]
    test_proc = subprocess.run(test_cmd, capture_output=True, text=True)
    test_execution_seconds = time.time() - test_start

    # Extract artifacts
    test_artifacts_dir.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["docker", "cp", f"{container_name}:/test_artifacts/.", str(test_artifacts_dir)],
        capture_output=True,
    )

    if not use_docker_exec:
        subprocess.run(["docker", "rm", container_name], capture_output=True)

    # Build result from exit code
    if test_proc.returncode == TIMEOUT_EXIT_CODE:
        result = VerificationResult(
            success=False,
            error_message=f"Test execution timed out after {test_timeout_seconds}s",
            image_build_seconds=image_build_seconds,
            test_execution_seconds=test_execution_seconds,
        )
    elif test_proc.returncode == 0:
        result = VerificationResult(
            success=True,
            image_build_seconds=image_build_seconds,
            test_execution_seconds=test_execution_seconds,
        )
    else:
        result = VerificationResult(
            success=False,
            error_message=f"Tests failed (exit {test_proc.returncode})",
            image_build_seconds=image_build_seconds,
            test_execution_seconds=test_execution_seconds,
        )

    return enrich_verification_with_junit(result, test_artifacts_dir)


def _print_result(label: str, result: VerificationResult) -> None:
    """Print a verification result summary."""
    status = "✅ PASS" if result.success else "❌ FAIL"
    parts = [f"  {label}: {status}"]
    if result.tests_passed or result.tests_failed or result.tests_skipped:
        parts.append(
            f"  passed={result.tests_passed} failed={result.tests_failed} "
            f"skipped={result.tests_skipped} total={len(result.test_results)}"
        )
    if result.test_execution_seconds is not None:
        parts.append(f"  time={result.test_execution_seconds:.1f}s")
    if result.error_message:
        # Truncate long error messages
        msg = result.error_message[:200]
        parts.append(f"  error: {msg}")
    print("\n".join(parts))


def main() -> None:
    parser = argparse.ArgumentParser(description="Reproduce verification from an S3 eval result")
    parser.add_argument(
        "s3_trial_dir",
        help="S3 path to the trial directory (e.g. s3://…/claude-opus/flask/trial_0/)",
    )
    parser.add_argument(
        "--broken-only",
        action="store_true",
        help="Skip clean verification, only run broken branches",
    )
    parser.add_argument(
        "--branches",
        help="Comma-separated list of broken branches to test (default: all from eval_result.json)",
    )
    parser.add_argument(
        "--test-timeout",
        type=int,
        default=300,
        help="Test timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--no-touch",
        action="store_true",
        help="Don't touch source files after docker cp (for debugging timestamp issues)",
    )
    args = parser.parse_args()

    s3_dir = args.s3_trial_dir.rstrip("/")

    # Create temp workspace
    work_dir = Path(tempfile.mkdtemp(prefix="repro-verify-"))
    print(f"Working directory: {work_dir}")

    try:
        # 1. Download eval_result.json to get repo info
        print("Downloading eval_result.json...")
        eval_result_path = work_dir / "eval_result.json"
        _download_s3(f"{s3_dir}/eval_result.json", eval_result_path)
        with eval_result_path.open() as f:
            eval_result = json.load(f)

        repo_entry = eval_result["repo_entry"]
        repo_id = repo_entry["id"]
        print(f"Repo: {repo_id}")

        # Get S3 repo cache prefix from eval_config
        eval_config = eval_result.get("eval_config", {})
        s3_repo_cache = eval_config.get("s3_repo_cache_prefix", "")

        # 2. Download devcontainer tarball
        print("Downloading devcontainer tarball...")
        dc_tarball_path = work_dir / "devcontainer.tar.gz"
        _download_s3(f"{s3_dir}/devcontainer.tar.gz", dc_tarball_path)

        # 3. Download repo tarball
        print("Downloading repo tarball...")
        repo_tarball_path = work_dir / "repo.tar.gz"
        if s3_repo_cache:
            repo_s3 = f"{s3_repo_cache.rstrip('/')}/{repo_id}.tar.gz"
        else:
            # Guess from the eval_result structure
            repo_s3 = (
                f"s3://int8-datasets/keystone/evals/repo-tarballs-with-mutations/{repo_id}.tar.gz"
            )
        _download_s3(repo_s3, repo_tarball_path)

        # 4. Extract repo (bare git) and clone
        print("Setting up repo...")
        repo_extract = work_dir / "repo_extract"
        repo_extract.mkdir()
        with tarfile.open(repo_tarball_path, "r:gz") as tar:
            tar.extractall(repo_extract, filter="data")

        # Find the bare git repo
        bare_repo = None
        for p in repo_extract.iterdir():
            if p.is_dir() and (p / "HEAD").exists():
                bare_repo = p
                break
        if bare_repo is None:
            print("ERROR: Could not find bare git repo in tarball", file=sys.stderr)
            sys.exit(1)

        project_dir = work_dir / "project"
        subprocess.run(
            ["git", "clone", str(bare_repo), str(project_dir)],
            capture_output=True,
            check=True,
        )

        # 5. Overlay devcontainer
        with tarfile.open(dc_tarball_path, "r:gz") as tar:
            tar.extractall(project_dir, filter="data")

        # 6. Build Docker image
        image_name = f"repro-verify-{repo_id}"
        container_name = f"repro-verify-{repo_id}-container"
        print(f"Building Docker image '{image_name}'...")
        build_start = time.time()
        build_proc = subprocess.run(
            [
                "docker",
                "build",
                "-t",
                image_name,
                "-f",
                str(project_dir / ".devcontainer" / "Dockerfile"),
                str(project_dir),
            ],
            capture_output=True,
            text=True,
        )
        build_seconds = time.time() - build_start
        if build_proc.returncode != 0:
            print(f"ERROR: Docker build failed ({build_seconds:.1f}s):", file=sys.stderr)
            print(build_proc.stderr[-2000:], file=sys.stderr)
            sys.exit(1)
        print(f"Image built in {build_seconds:.1f}s")

        # 7. Clean verification
        if not args.broken_only:
            print("\n=== Clean verification ===")
            subprocess.run(["docker", "rm", "-f", container_name], capture_output=True)
            with tempfile.TemporaryDirectory(prefix="clean-artifacts-") as artifacts_dir:
                clean_result = _run_tests_in_container(
                    container_name=container_name,
                    test_timeout_seconds=args.test_timeout,
                    test_artifacts_dir=Path(artifacts_dir),
                    image_name=image_name,
                    image_build_seconds=build_seconds,
                )
            _print_result("Clean", clean_result)

        # 8. Broken-branch verifications
        broken_refs: list[str] = []
        if args.branches:
            broken_refs = [b.strip() for b in args.branches.split(",")]
        else:
            # Get from eval_result or repo_entry
            br = eval_result.get("bootstrap_result", {})
            bcv = br.get("broken_commit_verifications", {})
            if isinstance(bcv, dict):
                broken_refs = list(bcv.keys())
            elif repo_entry.get("broken_branches"):
                broken_refs = repo_entry["broken_branches"]

        if broken_refs:
            # Detect WORKDIR from image
            workdir_proc = subprocess.run(
                ["docker", "inspect", image_name, "--format", "{{.Config.WorkingDir}}"],
                capture_output=True,
                text=True,
            )
            project_dir_in_container = workdir_proc.stdout.strip() or "/project"

            # Start persistent container
            broken_container = f"repro-broken-{repo_id}"
            subprocess.run(["docker", "rm", "-f", broken_container], capture_output=True)
            subprocess.run(
                [
                    "docker",
                    "run",
                    "-d",
                    "--name",
                    broken_container,
                    image_name,
                    "sleep",
                    "infinity",
                ],
                capture_output=True,
                check=True,
            )

            print(f"\n=== Broken-branch verification ({len(broken_refs)} branches) ===")
            print(f"Container WORKDIR: {project_dir_in_container}")
            if args.no_touch:
                print("⚠️  --no-touch mode: skipping file touch after docker cp")

            unexpected_passes = 0

            def _copy_files_from_ref(source_ref: str, files: list[str]) -> None:
                """Extract specific files from source_ref and copy into container."""
                if not files:
                    return
                archive_proc = subprocess.run(
                    ["git", "archive", source_ref, "--", *files],
                    cwd=project_dir,
                    capture_output=True,
                    check=True,
                )
                with tempfile.NamedTemporaryFile(suffix=".tar", delete=False) as tmp:
                    tmp.write(archive_proc.stdout)
                    tmp_path = tmp.name
                try:
                    with tempfile.TemporaryDirectory() as extract_dir:
                        subprocess.run(
                            ["tar", "xf", tmp_path, "-C", extract_dir],
                            check=True,
                            capture_output=True,
                        )
                        if not args.no_touch:
                            subprocess.run(
                                ["find", extract_dir, "-type", "f", "-exec", "touch", "{}", "+"],
                                capture_output=True,
                            )
                        subprocess.run(
                            [
                                "docker",
                                "cp",
                                f"{extract_dir}/.",
                                f"{broken_container}:{project_dir_in_container}/",
                            ],
                            check=True,
                            capture_output=True,
                        )
                finally:
                    Path(tmp_path).unlink(missing_ok=True)

            try:
                for ref in broken_refs:
                    full_ref = f"remotes/origin/{ref}" if not ref.startswith("remotes/") else ref
                    # Find changed files
                    diff_proc = subprocess.run(
                        ["git", "diff", "--name-only", "HEAD", full_ref],
                        cwd=project_dir,
                        capture_output=True,
                        text=True,
                    )
                    if diff_proc.returncode != 0:
                        print(f"  {ref}: ⚠️  git diff failed")
                        continue
                    changed = [f for f in diff_proc.stdout.strip().split("\n") if f]

                    # Apply mutation (only changed files)
                    try:
                        _copy_files_from_ref(full_ref, changed)
                    except Exception as e:
                        print(f"  {ref}: ⚠️  apply failed: {e}")
                        continue

                    # Run tests
                    with tempfile.TemporaryDirectory(prefix=f"broken-{ref}-") as ad:
                        result = _run_tests_in_container(
                            container_name=broken_container,
                            test_timeout_seconds=args.test_timeout,
                            test_artifacts_dir=Path(ad),
                            use_docker_exec=True,
                        )
                    _print_result(ref, result)
                    if result.success:
                        unexpected_passes += 1

                    # Reverse mutation (restore HEAD versions)
                    try:
                        _copy_files_from_ref("HEAD", changed)
                    except Exception:
                        print(f"  ⚠️  Failed to restore HEAD after {ref}")

                # Restoration check (container should already be clean)
                print("\n--- Restoration check (HEAD) ---")
                with tempfile.TemporaryDirectory(prefix="restoration-") as ad:
                    restore_result = _run_tests_in_container(
                        container_name=broken_container,
                        test_timeout_seconds=args.test_timeout,
                        test_artifacts_dir=Path(ad),
                        use_docker_exec=True,
                    )
                _print_result("HEAD (restoration)", restore_result)

            finally:
                subprocess.run(["docker", "stop", broken_container], capture_output=True)
                subprocess.run(["docker", "rm", broken_container], capture_output=True)

            print("\n=== Summary ===")
            print(f"Unexpected broken-branch passes: {unexpected_passes}/{len(broken_refs)}")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)
        print(f"\nCleaned up {work_dir}")


if __name__ == "__main__":
    main()
