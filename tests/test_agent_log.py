"""Tests for the agent_log module."""

import tempfile
from collections.abc import Generator
from datetime import UTC, datetime
from pathlib import Path

import pytest

from keystone.agent_log import (
    AgentLog,
    AgentRunRecord,
    CacheKey,
    CLIRunRecord,
    StreamEvent,
)
from keystone.schema import AgentConfig


@pytest.fixture
def temp_db() -> Generator[Path, None, None]:
    """Create a temporary database path."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir) / "test.sqlite"


def test_agent_log_create_db(temp_db: Path) -> None:
    """Test creating a new database."""
    agent_log = AgentLog(str(temp_db))
    # Write something to trigger db creation (SQLAlchemy is lazy)
    record = CLIRunRecord(
        id=agent_log.generate_run_id(),
        timestamp=datetime.now(UTC),
        cwd="/test",
        args=["test"],
        cache_hit=False,
    )
    agent_log.log_cli_run(record)
    assert temp_db.exists()
    agent_log.close()


def test_cli_run_logging(temp_db: Path) -> None:
    """Test logging CLI runs."""
    agent_log = AgentLog(str(temp_db))
    run_id = agent_log.generate_run_id()

    record = CLIRunRecord(
        id=run_id,
        timestamp=datetime.now(UTC),
        cwd="/test/path",
        args=["bootstrap", "--project_root", "/test"],
        cache_hit=False,
        bootstrap_result_json='{"success": true}',
    )
    agent_log.log_cli_run(record)
    agent_log.close()


def test_agent_run_logging_and_cache_lookup(temp_db: Path) -> None:
    """Test logging agent runs and cache lookup."""
    agent_log = AgentLog(str(temp_db))
    cli_run_id = agent_log.generate_run_id()

    cache_key = CacheKey(
        git_tree_hash="abc123",
        prompt_hash="def456",
        agent_config_json='{"agent_cmd": "claude"}',
        cache_version="v1",
    )

    # Log a successful run
    record = AgentRunRecord(
        cli_run_id=cli_run_id,
        timestamp=datetime.now(UTC),
        cache_key=cache_key,
        events=[
            StreamEvent(stream="stdout", line="hello"),
            StreamEvent(stream="stderr", line="world"),
        ],
        devcontainer_tarball=b"tarball data",
        return_code=0,
        claude_dir_tarball=None,
    )
    agent_log.log_agent_run(record)

    # Lookup should find it
    cached = agent_log.lookup_cache(cache_key)
    assert cached is not None
    assert cached.return_code == 0
    assert len(cached.events) == 2
    assert cached.devcontainer_tarball == b"tarball data"

    agent_log.close()


def test_cache_only_returns_successful_runs(temp_db: Path) -> None:
    """Test that cache lookup only returns successful runs."""
    agent_log = AgentLog(str(temp_db))

    cache_key = CacheKey(
        git_tree_hash="abc123",
        prompt_hash="def456",
        agent_config_json='{"agent_cmd": "claude"}',
        cache_version="",
    )

    # Log a failed run
    record = AgentRunRecord(
        cli_run_id=agent_log.generate_run_id(),
        timestamp=datetime.now(UTC),
        cache_key=cache_key,
        events=[StreamEvent(stream="stderr", line="error")],
        devcontainer_tarball=b"",
        return_code=1,  # Failed
        claude_dir_tarball=None,
    )
    agent_log.log_agent_run(record)

    # Lookup should NOT find it (failed run)
    cached = agent_log.lookup_cache(cache_key)
    assert cached is None

    agent_log.close()


def test_agent_config_cache_key() -> None:
    """Test AgentConfig produces stable cache key JSON."""
    config1 = AgentConfig(
        agent_cmd="claude",
        max_budget_usd=1.0,
        agent_time_limit_secs=3600,
        agent_in_modal=True,
    )
    config2 = AgentConfig(
        agent_cmd="claude",
        max_budget_usd=1.0,
        agent_time_limit_secs=3600,
        agent_in_modal=True,
    )
    # Same config should produce same JSON
    assert config1.to_cache_key_json() == config2.to_cache_key_json()

    # Different config should produce different JSON
    config3 = AgentConfig(
        agent_cmd="claude",
        max_budget_usd=2.0,  # Different
        agent_time_limit_secs=3600,
        agent_in_modal=True,
    )
    assert config1.to_cache_key_json() != config3.to_cache_key_json()


def test_cache_key_hash() -> None:
    """Test CacheKey hash computation."""
    key1 = CacheKey(
        git_tree_hash="abc",
        prompt_hash="def",
        agent_config_json="{}",
        cache_version="v1",
    )
    key2 = CacheKey(
        git_tree_hash="abc",
        prompt_hash="def",
        agent_config_json="{}",
        cache_version="v1",
    )
    # Same keys should produce same hash
    assert key1.compute_hash() == key2.compute_hash()

    # Different version should produce different hash
    key3 = CacheKey(
        git_tree_hash="abc",
        prompt_hash="def",
        agent_config_json="{}",
        cache_version="v2",  # Different
    )
    assert key1.compute_hash() != key3.compute_hash()
