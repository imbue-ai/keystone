import hashlib
import json
import logging
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from bootstrap_devcontainer.agent_cache import (
    AgentCache,
    CacheValue,
    EventCollector,
    compute_cache_key,
    extract_devcontainer_tarball,
)
from bootstrap_devcontainer.agent_runner import TIMEOUT_EXIT_CODE, LocalAgentRunner
from bootstrap_devcontainer.constants import (
    ANSI_BLUE,
    ANSI_CYAN,
    ANSI_GREEN,
    ANSI_MAGENTA,
    ANSI_RESET,
    STATUS_MARKER,
    SUMMARY_MARKER,
)
from bootstrap_devcontainer.git_utils import (
    create_git_archive_bytes,
    get_git_tree_hash,
    is_git_dirty,
    is_git_repo,
)
from bootstrap_devcontainer.prompts import build_agent_prompt
from bootstrap_devcontainer.schema import (
    AgentStatusMessage,
    BootstrapResult,
    InferenceCost,
    TestSummary,
    TokenSpending,
    VerificationResult,
)


class ISOFormatter(logging.Formatter):
    """Log formatter with ISO 8601 timestamps including milliseconds and timezone."""

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:  # noqa: ARG002
        dt = datetime.fromtimestamp(record.created, tz=UTC).astimezone()
        return dt.strftime("%Y-%m-%dT%H:%M:%S.") + f"{int(record.msecs):03d}{dt.strftime('%z')}"


# Configure logging with standard format including timestamp, thread, and source location
_handler = logging.StreamHandler()
_handler.setFormatter(
    ISOFormatter("%(asctime)s %(thread)d %(name)s %(filename)s:%(lineno)d %(message)s")
)
logging.root.addHandler(_handler)
logging.root.setLevel(logging.INFO)
# Enable DEBUG for our own modules
logging.getLogger("bootstrap_devcontainer").setLevel(logging.DEBUG)

app = typer.Typer()
console = Console(stderr=True, force_terminal=True)


