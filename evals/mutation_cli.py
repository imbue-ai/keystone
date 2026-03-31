"""CLI entry point for the mutation pipeline.

Usage:
    uv run python evals/mutation_cli.py run --repo_list evals/examples/repos.jsonl --s3_prefix s3://…/mutations/
"""

from mutation_flow import cli_app

if __name__ == "__main__":
    cli_app()
