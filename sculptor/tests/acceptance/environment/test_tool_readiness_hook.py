"""Integration tests for tool readiness hook configuration.

Tests that the hook is properly configured in Docker environments,
including script copying, settings.json creation, and path configuration.
"""

import json
import tempfile
from pathlib import Path
from typing import Any

import pytest

from sculptor.agents.default.claude_code_sdk.config_service_plugin import _merge_tool_readiness_hook
from sculptor.interfaces.environments.base import Environment
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.tool_readiness import READY_FILE
from sculptor.services.environment_service.tool_readiness import ToolReadinessBlocker
from sculptor.services.environment_service.tool_readiness import ToolReadinessManager


# TODO: instead of the legacy _configure_tool_readiness_hook, test that configuration succeeds after a task is started.
def _read_existing_settings(environment: Environment, settings_path: Path) -> dict[str, Any]:
    try:
        content = environment.read_file(str(settings_path))
    except FileNotFoundError:
        return {}
    settings = json.loads(content)
    return settings


def _configure_tool_readiness_hook(environment: Environment, timeout_seconds: int = 120) -> None:
    settings_dir = environment.get_container_user_home_directory() / ".claude"
    settings_path = settings_dir / "settings.json"

    existing_settings = _read_existing_settings(environment, settings_path)
    if not isinstance(existing_settings, dict):
        raise TypeError(f"Expected existing settings to be a dict, got {type(existing_settings).__name__}")
    merged_settings = _merge_tool_readiness_hook(existing_settings, timeout_seconds)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json") as tmp_file:
        json.dump(merged_settings, tmp_file, indent=2)
        tmp_file.flush()

        environment.run_process_to_completion(["mkdir", "-p", str(settings_dir)], is_checked_after=True, secrets={})
        environment.copy_from_local(Path(tmp_file.name), str(settings_path))


def test_hook_script_copied_to_container(docker_environment: DockerEnvironment) -> None:
    """Verify hook script is copied to container during configuration."""
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    assert docker_environment.exists("/imbue_addons/check_tool_readiness.sh"), "Hook script should exist in container"


def test_hook_script_is_executable(docker_environment: DockerEnvironment) -> None:
    """Verify hook script has executable permissions."""
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    # Check script is executable
    result = docker_environment.run_process_to_completion(
        ["test", "-x", "/imbue_addons/check_tool_readiness.sh"], is_checked_after=False, secrets={}
    )
    assert result.returncode == 0, "Hook script should be executable"


def test_settings_json_created(docker_environment: DockerEnvironment) -> None:
    """Verify settings.json is created in correct location."""
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    user_home = docker_environment.get_container_user_home_directory()
    settings_path = user_home / ".claude" / "settings.json"

    assert docker_environment.exists(str(settings_path)), f"Settings file should exist at {settings_path}"


def test_settings_json_contains_hook_configuration(docker_environment: DockerEnvironment) -> None:
    """Verify settings.json contains proper hook configuration."""
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=60)

    user_home = docker_environment.get_container_user_home_directory()
    settings_path = user_home / ".claude" / "settings.json"

    content = docker_environment.read_file(str(settings_path))
    settings = json.loads(content)

    # Verify hook structure
    assert "hooks" in settings
    assert "PreToolUse" in settings["hooks"]
    assert len(settings["hooks"]["PreToolUse"]) > 0

    # Get first PreToolUse hook
    hook_config = settings["hooks"]["PreToolUse"][0]
    assert "matcher" in hook_config
    assert hook_config["matcher"] == "*"  # Should match all tools

    # Verify hook command configuration
    assert "hooks" in hook_config
    assert len(hook_config["hooks"]) > 0
    command_hook = hook_config["hooks"][0]

    assert command_hook["type"] == "command"
    assert "check_tool_readiness.sh" in command_hook["command"]


def test_settings_json_contains_timeout_env_var(docker_environment: DockerEnvironment) -> None:
    """Verify timeout is passed via environment variable."""
    timeout = 90
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=timeout)

    user_home = docker_environment.get_container_user_home_directory()
    settings_path = user_home / ".claude" / "settings.json"

    content = docker_environment.read_file(str(settings_path))
    settings = json.loads(content)

    # Get hook env configuration
    hook_config = settings["hooks"]["PreToolUse"][0]["hooks"][0]
    assert "env" in hook_config
    assert "SCULPTOR_TOOL_READINESS_TIMEOUT" in hook_config["env"]
    assert hook_config["env"]["SCULPTOR_TOOL_READINESS_TIMEOUT"] == str(timeout)


