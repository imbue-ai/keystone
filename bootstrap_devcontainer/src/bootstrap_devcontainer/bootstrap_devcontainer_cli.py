import hashlib
import json
import logging
import shlex
import subprocess
import sys
import time
from pathlib import Path

import typer
from rich.console import Console

from bootstrap_devcontainer.agent_cache import (
    AgentCache,
    CacheValue,
    EventCollector,
    compute_cache_key,
    compute_directory_hash,
    extract_devcontainer_tarball,
)
from bootstrap_devcontainer.agent_runner import LocalAgentRunner
from bootstrap_devcontainer.schema import (
    BootstrapResult,
    TestSummary,
    TokenSpending,
)

# Configure logging with detailed format for our modules only
# Avoid DEBUG level for noisy third-party libs like hpack, httpcore, etc.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(filename)s:%(lineno)d %(funcName)s] [%(thread)d] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
)
# Enable DEBUG for our own modules
logging.getLogger("bootstrap_devcontainer").setLevel(logging.DEBUG)

app = typer.Typer()
console = Console(force_terminal=True)


def check_docker_available() -> bool:
    """Check if Docker CLI is installed and daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            timeout=10,
        )
        if result.returncode != 0:
            console.print("[red]Error: Docker daemon is not running.[/red]")
            return False
        return True
    except FileNotFoundError:
        console.print("[red]Error: Docker CLI is not installed or not in PATH.[/red]")
        return False
    except subprocess.TimeoutExpired:
        console.print("[red]Error: Docker command timed out.[/red]")
        return False


STATUS_MARKER = "BOOTSTRAP_DEVCONTAINER_STATUS:"


class TestReports:
    """Container for per-language test summaries."""

    def __init__(self) -> None:
        self.pytest_summary: TestSummary | None = None
        self.go_test_summary: TestSummary | None = None
        self.node_test_summary: TestSummary | None = None
        self.cargo_test_summary: TestSummary | None = None


def parse_test_reports(test_artifacts_dir: Path) -> TestReports:
    """Parse test reports from various formats (pytest, go, node, cargo)."""
    reports = TestReports()

    # Try pytest JSON report
    pytest_report = test_artifacts_dir / "pytest-json-report.json"
    if pytest_report.exists():
        try:
            report_data = json.loads(pytest_report.read_text())
            tests = report_data.get("tests", [])
            passed = sorted([t["nodeid"] for t in tests if t.get("outcome") == "passed"])
            failed = sorted([t["nodeid"] for t in tests if t.get("outcome") == "failed"])
            skipped = sorted([t["nodeid"] for t in tests if t.get("outcome") == "skipped"])
            reports.pytest_summary = TestSummary(
                passed_count=len(passed),
                failed_count=len(failed),
                skipped_count=len(skipped),
                passed_tests=passed,
                failed_tests=failed,
                skipped_tests=skipped,
            )
        except Exception as e:
            print(f"Error parsing pytest report: {e}", file=sys.stderr)

    # Try Go test JSON report
    go_report = test_artifacts_dir / "go-test-report.json"
    if go_report.exists():
        try:
            passed, failed, skipped = [], [], []
            for line in go_report.read_text().strip().split("\n"):
                if not line:
                    continue
                event = json.loads(line)
                if event.get("Action") == "pass" and event.get("Test"):
                    passed.append(event["Test"])
                elif event.get("Action") == "fail" and event.get("Test"):
                    failed.append(event["Test"])
                elif event.get("Action") == "skip" and event.get("Test"):
                    skipped.append(event["Test"])
            reports.go_test_summary = TestSummary(
                passed_count=len(passed),
                failed_count=len(failed),
                skipped_count=len(skipped),
                passed_tests=sorted(passed),
                failed_tests=sorted(failed),
                skipped_tests=sorted(skipped),
            )
        except Exception as e:
            print(f"Error parsing Go test report: {e}", file=sys.stderr)

    # Try Node test JSON report
    node_report = test_artifacts_dir / "node-test-report.json"
    if node_report.exists():
        try:
            report_data = json.loads(node_report.read_text())
            passed, failed, skipped = [], [], []
            for test in report_data.get("tests", []):
                name = test.get("name", "")
                status = test.get("status", "")
                if status == "passed":
                    passed.append(name)
                elif status == "failed":
                    failed.append(name)
                elif status == "skipped":
                    skipped.append(name)
            reports.node_test_summary = TestSummary(
                passed_count=len(passed),
                failed_count=len(failed),
                skipped_count=len(skipped),
                passed_tests=sorted(passed),
                failed_tests=sorted(failed),
                skipped_tests=sorted(skipped),
            )
        except Exception as e:
            print(f"Error parsing Node test report: {e}", file=sys.stderr)

    # Try Cargo test JSON report
    cargo_report = test_artifacts_dir / "cargo-test-report.json"
    if cargo_report.exists():
        try:
            passed, failed, skipped = [], [], []
            for line in cargo_report.read_text().strip().split("\n"):
                if not line:
                    continue
                event = json.loads(line)
                if event.get("type") == "test" and event.get("event") == "ok":
                    passed.append(event.get("name", ""))
                elif event.get("type") == "test" and event.get("event") == "failed":
                    failed.append(event.get("name", ""))
                elif event.get("type") == "test" and event.get("event") == "ignored":
                    skipped.append(event.get("name", ""))
            reports.cargo_test_summary = TestSummary(
                passed_count=len(passed),
                failed_count=len(failed),
                skipped_count=len(skipped),
                passed_tests=sorted(passed),
                failed_tests=sorted(failed),
                skipped_tests=sorted(skipped),
            )
        except Exception as e:
            print(f"Error parsing Cargo test report: {e}", file=sys.stderr)

    return reports


SUMMARY_MARKER = "BOOTSTRAP_DEVCONTAINER_SUMMARY:"

AGENT_PROMPT_TEMPLATE = """
We need to build an appropriate dev container and Dockerfile in which this project's test suite runs successfully. You are currently at the project root.

