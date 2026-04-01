#!/usr/bin/env python3
"""CLI to find and archive eval run directories where success==false."""

from __future__ import annotations

import json

import fsspec
import typer
from rich.console import Console

app = typer.Typer()
console = Console()


def _get_fs(path: str) -> tuple[fsspec.AbstractFileSystem, str]:
    """Return (filesystem, normalized_path) for the given fsspec URI."""
    fs, root = fsspec.core.url_to_fs(path)
    return fs, root


def _find_failed_dirs(fs: fsspec.AbstractFileSystem, root: str) -> list[str]:
    """Walk *root* and return directories containing eval_result.json with success==false."""
    failed: list[str] = []
    # glob for all eval_result.json files under root
    pattern = root.rstrip("/") + "/**/eval_result.json"
    for result_path in fs.glob(pattern):
        assert isinstance(result_path, str)
        try:
            with fs.open(result_path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, FileNotFoundError, OSError) as exc:
            console.print(f"[yellow]⚠ skipping {result_path}: {exc}[/yellow]")
            continue

        if data.get("success") is False:
            # The directory containing eval_result.json
            parent = result_path.rsplit("/", 1)[0]
            failed.append(parent)

    return sorted(failed)


def _protocol_prefix(path: str) -> str:
    """Return the protocol prefix (e.g. 's3://') from the original path, or '' for local."""
    if "://" in path:
        return path.split("://", maxsplit=1)[0] + "://"
    return ""


def _move_tree(fs: fsspec.AbstractFileSystem, src: str, dst: str) -> None:
    """Recursively copy *src* to *dst* then remove *src*.

    fsspec doesn't have a universal rename/move, so we copy + delete.
    For S3 this stays server-side via the copy implementation in s3fs.
    """
    fs.copy(src, dst, recursive=True)
    fs.rm(src, recursive=True)


@app.command()
def main(
    path: str = typer.Argument(help="fsspec root path (file:///... or s3://...)"),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="List directories but don't act"),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip confirmation prompt"),
) -> None:
    """Find eval runs with success==false and archive them."""
    fs, root = _get_fs(path)
    prefix = _protocol_prefix(path)

    console.print(f"[bold]Scanning[/bold] {prefix}{root} …")
    failed = _find_failed_dirs(fs, root)

    if not failed:
        console.print("[green]No failed runs found.[/green]")
        raise SystemExit(0)

    archive_root = root.rstrip("/") + "_failed_run_archive"

    console.print(f"\n[bold red]Found {len(failed)} failed run(s):[/bold red]\n")
    for d in failed:
        rel = d[len(root.rstrip("/")) :]  # e.g. /model/task/trial_0
        console.print(f"  {prefix}{d}")
        console.print(f"    → {prefix}{archive_root}{rel}")

    if dry_run:
        console.print("\n[yellow]Dry run — nothing archived.[/yellow]")
        raise SystemExit(0)

    if not yes:
        confirm = console.input(f"\n[bold]Archive these {len(failed)} directories? [y/N] [/bold]")
        if confirm.strip().lower() not in ("y", "yes"):
            console.print("[yellow]Aborted.[/yellow]")
            raise SystemExit(1)

    for d in failed:
        rel = d[len(root.rstrip("/")) :]
        dest = archive_root + rel
        console.print(f"  [cyan]archiving[/cyan] {prefix}{d} → {prefix}{dest}")
        _move_tree(fs, d, dest)

    console.print(f"\n[green]Archived {len(failed)} failed run(s).[/green]")


if __name__ == "__main__":
    app()
