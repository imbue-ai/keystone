"""Tests for the guardrail.sh validation script."""

import shutil
import subprocess
from pathlib import Path

import pytest
from conftest import SAMPLES_DIR, init_git_repo


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """Create a workspace with a sample project for guardrail testing."""
    project_dir = tmp_path / "project"
    project_dir.mkdir()
    # Copy a minimal Python project
    shutil.copytree(SAMPLES_DIR / "python_project", project_dir, dirs_exist_ok=True)
    init_git_repo(project_dir)

    # Copy guardrail.sh into the workspace (as the runners would)
    guardrail_src = Path(__file__).parent.parent / "src" / "keystone" / "guardrail.sh"
    guardrail_dst = project_dir / "guardrail.sh"
    guardrail_dst.write_bytes(guardrail_src.read_bytes())
    guardrail_dst.chmod(0o755)

    return project_dir


def _run_guardrail(workspace: Path) -> subprocess.CompletedProcess[str]:
    """Run guardrail.sh in the given workspace."""
    return subprocess.run(
        ["bash", str(workspace / "guardrail.sh")],
        cwd=workspace,
        capture_output=True,
        text=True,
    )


def test_guardrail_fails_with_no_devcontainer(workspace: Path) -> None:
    """Guardrail should fail when no .devcontainer directory exists."""
    result = _run_guardrail(workspace)
    assert result.returncode != 0
    assert "FAIL" in result.stdout
    assert ".devcontainer/ directory is MISSING" in result.stdout


def test_guardrail_fails_with_missing_dockerfile(workspace: Path) -> None:
    """Guardrail should fail when Dockerfile is missing."""
    devcontainer_dir = workspace / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text('{"build": {}}')
    (devcontainer_dir / "run_all_tests.sh").write_text(
        "#!/bin/bash\nmkdir -p /test_artifacts/junit\necho '{\"success\": true}' > /test_artifacts/final_result.json\n"
    )
    (devcontainer_dir / "run_all_tests.sh").chmod(0o755)

    result = _run_guardrail(workspace)
    assert result.returncode != 0
    assert "Dockerfile is MISSING" in result.stdout


def test_guardrail_fails_with_missing_run_all_tests(workspace: Path) -> None:
    """Guardrail should fail when run_all_tests.sh is missing."""
    devcontainer_dir = workspace / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text('{"build": {}}')
    (devcontainer_dir / "Dockerfile").write_text(
        "FROM python:3.12\nRUN mkdir -p /test_artifacts && chmod 777 /test_artifacts\nCOPY .devcontainer/run_all_tests.sh /run_all_tests.sh\nRUN chmod +x /run_all_tests.sh\n"
    )

    result = _run_guardrail(workspace)
    assert result.returncode != 0
    assert "run_all_tests.sh is MISSING" in result.stdout


def test_guardrail_checks_dockerfile_structure(workspace: Path) -> None:
    """Guardrail should check Dockerfile has FROM, test_artifacts, and COPY run_all_tests."""
    devcontainer_dir = workspace / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text('{"build": {}}')
    # Dockerfile missing key elements
    (devcontainer_dir / "Dockerfile").write_text("# empty dockerfile\n")
    run_script = devcontainer_dir / "run_all_tests.sh"
    run_script.write_text(
        "#!/bin/bash\nmkdir -p /test_artifacts/junit\necho '{\"success\": true}' > /test_artifacts/final_result.json\n"
    )
    run_script.chmod(0o755)

    result = _run_guardrail(workspace)
    assert result.returncode != 0
    assert "missing a FROM instruction" in result.stdout
    assert "does not create /test_artifacts" in result.stdout
    assert "does not COPY run_all_tests.sh" in result.stdout


def test_guardrail_checks_run_all_tests_structure(workspace: Path) -> None:
    """Guardrail should check run_all_tests.sh has shebang, junit, and final_result."""
    devcontainer_dir = workspace / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text('{"build": {}}')
    (devcontainer_dir / "Dockerfile").write_text(
        "FROM python:3.12\nRUN mkdir -p /test_artifacts && chmod 777 /test_artifacts\nCOPY .devcontainer/run_all_tests.sh /run_all_tests.sh\nRUN chmod +x /run_all_tests.sh\n"
    )
    # run_all_tests.sh missing key elements
    run_script = devcontainer_dir / "run_all_tests.sh"
    run_script.write_text("echo 'hello'\n")  # No shebang, no junit, no final_result
    run_script.chmod(0o755)

    result = _run_guardrail(workspace)
    assert result.returncode != 0
    assert "missing a shebang" in result.stdout
    assert "does not reference junit" in result.stdout
    assert "does not write final_result.json" in result.stdout


