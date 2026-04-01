"""Main user entry point for the Keystone CLI."""

import logging
import os
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
    extract_devcontainer_tarball,
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
from keystone.llm_provider.pricing import estimate_cost_usd
from keystone.logging_utils import ISOFormatter
from keystone.modal.modal_runner import ModalAgentRunner
from keystone.prompts import build_prompt
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
        "2026-02-26_a",
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
    docker_registry_mirror: str | None = typer.Option(
        os.environ.get("DOCKER_REGISTRY_MIRROR"),
        "--docker_registry_mirror",
        help=(
            "URL of a Docker Hub pull-through cache mirror.  The Docker daemon is configured "
            "to check this mirror first for all image pulls, avoiding Docker Hub rate limits.  "
            "Reads from DOCKER_REGISTRY_MIRROR env var if not passed explicitly.  "
            "Pass --docker_registry_mirror='' to disable."
        ),
    ),
    guardrail: bool = typer.Option(
        True,
        "--guardrail/--no_guardrail",
        help="Enable or disable guardrail structural checks.",
    ),
    use_agents_md: bool = typer.Option(
        False,
        "--use_agents_md/--no_use_agents_md",
        help="Use AGENTS.md file + short CLI prompt instead of full inline prompt.",
    ),
    claude_reasoning_level: str | None = typer.Option(
        None,
        "--claude_reasoning_level",
        help="Reasoning level for Claude provider (e.g. 'low', 'medium', 'high'). Required when provider is 'claude'.",
    ),
    codex_reasoning_level: str | None = typer.Option(
        None,
        "--codex_reasoning_level",
        help="Reasoning level for Codex provider (e.g. 'low', 'medium', 'high'). Required when provider is 'codex'.",
    ),
    broken_commit_hashes: str | None = typer.Option(
        None,
        "--broken_commit_hashes",
        help="Comma-separated broken commit hashes for mutation-augmented eval re-verification.",
    ),
    cost_poll_interval_seconds: int = typer.Option(
        30,
        "--cost_poll_interval_seconds",
        help="How often (seconds) to poll ccusage and enforce --max_budget_usd. 0 disables.",
    ),
) -> None:
    """Bootstrap a devcontainer for a project."""
    logging.info(
        f"Starting keystone CLI, version: {Path.cwd()=}, {project_root=}, {test_artifacts_dir=}, {agent_cmd=}, {provider_name=}, {model=}, {max_budget_usd=}, {log_db=}, {require_cache_hit=}, {no_cache_replay=}, {cache_version=}, {output_file=}, {agent_in_modal=}, {agent_time_limit_seconds=}, {image_build_timeout_seconds=}, {test_timeout_seconds=}, {docker_registry_mirror=}, {guardrail=}, {get_version_info()=}"
    )
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

    # Validate --model and reasoning flags only when using a built-in provider
    # (i.e. no custom --agent_cmd).  Custom agent commands don't use these.
    if agent_cmd is None:
        if model is None:
            console.print("[bold red]Error:[/bold red] --model is required.")
            raise typer.Exit(code=1)

        # Validate reasoning level flags match the active provider.
        # The appropriate flag must be set, and the *other* provider's flag must NOT be set.
        _reasoning_errors: list[str] = []
        if provider_name == "claude":
            if claude_reasoning_level is None:
                _reasoning_errors.append(
                    "--claude_reasoning_level is required when provider is 'claude'."
                )
            if codex_reasoning_level is not None:
                _reasoning_errors.append(
                    "--codex_reasoning_level must not be set when provider is 'claude'."
                )
        elif provider_name == "codex":
            if codex_reasoning_level is None:
                _reasoning_errors.append(
                    "--codex_reasoning_level is required when provider is 'codex'."
                )
            if claude_reasoning_level is not None:
                _reasoning_errors.append(
                    "--claude_reasoning_level must not be set when provider is 'codex'."
                )
        else:
            # Other providers (e.g. opencode) don't support reasoning level flags.
            if claude_reasoning_level is not None:
                _reasoning_errors.append(
                    f"--claude_reasoning_level must not be set when provider is '{provider_name}'."
                )
            if codex_reasoning_level is not None:
                _reasoning_errors.append(
                    f"--codex_reasoning_level must not be set when provider is '{provider_name}'."
                )
        if _reasoning_errors:
            for err in _reasoning_errors:
                console.print(f"[bold red]Error:[/bold red] {err}")
            raise typer.Exit(code=1)

    # OpenCode doesn't write transcript files that ccusage can read, so
    # the cost-limit monitor cannot enforce a budget.  Reject early rather
    # than silently ignoring the flag.
    if provider_name == "opencode" and max_budget_usd and max_budget_usd > 0:
        console.print(
            "[bold red]Error:[/bold red] --max_budget_usd is not supported with the "
            "opencode provider. OpenCode does not write transcript files that ccusage "
            "can read, so cost-limit enforcement cannot work."
        )
        raise typer.Exit(code=1)

    # Build agent config early — needed for prompt generation and cache key.
    assert max_budget_usd is not None
    agent_config = AgentConfig(
        max_budget_usd=max_budget_usd,
        agent_time_limit_seconds=agent_time_limit_seconds,
        agent_in_modal=agent_in_modal,
        provider=provider_name,
        model=model,
        agent_cmd=agent_cmd,  # None means infer from provider.default_cmd at run time
        claude_reasoning_level=claude_reasoning_level,
        codex_reasoning_level=codex_reasoning_level,
        guardrail=guardrail,
        use_agents_md=use_agents_md,
        cost_poll_interval_seconds=cost_poll_interval_seconds,
    )

    prompt_result = build_prompt(agent_config)

    start_time = time.monotonic()
    start_datetime = datetime.now(UTC)

    # Set up runner based on --agent_in_modal flag
    if agent_in_modal:
        if docker_registry_mirror:
            console.print(f"[blue]Docker Hub mirror:[/blue] {docker_registry_mirror}")
        else:
            console.print(
                "[bold red]Error:[/bold red] No Docker registry mirror configured. "
                "Set the DOCKER_REGISTRY_MIRROR environment variable "
                "(e.g. export DOCKER_REGISTRY_MIRROR=https://mirror.gcr.io) "
                "or pass --docker_registry_mirror explicitly."
            )
            raise typer.Exit(code=1)

        inner_runner = ModalAgentRunner(
            agent_time_limit_seconds=agent_time_limit_seconds,
            docker_registry_mirror=docker_registry_mirror or None,
        )
    else:
        if docker_registry_mirror:
            console.print(
                "[yellow]Warning:[/yellow] --docker_registry_mirror is ignored when running locally"
            )
        inner_runner = LocalAgentRunner()

    # Instantiate the LLM provider
    provider = get_provider(agent_config)

    exit_code = 1
    verification_success = False
    agent_summary: AgentStatusMessage | None = None
    status_messages: list[AgentStatusMessage] = []
    agent_errors: list[str] = []
    agent_stderr_lines: list[str] = []
    # Accumulate token deltas from AgentCostEvent for fallback cost estimation
    _total_input_tokens = 0
    _total_output_tokens = 0
    _total_cached_tokens = 0
    _total_cache_creation_tokens = 0
    _total_cost_usd_from_events = 0.0  # Sum of per-event cost_usd (if provider reports it)

    def _make_status_message(message: str) -> AgentStatusMessage:
        """Create an AgentStatusMessage with current timestamp."""
        return AgentStatusMessage(
            timestamp=datetime.now(UTC).isoformat(),
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
        nonlocal \
            _total_input_tokens, \
            _total_output_tokens, \
            _total_cached_tokens, \
            _total_cache_creation_tokens, \
            _total_cost_usd_from_events
        for event in provider.parse_stdout_line(line):
            match event:
                case AgentTextEvent(text=text):
                    if not check_and_print_status(text):
                        logging.info(f"Assistant: {text}")
                case AgentToolCallEvent(name=name, input=input_data):
                    logging.info(f"Tool Call: {name}({input_data})")
                case AgentToolResultEvent(tool_name=name, output=output):
                    logging.info(f"Tool Result: {name} -> {output[:200]}")
                case AgentCostEvent() as cost_event:
                    # Accumulate token deltas as fallback when ccusage has no data
                    _total_input_tokens += cost_event.input_tokens
                    _total_output_tokens += cost_event.output_tokens
                    _total_cached_tokens += cost_event.cached_tokens
                    _total_cache_creation_tokens += cost_event.cache_creation_tokens
                    if cost_event.cost_usd is not None:
                        _total_cost_usd_from_events += cost_event.cost_usd
                case AgentErrorEvent(message=msg):
                    logging.error(f"Agent error: {msg}")
                    agent_errors.append(msg)

    def process_stderr_line(line: str) -> None:
        """Forward agent stderr to our stderr and capture for error reporting."""
        print(f"Agent stderr: {line}", file=sys.stderr, flush=True)
        agent_stderr_lines.append(line)

    try:
        # Set up logging/caching
        effective_log_db = log_db or str(Path.home() / ".imbue_keystone" / "log.sqlite")
        agent_log = AgentLog(effective_log_db)
        cli_run_id = agent_log.generate_run_id()

        # Compute cache key
        cache_key = compute_cache_key(
            prompt_result.cli_prompt, project_root, agent_config, cache_version
        )
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
                prompt_result.cli_prompt,
                project_archive,
                agent_config,
                provider,
                agents_md=prompt_result.agents_md,
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
        if runner.cost_limit_exceeded:
            logging.warning("Agent terminated: cost limit ($%.2f) exceeded", max_budget_usd)
        devcontainer_tarball = runner.get_devcontainer_tarball()
        logging.info(f"Devcontainer tarball size: {len(devcontainer_tarball)} bytes")

        # Extract .devcontainer to project_root so files are available on the host
        # (for generated_files in the result, and for callers that inspect project_root).
        extract_devcontainer_tarball(devcontainer_tarball, project_root)

        # Write agent state directory tarball (e.g. ~/.claude, ~/.codex) to disk if available
        agent_dir_tarball = runner.get_agent_dir_tarball()
        if agent_dir_tarball and output_file:
            agent_dir_path = output_file.parent / "agent_dir.tar.gz"
            agent_dir_path.write_bytes(agent_dir_tarball)
            logging.info(
                f"Agent dir tarball ({len(agent_dir_tarball)} bytes) written to {agent_dir_path}"
            )

        # Get inference cost via ccusage (Modal only; returns None for local runs)
        inference_cost = runner.get_inference_cost(provider_name) or InferenceCost()

        # Fallback: if ccusage returned no data but we have cost/token info from
        # the provider's streaming events, use that instead.
        if (
            inference_cost.cost_usd == 0.0
            and inference_cost.ccusage_raw is None
            and (
                _total_cost_usd_from_events > 0
                or (_total_input_tokens + _total_output_tokens + _total_cached_tokens) > 0
            )
        ):
            # Prefer provider-reported cost; fall back to token-based estimate
            if _total_cost_usd_from_events > 0:
                final_cost = _total_cost_usd_from_events
            else:
                model_str = model.value if model else None
                if model_str and "/" in model_str:
                    model_str = model_str.split("/", 1)[1]
                final_cost = estimate_cost_usd(
                    input_tokens=_total_input_tokens,
                    cached_tokens=_total_cached_tokens,
                    output_tokens=_total_output_tokens,
                    cache_creation_tokens=_total_cache_creation_tokens,
                    model=model_str,
                )
            inference_cost = InferenceCost(
                cost_usd=final_cost,
                token_spending=TokenSpending(
                    input=_total_input_tokens,
                    cached=_total_cached_tokens,
                    output=_total_output_tokens,
                    cache_creation=_total_cache_creation_tokens,
                ),
            )
            logging.info(
                "ccusage had no data; using streaming event cost: "
                "$%.4f (input=%d cached=%d output=%d cache_creation=%d)",
                final_cost,
                _total_input_tokens,
                _total_cached_tokens,
                _total_output_tokens,
                _total_cache_creation_tokens,
            )

        agent_work_seconds = time.monotonic() - start_time

        # Verification step
        logging.info(f"{ANSI_BLUE}Verifying agent's work...{ANSI_RESET}")

        dockerfile_path = project_root / ".devcontainer" / "Dockerfile"
        test_script_path = project_root / ".devcontainer" / "run_all_tests.sh"

        def _print_devcontainer_files() -> None:
            """Print Dockerfile and test script for visibility."""
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

        _print_devcontainer_files()

        verification_error: str | None = None
        image_build_seconds: float | None = None
        test_execution_seconds: float | None = None

        def _run_verification(tarball: bytes) -> VerificationResult:
            return runner.verify(
                project_archive,
                tarball,
                test_artifacts_dir,
                image_build_timeout_seconds,
                test_timeout_seconds,
            )

        try:
            verify_result = _run_verification(devcontainer_tarball)
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
        if not xml_file.is_file():
            print(f"Skipping non-file: {xml_file}", file=sys.stderr)
            continue
        try:
            test_results.extend(parse_junit_xml(xml_file))
        except Exception as e:
            print(
                f"Warning: failed to parse JUnit XML {xml_file.name}: {e}",
                file=sys.stderr,
            )

    # Build verification result
    n_passed = sum(1 for t in test_results if t.passed and not t.skipped)
    n_failed = sum(1 for t in test_results if not t.passed and not t.skipped)
    n_skipped = sum(1 for t in test_results if t.skipped)

    if test_results:
        console.print(
            f"[bold]Test summary:[/bold] "
            f"[green]{n_passed} passed[/green], "
            f"[red]{n_failed} failed[/red], "
            f"[yellow]{n_skipped} skipped[/yellow] "
            f"({len(test_results)} total)"
        )
    verification = VerificationResult(
        success=verification_success,
        error_message=verification_error,
        image_build_seconds=image_build_seconds,
        test_execution_seconds=test_execution_seconds,
        tests_passed=n_passed,
        tests_failed=n_failed,
        tests_skipped=n_skipped,
        test_results=test_results,
    )

    # Verification passing is the source of truth for success — the agent may
    # exit non-zero (e.g. timeout code 124) yet still produce correct output.
    overall_success = verification_success
    error_message: str | None = None

    # Build agent error context from structured errors and stderr (if agent failed
    # and wasn't just a timeout, since timeouts are already reported separately).
    agent_error_context: str | None = None
    if exit_code != 0 and not agent_timed_out:
        parts: list[str] = []
        if agent_errors:
            parts.append("Agent errors: " + "; ".join(agent_errors))
        if agent_stderr_lines:
            # Include last 20 lines of stderr for context
            tail = agent_stderr_lines[-20:]
            parts.append("Agent stderr (last lines):\n" + "\n".join(tail))
        if parts:
            agent_error_context = "\n".join(parts)

    if not overall_success:
        if verification_error and agent_error_context:
            error_message = f"{verification_error}\n\nRoot cause — {agent_error_context}"
        elif verification_error:
            error_message = verification_error
        elif agent_error_context:
            error_message = agent_error_context
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

    # Broken-commit re-verification (mutation-augmented eval)
    broken_commit_verifications: dict[str, VerificationResult] = {}
    post_broken_commits_verification: VerificationResult | None = None
    unexpected_broken_commit_passes = 0

    parsed_broken_hashes = (
        [h.strip() for h in broken_commit_hashes.split(",") if h.strip()]
        if broken_commit_hashes
        else []
    )
    # Access the inner runner for broken-commit verification
    inner_runner = getattr(runner, "_inner", runner)
    has_broken_method = hasattr(inner_runner, "run_broken_commit_verifications")
    if parsed_broken_hashes and overall_success and has_broken_method:
        logging.info(
            "Running broken-commit re-verification for %d commits...",
            len(parsed_broken_hashes),
        )
        try:
            # LocalAgentRunner needs project_root for git archive;
            # ModalAgentRunner uses the sandbox's /project repo.
            if isinstance(inner_runner, LocalAgentRunner):
                broken_commit_verifications, post_broken_commits_verification = (
                    inner_runner.run_broken_commit_verifications(
                        parsed_broken_hashes,
                        test_timeout_seconds,
                        project_root=project_root,
                    )
                )
            elif isinstance(inner_runner, ModalAgentRunner):
                broken_commit_verifications, post_broken_commits_verification = (
                    inner_runner.run_broken_commit_verifications(
                        parsed_broken_hashes,
                        test_timeout_seconds,
                    )
                )
            else:
                logging.warning(
                    "Broken-commit verification not supported for runner type: %s",
                    type(inner_runner).__name__,
                )
            unexpected_broken_commit_passes = sum(
                1 for v in broken_commit_verifications.values() if v.tests_failed == 0
            )
            if unexpected_broken_commit_passes > 0:
                logging.warning(
                    "⚠️  %d broken commit(s) unexpectedly passed all tests!",
                    unexpected_broken_commit_passes,
                )
        except Exception as e:
            logging.error("Broken-commit verification failed: %s", e)

    output = BootstrapResult(
        success=overall_success,
        error_message=error_message,
        cli_args=sys.argv,
        agent=AgentExecution(
            start_time=start_datetime.isoformat(),
            end_time=datetime.now(UTC).isoformat(),
            duration_seconds=agent_work_seconds,
            exit_code=exit_code,
            timed_out=agent_timed_out,
            cost_limit_exceeded=runner.cost_limit_exceeded,
            summary=agent_summary,
            status_messages=status_messages,
            error_messages=agent_errors,
            cost=inference_cost,
        ),
        verification=verification,
        generated_files=generated_files,
        broken_commit_verifications=broken_commit_verifications,
        post_broken_commits_verification=post_broken_commits_verification,
        unexpected_broken_commit_passes=unexpected_broken_commit_passes,
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
        devcontainer_dir = project_root / ".devcontainer"
        if devcontainer_dir.exists():
            logging.info(f"Failed devcontainer output preserved at: {devcontainer_dir}")
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