def check_docker_available() -> bool:
    """Check if Docker CLI is installed and daemon is running."""
    try:
        result = subprocess.run(
            ["docker", "ps"],
            capture_output=True,
            timeout=10,
            check=False,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


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


@app.command()
def bootstrap(
    project_root: Path | None = typer.Option(
        ..., "--project_root", help="Path to the source project"
    ),
    test_artifacts_dir: Path = typer.Option(
        ..., "--test_artifacts_dir", help="Directory for test artifacts"
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
    agent_time_limit_secs: int = typer.Option(
        3600,
        "--agent_time_limit_secs",
        help="Maximum seconds for agent execution (uses timeout command)",
    ),
):
    assert project_root is not None, "--project_root is required"
    project_root = project_root.resolve()

    # Verify project_root is a git repository
    if not is_git_repo(project_root):
        console.print(
            f"[red]Error:[/red] {project_root} is not a git repository.\n"
            "The project must be versioned with git. To initialize:\n"
            f"  cd {project_root}\n"
            "  git init && git add -A && git commit -m 'Initial commit'",
        )
        raise typer.Exit(1)

    # Warn if working directory is dirty
    if is_git_dirty(project_root):
        console.print(
            f"[yellow]Warning:[/yellow] {project_root} has uncommitted changes. "
            "Only committed files will be processed.",
        )

    # Log the git tree hash we're working from
    tree_hash = get_git_tree_hash(project_root)
    console.print(f"Working from git tree: [cyan]{tree_hash}[/cyan]")

    if test_artifacts_dir is not None:
        test_artifacts_dir = test_artifacts_dir.resolve()
        test_artifacts_dir.mkdir(parents=True, exist_ok=True)

    # Build prompt
    prompt = build_agent_prompt(agent_in_modal)

    start_time = time.time()
    start_datetime = datetime.now(UTC)

    # Set up runner based on --agent_in_modal flag
    if agent_in_modal:
        from bootstrap_devcontainer.modal.modal_runner import ModalAgentRunner

        runner = ModalAgentRunner()
    else:
        runner = LocalAgentRunner()

    token_spending = {"input": 0, "cached": 0, "output": 0, "cache_creation": 0}
    total_cost_usd = 0.0
    model_name = ""
    exit_code = 1
    agent_timed_out = False
    verification_success = False
    agent_summary: AgentStatusMessage | None = None
    status_messages: list[AgentStatusMessage] = []

    def _make_status_message(message: str) -> AgentStatusMessage:
        """Create an AgentStatusMessage with current timestamp."""
        return AgentStatusMessage(
            timestamp=datetime.now(UTC),
            message=message,
        )

    def check_and_print_status(text: str) -> bool:
        """Check for status/summary markers in text and print in blue if found.

        Returns True if a marker was found.
        """
        nonlocal agent_summary, status_messages
        found = False
        for line in text.split("\n"):
            if STATUS_MARKER in line:
                # Extract the status message after the marker
                idx = line.find(STATUS_MARKER)
                status_msg = line[idx:].strip()
                # Extract just the message part after the marker
                msg_content = status_msg[len(STATUS_MARKER) :].strip()
                status_messages.append(_make_status_message(msg_content))
                logging.debug(f"Found status marker, printing: {status_msg}")
                print(f"{ANSI_BLUE}{status_msg}{ANSI_RESET}", flush=True)
                found = True
            elif SUMMARY_MARKER in line:
                # Extract the summary message after the marker
                idx = line.find(SUMMARY_MARKER)
                full_marker = line[idx:].strip()
                # Extract just the message part after the marker
                msg_content = full_marker[len(SUMMARY_MARKER) :].strip()
                agent_summary = _make_status_message(msg_content)
                print(f"{ANSI_BLUE}{full_marker}{ANSI_RESET}", flush=True)
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
                            logging.info(f"Assistant: {txt}")
                    elif item.get("type") == "tool_use":
                        name = item.get("name")
                        input_data = item.get("input", {})
                        logging.info(f"Tool Call: {name}({input_data})")

            elif msg_type == "result":
                # total_cost_usd from API is already cumulative for the session
                total_cost_usd = data.get("total_cost_usd", 0.0)
                model_name = data.get("model", "")
                # usage tokens are per-turn, so accumulate them
                usage = data.get("usage", {})
                token_spending["input"] += usage.get("input_tokens", 0)
                token_spending["cached"] += usage.get("cache_read_input_tokens", 0)
                token_spending["output"] += usage.get("output_tokens", 0)
                token_spending["cache_creation"] += usage.get("cache_creation_input_tokens", 0)

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

    try:
        # Set up cache if requested
        cache: AgentCache | None = None
        cache_key: str | None = None
        if sqlite_cache_dir is not None:
            cache = AgentCache(sqlite_cache_dir)
            cache_key = compute_cache_key(prompt, project_root)

        # Check cache first
        cached_value: CacheValue | None = None
        if cache is not None and cache_key is not None:
            # Print cache key components
            tree_hash = get_git_tree_hash(project_root)
            prompt_hash = hashlib.md5(prompt.encode("utf-8")).hexdigest()
            print(
                f"Cache lookup - git tree: {tree_hash}, prompt MD5: {prompt_hash}",
                file=sys.stderr,
            )
            cached_value = cache.get(cache_key)

        # Create project archive once - used for both agent run and verification
        project_archive = create_git_archive_bytes(project_root)

        devcontainer_tarball: bytes = b""
        if cached_value is not None:
            # Cache hit - replay events and restore .devcontainer
            print(
                f"{ANSI_GREEN}CACHE HIT: Replaying cached agent output from {sqlite_cache_dir}{ANSI_RESET}",
                file=sys.stderr,
            )
            print(
                f"  Cached return_code: {cached_value.return_code}, "
                f"events: {len(cached_value.events)}, "
                f"tarball size: {len(cached_value.devcontainer_tarball)} bytes",
                file=sys.stderr,
            )
            for event in cached_value.events:
                if event.stream == "stdout":
                    process_stdout_line(event.line)
                else:
                    process_stderr_line(event.line)
            extract_devcontainer_tarball(cached_value.devcontainer_tarball, project_root)
            exit_code = cached_value.return_code
            devcontainer_tarball = cached_value.devcontainer_tarball
        else:
            # Cache miss - run agent
            if cache is not None:
                print(
                    f"{ANSI_MAGENTA}CACHE MISS: Running agent (cache: {sqlite_cache_dir}){ANSI_RESET}",
                    file=sys.stderr,
                )
            else:
                print(f"Starting agent with command: {agent_cmd}", file=sys.stderr)

            event_collector: EventCollector | None = None
            if cache is not None:
                event_collector = EventCollector()

            try:
                assert agent_cmd is not None
                assert max_budget_usd is not None

                for event in runner.run(
                    prompt, project_archive, max_budget_usd, agent_cmd, agent_time_limit_secs
                ):
                    if event_collector is not None:
                        event_collector.add(event.stream, event.line)
                    if event.stream == "stdout":
                        process_stdout_line(event.line)
                    else:
                        process_stderr_line(event.line)

                exit_code = runner.exit_code
                if exit_code == TIMEOUT_EXIT_CODE:
                    agent_timed_out = True

                # If the agent succeeded (or at least finished), extract the .devcontainer
                # so it's available for local use and for the next verification step
                try:
                    devcontainer_tarball = runner.get_devcontainer_tarball()
                    extract_devcontainer_tarball(devcontainer_tarball, project_root)

                    # Store in cache if caching is enabled and agent succeeded
                    if (
                        cache is not None
                        and cache_key is not None
                        and event_collector is not None
                        and exit_code == 0
                    ):
                        cache_value = CacheValue(
                            events=event_collector.get_events(),
                            devcontainer_tarball=devcontainer_tarball,
                            return_code=exit_code,
                        )
                        cache.set(cache_key, cache_value)
                        cache.close()
                    elif cache is not None and exit_code != 0:
                        print(
                            f"Skipping cache (agent failed with exit_code={exit_code})",
                            file=sys.stderr,
                        )
                except Exception as e:
                    print(f"Warning: could not extract/cache .devcontainer: {e}", file=sys.stderr)
            except Exception as e:
                print(f"Error running agent: {e}", file=sys.stderr)
                exit_code = 1

        agent_work_seconds = time.time() - start_time

        # Verification step
        logging.info(f"{ANSI_CYAN}Verifying agent's work...{ANSI_RESET}")

        # Print Dockerfile and test script for visibility
        dockerfile_path = project_root / ".devcontainer" / "Dockerfile"
        test_script_path = project_root / ".devcontainer" / "run_all_tests.sh"

        if dockerfile_path.exists():
            print("=" * 60, file=sys.stderr)
            print(f"Dockerfile: {dockerfile_path}", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print(dockerfile_path.read_text(), file=sys.stderr)

        if test_script_path.exists():
            print("=" * 60, file=sys.stderr)
            print(f"Test script: {test_script_path}", file=sys.stderr)
            print("=" * 60, file=sys.stderr)
            print(test_script_path.read_text(), file=sys.stderr)

        verification_error: str | None = None
        image_build_seconds: float | None = None
        test_execution_seconds: float | None = None
        try:
            verify_result = runner.verify(project_archive, devcontainer_tarball, test_artifacts_dir)
            verification_success = verify_result.success
            verification_error = verify_result.error_message
            image_build_seconds = verify_result.image_build_seconds
            test_execution_seconds = verify_result.test_execution_seconds
        except Exception as e:
            print(f"Verification error: {e}", file=sys.stderr)
            verification_success = False
            verification_error = str(e)

    finally:
        runner.cleanup()

    # Parse test reports from various formats
    test_reports = parse_test_reports(test_artifacts_dir)

    # Build verification result
    verification = VerificationResult(
        success=verification_success,
        error_message=verification_error,
        image_build_seconds=image_build_seconds,
        test_execution_seconds=test_execution_seconds,
        pytest_summary=test_reports.pytest_summary,
        go_test_summary=test_reports.go_test_summary,
        node_test_summary=test_reports.node_test_summary,
        cargo_test_summary=test_reports.cargo_test_summary,
    )

    overall_success = verification_success and exit_code == 0
    error_message: str | None = None
    if not overall_success:
        if verification_error:
            error_message = verification_error
        elif exit_code != 0:
            error_message = f"Agent exited with code {exit_code}"

    output = BootstrapResult(
        success=overall_success,
        error_message=error_message,
        agent_timed_out=agent_timed_out,
        start_time=start_datetime,
        end_time=datetime.now(UTC),
        agent_summary=agent_summary,
        status_messages=status_messages,
        agent_work_seconds=agent_work_seconds,
        model=model_name,
        cost=InferenceCost(
            cost_usd=total_cost_usd,
            token_spending=TokenSpending(**token_spending),
        ),
        agent_exit_code=exit_code,
        verification=verification,
    )

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(output.model_dump_json(indent=2))
        print(f"Result written to {output_file}", file=sys.stderr)
    else:
        print(output.model_dump_json(indent=2))

    if not overall_success:
        raise typer.Exit(code=1)


def main():
    """Entry point for the CLI."""
    app()


if __name__ == "__main__":
    main()