def test_guardrail_passes_with_valid_files(workspace: Path) -> None:
    """Guardrail should pass with properly structured files (skipping Docker build)."""
    devcontainer_dir = workspace / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text(
        '{"build": {"dockerfile": "Dockerfile", "context": ".."}}'
    )
    (devcontainer_dir / "Dockerfile").write_text(
        "FROM python:3.12-slim\n"
        "WORKDIR /project_src\n"
        "RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts\n"
        "COPY pyproject.toml ./\n"
        "COPY .devcontainer/run_all_tests.sh /run_all_tests.sh\n"
        "RUN chmod +x /run_all_tests.sh\n"
    )
    run_script = devcontainer_dir / "run_all_tests.sh"
    run_script.write_text(
        "#!/bin/bash\n"
        "set -euo pipefail\n"
        "mkdir -p /test_artifacts/junit\n"
        "pytest --junitxml=/test_artifacts/junit/pytest.xml\n"
        "echo '{\"success\": true}' > /test_artifacts/final_result.json\n"
    )
    run_script.chmod(0o755)

    result = _run_guardrail(workspace)
    # File structure checks should all pass (Docker build may fail since
    # we don't have a real project setup, but the file checks pass)
    assert "PASS: .devcontainer/ directory exists" in result.stdout
    assert "PASS: .devcontainer/devcontainer.json exists" in result.stdout
    assert "PASS: .devcontainer/Dockerfile exists" in result.stdout
    assert "PASS: .devcontainer/run_all_tests.sh exists" in result.stdout
    assert "PASS: Dockerfile has a FROM instruction" in result.stdout
    assert "PASS: Dockerfile references test_artifacts directory" in result.stdout
    assert "PASS: Dockerfile copies run_all_tests.sh" in result.stdout
    assert "PASS: run_all_tests.sh has a shebang line" in result.stdout
    assert "PASS: run_all_tests.sh references junit output" in result.stdout
    assert "PASS: run_all_tests.sh writes final_result.json" in result.stdout


def test_guardrail_warns_on_copy_dot_dot(workspace: Path) -> None:
    """Guardrail should warn about COPY . . pattern."""
    devcontainer_dir = workspace / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text('{"build": {}}')
    (devcontainer_dir / "Dockerfile").write_text(
        "FROM python:3.12\n"
        "WORKDIR /project_src\n"
        "RUN mkdir -p /test_artifacts && chmod 777 /test_artifacts\n"
        "COPY . .\n"
        "COPY .devcontainer/run_all_tests.sh /run_all_tests.sh\n"
        "RUN chmod +x /run_all_tests.sh\n"
    )
    run_script = devcontainer_dir / "run_all_tests.sh"
    run_script.write_text(
        "#!/bin/bash\nmkdir -p /test_artifacts/junit\necho '{\"success\": true}' > /test_artifacts/final_result.json\n"
    )
    run_script.chmod(0o755)

    result = _run_guardrail(workspace)
    assert "WARN" in result.stdout
    assert "COPY . ." in result.stdout


def test_guardrail_checks_executable_permission(workspace: Path) -> None:
    """Guardrail should fail if run_all_tests.sh is not executable."""
    devcontainer_dir = workspace / ".devcontainer"
    devcontainer_dir.mkdir()
    (devcontainer_dir / "devcontainer.json").write_text('{"build": {}}')
    (devcontainer_dir / "Dockerfile").write_text(
        "FROM python:3.12\nRUN mkdir -p /test_artifacts && chmod 777 /test_artifacts\nCOPY .devcontainer/run_all_tests.sh /run_all_tests.sh\nRUN chmod +x /run_all_tests.sh\n"
    )
    run_script = devcontainer_dir / "run_all_tests.sh"
    run_script.write_text(
        "#!/bin/bash\nmkdir -p /test_artifacts/junit\necho '{\"success\": true}' > /test_artifacts/final_result.json\n"
    )
    # Intentionally NOT making it executable
    run_script.chmod(0o644)

    result = _run_guardrail(workspace)
    assert result.returncode != 0
    assert "NOT executable" in result.stdout