def test_ready_file_path_passed_as_argument(docker_environment: DockerEnvironment) -> None:
    """Verify ready file path is passed as script argument."""
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    user_home = docker_environment.get_container_user_home_directory()
    settings_content = docker_environment.read_file(str(user_home / ".claude" / "settings.json"))
    settings = json.loads(settings_content)

    # Get command
    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]

    # Verify path is in command as argument
    ready_file_path = str(READY_FILE)
    assert ready_file_path in command, f"Ready file path {ready_file_path} should be in command"

    # Verify format is: script_path ready_file_path
    assert command.endswith(ready_file_path), "Ready file path should be last argument"


def test_hook_can_be_configured_multiple_times(docker_environment: DockerEnvironment) -> None:
    """Verify hook can be reconfigured without errors."""
    # Configure once
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=60)

    # Configure again with different timeout - should succeed
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    # Verify latest configuration
    user_home = docker_environment.get_container_user_home_directory()
    settings_content = docker_environment.read_file(str(user_home / ".claude" / "settings.json"))
    settings = json.loads(settings_content)

    timeout = settings["hooks"]["PreToolUse"][0]["hooks"][0]["env"]["SCULPTOR_TOOL_READINESS_TIMEOUT"]
    assert timeout == "120", "Should have latest timeout value"


@pytest.mark.parametrize("timeout_value", [30, 60, 120, 240])
def test_hook_with_various_timeouts(docker_environment: DockerEnvironment, timeout_value: int) -> None:
    """Test hook configuration with various timeout values."""
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=timeout_value)

    user_home = docker_environment.get_container_user_home_directory()
    settings_content = docker_environment.read_file(str(user_home / ".claude" / "settings.json"))
    settings = json.loads(settings_content)

    configured_timeout = settings["hooks"]["PreToolUse"][0]["hooks"][0]["env"]["SCULPTOR_TOOL_READINESS_TIMEOUT"]
    assert configured_timeout == str(timeout_value)


def test_settings_directory_created_if_missing(docker_environment: DockerEnvironment) -> None:
    """Verify .claude directory is created if it doesn't exist."""
    user_home = docker_environment.get_container_user_home_directory()
    claude_dir = user_home / ".claude"

    # Remove .claude directory if it exists
    docker_environment.run_process_to_completion(["rm", "-rf", str(claude_dir)], is_checked_after=True, secrets={})

    # Configure hook - should create directory
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    assert docker_environment.exists(str(claude_dir)), ".claude directory should be created"


def test_hook_script_content_valid(docker_environment: DockerEnvironment) -> None:
    """Verify copied script has valid bash syntax."""
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    # Try to check bash syntax
    result = docker_environment.run_process_to_completion(
        ["bash", "-n", "/imbue_addons/check_tool_readiness.sh"], is_checked_after=False, secrets={}
    )
    assert result.returncode == 0, "Hook script should have valid bash syntax"


def test_hook_script_has_shebang(docker_environment: DockerEnvironment) -> None:
    """Verify hook script starts with proper shebang."""
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    # Read first line of script
    result = docker_environment.run_process_to_completion(
        ["head", "-n", "1", "/imbue_addons/check_tool_readiness.sh"], is_checked_after=True, secrets={}
    )

    first_line = result.stdout.strip()
    assert first_line.startswith("#!/"), "Script should start with shebang"
    assert "bash" in first_line, "Script should use bash"


def test_integration_with_tool_readiness_manager(docker_environment: DockerEnvironment) -> None:
    """Test that hook configuration works with ToolReadinessManager."""
    # Configure hook
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=30)

    # Create manager and add blocker
    manager = ToolReadinessManager(docker_environment, task_id=None)
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED)

    # Ready file should not exist yet
    assert not manager.is_ready()

    # Clear blocker
    manager.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)

    # Now ready file should exist
    assert manager.is_ready()

    # Verify file is at the path configured in settings
    user_home = docker_environment.get_container_user_home_directory()
    settings_content = docker_environment.read_file(str(user_home / ".claude" / "settings.json"))
    settings = json.loads(settings_content)

    command = settings["hooks"]["PreToolUse"][0]["hooks"][0]["command"]
    # Extract path from command (it's the last part)
    configured_path = command.split()[-1]

    # Verify file exists at that path
    assert docker_environment.exists(configured_path), f"Ready file should exist at configured path {configured_path}"


