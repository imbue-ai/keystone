"""Worker logic for processing a single repo."""
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path
from typing import Optional

from config import AgentConfig, WorkerResult


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
        "customApiKeyResponses": {
            "approved": [api_key_suffix],
            "rejected": []
        },
        "hasCompletedOnboarding": True,
        "shiftEnterKeyBindingInstalled": True,
        "theme": "dark"
    }
    
    claude_json_path = home_dir / ".claude.json"
    with open(claude_json_path, "w") as f:
        json.dump(claude_json, f, indent=2)
    
    # Also create settings.json with permissive defaults for automation
    settings_json = {
        "permissions": {
            "allow": [
                "Bash(*)",
                "Read(*)",
                "Write(*)",
                "Edit(*)"
            ]
        }
    }
    settings_path = claude_dir / "settings.json"
    with open(settings_path, "w") as f:
        json.dump(settings_json, f, indent=2)


def find_session_file(project_path: Path, home_dir: Path) -> Optional[Path]:
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
    jsonl_files = [
        f for f in session_dir.glob("*.jsonl")
        if not f.name.startswith("agent-")
    ]
    
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
    
    try:
        # Extract tarball
        project_dir = work_dir / "project"
        project_dir.mkdir()
        
        with tarfile.open(tarball_path, "r:gz") as tar:
            tar.extractall(project_dir)
        
        # If tarball contained a single root dir, descend into it
        contents = list(project_dir.iterdir())
        if len(contents) == 1 and contents[0].is_dir():
            actual_project_dir = contents[0]
        else:
            actual_project_dir = project_dir
        
        # Set up test artifacts dir
        test_artifacts_dir = work_dir / "test_artifacts"
        test_artifacts_dir.mkdir()
        
        # Set up a fake home for Claude config
        fake_home = work_dir / "home"
        fake_home.mkdir()
        setup_claude_config(anthropic_api_key, fake_home)
        
        # Build the command
        # For local execution, use uv run with the local bootstrap_devcontainer
        # For remote/uvx execution, use git spec
        
        # Check if we're in the bootstrap_devcontainer repo (local development)
        local_bootstrap = Path(__file__).parent.parent / "bootstrap_devcontainer.py"
        
        if local_bootstrap.exists():
            # Local development - run directly
            cmd = [
                "uv", "run", "python",
                str(local_bootstrap),
                str(actual_project_dir),
                "--test-artifacts-dir", str(test_artifacts_dir),
                "--max-budget-usd", str(agent_config.max_budget_usd),
            ]
        else:
            # Remote execution - use uvx with git spec
            git_spec = f"git+{agent_config.bootstrap_git_url}@{agent_config.bootstrap_git_ref}"
            cmd = [
                "uvx",
                "--from", git_spec,
                "bootstrap-devcontainer",
                str(actual_project_dir),
                "--test-artifacts-dir", str(test_artifacts_dir),
                "--max-budget-usd", str(agent_config.max_budget_usd),
            ]
        
        if agent_config.use_cache:
            cache_file = fake_home / ".cache" / "bootstrap_devcontainer.sqlite"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            cmd.extend(["--sqlite-cache-file", str(cache_file)])
        
        # Run the bootstrap command
        env = os.environ.copy()
        env["HOME"] = str(fake_home)
        env["ANTHROPIC_API_KEY"] = anthropic_api_key
        
        timeout_secs = agent_config.timeout_minutes * 60
        
        result = subprocess.run(
            cmd,
            cwd=actual_project_dir,
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout_secs,
        )
        
        # Parse result from stdout (last line should be JSON)
        bootstrap_result = None
        for line in reversed(result.stdout.strip().split("\n")):
            try:
                bootstrap_result = json.loads(line)
                break
            except json.JSONDecodeError:
                continue
        
        success = result.returncode == 0
        
        # Collect output artifacts
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Package .devcontainer as tarball
        devcontainer_dir = actual_project_dir / ".devcontainer"
        devcontainer_tarball = None
        if devcontainer_dir.exists():
            devcontainer_tarball = output_dir / "devcontainer.tar.gz"
            with tarfile.open(devcontainer_tarball, "w:gz") as tar:
                tar.add(devcontainer_dir, arcname=".devcontainer")
        
        # Find and copy session file
        session_file = find_session_file(actual_project_dir, fake_home)
        session_output = None
        if session_file:
            session_output = output_dir / "session.jsonl"
            shutil.copy(session_file, session_output)
        
        # Save bootstrap result JSON
        if bootstrap_result:
            result_json = output_dir / "bootstrap_result.json"
            with open(result_json, "w") as f:
                json.dump(bootstrap_result, f, indent=2)
        
        # Save stdout/stderr for debugging
        with open(output_dir / "stdout.txt", "w") as f:
            f.write(result.stdout)
        with open(output_dir / "stderr.txt", "w") as f:
            f.write(result.stderr)
        
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
            error_message=str(e),
        )
    finally:
        # Clean up work dir
        shutil.rmtree(work_dir, ignore_errors=True)
