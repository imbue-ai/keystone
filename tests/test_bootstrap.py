import subprocess
import json
import pytest
from pathlib import Path


def test_cli_help():
    result = subprocess.run(
        ["python3", "bootstrap_devcontainer.py", "--help"],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0
    assert "bootstrap_devcontainer.py [OPTIONS] PROJECT_ROOT" in result.stdout


@pytest.mark.manual
def test_e2e_sample_project(tmp_path):
    import shutil

    # Copy sample project to tmp_path to avoid modifying the original source tree
    original_project_root = Path("samples/python_project").resolve()
    project_root = tmp_path / "project"
    shutil.copytree(original_project_root, project_root)

    scratch_dir = tmp_path / "scratch"

    # This will actually run the agent. It's expensive and slow.
    cmd = [
        "python3",
        "bootstrap_devcontainer.py",
        str(project_root),
        "--scratch-dir",
        str(scratch_dir),
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)

    assert result.returncode == 0
    output = json.loads(result.stdout)
    assert "success" in output
    assert "total_time" in output
    assert "token_spending" in output

    # Check if .devcontainer was created
    assert (project_root / ".devcontainer" / "devcontainer.json").exists()
    assert (project_root / ".devcontainer" / "Dockerfile").exists()
    assert (project_root / ".devcontainer" / "run_all_tests.sh").exists()
