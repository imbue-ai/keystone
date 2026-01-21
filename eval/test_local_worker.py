"""Test the eval harness on samples/python_project.

This test uses caching to avoid repeated agent runs.

Usage:
    cd eval
    uv run pytest test_local_worker.py -v
"""
import os
from pathlib import Path

import pytest

from config import AgentConfig
from flow import create_tarball_from_dir, eval_local_tarball_flow


@pytest.fixture
def samples_dir() -> Path:
    """Path to the sample python project."""
    path = Path(__file__).parent.parent / "samples" / "python_project"
    if not path.exists():
        pytest.skip(f"Sample project not found at {path}")
    return path


@pytest.mark.skipif(
    not os.environ.get("ANTHROPIC_API_KEY"),
    reason="ANTHROPIC_API_KEY not set"
)
def test_eval_local_tarball_flow(samples_dir: Path, tmp_path: Path) -> None:
    """Test that eval_local_tarball_flow succeeds on the sample project."""
    # Create tarball
    tarball_path = tmp_path / "python_project.tar.gz"
    create_tarball_from_dir(samples_dir, tarball_path)
    
    # Configure with caching enabled
    agent_config = AgentConfig(
        max_budget_usd=1.0,
        use_cache=True,
        timeout_minutes=30,
    )
    
    # Run the flow
    result = eval_local_tarball_flow(
        tarball_path=str(tarball_path),
        agent_config=agent_config,
        output_dir=str(tmp_path / "result"),
    )
    
    # Assert success
    assert result.success, f"Eval failed: {result.error_message}"
    
    # Verify output files exist
    result_dir = tmp_path / "result"
    assert result_dir.exists(), "Result directory not created"
