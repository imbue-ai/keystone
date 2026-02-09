import json
import logging
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from bootstrap_devcontainer.agent_log import (
    AgentLog,
    AgentRunRecord,
    CLIRunRecord,
    StreamEvent,
    compute_cache_key,
    extract_devcontainer_tarball,
)
from bootstrap_devcontainer.agent_runner import TIMEOUT_EXIT_CODE, LocalAgentRunner
from bootstrap_devcontainer.constants import (
    ANSI_BLUE,
    ANSI_GREEN,
    ANSI_MAGENTA,
    ANSI_RED,
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
from bootstrap_devcontainer.report_parsers import parse_junit_xml
from bootstrap_devcontainer.schema import (
    AgentConfig,
    AgentExecution,
    AgentStatusMessage,
    BootstrapResult,
    GeneratedFiles,
    InferenceCost,
    TokenSpending,
    VerificationResult,
)
from bootstrap_devcontainer.version import get_version_info


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
    log_db: str | None = typer.Option(
        None,
        "--log_db",
        help="Database for logging/caching. SQLite path or postgresql:// URL (default: ~/.bootstrap_devcontainer/log.sqlite)",
    ),
    require_cache_hit: bool = typer.Option(
        False,
        "--require_cache_hit",
        help="Fail immediately if cache miss (useful for CI/testing)",
    ),
    no_cache_replay: bool = typer.Option(
        False,
        "--no_cache_replay",
        help="Skip cache lookup but still log the run (force fresh execution)",
    ),
    cache_version: str = typer.Option(
        "2026-02-09",
        "--cache_version",
        help="String appended to cache key to invalidate old entries",
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
    image_build_timeout_secs: int = typer.Option(
        30 * 60,
        "--image_build_timeout_secs",
        help="Maximum seconds for building the devcontainer image",
    ),
    test_timeout_secs: int = typer.Option(
        30 * 60,
        "--test_timeout_secs",
        help="Maximum seconds for running tests",
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
        # Set up logging/caching
        # Default to ~/.bootstrap_devcontainer/log.sqlite if not specified
        effective_log_db = log_db or str(Path.home() / ".bootstrap_devcontainer" / "log.sqlite")

        agent_log = AgentLog(effective_log_db)
        cli_run_id = agent_log.generate_run_id()

        # Build agent config (part of cache key)
        assert agent_cmd is not None
        assert max_budget_usd is not None
        agent_config = AgentConfig(
            agent_cmd=agent_cmd,
            max_budget_usd=max_budget_usd,
            agent_time_limit_secs=agent_time_limit_secs,
            agent_in_modal=agent_in_modal,
        )

        # Compute cache key
        cache_key = compute_cache_key(prompt, project_root, agent_config, cache_version)

        # Print cache key components for debugging
        print(
            f"Cache key - git tree: {cache_key.git_tree_hash}, "
            f"prompt MD5: {cache_key.prompt_hash}, "
            f"config: {agent_config.to_cache_key_json()[:50]}..., "
            f"version: {cache_version or '(none)'}",
            file=sys.stderr,
        )

        # Check cache (unless --no_cache_replay)
        cached_run: AgentRunRecord | None = None
        cache_hit = False
        if not no_cache_replay:
            cached_run = agent_log.lookup_cache(cache_key)
            if cached_run is not None:
                cache_hit = True

        # Handle --require_cache_hit
        if require_cache_hit and not cache_hit:
            console.print("[red]Error:[/red] --require_cache_hit specified but cache miss")
            raise typer.Exit(1)

        # Create project archive once - used for both agent run and verification
        project_archive = create_git_archive_bytes(project_root)

        devcontainer_tarball: bytes = b""
        collected_events: list[StreamEvent] = []

        if cache_hit and cached_run is not None:
            # Cache hit - replay events and restore .devcontainer
            print(
                f"{ANSI_GREEN}CACHE HIT: Replaying cached agent output from {effective_log_db}{ANSI_RESET}",
                file=sys.stderr,
            )
            print(
                f"  Cached return_code: {cached_run.return_code}, "
                f"events: {len(cached_run.events)}, "
                f"tarball size: {len(cached_run.devcontainer_tarball)} bytes",
                file=sys.stderr,
            )
            for event in cached_run.events:
                if event.stream == "stdout":
                    process_stdout_line(event.line)
                else:
                    process_stderr_line(event.line)
            extract_devcontainer_tarball(cached_run.devcontainer_tarball, project_root)
            exit_code = cached_run.return_code
            devcontainer_tarball = cached_run.devcontainer_tarball
        else:
            # Cache miss (or --no_cache_replay) - run agent
            if no_cache_replay:
                print(
                    f"{ANSI_MAGENTA}CACHE BYPASS (--no_cache_replay): Running agent{ANSI_RESET}",
                    file=sys.stderr,
                )
            else:
                print(
                    f"{ANSI_MAGENTA}CACHE MISS: Running agent (log: {effective_log_db}){ANSI_RESET}",
                    file=sys.stderr,
                )

            try:
                for event in runner.run(
                    prompt, project_archive, max_budget_usd, agent_cmd, agent_time_limit_secs
                ):
                    # Collect all events for logging
                    collected_events.append(StreamEvent(stream=event.stream, line=event.line))
                    if event.stream == "stdout":
                        process_stdout_line(event.line)
                    else:
                        process_stderr_line(event.line)

                exit_code = runner.exit_code
                if exit_code == TIMEOUT_EXIT_CODE:
                    agent_timed_out = True

                # Extract .devcontainer and log the run
                try:
                    devcontainer_tarball = runner.get_devcontainer_tarball()
                    extract_devcontainer_tarball(devcontainer_tarball, project_root)

                    # Extract ~/.claude tarball if available (Modal only)
                    claude_dir_tarball = runner.get_claude_dir_tarball()

                    # Log agent run (always, regardless of success - for analytics)
                    agent_run_record = AgentRunRecord(
                        cli_run_id=cli_run_id,
                        timestamp=datetime.now(UTC),
                        cache_key=cache_key,
                        events=collected_events,
                        devcontainer_tarball=devcontainer_tarball,
                        return_code=exit_code,
                        claude_dir_tarball=claude_dir_tarball,
                    )
                    agent_log.log_agent_run(agent_run_record)

                    if exit_code != 0:
                        print(
                            f"Agent failed (exit_code={exit_code}), logged but not cached for replay",
                            file=sys.stderr,
                        )
                except Exception as e:
                    print(f"Warning: could not extract/log .devcontainer: {e}", file=sys.stderr)
            except Exception as e:
                print(f"Error running agent: {e}", file=sys.stderr)
                exit_code = 1

        agent_work_seconds = time.time() - start_time

        # Verification step
        logging.info(f"{ANSI_BLUE}Verifying agent's work...{ANSI_RESET}")

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
            verify_result = runner.verify(
                project_archive,
                devcontainer_tarball,
                test_artifacts_dir,
                image_build_timeout_secs,
                test_timeout_secs,
            )
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

    # Parse all JUnit XML test reports from junit/ subdirectory
    test_results = []
    for xml_file in test_artifacts_dir.glob("junit/*.xml"):
        test_results.extend(parse_junit_xml(xml_file))

    # Build verification result
    verification = VerificationResult(
        success=verification_success,
        error_message=verification_error,
        image_build_seconds=image_build_seconds,
        test_execution_seconds=test_execution_seconds,
        test_results=test_results,
    )

    overall_success = verification_success and exit_code == 0
    error_message: str | None = None
    if not overall_success:
        if verification_error:
            error_message = verification_error
        elif exit_code != 0:
            error_message = f"Agent exited with code {exit_code}"

    # Read generated files for inclusion in result
    devcontainer_json_path = project_root / ".devcontainer" / "devcontainer.json"
    generated_files = GeneratedFiles(
        devcontainer_json=devcontainer_json_path.read_text()
        if devcontainer_json_path.exists()
        else None,
        dockerfile=dockerfile_path.read_text() if dockerfile_path.exists() else None,
        run_all_tests_sh=test_script_path.read_text() if test_script_path.exists() else None,
    )

    output = BootstrapResult(
        success=overall_success,
        error_message=error_message,
        agent=AgentExecution(
            start_time=start_datetime,
            end_time=datetime.now(UTC),
            duration_seconds=agent_work_seconds,
            exit_code=exit_code,
            timed_out=agent_timed_out,
            model=model_name,
            summary=agent_summary,
            status_messages=status_messages,
            cost=InferenceCost(
                cost_usd=total_cost_usd,
                token_spending=TokenSpending(**token_spending),
            ),
        ),
        verification=verification,
        generated_files=generated_files,
    )

    # Log CLI run (with result)
    cli_run_record = CLIRunRecord(
        id=cli_run_id,
        timestamp=start_datetime,
        cwd=str(Path.cwd()),
        args=sys.argv,
        cache_hit=cache_hit,
        bootstrap_result_json=output.model_dump_json(),
    )
    agent_log.log_cli_run(cli_run_record)
    agent_log.close()

    if output_file:
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(output.model_dump_json(indent=2))
        logging.info(f"Result written to {output_file}")
    else:
        print(output.model_dump_json(indent=2), flush=True)

    if not overall_success:
        raise typer.Exit(code=1)

    # Log the output location
    devcontainer_dir = project_root / ".devcontainer"
    if devcontainer_dir.exists():
        logging.info(f"{ANSI_BLUE}Wrote devcontainer to: {devcontainer_dir}{ANSI_RESET}")
    else:
        logging.error(
            f"{ANSI_RED}Devcontainer directory does not exist: {devcontainer_dir}{ANSI_RESET}"
        )
        raise typer.Exit(code=1)


def main():
    """Entry point for the CLI."""
    logging.info("Starting bootstrap_devcontainer CLI, version: %s", get_version_info())
    app()


if __name__ == "__main__":
    main()
