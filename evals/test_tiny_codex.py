"""Integration test: run codex eval on the requests repo.

This test runs the actual eval harness with the codex provider (default model)
against the psf/requests repo. It uses the same configuration as the passing
keystone test (test_e2e_codex_on_modal) — provider=codex with no explicit model,
letting codex CLI pick its default.

Requires Modal credentials, an OpenAI API key, and network access.
Expensive (~$5 + Modal compute) so it's marked as slow and modal.

Usage:
    cd evals
    uv run pytest test_tiny_codex.py -v -s --timeout=2400
"""

import json
import traceback
from pathlib import Path

import pytest
from config import AgentConfig, EvalConfig, EvalRunConfig
from eval_cli import app
from typer.testing import CliRunner

REPOS_JSONL = Path(__file__).parent / "test_data" / "tiny_codex" / "repos.jsonl"


@pytest.mark.slow
@pytest.mark.modal
def test_tiny_codex_eval(tmp_path: Path) -> None:
    """Run the codex eval (default model) on requests repo and check it succeeds."""
    runner = CliRunner()

    output_dir = tmp_path / "output"
    output_dir.mkdir()

    run_config = EvalRunConfig(
        description="Tiny codex smoke test: single repo, codex default model.",
        repo_list_path=str(REPOS_JSONL),
        s3_output_prefix=output_dir.as_uri() + "/",
        s3_repo_cache_prefix=(tmp_path / "repo_cache").as_uri() + "/",
        configs=[
            EvalConfig(
                name="codex-default",
                agent_config=AgentConfig(
                    provider="codex",
                    max_budget_usd=5.0,
                    evaluator=True,
                    timeout_minutes=20,
                ),
                trials_per_repo=1,
            ),
        ],
    )

    config_file = tmp_path / "eval_config.json"
    config_file.write_text(run_config.model_dump_json(indent=2))

    result = runner.invoke(app, ["--config_file", str(config_file), "--no_cache_replay"])

    print("=== CLI OUTPUT ===")
    print(result.output)
    if result.exception:
        traceback.print_exception(
            type(result.exception), result.exception, result.exception.__traceback__
        )

    assert result.exit_code == 0, f"CLI exited with code {result.exit_code}:\n{result.output}"

    # Check output structure
    config_dir = output_dir / "codex-default"
    summary_file = config_dir / "eval_summary.json"
    assert summary_file.exists(), f"Missing eval_summary.json at {summary_file}"

    with summary_file.open() as f:
        summary = json.load(f)

    assert len(summary["results"]) == 1, f"Expected 1 result, got {len(summary['results'])}"
    repo_result = summary["results"][0]
    assert repo_result["success"], (
        f"requests repo should succeed, got error: {repo_result.get('error_message')}"
    )

    # Check devcontainer artifacts were uploaded
    repo_output_dir = config_dir / "requests"
    devcontainer_artifact_dir = repo_output_dir / "devcontainer"
    assert devcontainer_artifact_dir.exists(), (
        f"Devcontainer artifacts not uploaded to {devcontainer_artifact_dir}"
    )

    result_file = repo_output_dir / "eval_result.json"
    assert result_file.exists(), f"Missing eval_result.json at {result_file}"

    print("\n✓ codex (default model): requests repo succeeded")
    print(f"  Output at: {config_dir}")
    if devcontainer_artifact_dir.exists():
        for f in devcontainer_artifact_dir.iterdir():
            print(f"  Artifact: {f.name} ({f.stat().st_size} bytes)")
