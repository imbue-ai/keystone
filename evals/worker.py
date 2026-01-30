"""Worker logic for processing a single repo."""

import json
import logging
import os
import shutil
import subprocess
import tarfile
import tempfile
import traceback
from pathlib import Path

from config import AgentConfig, WorkerResult
from git_utils import resolve_git_ref

from bootstrap_devcontainer.process_runner import run_process

logger = logging.getLogger(__name__)


def setup_claude_config(api_key: str, home_dir: Path) -> None:
    """Set up ~/.claude.json for non-interactive Claude Code usage.

    Based on 2025/2026 best practices:
    - Pre-approve the API key to skip OAuth flow
    - Mark onboarding as complete
    - Configure for headless operation
    """
    claude_dir = home_dir / ".claude"
    claude_dir.mkdir(parents=True, exist_ok=True)

    # Get last 20 chars of API key for approval
    api_key_suffix = api_key[-20:] if len(api_key) >= 20 else api_key

    claude_json = {
        "customApiKeyResponses": {"approved": [api_key_suffix], "rejected": []},
        "hasCompletedOnboarding": True,
        "shiftEnterKeyBindingInstalled": True,
        "theme": "dark",
    }

    claude_json_path = home_dir / ".claude.json"
    with claude_json_path.open("w") as f:
        json.dump(claude_json, f, indent=2)

    # Also create settings.json with permissive defaults for automation
    settings_json = {"permissions": {"allow": ["Bash(*)", "Read(*)", "Write(*)", "Edit(*)"]}}
    settings_path = claude_dir / "settings.json"
    with settings_path.open("w") as f:
        json.dump(settings_json, f, indent=2)


def find_session_file(project_path: Path, home_dir: Path) -> Path | None:
    """Find the Claude session JSONL file for a project.

    Claude Code stores sessions in ~/.claude/projects/<encoded-path>/*.jsonl
    The path is encoded by replacing / with - and prepending -
    """
    projects_dir = home_dir / ".claude" / "projects"
    if not projects_dir.exists():
        return None

    # Encode the project path like Claude does
    # /some/path -> -some-path
    encoded_path = str(project_path).replace("/", "-")
    if not encoded_path.startswith("-"):
        encoded_path = "-" + encoded_path

    session_dir = projects_dir / encoded_path
    if not session_dir.exists():
        return None

    # Find the most recent .jsonl file (not agent-* files)
    jsonl_files = [f for f in session_dir.glob("*.jsonl") if not f.name.startswith("agent-")]

    if not jsonl_files:
        return None

    # Return most recently modified
    return max(jsonl_files, key=lambda f: f.stat().st_mtime)


