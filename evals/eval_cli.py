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
    clone_dir: Path = typer.Option(
        ...,
        "--clone_dir",
        help="Directory for pristine repo clones (cached)",
    ),
    worktree_dir: Path = typer.Option(
        ...,
        "--worktree_dir",
        help="Directory for repo worktrees",
    ),
    output_path: Path = typer.Option(None, "--output_path", help="Path to write JSON output"),
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
    )

    console.print(f"[bold]Running eval on {repo_list_path}[/bold]")
    console.print(f"  Clone dir: {clone_dir}")
    console.print(f"  Worktree dir: {worktree_dir}")
    console.print(f"  Max budget: ${max_budget_usd}")

    output = eval_flow(
        repo_list_path=str(repo_list_path),
        clone_dir=str(clone_dir),
        worktree_dir=str(worktree_dir),
        eval_config=eval_config,
        output_path=str(output_path) if output_path else None,
        limit=limit,
    )

    # Print summary
    success_count = sum(1 for r in output.results if r.success)
    console.print(f"\n[bold]Results: {success_count}/{len(output.results)} succeeded[/bold]")

    for result in output.results:
        status = "[green]✓[/green]" if result.success else "[red]✗[/red]"
        repo_name = result.repo_entry.repo.split("/")[-1]
        console.print(f"  {status} {repo_name}")
        if not result.success and result.error_message:
            for line in result.error_message.strip().split("\n")[:3]:
                console.print(f"      {line[:100]}")

    if output_path:
        console.print(f"\nOutput written to: {output_path}")


if __name__ == "__main__":
    app()
