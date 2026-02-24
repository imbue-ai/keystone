"""Main user entry point for the Keystone CLI."""

import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import typer
from rich.console import Console

from keystone.agent_log import (
    AgentLog,
    CLIRunRecord,
    compute_cache_key,
)
from keystone.agent_runner import LocalAgentRunner
from keystone.cached_runner import CachedAgentRunner, CacheMissError
from keystone.constants import (
    ANSI_BLUE,
    ANSI_RED,
    ANSI_RESET,
    STATUS_MARKER,
    SUMMARY_MARKER,
)
from keystone.git_utils import (
    create_git_archive_bytes,
    get_git_tree_hash,
    is_git_dirty,
    is_git_repo,
)
from keystone.junit_report_parser import parse_junit_xml
from keystone.llm_provider import (
    AgentCostEvent,
    AgentErrorEvent,
    AgentTextEvent,
    AgentToolCallEvent,
    AgentToolResultEvent,
    get_provider,
)
from keystone.logging_utils import ISOFormatter
from keystone.modal.modal_runner import ModalAgentRunner
from keystone.prompts import build_agent_prompt
from keystone.schema import (
    AgentConfig,
    AgentExecution,
    AgentStatusMessage,
    BootstrapResult,
    GeneratedFiles,
    InferenceCost,
    LLMModel,
    TokenSpending,
    VerificationResult,
)
from keystone.version import get_version_info

# Configure logging with standard format including timestamp, thread, and source location
_handler = logging.StreamHandler()
_handler.setFormatter(
    ISOFormatter("%(asctime)s %(thread)d %(name)s %(filename)s:%(lineno)d %(message)s")
)
logging.root.addHandler(_handler)
logging.root.setLevel(logging.INFO)
# Enable DEBUG for our own modules
logging.getLogger("keystone").setLevel(logging.DEBUG)
# Suppress noisy third-party loggers (hpack, httpcore, etc. from Modal's HTTP stack)
for _noisy_logger in ("hpack", "httpcore", "httpx", "grpc", "h2"):
    logging.getLogger(_noisy_logger).setLevel(logging.WARNING)

app = typer.Typer()
console = Console(stderr=True, force_terminal=True)


