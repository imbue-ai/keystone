"""CLI for the eval harness."""
import json
import tempfile
from pathlib import Path
from typing import Optional

import json5
import typer
from rich.console import Console

from config import AgentConfig, EvalConfig
from flow import create_tarball_from_dir, eval_flow, eval_local_tarball_flow

app = typer.Typer(help="Eval harness for bootstrap_devcontainer")
console = Console()


@app.command()
def run(
    agent_config_path: Path = typer.Argument(..., help="Path to agent_config.json5"),
    repo_list_path: Path = typer.Argument(..., help="Path to repo_list.jsonl"),
    output_dir: Path = typer.Option(
        Path("./eval_output"),
        "--output-dir", "-o",
        help="Output directory for results"
    ),
    execution_mode: str = typer.Option(
        "local",
        "--mode", "-m",
        help="Execution mode: 'local' or 'modal'"
    ),
):
    """Run the eval harness on a list of repos."""
    # Load agent config
    with open(agent_config_path) as f:
        agent_config_dict = json5.load(f)
    
    agent_config = AgentConfig(**agent_config_dict)
    
    eval_config = EvalConfig(
        agent_config=agent_config,
        execution_mode=execution_mode,
    )
    
    console.print(f"[bold]Running eval with {execution_mode} mode[/bold]")
    console.print(f"Agent config: {agent_config}")
    
    results = eval_flow(
        repo_list_path=str(repo_list_path),
        eval_config=eval_config,
        output_dir=str(output_dir),
    )
    
    # Print summary
    success_count = sum(1 for r in results if r.success)
    console.print(f"\n[bold]Results: {success_count}/{len(results)} succeeded[/bold]")
    
    for i, result in enumerate(results):
        status = "[green]✓[/green]" if result.success else "[red]✗[/red]"
        console.print(f"  {status} {result.s3_repo_tarball}")
        if not result.success and result.error_message:
            console.print(f"    Error: {result.error_message[:100]}")


@app.command()
def test_local(
    source_dir: Path = typer.Argument(..., help="Path to source directory to test"),
    output_dir: Optional[Path] = typer.Option(
        None,
        "--output-dir", "-o",
        help="Output directory for results"
    ),
    max_budget_usd: float = typer.Option(
        1.0,
        "--max-budget-usd",
        help="Maximum budget in USD"
    ),
    use_cache: bool = typer.Option(
        True,
        "--use-cache/--no-cache",
        help="Whether to use result caching"
    ),
):
    """Test the eval harness with a local source directory.
    
    Creates a tarball from the source directory and runs the eval.
    """
    source_dir = source_dir.resolve()
    
    if output_dir is None:
        output_dir = Path(tempfile.mkdtemp(prefix="eval_test_"))
    else:
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
    
    console.print(f"[bold]Testing with source: {source_dir}[/bold]")
    console.print(f"Output directory: {output_dir}")
    
    # Create tarball
    tarball_path = output_dir / f"{source_dir.name}.tar.gz"
    create_tarball_from_dir(source_dir, tarball_path)
    console.print(f"Created tarball: {tarball_path}")
    
    # Run eval
    agent_config = AgentConfig(
        max_budget_usd=max_budget_usd,
        use_cache=use_cache,
    )
    
    result = eval_local_tarball_flow(
        tarball_path=str(tarball_path),
        agent_config=agent_config,
        output_dir=str(output_dir / "result"),
    )
    
    # Print result
    if result.success:
        console.print("\n[bold green]SUCCESS[/bold green]")
        if result.bootstrap_result:
            console.print(f"Bootstrap result: {json.dumps(result.bootstrap_result, indent=2)}")
    else:
        console.print("\n[bold red]FAILED[/bold red]")
        console.print(f"Error: {result.error_message}")
    
    console.print(f"\nOutputs in: {output_dir}")
    
    return result


if __name__ == "__main__":
    app()