def test_preserves_existing_settings(docker_environment: DockerEnvironment) -> None:
    """Verify that existing settings are preserved when configuring hook."""
    user_home = docker_environment.get_container_user_home_directory()
    settings_path = user_home / ".claude" / "settings.json"

    # Create initial settings with custom values
    initial_settings = {
        "customSetting": "should_be_preserved",
        "environment": {"CUSTOM_VAR": "custom_value"},
        "permissions": {"deny": ["*.secret"]},
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write",
                    "hooks": [{"type": "command", "command": "/usr/bin/custom-hook.sh"}],
                }
            ],
            "PostToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": "/usr/bin/post-hook.sh"}],
                }
            ],
        },
    }

    # Write initial settings
    docker_environment.run_process_to_completion(
        ["mkdir", "-p", str(user_home / ".claude")], is_checked_after=True, secrets={}
    )
    docker_environment.write_file(str(settings_path), json.dumps(initial_settings, indent=2))

    # Configure tool readiness hook
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=60)

    # Read back settings
    content = docker_environment.read_file(str(settings_path))
    merged_settings = json.loads(content)

    # Verify existing settings are preserved
    assert merged_settings["customSetting"] == "should_be_preserved"
    assert merged_settings["environment"]["CUSTOM_VAR"] == "custom_value"
    assert merged_settings["permissions"]["deny"] == ["*.secret"]

    # Verify existing hooks are preserved
    assert "PostToolUse" in merged_settings["hooks"]
    assert len(merged_settings["hooks"]["PostToolUse"]) == 1
    assert merged_settings["hooks"]["PostToolUse"][0]["matcher"] == "*"

    # Verify PreToolUse hooks - should have both Write and * matchers
    pre_tool_use_hooks = merged_settings["hooks"]["PreToolUse"]
    assert len(pre_tool_use_hooks) == 2

    # Find the Write matcher hook (should be preserved)
    write_hook = next((h for h in pre_tool_use_hooks if h["matcher"] == "Write"), None)
    assert write_hook is not None
    assert write_hook["hooks"][0]["command"] == "/usr/bin/custom-hook.sh"

    # Find the * matcher hook (our tool readiness hook, appended last)
    wildcard_hook = next((h for h in pre_tool_use_hooks if h["matcher"] == "*"), None)
    assert wildcard_hook is not None
    assert "check_tool_readiness.sh" in wildcard_hook["hooks"][0]["command"]


def test_appends_hook_alongside_existing_wildcard(docker_environment: DockerEnvironment) -> None:
    """Verify that hook is appended even when wildcard hook already exists.

    Multiple hooks with the same matcher can coexist and run in parallel.
    """
    user_home = docker_environment.get_container_user_home_directory()
    settings_path = user_home / ".claude" / "settings.json"

    # Create initial settings with existing wildcard hook
    initial_settings = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "*",
                    "hooks": [{"type": "command", "command": "/existing-hook.sh"}],
                }
            ]
        }
    }

    # Write initial settings
    docker_environment.run_process_to_completion(
        ["mkdir", "-p", str(user_home / ".claude")], is_checked_after=True, secrets={}
    )
    docker_environment.write_file(str(settings_path), json.dumps(initial_settings, indent=2))

    # Configure tool readiness hook
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=60)

    # Read back settings
    content = docker_environment.read_file(str(settings_path))
    merged_settings = json.loads(content)

    # Should have TWO PreToolUse hooks with matcher "*" (both run in parallel)
    pre_tool_use_hooks = merged_settings["hooks"]["PreToolUse"]
    wildcard_hooks = [h for h in pre_tool_use_hooks if h["matcher"] == "*"]

    assert len(wildcard_hooks) == 2, "Should have both wildcard hooks"

    # Verify both hooks are present
    commands = [h["hooks"][0]["command"] for h in wildcard_hooks]
    assert any("/existing-hook.sh" in cmd for cmd in commands), "Original hook should be preserved"
    assert any("check_tool_readiness.sh" in cmd for cmd in commands), "New hook should be appended"


