"""CLI for the eval harness."""

import logging
from pathlib import Path

import json5
import typer
from config import EvalConfig, EvalOutput, EvalRunConfig
from flow import eval_flow
from rich.console import Console

# Configure logging: WARNING for third-party, INFO for our code and prefect
logging.basicConfig(
    level=logging.WARNING,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
# Allow INFO from our own modules; DEBUG for prefect task runs so keystone
# stderr lines (logged at DEBUG) appear in the Prefect UI.
for _logger_name in ("flow", "eval_cli", "prefect.flow_runs"):
    logging.getLogger(_logger_name).setLevel(logging.INFO)
logging.getLogger("prefect.task_runs").setLevel(logging.DEBUG)

app = typer.Typer(help="Eval harness for keystone")
console = Console()


def _print_results(outputs: list[EvalOutput], eval_configs: list[EvalConfig]) -> None:
    """Print a summary of results for each eval config."""
    for output, eval_config in zip(outputs, eval_configs, strict=True):
        label = eval_config.name or "unnamed"
        success_count = sum(1 for r in output.results if r.success)
        console.print(
            f"\n[bold]Results [{label}]: {success_count}/{len(output.results)} succeeded[/bold]"
        )

        for result in output.results:
            status = "[green]✓[/green]" if result.success else "[red]✗[/red]"
            console.print(f"  {status} {result.repo_entry.id}")
            if not result.success and result.error_message:
                for line in result.error_message.strip().split("\n")[:3]:
                    console.print(f"      {line[:100]}")

        console.print(f"  Results uploaded to: {eval_config.s3_output_prefix}")


@app.command()
def run(
    config_file: Path = typer.Option(
        ...,
        "--config_file",
        help="Path to JSON config file (EvalRunConfig).",
    ),
    no_cache_replay: bool = typer.Option(False, "--no_cache_replay", help="Force fresh execution"),
    require_cache_hit: bool = typer.Option(False, "--require_cache_hit", help="Fail if cache miss"),
    no_evaluator: bool = typer.Option(False, "--no_evaluator", help="Skip LLM evaluator"),
    no_guardrail: bool = typer.Option(False, "--no_guardrail", help="Skip guardrail checks"),
    limit: int | None = typer.Option(None, "--limit", help="Limit to first N repos"),
) -> None:
    """Run the eval harness on a list of repos.

    Provide ``--config_file`` pointing to an ``EvalRunConfig`` JSON file.
    CLI flags ``--no_cache_replay``, ``--require_cache_hit``, and ``--limit``
    are applied as overrides on top of the config file.
    """
    config_path = str(config_file)
    if config_path.startswith("s3://"):
        import fsspec

        with fsspec.open(config_path, "r") as f:
            raw: dict = json5.loads(f.read())  # type: ignore[assignment]
    else:
        raw: dict = json5.loads(config_file.read_text())  # type: ignore[assignment]
    run_config = EvalRunConfig(**raw)

    effective_limit = limit if limit is not None else run_config.limit

    resolved_configs = [
        run_config.resolve_config(cfg, i) for i, cfg in enumerate(run_config.configs)
    ]

    # Apply CLI overrides to all configs
    if no_cache_replay:
        for cfg in resolved_configs:
            cfg.agent_config = cfg.agent_config.model_copy(update={"no_cache_replay": True})
    if require_cache_hit:
        for cfg in resolved_configs:
            cfg.agent_config = cfg.agent_config.model_copy(update={"require_cache_hit": True})
    if no_evaluator:
        for cfg in resolved_configs:
            cfg.agent_config = cfg.agent_config.model_copy(update={"evaluator": False})
    if no_guardrail:
        for cfg in resolved_configs:
            cfg.agent_config = cfg.agent_config.model_copy(update={"no_guardrail": True})

    # Print plan
    console.print(f"\n[bold]Eval run: {len(resolved_configs)} configs[/bold]")
    console.print(f"  Description: {run_config.description}")
    console.print(f"  Repos: {run_config.repo_list_path}")
    console.print(f"  S3 output: {run_config.s3_output_prefix}")
    console.print(f"  S3 repo cache: {run_config.s3_repo_cache_prefix}")
    for cfg in resolved_configs:
        console.print(
            f"  - {cfg.name}: provider={cfg.agent_config.provider}, "
            f"model={cfg.agent_config.model.value if cfg.agent_config.model else 'default'}"
        )

    outputs = eval_flow(
        repo_list_path=run_config.repo_list_path,
        eval_configs=resolved_configs,
        s3_repo_cache_prefix=run_config.s3_repo_cache_prefix,
        limit=effective_limit,
    )

    _print_results(outputs, resolved_configs)


if __name__ == "__main__":
    app()
