import json
import logging
import shlex
import shutil
import subprocess

import pytest
from pathlib import Path

from process_runner import run_process

logger = logging.getLogger(__name__)


def test_cli_help() -> None:
    result = subprocess.run(
        ["python3", "bootstrap_devcontainer.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "bootstrap_devcontainer.py [OPTIONS] PROJECT_ROOT" in result.stdout


def test_e2e_with_fake_agent(tmp_path: Path) -> None:
    """
    Test the full Docker mechanics using a deterministic fake agent.
    This tests the devcontainer build and test execution without LLM dependencies.
    """

    # Copy sample project to tmp_path
    original_project_root = Path("samples/python_project").resolve()
    project_root = tmp_path / "project"
    shutil.copytree(original_project_root, project_root)

    scratch_dir = tmp_path / "scratch"
    fake_agent = Path("tests/fake_agent.py").resolve()

    logger.info("=" * 60)
    logger.info("E2E Test with Fake Agent Starting")
    logger.info("Project root: %s", project_root)
    logger.info("Scratch dir: %s", scratch_dir)
    logger.info("=" * 60)

    cmd = [
        "python3", "-u",
        "bootstrap_devcontainer.py",
        str(project_root),
        "--scratch-dir", str(scratch_dir),
        "--agent-cmd", f"python3 {shlex.quote(str(fake_agent))}",
    ]

    logger.info("Running: %s", ' '.join(cmd))

    result = run_process(cmd, log_prefix="[fake-agent]")

    logger.info("Return code: %s", result.returncode)

    assert result.returncode == 0, f"Process failed: {result.stderr}"
    output = json.loads(result.stdout)
    assert output["success"], f"Test failed: {output}"

    # Check artifacts
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()
    assert (scratch_dir / "pytest-json-report.json").exists()
    assert (scratch_dir / "final_result.json").exists()


@pytest.mark.manual
def test_e2e_sample_project(tmp_path: Path) -> None:
    # Copy sample project to tmp_path to avoid modifying the original source tree
    original_project_root = Path("samples/python_project").resolve()
    project_root = tmp_path / "project"
    shutil.copytree(original_project_root, project_root)

    scratch_dir = tmp_path / "scratch"

    logger.info("=" * 60)
    logger.info("E2E Test Starting")
    logger.info("Project root: %s", project_root)
    logger.info("Scratch dir: %s", scratch_dir)
    logger.info("=" * 60)

    # Use -u for unbuffered Python output
    cmd = [
        "python3", "-u",
        "bootstrap_devcontainer.py",
        str(project_root),
        "--scratch-dir",
        str(scratch_dir),
    ]

    logger.info("Running: %s", ' '.join(cmd))

    result = run_process(cmd, log_prefix="[e2e]")

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "success" in output
    assert "total_time" in output
    assert "token_spending" in output

    # Check if .devcontainer was created
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()
