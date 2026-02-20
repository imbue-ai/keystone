"""CLI for the eval harness."""

import logging
from pathlib import Path

import typer
from config import AgentConfig, EvalConfig
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


@app.command()
def run(
    repo_list_path: Path = typer.Option(..., "--repo_list_path", help="Path to repo_list.jsonl"),
    s3_output_prefix: str = typer.Option(
        ...,
        "--s3_output_prefix",
        help="S3 prefix for per-repo results (e.g. s3://bucket/evals/2026-02-20/)",
    ),
    s3_repo_cache_prefix: str = typer.Option(
        "s3://int8-datasets/keystone/evals/repo-tarballs/",
        "--s3_repo_cache_prefix",
        help="S3 prefix for cached repo tarballs",
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
    require_cache_hit: bool = typer.Option(False, "--require_cache_hit", help="Fail if cache miss"),
    no_cache_replay: bool = typer.Option(False, "--no_cache_replay", help="Force fresh execution"),
    docker_cache_secret: str = typer.Option(
        "keystone-docker-registry-config",
        "--docker_cache_secret",
        help="Modal secret name for Docker build cache registry credentials",
    ),
    limit: int | None = typer.Option(None, "--limit", help="Limit to first N repos"),
) -> None:
    """Run the eval harness on a list of repos."""
    agent_config = AgentConfig(
        max_budget_usd=max_budget_usd,
        timeout_minutes=timeout_minutes,
        log_db=log_db,
        require_cache_hit=require_cache_hit,
        no_cache_replay=no_cache_replay,
        docker_cache_secret=docker_cache_secret,
    )

    eval_config = EvalConfig(
        agent_config=agent_config,
        max_workers=max_workers,
        s3_output_prefix=s3_output_prefix,
        s3_repo_cache_prefix=s3_repo_cache_prefix,
    )

    console.print(f"[bold]Running eval on {repo_list_path}[/bold]")
    console.print(f"  S3 output: {s3_output_prefix}")
    console.print(f"  S3 repo cache: {s3_repo_cache_prefix}")
    console.print(f"  Max budget: ${max_budget_usd}")

    output = eval_flow(
        repo_list_path=str(repo_list_path),
        eval_config=eval_config,
        limit=limit,
    )

    # Print summary
    success_count = sum(1 for r in output.results if r.success)
    console.print(f"\n[bold]Results: {success_count}/{len(output.results)} succeeded[/bold]")

    for result in output.results:
        status = "[green]✓[/green]" if result.success else "[red]✗[/red]"
        console.print(f"  {status} {result.repo_entry.id}")
        if not result.success and result.error_message:
            for line in result.error_message.strip().split("\n")[:3]:
                console.print(f"      {line[:100]}")

    console.print(f"\nResults uploaded to: {s3_output_prefix}")


if __name__ == "__main__":
    app()
