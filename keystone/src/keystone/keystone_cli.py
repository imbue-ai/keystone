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
    create_devcontainer_tarball,
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
from keystone.evaluator import evaluate_agent_work, evaluate_and_fix, run_guardrail
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
from keystone.prompts import build_prompt
from keystone.schema import (
    AgentConfig,
    AgentExecution,
    AgentStatusMessage,
    BootstrapResult,
    EvaluatorResult,
    GeneratedFiles,
    InferenceCost,
    LLMModel,
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
    evaluator: bool = typer.Option(
        True,
        "--evaluator/--no_evaluator",
        help="Enable or disable the LLM evaluator (fix-up and passive check).",
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
) -> None:
    """Bootstrap a devcontainer for a project."""
    logging.info(
        f"Starting keystone CLI, version: {Path.cwd()=}, {project_root=}, {test_artifacts_dir=}, {agent_cmd=}, {provider_name=}, {model=}, {max_budget_usd=}, {log_db=}, {require_cache_hit=}, {no_cache_replay=}, {cache_version=}, {output_file=}, {agent_in_modal=}, {agent_time_limit_seconds=}, {image_build_timeout_seconds=}, {test_timeout_seconds=}, {docker_registry_mirror=}, {evaluator=}, {guardrail=}, {get_version_info()=}"
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

    # Build agent config early — needed for prompt generation and cache key.
    assert max_budget_usd is not None
    agent_config = AgentConfig(
        max_budget_usd=max_budget_usd,
        agent_time_limit_seconds=agent_time_limit_seconds,
        agent_in_modal=agent_in_modal,
        provider=provider_name,
        model=model,
        agent_cmd=agent_cmd,  # None means infer from provider.default_cmd at run time
        evaluator=evaluator,
        guardrail=guardrail,
        use_agents_md=use_agents_md,
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

        inner_runner = ModalAgentRunner(docker_registry_mirror=docker_registry_mirror or None)
    else:
        if docker_registry_mirror:
            console.print(
                "[yellow]Warning:[/yellow] --docker_registry_mirror is ignored when running locally"
            )
        inner_runner = LocalAgentRunner()

    # Instantiate the LLM provider
    provider = get_provider(provider_name, model=model.value if model else None)

    exit_code = 1
    verification_success = False
    evaluator_result: EvaluatorResult | None = None
    agent_summary: AgentStatusMessage | None = None
    status_messages: list[AgentStatusMessage] = []
    agent_errors: list[str] = []
    agent_stderr_lines: list[str] = []

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
        for event in provider.parse_stdout_line(line):
            match event:
                case AgentTextEvent(text=text):
                    if not check_and_print_status(text):
                        logging.info(f"Assistant: {text}")
                case AgentToolCallEvent(name=name, input=input_data):
                    logging.info(f"Tool Call: {name}({input_data})")
                case AgentToolResultEvent(tool_name=name, output=output):
                    logging.info(f"Tool Result: {name} -> {output[:200]}")
                case AgentCostEvent():
                    pass  # Cost is now computed post-hoc via ccusage
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

        effective_agent_cmd = agent_config.agent_cmd or provider.default_cmd

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
                max_budget_usd,
                effective_agent_cmd,
                agent_time_limit_seconds,
                provider,
                agents_md=prompt_result.agents_md,
                guardrail=guardrail,
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
        logging.info(f"Devcontainer tarball size: {len(devcontainer_tarball)} bytes")

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

        agent_work_seconds = time.monotonic() - start_time

        # Verification step (with evaluator fix-up loop on failure)
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

            # ---- Evaluator fix-up: attempt to repair on failure ----
            if not verification_success and verification_error and evaluator:
                devcontainer_dir = project_root / ".devcontainer"

                # Run guardrail and display output before evaluator LLM call
                if guardrail:
                    console.print("[yellow]Guardrail:[/yellow] Running structural checks...")
                    guardrail_output = run_guardrail(project_root)
                    console.print(guardrail_output)

                console.print(
                    "[yellow]Evaluator:[/yellow] Verification failed — "
                    "attempting LLM fix-up pass..."
                )

                evaluator_model = model.value if model else "claude-haiku-4-5-20251001"

                devcontainer_json_path = project_root / ".devcontainer" / "devcontainer.json"
                fix_files = {
                    "devcontainer_json": devcontainer_json_path.read_text()
                    if devcontainer_json_path.exists()
                    else None,
                    "dockerfile": dockerfile_path.read_text() if dockerfile_path.exists() else None,
                    "run_all_tests_sh": test_script_path.read_text()
                    if test_script_path.exists()
                    else None,
                }

                evaluator_result = evaluate_and_fix(
                    verification_error=verification_error,
                    generated_files=fix_files,
                    status_messages=[m.message for m in status_messages],
                    agent_summary=agent_summary.message if agent_summary else None,
                    devcontainer_dir=devcontainer_dir,
                    project_root=project_root,
                    model=evaluator_model,
                    guardrail=guardrail,
                )

                if evaluator_result.passed:
                    console.print(
                        f"[yellow]Evaluator:[/yellow] Wrote fixes — {evaluator_result.reasoning}"
                    )
                    _print_devcontainer_files()

                    # Rebuild tarball from fixed files and re-verify
                    fixed_tarball = create_devcontainer_tarball(project_root)
                    devcontainer_tarball = fixed_tarball

                    console.print(
                        "[yellow]Evaluator:[/yellow] Re-running verification with fixed files..."
                    )
                    verify_result_2 = _run_verification(fixed_tarball)
                    verification_success = verify_result_2.success
                    verification_error = verify_result_2.error_message
                    image_build_seconds = verify_result_2.image_build_seconds
                    test_execution_seconds = verify_result_2.test_execution_seconds

                    if verification_success:
                        console.print(
                            "[green]Evaluator:[/green] Fix-up succeeded! Verification now passes."
                        )
                    else:
                        console.print(
                            f"[red]Evaluator:[/red] Fix-up did not resolve "
                            f"the issue: {verification_error}"
                        )
                else:
                    console.print(
                        f"[red]Evaluator:[/red] Could not produce fixes — "
                        f"{evaluator_result.reasoning}"
                    )

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

    # Run passive LLM evaluator (only if the fix-up loop didn't already set it)
    if evaluator_result is None and evaluator:
        try:
            evaluator_model = model.value if model else "claude-haiku-4-5-20251001"

            # Run guardrail and display output before evaluator LLM call
            if guardrail:
                console.print("[yellow]Guardrail:[/yellow] Running structural checks...")
                guardrail_output = run_guardrail(project_root)
                console.print(guardrail_output)

            logging.info("Running LLM evaluator to check agent completeness...")
            evaluator_result = evaluate_agent_work(
                generated_files={
                    "devcontainer_json": generated_files.devcontainer_json,
                    "dockerfile": generated_files.dockerfile,
                    "run_all_tests_sh": generated_files.run_all_tests_sh,
                },
                agent_summary=agent_summary.message if agent_summary else None,
                status_messages=[m.message for m in status_messages],
                verification_success=verification_success,
                verification_error=verification_error,
                project_root=project_root,
                model=evaluator_model,
                guardrail=guardrail,
            )
            if evaluator_result.passed:
                console.print(f"[green]Evaluator:[/green] PASSED - {evaluator_result.reasoning}")
            else:
                console.print(f"[red]Evaluator:[/red] FAILED - {evaluator_result.reasoning}")
                for issue in evaluator_result.issues:
                    console.print(f"  - {issue}")
        except Exception as e:
            logging.warning(f"Evaluator failed (non-blocking): {e}")

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
            summary=agent_summary,
            status_messages=status_messages,
            error_messages=agent_errors,
            cost=inference_cost,
        ),
        verification=verification,
        evaluator=evaluator_result,
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