def process_repo(
    tarball_path: Path,
    agent_config: AgentConfig,
    output_dir: Path,
    anthropic_api_key: str,
) -> WorkerResult:
    """Process a single repo tarball.

    Args:
        tarball_path: Path to the input tarball
        agent_config: Configuration for the agent
        output_dir: Directory for output artifacts
        anthropic_api_key: API key for Claude

    Returns:
        WorkerResult with success/failure and artifact paths
    """
    work_dir = Path(tempfile.mkdtemp(prefix="eval_worker_"))
    logger.info(f"Created work directory: {work_dir}")

    try:
        # Extract tarball
        logger.info(f"Extracting tarball: {tarball_path}")
        project_dir = work_dir / "project"
        project_dir.mkdir()

        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(project_dir, filter="data")

        # If tarball contained a single root dir, descend into it
        contents = list(project_dir.iterdir())
        if len(contents) == 1 and contents[0].is_dir():
            actual_project_dir = contents[0]
        else:
            actual_project_dir = project_dir
        logger.info(f"Project directory: {actual_project_dir}")

        # Set up test artifacts dir
        test_artifacts_dir = work_dir / "test_artifacts"
        test_artifacts_dir.mkdir()
        logger.info(f"Test artifacts directory: {test_artifacts_dir}")

        # Set up environment
        env = os.environ.copy()

        # Disable git credential helpers to prevent keychain dialogs
        env["GIT_CONFIG_NOSYSTEM"] = "1"
        env["GIT_CONFIG_GLOBAL"] = "/dev/null"  # Ignore user's gitconfig
        env["GIT_TERMINAL_PROMPT"] = "0"
        env["GCM_INTERACTIVE"] = "never"

        # Get GitHub token for private repo access
        gh_token = env.get("GH_TOKEN") or env.get("GITHUB_TOKEN")
        if not gh_token:
            try:
                gh_result = subprocess.run(
                    ["gh", "auth", "token"],
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                if gh_result.returncode == 0 and gh_result.stdout.strip():
                    gh_token = gh_result.stdout.strip()
            except (subprocess.TimeoutExpired, FileNotFoundError):
                pass  # gh CLI not available

        # Resolve git ref (auto-detect from current repo if not specified)
        git_ref = agent_config.bootstrap_git_ref
        if git_ref is None:
            git_ref = resolve_git_ref(require_pushed=True)
            logger.info(f"Auto-resolved git ref: {git_ref}")

        # Build the command using uvx with git spec
        # Embed token in URL for private repo access
        git_url = agent_config.bootstrap_git_url
        if gh_token and "github.com" in git_url:
            git_url = git_url.replace(
                "https://github.com", f"https://x-access-token:{gh_token}@github.com"
            )
        git_spec = f"git+{git_url}@{git_ref}#subdirectory=bootstrap_devcontainer"
        logger.info(
            f"Using bootstrap_devcontainer from: {agent_config.bootstrap_git_url}@{git_ref}"
        )

        result_file = work_dir / "bootstrap_result.json"
        cmd = [
            "uvx",
            "--from",
            git_spec,
            "bootstrap-devcontainer",
            "--project_root",
            str(actual_project_dir),
            "--test_artifacts_dir",
            str(test_artifacts_dir),
            "--max_budget_usd",
            str(agent_config.max_budget_usd),
            "--output_file",
            str(result_file),
            "--agent_time_limit_secs",
            str(agent_config.timeout_minutes * 60),
        ]

        # Add modal flag
        if agent_config.agent_in_modal:
            cmd.append("--agent_in_modal")
        else:
            cmd.append("--agent_local")

        # Add log_db if specified
        if agent_config.log_db:
            cmd.extend(["--log_db", str(Path(agent_config.log_db).expanduser())])

        # If API key provided, set up isolated fake home with Claude config
        # Otherwise, use real home so claude CLI uses its own auth
        fake_home: Path | None = None
        if anthropic_api_key:
            fake_home = work_dir / "home"
            fake_home.mkdir()
            setup_claude_config(anthropic_api_key, fake_home)
            env["HOME"] = str(fake_home)
            env["ANTHROPIC_API_KEY"] = anthropic_api_key
            logger.info(f"Set up fake home with Claude config: {fake_home}")

        # Handle cache configuration
        if agent_config.sqlite_cache_dir:
            # Expand ~ to actual home path
            cache_dir = Path(agent_config.sqlite_cache_dir).expanduser()
            cache_dir.mkdir(parents=True, exist_ok=True)
            cmd.extend(["--sqlite_cache_dir", str(cache_dir)])
            logger.info(f"Using cache directory: {cache_dir}")
        else:
            logger.info("Caching disabled")

        timeout_secs = agent_config.timeout_minutes * 60
        logger.info(f"Running command with {timeout_secs}s timeout: {' '.join(cmd[:6])}...")

        # Use streaming process runner to re-stream bootstrap_devcontainer logs
        # TODO: Add timeout support via ["timeout", str(timeout_secs)] + cmd prefix if needed
        result = run_process(
            cmd,
            log_prefix="bootstrap",
            env=env,
            cwd=str(actual_project_dir),
        )

        # Read result from output file
        logger.info(f"Process completed with exit code: {result.returncode}")
        bootstrap_result = None
        if result_file.exists():
            try:
                bootstrap_result = json.loads(result_file.read_text())
                logger.info(f"Loaded bootstrap result from {result_file}")
            except json.JSONDecodeError:
                logger.info("Failed to parse bootstrap result JSON")
        else:
            logger.info(f"No result file found at {result_file}")

        success = result.returncode == 0
        logger.info(f"Success: {success}")

        # Collect output artifacts
        output_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"Collecting artifacts to: {output_dir}")

        # Package .devcontainer as tarball
        devcontainer_dir = actual_project_dir / ".devcontainer"
        devcontainer_tarball = None
        if devcontainer_dir.exists():
            devcontainer_tarball = output_dir / "devcontainer.tar.gz"
            with tarfile.open(devcontainer_tarball, "w:gz") as tar:
                tar.add(devcontainer_dir, arcname=".devcontainer")
            logger.info(f"Packaged .devcontainer to: {devcontainer_tarball}")
        else:
            logger.info("No .devcontainer directory found")

        # Find and copy session file
        home_dir = fake_home if fake_home else Path.home()
        session_file = find_session_file(actual_project_dir, home_dir)
        session_output = None
        if session_file:
            session_output = output_dir / "session.jsonl"
            shutil.copy(session_file, session_output)
            logger.info(f"Copied session file to: {session_output}")
        else:
            logger.info("No session file found")

        # Save bootstrap result JSON
        if bootstrap_result:
            result_json = output_dir / "bootstrap_result.json"
            with result_json.open("w") as f:
                json.dump(bootstrap_result, f, indent=2)

        # Save stdout/stderr for debugging
        with (output_dir / "stdout.txt").open("w") as f:
            f.write(result.stdout)
        with (output_dir / "stderr.txt").open("w") as f:
            f.write(result.stderr)
        logger.info("Saved stdout/stderr logs")

        return WorkerResult(
            s3_repo_tarball=str(tarball_path),  # Will be replaced with S3 URI by caller
            success=success,
            error_message=None if success else result.stderr[:1000],
            bootstrap_result=bootstrap_result,
            devcontainer_tarball_s3=str(devcontainer_tarball) if devcontainer_tarball else None,
            session_jsonl_s3=str(session_output) if session_output else None,
        )

    except subprocess.TimeoutExpired:
        return WorkerResult(
            s3_repo_tarball=str(tarball_path),
            success=False,
            error_message=f"Timeout after {agent_config.timeout_minutes} minutes",
        )
    except Exception as e:
        return WorkerResult(
            s3_repo_tarball=str(tarball_path),
            success=False,
            error_message=f"{e}\n{traceback.format_exc()}",
        )
    finally:
        # Clean up work dir
        shutil.rmtree(work_dir, ignore_errors=True)
