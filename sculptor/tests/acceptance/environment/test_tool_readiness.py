"""Unit tests for ToolReadinessManager.

Tests the blocker tracking logic and ready file creation.
Tests use the Environment interface and work with any environment type
(docker, local, etc.) if a parameterized environment fixture is available.
"""

from sculptor.interfaces.environments.base import Environment
from sculptor.services.environment_service.environments.docker_environment import DockerEnvironment
from sculptor.services.environment_service.tool_readiness import READY_FILE
from sculptor.services.environment_service.tool_readiness import ToolReadinessBlocker
from sculptor.services.environment_service.tool_readiness import ToolReadinessManager


def test_manager_initially_has_no_blockers(docker_environment: Environment):
    """Manager should start with no blockers."""
    manager = ToolReadinessManager(docker_environment, task_id=None)

    assert len(manager.get_pending_blockers()) == 0
    assert manager.get_pending_blockers() == set()


def test_add_single_blocker(docker_environment: Environment):
    """Should be able to add a single blocker."""
    manager = ToolReadinessManager(docker_environment, task_id=None)

    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED)

    assert len(manager.get_pending_blockers()) == 1
    assert ToolReadinessBlocker.REPO_SYNCED in manager.get_pending_blockers()


def test_add_multiple_blockers(docker_environment: DockerEnvironment):
    """Should be able to add multiple blockers at once."""
    manager = ToolReadinessManager(docker_environment, task_id=None)

    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED, ToolReadinessBlocker.BUILD_VERIFIED)

    assert len(manager.get_pending_blockers()) == 2
    assert ToolReadinessBlocker.REPO_SYNCED in manager.get_pending_blockers()
    assert ToolReadinessBlocker.BUILD_VERIFIED in manager.get_pending_blockers()


def test_clear_blocker_removes_it(docker_environment: DockerEnvironment):
    """Clearing a blocker should remove it from pending set."""
    manager = ToolReadinessManager(docker_environment, task_id=None)
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED, ToolReadinessBlocker.BUILD_VERIFIED)

    manager.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)

    assert len(manager.get_pending_blockers()) == 1
    assert ToolReadinessBlocker.REPO_SYNCED not in manager.get_pending_blockers()
    assert ToolReadinessBlocker.BUILD_VERIFIED in manager.get_pending_blockers()


def test_clearing_last_blocker_writes_ready_file(docker_environment: DockerEnvironment):
    """When last blocker is cleared, ready file should be created."""
    manager = ToolReadinessManager(docker_environment, task_id=None)
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED)

    # Ready file should not exist yet
    assert not manager.is_ready()

    # Clear the only blocker
    manager.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)

    # Now ready file should exist
    assert manager.is_ready()
    assert len(manager.get_pending_blockers()) == 0

    assert docker_environment.exists(str(READY_FILE))

    # Verify file exists in container
    result = docker_environment.run_process_to_completion(
        ["test", "-f", str(READY_FILE)], is_checked_after=False, secrets={}
    )
    assert result.returncode == 0


def test_clearing_multiple_blockers_sequentially(docker_environment: DockerEnvironment):
    """Should only write ready file when ALL blockers are cleared."""
    manager = ToolReadinessManager(docker_environment, task_id=None)
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED, ToolReadinessBlocker.BUILD_VERIFIED)

    # Clear first blocker - should NOT be ready yet
    manager.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)
    assert not manager.is_ready()
    assert len(manager.get_pending_blockers()) == 1

    # Clear second blocker - NOW should be ready
    manager.clear_blocker(ToolReadinessBlocker.BUILD_VERIFIED)
    assert manager.is_ready()
    assert len(manager.get_pending_blockers()) == 0


def test_clearing_unknown_blocker_is_safe(docker_environment: DockerEnvironment):
    """Clearing a blocker that wasn't added should not crash."""
    manager = ToolReadinessManager(docker_environment, task_id=None)
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED)

    # This should not crash or affect the existing blocker
    manager.clear_blocker(ToolReadinessBlocker.BUILD_VERIFIED)

    # Original blocker should still be pending
    assert ToolReadinessBlocker.REPO_SYNCED in manager.get_pending_blockers()
    assert len(manager.get_pending_blockers()) == 1


def test_clearing_same_blocker_twice_is_safe(docker_environment: DockerEnvironment):
    """Clearing the same blocker twice should not crash."""
    manager = ToolReadinessManager(docker_environment, task_id=None)
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED)

    manager.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)
    # Second clear should be safe (blocker already removed)
    manager.clear_blocker(ToolReadinessBlocker.REPO_SYNCED)

    assert len(manager.get_pending_blockers()) == 0
    assert manager.is_ready()


def test_is_ready_before_any_blockers(docker_environment: DockerEnvironment):
    """Manager with no blockers should not report as ready (no file written)."""
    manager = ToolReadinessManager(docker_environment, task_id=None)

    # No blockers added, but no file written either
    assert not manager.is_ready()


def test_get_pending_blockers_returns_copy(docker_environment: DockerEnvironment):
    """get_pending_blockers should return a copy, not internal set."""
    manager = ToolReadinessManager(docker_environment, task_id=None)
    manager.add_blockers(ToolReadinessBlocker.REPO_SYNCED)

    blockers = manager.get_pending_blockers()
    blockers.clear()  # Modify the returned set

    # Internal state should be unchanged
    assert len(manager.get_pending_blockers()) == 1


def test_blocker_enum_values():
    """Verify blocker enum has expected values."""
    assert ToolReadinessBlocker.REPO_SYNCED.value == "repo_synced"
    assert ToolReadinessBlocker.BUILD_VERIFIED.value == "build_verified"


def test_blocker_descriptions():
    """Verify blockers have human-readable descriptions."""
    assert len(ToolReadinessBlocker.REPO_SYNCED.description) > 0
    assert len(ToolReadinessBlocker.BUILD_VERIFIED.description) > 0
    assert "repo" in ToolReadinessBlocker.REPO_SYNCED.description.lower()
    assert "build" in ToolReadinessBlocker.BUILD_VERIFIED.description.lower()


def test_multiple_managers_independent(docker_environment: DockerEnvironment):
    """Multiple managers should track blockers independently."""
    manager1 = ToolReadinessManager(docker_environment, task_id=None)
    manager2 = ToolReadinessManager(docker_environment, task_id=None)

    manager1.add_blockers(ToolReadinessBlocker.REPO_SYNCED)
    manager2.add_blockers(ToolReadinessBlocker.BUILD_VERIFIED)

    # Each manager should have only its own blocker
    assert ToolReadinessBlocker.REPO_SYNCED in manager1.get_pending_blockers()
    assert ToolReadinessBlocker.REPO_SYNCED not in manager2.get_pending_blockers()

    assert ToolReadinessBlocker.BUILD_VERIFIED in manager2.get_pending_blockers()
    assert ToolReadinessBlocker.BUILD_VERIFIED not in manager1.get_pending_blockers()