Instructions:

1. Create a .devcontainer/devcontainer.json file at the project root.
2. Create a .devcontainer/Dockerfile alongside that.
3. Create a .devcontainer/run_all_tests.sh script alongside the Dockerfile (don't forget to make it executable!)
   a. run_all_tests.sh takes no arguments. It always writes test artifacts to /test_artifacts.
   b. It should return 0 (success) IFF all tests pass and forward enough information to stdout/stderr to enable debugging failing tests.
   c. /test_artifacts should be populated with artifacts from running the tests:
      i. For each command run, create a subdirectory with a good “name”.
      ii. In that directory, put files called stdout.txt and stderr.txt, with timestamps.
      iii. Tee the outputs to stdout/stderr.
      iv. Create language-specific JSON test reports in /test_artifacts:
          - Python: /test_artifacts/pytest-json-report.json (use pytest-json-report plugin)
          - Go: /test_artifacts/go-test-report.json (use `go test -json ./...`)
          - Node.js: /test_artifacts/node-test-report.json (use `node --test --test-reporter=json`)
          - Rust: /test_artifacts/cargo-test-report.json (use `cargo test -- -Z unstable-options --format json` or parse output)
      v. A file called /test_artifacts/final_result.json stating success/failure.
4. In the Dockerfile, COPY the input source tree into the image to /project_src as a penultimate step. (no volume mounts for the code.)
5. The Dockerfile should leave the CWD as /project_src.

Notes:
* Start by exploring the repository structure. Use commands like:
  - `find . -type f | sed 's/.*\\.//' | sort | uniq -c | sort -rn` to identify file types
  - `find . -iname '*test*'` to find test-related files and folders
* Only make changes in the .devcontainer/... subtree.
* Optimize the Dockerfile in stages for faster rebuilds.
* Run parts of test suites in parallel if feasible.
* Prefix commands with `time` to see how long they take, and `timeout` to set deadlines.
  Example: `time timeout 300 pytest tests/` to run tests with a 5-minute limit.
* If the project does docker operations (e.g., runs containers as part of tests), ensure the docker CLI
  is installed in the image and run the container with the docker socket exposed. Example:
  ```
  docker run --rm -it \\
    -v /var/run/docker.sock:/var/run/docker.sock \\
    docker:cli ps
  ```
* If tests cannot be fixed by Dockerfile changes, disable them via command line args.
* Disable code coverage collection in run_all_tests.sh (e.g., `pytest --no-cov` or `coverage run` flags) - coverage reports are slow and not needed.
* Emit status updates before and after each major action as plain text output (not via tool calls).
  Simply include the status line in your assistant message text, like:
  BOOTSTRAP_DEVCONTAINER_STATUS: Exploring repository structure to identify file types and test locations.
  Do NOT use echo or bash commands to emit these - just write them as regular assistant text output.
  Examples:
  - BOOTSTRAP_DEVCONTAINER_STATUS: Exploring repository structure to identify file types and test locations.
  - BOOTSTRAP_DEVCONTAINER_STATUS: Creating initial Dockerfile based on detected Python 3.11 project.
  - BOOTSTRAP_DEVCONTAINER_STATUS: Build failed due to missing dependency; adding libpq-dev to Dockerfile.

When finished, emit a final summary as plain text (not via tool calls):
BOOTSTRAP_DEVCONTAINER_SUMMARY: <One-line summary of what worked, what didn't, and any tips for future runs.>
Include anything you wish you had been told at the start. Examples:
- BOOTSTRAP_DEVCONTAINER_SUMMARY: Everything worked. Tip: this project needed uv installed in the container.
- BOOTSTRAP_DEVCONTAINER_SUMMARY: Tests pass. I wish I'd known earlier that exposing the docker socket to the devcontainer would allow running nested docker commands.

Please don't forget to emit the summary at the end.

To verify, use something like this (adding arguments as appropriate for permissions, etc.)
1. Build with `devcontainer build --workspace-folder .`
2. Run `docker run IMAGE ./.devcontainer/run_all_tests.sh` and check return code.
3. If needed, extract artifacts with `docker cp CONTAINER:/test_artifacts ./test_artifacts`.
"""


@app.command()
def bootstrap(
    project_root: Path | None = typer.Option(
        ..., "--project_root", help="Path to the source project"
    ),
    test_artifacts_dir: Path | None = typer.Option(
        None, "--test_artifacts_dir", help="Directory for test artifacts (optional)"
    ),
    agent_cmd: str | None = typer.Option("claude", "--agent_cmd", help="Agent command to run"),
    max_budget_usd: float | None = typer.Option(
        1.0, "--max_budget_usd", help="Maximum dollar amount to spend on agent inference"
    ),
    sqlite_cache_dir: Path | None = typer.Option(
        None, "--sqlite_cache_dir", help="SQLite cache file path (enables caching)"
    ),
    output_file: Path | None = typer.Option(
        None, "--output_file", help="Path to write JSON result (defaults to stdout)"
    ),
    agent_in_modal: bool = typer.Option(
        True,
        "--agent_in_modal/--agent_local",
        help="Run agent in Modal sandbox (default) or locally",
    ),
):
    # Check Docker is available before proceeding
    if not check_docker_available():
        console.print(
            "[red]Docker is required but not available. "
            "Please ensure Docker is installed and the daemon is running.[/red]"
        )
        raise typer.Exit(code=1)

    assert project_root is not None, "--project_root is required"
    project_root = project_root.resolve()
    if test_artifacts_dir is not None:
        test_artifacts_dir = test_artifacts_dir.resolve()
        test_artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Build prompt, adding Modal-specific guidance if needed
    prompt = AGENT_PROMPT_TEMPLATE
    if agent_in_modal:
        modal_addendum = """
IMPORTANT: You are running in a Modal sandbox environment. When using `docker run`,
you MUST use `--network host` for containers to have network access. Bridge networking
does not work in this environment due to gVisor/veth restrictions.

Example: `docker run --network host IMAGE CMD`
"""
        prompt = prompt + modal_addendum

    start_time = time.time()

    # Set up cache if requested
    cache: AgentCache | None = None
    cache_key: str | None = None
    if sqlite_cache_dir is not None:
        cache = AgentCache(sqlite_cache_dir)
        cache_key = compute_cache_key(prompt, project_root)

    token_spending = {"input": 0, "cached": 0, "output": 0, "cache_creation": 0}
    total_cost_usd = 0.0
    model_name = ""

    def check_and_print_status(text: str) -> bool:
        """Check for status/summary markers in text and print in blue if found.

        Returns True if a marker was found.
        """
        found = False
        for line in text.split("\n"):
            if STATUS_MARKER in line:
                # Extract the status message after the marker
                idx = line.find(STATUS_MARKER)
                status_msg = line[idx:].strip()
                print(status_msg, flush=True)
                found = True
            elif SUMMARY_MARKER in line:
                # Extract the summary message after the marker
                idx = line.find(SUMMARY_MARKER)
                summary_msg = line[idx:].strip()
                print(summary_msg, flush=True)
                found = True
        return found

    def process_stdout_line(line: str) -> None:
        """Process a line of agent stdout, extracting messages and token usage."""
        nonlocal total_cost_usd, model_name
        try:
            data = json.loads(line)
            msg_type = data.get("type")

            if msg_type == "assistant":
                content = data.get("message", {}).get("content", [])
                for item in content:
                    if item.get("type") == "text":
                        txt = item.get("text", "").strip()
                        if txt and not check_and_print_status(txt):
                            print(f"Assistant: {txt}", file=sys.stderr, flush=True)
                    elif item.get("type") == "tool_use":
                        name = item.get("name")
                        input_data = item.get("input", {})
                        print(f"Tool Call: {name}({input_data})", file=sys.stderr, flush=True)

            elif msg_type == "result":
                total_cost_usd = data.get("total_cost_usd", 0.0)
                model_name = data.get("model", "")
                usage = data.get("usage", {})
                token_spending["input"] = usage.get("input_tokens", 0)
                token_spending["cached"] = usage.get("cache_read_input_tokens", 0)
                token_spending["output"] = usage.get("output_tokens", 0)
                token_spending["cache_creation"] = usage.get("cache_creation_input_tokens", 0)

            # Log other message types at debug level for visibility
            elif msg_type:
                logging.debug(f"Agent message type={msg_type}: {line[:200]}")

        except json.JSONDecodeError:
            # Not JSON or partial JSON, log it for debugging purposes
            # We use debug level because legitimate partial chunks might trigger this
            logging.debug(f"Agent stdout (non-JSON): {line.strip()}")
            pass

    def process_stderr_line(line: str) -> None:
        """Forward agent stderr to our stderr."""
        print(f"Agent stderr: {line}", file=sys.stderr, flush=True)

    # Check cache first
    cached_value: CacheValue | None = None
    if cache is not None and cache_key is not None:
        # Print cache key components
        dir_hash = compute_directory_hash(project_root)
        prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
        print(
            f"Cache lookup - filesystem MD5: {dir_hash}, prompt MD5: {prompt_hash}", file=sys.stderr
        )
        cached_value = cache.get(cache_key)

    if cached_value is not None:
        # Cache hit - replay events and restore .devcontainer
        print(f"CACHE HIT: Replaying cached agent output from {sqlite_cache_dir}", file=sys.stderr)
        for event in cached_value.events:
            if event.stream == "stdout":
                process_stdout_line(event.line)
            else:
                process_stderr_line(event.line)
        extract_devcontainer_tarball(cached_value.devcontainer_tarball, project_root)
        exit_code = cached_value.return_code
    else:
        # Cache miss - run agent
        if cache is not None:
            print(f"CACHE MISS: Running agent (cache: {sqlite_cache_dir})", file=sys.stderr)
        else:
            print(f"Starting agent with command: {agent_cmd}", file=sys.stderr)

        event_collector: EventCollector | None = None
        if cache is not None:
            event_collector = EventCollector()

        # Select runner based on --agent_in_modal flag
        if agent_in_modal:
            from bootstrap_devcontainer.modal_runner import ModalAgentRunner

            runner = ModalAgentRunner()
        else:
            runner = LocalAgentRunner()

        try:
            assert agent_cmd is not None
            assert max_budget_usd is not None

            for event in runner.run(prompt, project_root, max_budget_usd, agent_cmd):
                if event_collector is not None:
                    event_collector.add(event.stream, event.line)
                if event.stream == "stdout":
                    process_stdout_line(event.line)
                else:
                    process_stderr_line(event.line)

            exit_code = runner.exit_code
        except Exception as e:
            print(f"Error running agent: {e}", file=sys.stderr)
            exit_code = 1

        # Store in cache if caching is enabled
        if cache is not None and cache_key is not None and event_collector is not None:
            tarball = runner.get_devcontainer_tarball()
            cache_value = CacheValue(
                events=event_collector.get_events(),
                devcontainer_tarball=tarball,
                return_code=exit_code,
            )
            cache.set(cache_key, cache_value)
            cache.close()

    agent_work_seconds = time.time() - start_time

    # Verification step
    print("Verifying agent's work...", file=sys.stderr)
    verification_success = False
    verification_start_time = time.time()
    try:
        image_name = f"bootstrap-test-{project_root.name.lower()}"

        # 1. Build the image
        build_cmd = [
            "devcontainer",
            "build",
            "--workspace-folder",
            str(project_root),
            "--image-name",
            image_name,
        ]
        print(f"Building image: {shlex.join(build_cmd)}", file=sys.stderr)
        build_proc = subprocess.run(build_cmd, capture_output=True, text=True)

        if build_proc.returncode == 0:
            # 2. Run tests (no --rm so we can extract artifacts)
            container_name = f"bootstrap-test-{project_root.name.lower()}-container"
            test_cmd = [
                "docker",
                "run",
                "--name",
                container_name,
                image_name,
                "./.devcontainer/run_all_tests.sh",
            ]
            print(f"Running tests: {shlex.join(test_cmd)}", file=sys.stderr)
            test_run = subprocess.run(test_cmd, capture_output=True, text=True)

            # 3. Extract artifacts from container if test_artifacts_dir is provided
            if test_artifacts_dir is not None:
                cp_cmd = [
                    "docker",
                    "cp",
                    f"{container_name}:/test_artifacts/.",
                    str(test_artifacts_dir),
                ]
                print(f"Extracting artifacts: {shlex.join(cp_cmd)}", file=sys.stderr)
                cp_result = subprocess.run(cp_cmd, capture_output=True, text=True)
                if cp_result.returncode != 0:
                    print(
                        f"Warning: artifact extraction failed: {cp_result.stderr}", file=sys.stderr
                    )

            # 4. Clean up container
            subprocess.run(["docker", "rm", container_name], capture_output=True)

            if test_run.returncode == 0:
                print("Verification successful!", file=sys.stderr)
                verification_success = True
            else:
                print(
                    f"Test run failed with return code {test_run.returncode}",
                    file=sys.stderr,
                )
                print(f"STDOUT: {test_run.stdout}", file=sys.stderr)
                print(f"STDERR: {test_run.stderr}", file=sys.stderr)
        else:
            print("Build failed", file=sys.stderr)
            print(f"STDOUT: {build_proc.stdout}", file=sys.stderr)
            print(f"STDERR: {build_proc.stderr}", file=sys.stderr)
    except Exception as e:
        print(f"Verification error: {e}", file=sys.stderr)

    verification_seconds = time.time() - verification_start_time

    # Parse test reports from various formats
    test_reports = TestReports()
    if test_artifacts_dir is not None:
        test_reports = parse_test_reports(test_artifacts_dir)

    output = BootstrapResult(
        success=verification_success and exit_code == 0,
        agent_work_seconds=agent_work_seconds,
        verification_seconds=verification_seconds,
        model=model_name,
        token_spending=TokenSpending(**token_spending),
        cost_usd=total_cost_usd,
        agent_exit_code=exit_code,
        pytest_summary=test_reports.pytest_summary,
        go_test_summary=test_reports.go_test_summary,
        node_test_summary=test_reports.node_test_summary,
        cargo_test_summary=test_reports.cargo_test_summary,
    )

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(output.model_dump_json(indent=2))
        print(f"Result written to {output_file}", file=sys.stderr)
    else:
        print(output.model_dump_json(indent=2))


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