@app.command()
def bootstrap(
    project_root: Path | None = typer.Option(
        ..., "--project_root", help="Path to the source project"
    ),
    test_artifacts_dir: Path = typer.Option(
        ..., "--test_artifacts_dir", help="Directory for test artifacts"
    ),
    agent_cmd: str | None = typer.Option(
        None, "--agent_cmd", help="Agent command to run (default: inferred from --provider)"
    ),
    provider_name: str = typer.Option(
        "claude",
        "--provider",
        help="LLM provider name (e.g. 'claude'). See keystone.llm_provider.PROVIDER_REGISTRY.",
    ),
    model: LLMModel | None = typer.Option(
        None,
        "--model",
        help="LLM model to use (e.g. claude-opus-4-6, gpt-5.2-codex)",
    ),
    max_budget_usd: float | None = typer.Option(
        1.0, "--max_budget_usd", help="Maximum dollar amount to spend on agent inference"
    ),
    log_db: str | None = typer.Option(
        None,
        "--log_db",
        help="Database for logging/caching. SQLite path or postgresql:// URL (default: ~/.imbue_keystone/log.sqlite)",
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
        help="String appended to cache key.  Bumping this invalidates the cache, forcing fresh runs.",
    ),
    output_file: Path | None = typer.Option(
        None, "--output_file", help="Path to write JSON result (defaults to stdout)"
    ),
    agent_in_modal: bool = typer.Option(
        True,
        "--agent_in_modal/--run_agent_locally_with_dangerously_skip_permissions",
        help="Run agent in Modal sandbox (default) or locally",
    ),
    agent_time_limit_seconds: int = typer.Option(
        60 * 60,
        "--agent_time_limit_seconds",
        help="Maximum seconds for agent execution (uses timeout command)",
    ),
    image_build_timeout_seconds: int = typer.Option(
        30 * 60,
        "--image_build_timeout_seconds",
        help="Maximum seconds for building the devcontainer image",
    ),
    test_timeout_seconds: int = typer.Option(
        30 * 60,
        "--test_timeout_seconds",
        help="Maximum seconds for running tests",
    ),
    docker_cache_secret: str | None = typer.Option(
        None,
        "--docker_cache_secret",
        help=(
            "Name of a Modal secret containing DOCKER_BUILD_CACHE_REGISTRY_URL, "
            "DOCKER_BUILD_CACHE_REGISTRY_USERNAME, and DOCKER_BUILD_CACHE_REGISTRY_PASSWORD. "
            "Enables registry-based Docker build caching when running in Modal."
        ),
    ),
    llm_api_secret: str | None = typer.Option(
        None,
        "--llm_api_secret",
        help=(
            "Name of a Modal secret containing the LLM provider's API key "
            "(e.g. OPENAI_API_KEY or ANTHROPIC_API_KEY). "
            "Used when the host environment does not have the key set."
        ),
    ),
) -> None:
    """Bootstrap a devcontainer for a project."""
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

    start_time = time.monotonic()
    start_datetime = datetime.now(UTC)

    # Set up runner based on --agent_in_modal flag
    if agent_in_modal:
        if docker_cache_secret:
            console.print(
                f"[blue]Docker build cache:[/blue] using Modal secret '{docker_cache_secret}'"
            )
        else:
            console.print("[dim]Docker build cache: not configured[/dim]")

        inner_runner = ModalAgentRunner(
            docker_cache_secret=docker_cache_secret,
            llm_api_secret=llm_api_secret,
        )
    else:
        if docker_cache_secret:
            console.print(
                "[yellow]Warning:[/yellow] --docker_cache_secret is ignored when running locally"
            )
        inner_runner = LocalAgentRunner()

    # Instantiate the LLM provider
    provider = get_provider(provider_name, model=model.value if model else None)
    effective_agent_cmd = agent_cmd if agent_cmd is not None else provider.default_cmd

    token_spending = TokenSpending()
    total_cost_usd = 0.0
    model_name = ""
    exit_code = 1
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
        """Check for status/summary markers in text and print in blue if found."""
        nonlocal agent_summary, status_messages
        found = False
        for line in text.split("\n"):
            if STATUS_MARKER in line:
                idx = line.find(STATUS_MARKER)
                status_msg = line[idx:].strip()
                msg_content = status_msg[len(STATUS_MARKER) :].strip()
                status_messages.append(_make_status_message(msg_content))
                logging.debug(f"Found status marker, printing: {status_msg}")
                print(f"{ANSI_BLUE}{status_msg}{ANSI_RESET}", flush=True)
                found = True
            elif SUMMARY_MARKER in line:
                idx = line.find(SUMMARY_MARKER)
                full_marker = line[idx:].strip()
                msg_content = full_marker[len(SUMMARY_MARKER) :].strip()
                agent_summary = _make_status_message(msg_content)
                print(f"{ANSI_BLUE}{full_marker}{ANSI_RESET}", flush=True)
                found = True
        return found

    def process_stdout_line(line: str) -> None:
        """Process a line of agent stdout via the LLM provider's event parser."""
        nonlocal total_cost_usd, model_name
        for event in provider.parse_stdout_line(line):
            match event:
                case AgentTextEvent(text=text):
                    if not check_and_print_status(text):
                        logging.info(f"Assistant: {text}")
                case AgentToolCallEvent(name=name, input=input_data):
                    logging.info(f"Tool Call: {name}({input_data})")
                case AgentToolResultEvent(tool_name=name, output=output):
                    logging.info(f"Tool Result: {name} -> {output[:200]}")
                case AgentCostEvent() as cost:
                    if cost.cost_usd is not None:
                        total_cost_usd = cost.cost_usd
                    if cost.model is not None:
                        model_name = cost.model
                    token_spending.input += cost.input_tokens
                    token_spending.cached += cost.cached_tokens
                    token_spending.output += cost.output_tokens
                    token_spending.cache_creation += cost.cache_creation_tokens
                case AgentErrorEvent(message=msg):
                    logging.error(f"Agent error: {msg}")

    def process_stderr_line(line: str) -> None:
        """Forward agent stderr to our stderr."""
        print(f"Agent stderr: {line}", file=sys.stderr, flush=True)

    try:
        # Set up logging/caching
        effective_log_db = log_db or str(Path.home() / ".imbue_keystone" / "log.sqlite")
        agent_log = AgentLog(effective_log_db)
        cli_run_id = agent_log.generate_run_id()

        # Build agent config (part of cache key — only behavioral params)
        assert max_budget_usd is not None
        agent_config = AgentConfig(
            agent_cmd=effective_agent_cmd,
            max_budget_usd=max_budget_usd,
            agent_time_limit_seconds=agent_time_limit_seconds,
            agent_in_modal=agent_in_modal,
            model=model,
        )

        # Compute cache key
        cache_key = compute_cache_key(prompt, project_root, agent_config, cache_version)
        print(
            f"Cache key - git tree: {cache_key.git_tree_hash}, "
            f"prompt MD5: {cache_key.prompt_hash}, "
            f"config: {agent_config.to_cache_key_json()[:50]}..., "
            f"version: {cache_version or '(none)'}",
            file=sys.stderr,
        )

        # Wrap the runner with caching (cache hit/miss is transparent)
        runner = CachedAgentRunner(
            inner=inner_runner,
            agent_log=agent_log,
            cache_key=cache_key,
            cli_run_id=cli_run_id,
            project_root=project_root,
            no_cache_replay=no_cache_replay,
            require_cache_hit=require_cache_hit,
        )

        # Create project archive once - used for both agent run and verification
        project_archive = create_git_archive_bytes(project_root)

        # Run agent (or replay from cache — the caller doesn't need to know)
        try:
            for event in runner.run(
                prompt,
                project_archive,
                max_budget_usd,
                effective_agent_cmd,
                agent_time_limit_seconds,
                provider,
            ):
                if event.stream == "stdout":
                    process_stdout_line(event.line)
                else:
                    process_stderr_line(event.line)
        except CacheMissError as err:
            console.print("[red]Error:[/red] --require_cache_hit specified but cache miss")
            raise typer.Exit(1) from err

        exit_code = runner.exit_code
        logging.info(f"Agent exited with code {exit_code}")
        cache_hit = runner.cache_hit
        agent_timed_out = runner.timed_out
        devcontainer_tarball = runner.get_devcontainer_tarball()

        agent_work_seconds = time.monotonic() - start_time

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
                image_build_timeout_seconds,
                test_timeout_seconds,
            )
            verification_success = verify_result.success
            verification_error = verify_result.error_message
            image_build_seconds = verify_result.image_build_seconds
            test_execution_seconds = verify_result.test_execution_seconds

            # Print verification errors for debugging
            if not verification_success and verification_error:
                print("=" * 60, file=sys.stderr)
                print("VERIFICATION FAILED:", file=sys.stderr)
                print("=" * 60, file=sys.stderr)
                print(verification_error, file=sys.stderr)
                print("=" * 60, file=sys.stderr)
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

    # Verification passing is the source of truth for success — the agent may
    # exit non-zero (e.g. timeout code 124) yet still produce correct output.
    overall_success = verification_success
    error_message: str | None = None
    if not overall_success:
        if verification_error:
            error_message = verification_error
        elif exit_code != 0:
            error_message = f"Agent exited with code {exit_code}"
    elif exit_code != 0:
        logging.warning(f"Agent exited with code {exit_code} but verification passed")

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
                token_spending=token_spending,
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
        logging.error(f"Keystone failed: {error_message or 'unknown error'}")
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
    logging.info("Starting keystone CLI, version: %s", get_version_info())
    app()


if __name__ == "__main__":
    main()
