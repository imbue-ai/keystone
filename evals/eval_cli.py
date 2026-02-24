"""CLI for the eval harness."""

import json
import logging
from pathlib import Path

import typer
from config import AgentConfig, EvalConfig, EvalRunConfig, LLMModel
from flow import eval_flow
from rich.console import Console

DEFAULT_LOG_PATH = Path.home() / ".imbue_keystone" / "log.sqlite"

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)

app = typer.Typer(help="Eval harness for keystone")
console = Console()


def _run_single_eval(
    eval_config: EvalConfig,
    repo_list_path: str,
    limit: int | None,
    config_label: str = "",
) -> None:
    """Run a single eval configuration and print results."""
    label = f" [{config_label}]" if config_label else ""
    console.print(f"\n[bold]Running eval{label}[/bold]")
    console.print(f"  S3 output: {eval_config.s3_output_prefix}")
    console.print(f"  S3 repo cache: {eval_config.s3_repo_cache_prefix}")
    console.print(f"  Provider: {eval_config.agent_config.provider}")
    console.print(f"  Max budget: ${eval_config.agent_config.max_budget_usd}")
    if eval_config.agent_config.model:
        console.print(f"  Model: {eval_config.agent_config.model.value}")
    if eval_config.trials_per_repo > 1:
        console.print(f"  Trials per repo: {eval_config.trials_per_repo}")

    output = eval_flow(
        repo_list_path=repo_list_path,
        eval_config=eval_config,
        limit=limit,
    )

    # Print summary
    success_count = sum(1 for r in output.results if r.success)
    console.print(f"\n[bold]Results{label}: {success_count}/{len(output.results)} succeeded[/bold]")

    for result in output.results:
        status = "[green]✓[/green]" if result.success else "[red]✗[/red]"
        console.print(f"  {status} {result.repo_entry.id}")
        if not result.success and result.error_message:
            for line in result.error_message.strip().split("\n")[:3]:
                console.print(f"      {line[:100]}")

    console.print(f"\nResults uploaded to: {eval_config.s3_output_prefix}")


@app.command()
def run(
    config_file: Path | None = typer.Option(
        None,
        "--config_file",
        help="Path to JSON config file (EvalRunConfig). When provided, most other CLI flags are ignored.",
    ),
    repo_list_path: Path | None = typer.Option(
        None, "--repo_list_path", help="Path to repo_list.jsonl"
    ),
    s3_output_prefix: str | None = typer.Option(
        None,
        "--s3_output_prefix",
        help="S3 prefix for per-repo results (e.g. s3://bucket/evals/2026-02-20/)",
    ),
    s3_repo_cache_prefix: str = typer.Option(
        "s3://int8-datasets/keystone/evals/repo-tarballs/",
        "--s3_repo_cache_prefix",
        help="S3 prefix for cached repo tarballs",
    ),
    provider: str = typer.Option(
        "claude", "--provider", help="LLM provider name (claude or codex)"
    ),
    max_budget_usd: float = typer.Option(
        1.0, "--max_budget_usd", help="Maximum budget per repo in USD"
    ),
    timeout_minutes: int = typer.Option(
        60, "--timeout_minutes", help="Timeout per repo in minutes"
    ),
    log_db: str = typer.Option(
        str(DEFAULT_LOG_PATH), "--log_db", help="Database for logging/caching"
    ),
    max_workers: int = typer.Option(4, "--max_workers", help="Max parallel workers"),
    trials_per_repo: int = typer.Option(
        1, "--trials_per_repo", help="Number of trials per repo (>1 disables caching)"
    ),
    model: LLMModel | None = typer.Option(
        None,
        "--model",
        help="LLM model to use (claude-haiku-4-5-20251001, claude-opus-4-6, gpt-5.1-codex-mini, gpt-5.2-codex)",
    ),
    require_cache_hit: bool = typer.Option(False, "--require_cache_hit", help="Fail if cache miss"),
    no_cache_replay: bool = typer.Option(False, "--no_cache_replay", help="Force fresh execution"),
    docker_cache_secret: str = typer.Option(
        "keystone-docker-registry-config",
        "--docker_cache_secret",
        help="Modal secret name for Docker build cache registry credentials",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Limit to first N repos"),
) -> None:
    """Run the eval harness on a list of repos.

    There are two modes:

    1. **Config file mode** (``--config_file``): Load an ``EvalRunConfig`` JSON
       file that specifies one or more ``EvalConfig`` entries. Each config is
       run sequentially.

    2. **CLI flag mode** (legacy): Build a single ``EvalConfig`` from the
       individual CLI flags.
    """
    if config_file is not None:
        # Config file mode: load and run each config
        raw = json.loads(config_file.read_text())
        run_config = EvalRunConfig(**raw)
        effective_limit = limit if limit is not None else run_config.limit
        for i, eval_cfg in enumerate(run_config.configs):
            label = eval_cfg.name or f"config {i}"
            _run_single_eval(eval_cfg, run_config.repo_list_path, effective_limit, label)
        return

    # Legacy CLI flag mode
    if repo_list_path is None:
        console.print("[red]Error:[/red] --repo_list_path is required (or use --config_file)")
        raise typer.Exit(1)
    if s3_output_prefix is None:
        console.print("[red]Error:[/red] --s3_output_prefix is required (or use --config_file)")
        raise typer.Exit(1)

    agent_config = AgentConfig(
        provider=provider,
        max_budget_usd=max_budget_usd,
        timeout_minutes=timeout_minutes,
        log_db=log_db,
        require_cache_hit=require_cache_hit,
        no_cache_replay=no_cache_replay,
        docker_cache_secret=docker_cache_secret,
        model=model,
    )

    eval_config = EvalConfig(
        agent_config=agent_config,
        max_workers=max_workers,
        trials_per_repo=trials_per_repo,
        s3_output_prefix=s3_output_prefix,
        s3_repo_cache_prefix=s3_repo_cache_prefix,
    )

    _run_single_eval(eval_config, str(repo_list_path), limit)


if __name__ == "__main__":
    app()
