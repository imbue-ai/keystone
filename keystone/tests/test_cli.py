"""Tests for CLI entry point and version resolution (no markers — all fast)."""

import json
import logging
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from conftest import init_git_repo
from typer.testing import CliRunner

from keystone.keystone_cli import app
from keystone.process_runner import run_process
from keystone.schema import BootstrapResult
from keystone.version import _UNKNOWN_VERSION, get_version_info

logger = logging.getLogger(__name__)


def test_cli_help() -> None:
    result = CliRunner().invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "[OPTIONS]" in result.stdout
    assert "--project_root" in result.stdout


@pytest.mark.manual
def test_cli_help_runs_from_uvx() -> None:
    """Test that the CLI can be installed and invoked via uvx from the public repo.

    The uvx command tested here is the one documented in the README:
        uvx --from 'git+https://github.com/imbue-ai/keystone@prod' keystone --help

    Marked manual because it requires network access and installs from git.
    """
    result = run_process(
        [
            "uvx",
            "--from",
            "git+https://github.com/imbue-ai/keystone@prod",
            "keystone",
            "--help",
        ],
        log_prefix="[uvx run]",
    )
    assert result.returncode == 0, f"uvx invocation failed:\n{result.stderr}"
    assert "--project_root" in result.stdout


def test_get_version_info_without_git(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """When CWD is not a git repo and no stamp file exists, get_version_info returns a fallback.

    This simulates the uvx-from-github scenario where the process runs outside
    any git repository and there is no baked-in version_stamp.json.
    """
    # Clear the @cache so we get a fresh call
    get_version_info.cache_clear()

    # Run from a directory that is not a git repo
    monkeypatch.chdir(tmp_path)

    result = get_version_info()
    assert result == _UNKNOWN_VERSION
    assert result.git_hash is None
    assert result.branch is None
    assert result.commit_timestamp is None

    # Clean up cache so other tests aren't affected
    get_version_info.cache_clear()


def test_cli_does_not_crash_in_empty_git_repo(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The CLI must not crash when --project_root is an empty git repo (no commits).

    This reproduces the bug seen when running via uvx:
        uvx --from 'git+https://github.com/imbue-ai/keystone@prod' keystone \\
            --project_root <empty-repo> --test_artifacts_dir <dir>

    In that scenario the CWD may be inside a git repo with no commits, causing
    ``git rev-parse HEAD`` to fail with exit code 128.  The version fallback
    chain must catch this and continue.
    """
    # Create an empty git repo (init but no commits)
    empty_repo = tmp_path / "empty_git_repo"
    empty_repo.mkdir()
    init_git_repo(empty_repo, add_all=False, commit=False)

    test_artifacts_dir = tmp_path / "test_artifacts"

    # Simulate uvx environment: CWD is the empty repo, no stamp file
    monkeypatch.chdir(empty_repo)
    get_version_info.cache_clear()

    try:
        cmd = [
            "--project_root",
            str(empty_repo),
            "--test_artifacts_dir",
            str(test_artifacts_dir),
            "--max_budget_usd",
            "0",
            "--run_agent_locally_with_dangerously_skip_permissions",
        ]
        result = CliRunner().invoke(app, cmd)

        # The CLI will fail because the empty repo has no devcontainer config etc.,
        # but it must NOT crash with CalledProcessError from git rev-parse HEAD.
        # Exit code 1 (controlled failure) is fine; a traceback with
        # CalledProcessError from version.py is not.
        assert "CalledProcessError" not in (result.stdout + (result.stderr or "")), (
            f"CLI crashed with CalledProcessError (version resolution bug):\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
    finally:
        get_version_info.cache_clear()


def test_get_version_info_from_direct_url(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate uvx installing from a git URL: no local git, but PEP 610 metadata exists.

    uv/pip write a direct_url.json into .dist-info with the resolved commit_id
    when installing from ``git+https://…@branch``.  We verify that
    get_version_info picks up the hash and branch from that metadata.
    """
    get_version_info.cache_clear()
    monkeypatch.chdir(tmp_path)

    fake_direct_url = json.dumps(
        {
            "url": "https://github.com/imbue-ai/keystone.git",
            "vcs_info": {
                "vcs": "git",
                "requested_revision": "prod",
                "commit_id": "4dba85a1880214f687d1779e75d06440cc4bb9ef",
            },
        }
    )

    mock_dist = MagicMock()
    mock_dist.read_text.return_value = fake_direct_url

    with patch("keystone.version.importlib.metadata.distribution", return_value=mock_dist):
        result = get_version_info()

    assert result.git_hash == "4dba85a1880214f687d1779e75d06440cc4bb9ef"
    assert result.branch == "prod"
    assert result.is_dirty is False
    assert result.commit_timestamp is None

    get_version_info.cache_clear()


def test_max_budget_zero_fails(tmp_path: Path, project_root: Path) -> None:
    """Test that setting --max_budget_usd 0 causes the claude agent to fail immediately.

    The CLI should return a non-zero exit code and include an error_message in
    the JSON output.
    """
    test_artifacts_dir = tmp_path / "test_artifacts"

    logger.info("=" * 60)
    logger.info("Testing max-budget-usd=0 causes failure")
    logger.info("Project root: %s", project_root)
    logger.info("=" * 60)

    cmd = [
        "--project_root",
        str(project_root),
        "--test_artifacts_dir",
        str(test_artifacts_dir),
        "--max_budget_usd",
        "0",
        "--run_agent_locally_with_dangerously_skip_permissions",
    ]

    logger.info("Running: keystone %s", " ".join(cmd))

    result = CliRunner().invoke(app, cmd)

    logger.info("Exit code: %s", result.exit_code)

    # CLI should return non-zero exit code on failure
    assert result.exit_code != 0, "Expected non-zero exit code with zero budget"

    # Parse JSON output - should still be present even on failure
    stdout_lines = result.stdout.strip().split("\n")
    json_start = None
    for i, line in enumerate(stdout_lines):
        if line.strip() == "{":
            json_start = i
            break

    assert json_start is not None, "Expected JSON output even on failure"
    json_str = "\n".join(stdout_lines[json_start:])
    output = BootstrapResult.model_validate_json(json_str)

    assert not output.success, "Expected success=false with zero budget"
    assert output.error_message, "Expected error_message in output"
