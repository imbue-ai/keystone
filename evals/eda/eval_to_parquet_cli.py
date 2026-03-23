"""CLI to package eval_result.json files into a single Parquet file.

Scans an fsspec-compatible directory (local or S3) for eval_result.json files,
validates each as a KeystoneRepoResult, and writes a flat Parquet file.

Usage examples:
    # S3 input, local output
    uv run python evals/eda/eval_to_parquet_cli.py \
        s3://int8-datasets/keystone/evals/2026-03-08_run \
        ./results.parquet

    # S3 input, S3 output
    uv run python evals/eda/eval_to_parquet_cli.py \
        s3://bucket/evals/run1 \
        s3://bucket/evals/run1/results.parquet

    # Local input, local output
    uv run python evals/eda/eval_to_parquet_cli.py \
        /tmp/eval_output \
        /tmp/results.parquet
"""

import json
import sys
from pathlib import Path

import fsspec
import pandas as pd
import typer
from pydantic import ValidationError
from rich.console import Console
from tqdm import tqdm

# Ensure the project root is importable.
_project_root = str(Path(__file__).resolve().parents[2])
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from eval_schema import KeystoneRepoResult  # noqa: E402

app = typer.Typer(help="Package eval_result.json files into Parquet.")
console = Console()


def _deduped_test_names(ver: object | None) -> dict[str, bool] | None:
    """Build {test_name: ever_passed} from verification test results, deduplicating by name.

    Some agents run the test suite more than once, causing every test name to
    appear multiple times.  We collapse duplicates and use logical-OR for the
    passed flag so that a test counts as passed if *any* execution passed.
    """
    if ver is None:
        return None
    tr = ver.test_results  # type: ignore[union-attr]
    if not tr:
        return None
    seen: dict[str, bool] = {}
    for t in tr:
        # Normalize: strip trailing "()" so Java test names like
        # "contextLoads" and "contextLoads()" collapse to one entry.
        name = t.name[:-2] if t.name.endswith("()") else t.name
        seen[name] = seen.get(name, False) or t.passed
    return seen


def _deduped_discovered(ver: object | None) -> int | None:
    """Count of unique test names (passed + failed)."""
    seen = _deduped_test_names(ver)
    return len(seen) if seen is not None else None


def _deduped_passed(ver: object | None) -> int | None:
    """Count of unique test names where any execution passed."""
    seen = _deduped_test_names(ver)
    return sum(1 for v in seen.values() if v) if seen is not None else None


def _deduped_failed(ver: object | None) -> int | None:
    """Count of unique test names where no execution passed."""
    seen = _deduped_test_names(ver)
    return sum(1 for v in seen.values() if not v) if seen is not None else None


def _build_record(r: KeystoneRepoResult, source_path: str) -> dict:
    """Flatten a KeystoneRepoResult into a dict suitable for a DataFrame row."""
    br = r.bootstrap_result
    agent = br.agent if br else None
    ver = br.verification if br else None
    cost = agent.cost if agent else None

    config_name = r.eval_config.name if r.eval_config else None

    status_messages: list[dict] = []
    if agent and agent.status_messages:
        status_messages = [sm.model_dump() for sm in agent.status_messages]

    return {
        "source_path": source_path,
        "raw_json": r.model_dump_json(),
        "config_name": config_name,
        "repo_id": r.repo_entry.id,
        "trial_index": r.trial_index,
        "success": r.success,
        "error_message": r.error_message,
        "agent_exit_code": agent.exit_code if agent else None,
        "agent_walltime_seconds": agent.duration_seconds if agent else None,
        "agent_timed_out": agent.timed_out if agent else None,
        "cost_usd": cost.cost_usd if cost else None,
        "input_tokens": cost.token_spending.input if cost else None,
        "output_tokens": cost.token_spending.output if cost else None,
        "image_build_seconds": ver.image_build_seconds if ver else None,
        "test_execution_seconds": ver.test_execution_seconds if ver else None,
        "tests_passed": _deduped_passed(ver),
        "tests_failed": _deduped_failed(ver),
        "tests_discovered": _deduped_discovered(ver),
        "summary": agent.summary.message if agent and agent.summary else None,
        "status_messages": json.dumps(status_messages),
    }


@app.command()
def main(
    input_path: str = typer.Argument(
        help="fsspec-compatible base directory to scan for eval_result.json files."
    ),
    output_path: str = typer.Argument(help="fsspec-compatible path for the output Parquet file."),
) -> None:
    """Scan INPUT_PATH for eval_result.json files and write OUTPUT_PATH as Parquet."""
    # --- discover files ---
    in_fs, in_prefix = fsspec.core.url_to_fs(input_path)
    in_prefix = in_prefix.rstrip("/")
    json_paths: list[str] = in_fs.glob(f"{in_prefix}/**/eval_result.json")

    if not json_paths:
        console.print(f"[red]No eval_result.json files found under {input_path}[/red]")
        raise typer.Exit(code=1)

    console.print(f"Found [bold]{len(json_paths)}[/bold] eval_result.json files")

    # --- load and validate ---
    records: list[dict] = []
    errors: list[dict] = []
    for path in tqdm(json_paths, desc="Loading JSON files"):
        with in_fs.open(path, "r") as f:
            raw = json.load(f)
        try:
            result = KeystoneRepoResult(**raw)
        except ValidationError as e:
            errors.append({"path": path, "error": str(e)})
            continue
        records.append(_build_record(result, source_path=path))

    console.print(
        f"Validated [bold green]{len(records)}[/bold green] results, "
        f"[bold red]{len(errors)}[/bold red] validation errors"
    )
    for err in errors[:5]:
        console.print(f"  [red]FAIL[/red]: {err['path']}")
        console.print(f"        {err['error'][:200]}")

    if not records:
        console.print("[red]No valid results to write.[/red]")
        raise typer.Exit(code=1)

    # --- write parquet ---
    df = pd.DataFrame(records)
    out_fs, out_prefix = fsspec.core.url_to_fs(output_path)
    with out_fs.open(out_prefix, "wb") as f:
        df.to_parquet(f, index=False)

    console.print(f"Wrote [bold]{len(df)}[/bold] rows ({len(df.columns)} columns) to {output_path}")


if __name__ == "__main__":
    app()