def test_preserves_settings_when_no_prior_file(docker_environment: DockerEnvironment) -> None:
    """Verify that hook configuration works correctly when no settings.json exists."""
    user_home = docker_environment.get_container_user_home_directory()
    settings_path = user_home / ".claude" / "settings.json"

    # Remove settings file if it exists
    docker_environment.run_process_to_completion(["rm", "-f", str(settings_path)], is_checked_after=True, secrets={})

    # Configure hook - should create new file
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)

    # Verify file exists and has correct structure
    assert docker_environment.exists(str(settings_path))

    content = docker_environment.read_file(str(settings_path))
    settings = json.loads(content)

    # Should only have hooks configuration
    assert "hooks" in settings
    assert "PreToolUse" in settings["hooks"]
    assert len(settings["hooks"]["PreToolUse"]) == 1
    assert settings["hooks"]["PreToolUse"][0]["matcher"] == "*"


def test_mark_ready_creates_marker_file(docker_environment: DockerEnvironment) -> None:
    """Verify mark_ready() creates the marker file when not present."""
    manager = ToolReadinessManager(docker_environment, task_id=None)

    # Initially not ready
    assert not manager.is_ready()

    # Mark as ready
    manager.mark_ready()

    # Now should be ready
    assert manager.is_ready()
    assert docker_environment.exists(str(READY_FILE))


def test_mark_ready_is_idempotent(docker_environment: DockerEnvironment) -> None:
    """Verify mark_ready() can be called multiple times safely."""
    manager = ToolReadinessManager(docker_environment, task_id=None)

    # Mark ready multiple times
    manager.mark_ready()
    manager.mark_ready()
    manager.mark_ready()

    # Should still be ready
    assert manager.is_ready()


def test_removes_stale_marker_file(docker_environment: DockerEnvironment) -> None:
    """Verify stale marker files are removed before adding blockers."""
    manager = ToolReadinessManager(docker_environment, task_id=None)

    # Create a stale marker file (simulating environment reuse)
    manager.mark_ready()
    assert manager.is_ready()

    # Remove stale marker using the proper API
    manager.remove_ready_marker()

    # Marker should be gone
    assert not manager.is_ready()

    # Add blocker
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED)

    # Should still not be ready (blocker not cleared yet)
    assert not manager.is_ready()

    # Clear blocker
    manager.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)

    # Now should be ready
    assert manager.is_ready()


def test_environment_reuse_scenario_no_blockers(docker_environment: DockerEnvironment) -> None:
    """Verify environment reuse scenario where we mark ready without adding blockers.

    This simulates the code path in setup.py where used_old_env=True and we skip
    adding blockers, immediately marking the environment as ready.
    """
    _configure_tool_readiness_hook(docker_environment, timeout_seconds=120)
    manager = ToolReadinessManager(docker_environment, task_id=None)

    # Simulate environment reuse: remove stale marker
    manager.remove_ready_marker()

    # In reuse scenario, we don't add blockers - we mark ready immediately
    # This is what happens when will_sync_repo = False
    manager.mark_ready()

    # Should be ready immediately
    assert manager.is_ready()

    # No blockers should be pending
    assert len(manager.get_pending_blockers()) == 0


def test_stale_marker_with_new_blockers(docker_environment: DockerEnvironment) -> None:
    """Verify that stale marker doesn't interfere with new blocker workflow.

    This tests the scenario where an environment is reused but we DO need to
    sync the repo (e.g., forked task with changes).
    """
    manager = ToolReadinessManager(docker_environment, task_id=None)

    # Simulate stale marker from previous session
    manager.mark_ready()
    assert manager.is_ready()

    # Remove stale marker (as setup.py does)
    manager.remove_ready_marker()

    # Verify marker is gone
    assert not manager.is_ready()

    # Add blocker (will sync repo)
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED)
    assert not manager.is_ready()

    # Simulate repo sync completion
    manager.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)

    # Now should be ready
    assert manager.is_ready()


def test_remove_ready_marker_is_idempotent(docker_environment: DockerEnvironment) -> None:
    """Verify remove_ready_marker() can be called when marker doesn't exist."""
    manager = ToolReadinessManager(docker_environment, task_id=None)

    # Remove marker when it doesn't exist (should not error)
    manager.remove_ready_marker()

    # Should still not be ready
    assert not manager.is_ready()

    # Can call multiple times
    manager.remove_ready_marker()
    manager.remove_ready_marker()

    # Still not ready
    assert not manager.is_ready()
