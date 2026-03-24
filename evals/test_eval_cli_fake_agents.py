"""Test eval CLI with fake agents (claude + codex) and multiple models.

Runs 4 eval configurations using fake agents locally (no Modal, no LLM).
Each config uses a different agent/model combination:
  1. fake_claude_agent + sonnet
  2. fake_claude_agent + haiku
  3. fake_codex_agent + gpt-5-codex
  4. fake_codex_agent + o3

Only python_project is expected to succeed with the fake agents.
"""

import json
import shutil
import subprocess
import traceback
from pathlib import Path

import pytest
from eval_cli import app
from eval_schema import EvalConfig, EvalRunConfig
from typer.testing import CliRunner

from keystone.schema import AgentConfig, KeystoneConfig, LLMModel

SAMPLES_DIR = Path(__file__).parent.parent / "samples"
# On Modal, fake agents are baked into the image at /usr/local/bin/
FAKE_CLAUDE_AGENT_CMD = "fake_claude_agent.py"
FAKE_CODEX_AGENT_CMD = "fake_codex_agent.py"


def init_git_repo(path: Path) -> None:
    """Initialize a git repository with test config."""
    subprocess.run(["git", "init"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Test"],
        cwd=path,
        capture_output=True,
        check=True,
    )
    subprocess.run(["git", "add", "-A"], cwd=path, capture_output=True, check=True)
    subprocess.run(
        ["git", "commit", "-m", "Initial commit"],
        cwd=path,
        capture_output=True,
        check=True,
    )


@pytest.fixture
def sample_repo(tmp_path: Path) -> tuple[Path, list[str]]:
    """Create a git repo from samples/python_project and return (repo_list_path, repo_paths).

    Uses only python_project to keep the test fast.
    """
    repos_dir = tmp_path / "repos"
    repos_dir.mkdir()
    repo_paths: list[str] = []

    src = SAMPLES_DIR / "python_project"
    if not src.exists():
        pytest.skip(f"Sample not found: {src}")

    dest = repos_dir / "python_project"
    shutil.copytree(src, dest)
    init_git_repo(dest)
    repo_paths.append(str(dest))

    # Write repo list JSONL (with commit hashes for reproducibility)
    repo_list_path = tmp_path / "repos.jsonl"
    with repo_list_path.open("w") as f:
        for path in repo_paths:
            repo_id = Path(path).name
            commit_hash = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=path,
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            f.write(json.dumps({"id": repo_id, "repo": path, "commit_hash": commit_hash}) + "\n")

    return repo_list_path, repo_paths


# The 4 agent/model/provider combinations to test
# (name, agent_cmd, provider, model, claude_reasoning_level, codex_reasoning_level)
FAKE_AGENT_CONFIGS = [
    ("claude-haiku", FAKE_CLAUDE_AGENT_CMD, "claude", LLMModel.HAIKU, "medium", None),
    ("claude-opus", FAKE_CLAUDE_AGENT_CMD, "claude", LLMModel.OPUS, "medium", None),
    ("codex-mini", FAKE_CODEX_AGENT_CMD, "codex", LLMModel.CODEX_MINI, None, "high"),
    ("codex", FAKE_CODEX_AGENT_CMD, "codex", LLMModel.CODEX, None, "high"),
]


@pytest.mark.modal
def test_eval_cli_fake_agents_config_file(
    sample_repo: tuple[Path, list[str]],
    tmp_path: Path,
) -> None:
    """Run 4 fake agent evals via eval_cli.py using --config_file mode.

    Uses the Typer CliRunner to invoke the eval CLI with an EvalRunConfig
    JSON file that defines 4 configurations (2 agents x 2 models).
    Each runs on Modal against python_project.
    """
    repo_list_path, _repo_paths = sample_repo
    runner = CliRunner()

    # Global output dirs (shared repo cache, per-config output subdirs)
    s3_output_dir = tmp_path / "s3_output"
    s3_cache_dir = tmp_path / "s3_cache"
    s3_output_dir.mkdir()
    s3_cache_dir.mkdir()

    # Build 4 EvalConfig entries (s3 prefixes resolved from globals)
    configs: list[EvalConfig] = []
    for name, agent_cmd, provider, model, claude_rl, codex_rl in FAKE_AGENT_CONFIGS:
        configs.append(
            EvalConfig(
                name=name,
                keystone_config=KeystoneConfig(
                    agent_config=AgentConfig(
                        max_budget_usd=1.0,
                        agent_time_limit_seconds=60,
                        cost_poll_interval_seconds=30,
                        agent_in_modal=True,
                        agent_cmd=agent_cmd,
                        model=model,
                        provider=provider,
                        claude_reasoning_level=claude_rl,
                        codex_reasoning_level=codex_rl,
                        guardrail=False,
                        use_agents_md=True,
                    ),
                ),
            )
        )

    # Write the EvalRunConfig JSON
    run_config = EvalRunConfig(
        description="Fake agent integration test: 4 configs x 1 repo.",
        repo_list_path=str(repo_list_path),
        s3_output_prefix=s3_output_dir.as_uri() + "/",
        s3_repo_cache_prefix=s3_cache_dir.as_uri() + "/",
        configs=configs,
    )
    config_file = tmp_path / "eval_config.json"
    config_file.write_text(run_config.model_dump_json(indent=2))

    # Invoke the CLI
    result = runner.invoke(app, ["--config_file", str(config_file)])

    print("=== CLI OUTPUT ===")
    print(result.output)
    if result.exception:
        traceback.print_exception(
            type(result.exception), result.exception, result.exception.__traceback__
        )

    assert result.exit_code == 0, f"CLI exited with code {result.exit_code}:\n{result.output}"

    # Verify we got 4 distinct output subdirectories (one per config name)
    config_output_dirs = [s3_output_dir / name for name, _, _, _, _, _ in FAKE_AGENT_CONFIGS]
    assert len(set(config_output_dirs)) == len(FAKE_AGENT_CONFIGS), (
        "Output directories should all be distinct"
    )

    # Verify each configuration produced results with the correct model embedded
    for name, _agent_cmd, _provider, model, _claude_rl, _codex_rl in FAKE_AGENT_CONFIGS:
        config_dir = s3_output_dir / name

        # Check eval_summary.json was written
        summary_file = config_dir / "eval_summary.json"
        assert summary_file.exists(), f"Missing eval_summary.json for {name}"

        with summary_file.open() as f:
            summary = json.load(f)

        assert len(summary["results"]) == 1, f"Expected 1 result for {name}"
        repo_result = summary["results"][0]

        # python_project should succeed with fake agents
        assert repo_result["success"], (
            f"{name}: python_project should succeed, got error: {repo_result.get('error_message')}"
        )

        # Verify per-repo result file exists (path always includes trial_0 subdirectory)
        repo_output_dir = config_dir / "python_project" / "trial_0"
        result_file = repo_output_dir / "eval_result.json"
        assert result_file.exists(), f"Missing eval_result.json for {name}/python_project"

        # Verify the model name is embedded in the eval_result (bootstrap_result
        # captures the keystone JSON output which includes the Dockerfile content
        # and agent status messages containing the model label).
        result_data = json.loads(result_file.read_text())
        result_text = json.dumps(result_data)
        assert model.value in result_text, (
            f"{name}: expected model '{model.value}' to appear in eval_result.json"
        )

        print(f"\n✓ {name} (model={model.value}): python_project succeeded, model found in output")
