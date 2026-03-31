"""Mutation pipeline: generate broken-commit branches for eval integrity checking.

Phase 1 of the mutation-augmented eval pipeline. For each repo in a JSONL list,
spawns a Claude Code agent in a Modal sandbox to introduce N small test-breaking
commits. Each commit is stored as a branch (broken-1…broken-N) in a bare git
tarball uploaded to S3.
"""

import contextlib
import json
import logging
import subprocess
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

import fsspec
import modal
import typer
from eval_schema import RepoEntry
from pydantic import BaseModel, Field

from keystone.modal.image import create_modal_image

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Config / Result models
# ---------------------------------------------------------------------------


class MutationRunConfig(BaseModel):
    """Configuration for a mutation pipeline run."""

    repo_list_path: str
    s3_output_prefix: str
    n_mutations: int = 5
    modal_timeout_seconds: int = 600


class MutationResult(BaseModel):
    """Result of mutating a single repo."""

    repo_id: str
    broken_commit_hashes: list[str] = Field(default_factory=list)
    s3_tarball_path: str = ""
    warnings: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Prompt
# ---------------------------------------------------------------------------

MUTATION_PROMPT_TEMPLATE = """\
You are a code mutation agent. Your job is to introduce {n} small, independent
test-breaking changes to the source code in /project.

**Context:**
- Language: {language}
- Build system: {build_system}
- Tests: {tests}
- Notes: {notes}

**Rules:**
1. Only modify SOURCE files — never modify test files.
2. Each change should be small, obvious, and self-contained (e.g., insert
   `raise AssertionError("mutation")` at the start of a function, change a
   return value, flip a comparison operator).
3. For each mutation, create a branch off the current HEAD:
   - `git checkout -b broken-{{i}}` (where i = 1..{n})
   - Make the change, `git add -A`, `git commit -m "mutation {{i}}"`
   - `git checkout main` (return to base before the next mutation)
4. Do NOT chain mutations — each broken-{{i}} branch should diverge from the
   same base commit (main/HEAD).
5. Explore the repo first to find appropriate source files to mutate. Target
   functions that are exercised by tests.
6. You do NOT have a working build environment. Just make your best guess at
   which source changes will break tests.

After creating all branches, run: `git branch -v`
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_git(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    """Run a git command and return the result."""
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _load_repos(repo_list_path: str, limit: int | None = None) -> list[RepoEntry]:
    """Load and validate repo entries from a JSONL file."""
    repos: list[RepoEntry] = []
    with Path(repo_list_path).open() as f:
        for line_str in f:
            line_str = line_str.strip()
            if line_str:
                repos.append(RepoEntry(**json.loads(line_str)))

    ids = [r.id for r in repos]
    if len(ids) != len(set(ids)):
        dupes = [k for k, v in Counter(ids).items() if v > 1]
        raise ValueError(f"Duplicate repo IDs found: {dupes}")

    if limit is not None:
        repos = repos[:limit]
    return repos


# ---------------------------------------------------------------------------
# S3 helpers (thin wrappers around fsspec)
# ---------------------------------------------------------------------------


def _s3_write_bytes(path: str, data: bytes) -> None:
    with fsspec.open(path, "wb") as f:
        f.write(data)


# ---------------------------------------------------------------------------
# Core mutation task
# ---------------------------------------------------------------------------


def mutate_repo_task(
    repo_entry: RepoEntry,
    s3_prefix: str,
    n: int,
    modal_timeout_seconds: int,
) -> MutationResult:
    """Clone a repo, run Claude Code to create broken branches, package and upload."""
    repo_id = repo_entry.id
    result = MutationResult(repo_id=repo_id)

    with tempfile.TemporaryDirectory() as tmp_dir:
        clone_path = Path(tmp_dir) / repo_id
        log.info("[%s] Cloning %s at %s...", repo_id, repo_entry.repo, repo_entry.commit_hash[:12])

        # Clone and checkout pinned commit
        _run_git(["clone", "--recurse-submodules", repo_entry.repo, str(clone_path)])
        _run_git(["checkout", repo_entry.commit_hash], cwd=clone_path)
        _run_git(["submodule", "update", "--recursive"], cwd=clone_path)

        # Ensure we have a 'main' branch at the base commit
        try:
            _run_git(["branch", "-M", "main"], cwd=clone_path)
        except subprocess.CalledProcessError:
            _run_git(["checkout", "-b", "main"], cwd=clone_path)

        # Create Modal sandbox and run Claude Code
        prompt = MUTATION_PROMPT_TEMPLATE.format(
            n=n,
            language=repo_entry.language or "unknown",
            build_system=repo_entry.build_system or "unknown",
            tests=repo_entry.tests or "unknown",
            notes=repo_entry.notes or "",
        )

        try:
            broken_hashes = _run_mutation_in_modal(clone_path, prompt, n, modal_timeout_seconds)
            result.broken_commit_hashes = broken_hashes
            if len(broken_hashes) < n:
                result.warnings.append(f"Only {len(broken_hashes)}/{n} broken branches created")
        except Exception as e:
            log.error("[%s] Mutation failed: %s", repo_id, e)
            result.warnings.append(f"Mutation failed: {e}")
            return result

        if not result.broken_commit_hashes:
            result.warnings.append("No broken commits produced")
            return result

        # Package as bare git tarball
        bare_path = Path(tmp_dir) / f"{repo_id}.git"
        _run_git(["clone", "--bare", str(clone_path), str(bare_path)])

        tarball_path = Path(tmp_dir) / f"{repo_id}.tar.gz"
        subprocess.run(
            ["tar", "-czf", str(tarball_path), "-C", str(bare_path.parent), bare_path.name],
            check=True,
        )

        # Upload to S3
        s3_tarball_path = f"{s3_prefix.rstrip('/')}/{repo_id}.tar.gz"
        log.info("[%s] Uploading tarball to %s", repo_id, s3_tarball_path)
        _s3_write_bytes(s3_tarball_path, tarball_path.read_bytes())
        result.s3_tarball_path = s3_tarball_path

    return result


def _run_mutation_in_modal(
    clone_path: Path,
    prompt: str,
    n: int,
    timeout_seconds: int,
) -> list[str]:
    """Upload repo to Modal sandbox, run Claude Code, extract broken branch hashes."""
    modal.enable_output()
    app = modal.App.lookup("keystone-mutation", create_if_missing=True)
    image = create_modal_image()

    sb = modal.Sandbox.create(
        app=app,
        image=image,
        timeout=timeout_seconds + 120,  # buffer for setup
    )

    try:
        # Upload repo as tarball
        tarball_data = subprocess.run(
            ["tar", "-czf", "-", "-C", str(clone_path), "."],
            capture_output=True,
            check=True,
        ).stdout

        with sb.open("/tmp/project.tar.gz", "wb") as f:
            f.write(tarball_data)

        sb.exec("mkdir", "-p", "/project").wait()
        sb.exec("tar", "-xzf", "/tmp/project.tar.gz", "-C", "/project").wait()

        # Configure git
        sb.exec("git", "-C", "/project", "config", "user.name", "mutation-agent").wait()
        sb.exec("git", "-C", "/project", "config", "user.email", "mutation@eval").wait()

        # Run Claude Code with the mutation prompt
        proc = sb.exec(
            "timeout",
            str(timeout_seconds),
            "claude",
            "-p",
            prompt,
            "--allowedTools",
            "Bash,Read,Write,Edit",
            cwd="/project",
        )
        proc.wait()

        # Read back branch hashes
        branch_proc = sb.exec("git", "-C", "/project", "branch", "-v")
        branch_output_lines: list[str] = []
        for chunk in branch_proc.stdout:
            branch_output_lines.append(chunk)
        branch_proc.wait()
        broken_hashes: list[str] = []
        for i in range(1, n + 1):
            branch_name = f"broken-{i}"
            # Get the commit hash for this branch
            hash_proc = sb.exec("git", "-C", "/project", "rev-parse", f"refs/heads/{branch_name}")
            hash_lines: list[str] = []
            for chunk in hash_proc.stdout:
                hash_lines.append(chunk)
            exit_code = hash_proc.wait()
            if exit_code == 0:
                commit_hash = "".join(hash_lines).strip()
                if commit_hash:
                    broken_hashes.append(commit_hash)
            else:
                log.warning("Branch %s not found", branch_name)

        # Pull the mutated repo back
        sb.exec("tar", "-czf", "/tmp/mutated.tar.gz", "-C", "/project", ".").wait()
        with sb.open("/tmp/mutated.tar.gz", "rb") as f:
            mutated_data = f.read()

        # Extract mutated repo over the clone to get the branches locally
        subprocess.run(
            ["tar", "-xzf", "-", "-C", str(clone_path)],
            input=mutated_data,
            check=True,
        )

        return broken_hashes

    finally:
        with contextlib.suppress(Exception):
            sb.terminate()


# ---------------------------------------------------------------------------
# Flow: orchestrate mutation across all repos
# ---------------------------------------------------------------------------


def mutation_flow(
    repo_list_path: str,
    s3_prefix: str,
    n_mutations: int = 5,
    limit: int | None = None,
    modal_timeout_seconds: int = 600,
) -> list[MutationResult]:
    """Run mutation pipeline for all repos, write amended JSONL."""
    repos = _load_repos(repo_list_path, limit)
    log.info("Loaded %d repos from %s", len(repos), repo_list_path)

    # Run mutations (sequential for now; can be parallelized later)
    results: list[MutationResult] = []
    for repo_entry in repos:
        result = mutate_repo_task(repo_entry, s3_prefix, n_mutations, modal_timeout_seconds)
        results.append(result)

    # Write amended JSONL with broken_commit_hashes
    output_path = Path("evals/examples/repos_with_mutations.jsonl")
    output_path.parent.mkdir(parents=True, exist_ok=True)

    result_map: dict[str, MutationResult] = {r.repo_id: r for r in results}
    with output_path.open("w") as f:
        for repo_entry in repos:
            data: dict[str, Any] = repo_entry.model_dump()
            mutation = result_map.get(repo_entry.id)
            if mutation:
                data["broken_commit_hashes"] = mutation.broken_commit_hashes
            f.write(json.dumps(data) + "\n")

    log.info("Wrote %s with %d entries", output_path, len(repos))

    # Print summary
    total_mutations = sum(len(r.broken_commit_hashes) for r in results)
    total_warnings = sum(len(r.warnings) for r in results)
    log.info(
        "Mutation complete: %d repos, %d total broken commits, %d warnings",
        len(results),
        total_mutations,
        total_warnings,
    )

    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

cli_app = typer.Typer(help="Mutation pipeline for eval integrity checking.")


@cli_app.command()
def run(
    repo_list: str = typer.Option(..., help="Path to repos.jsonl"),
    s3_prefix: str = typer.Option(..., help="S3 prefix for mutation tarballs"),
    n_mutations: int = typer.Option(5, help="Number of mutations per repo"),
    limit: int | None = typer.Option(None, help="Limit to first N repos"),
    modal_timeout_seconds: int = typer.Option(600, help="Modal sandbox timeout"),
) -> None:
    """Run the mutation pipeline."""
    logging.basicConfig(level=logging.INFO)
    mutation_flow(
        repo_list_path=repo_list,
        s3_prefix=s3_prefix,
        n_mutations=n_mutations,
        limit=limit,
        modal_timeout_seconds=modal_timeout_seconds,
    )


if __name__ == "__main__":
    cli_app()
