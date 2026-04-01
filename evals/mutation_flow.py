"""Mutation pipeline: generate broken-commit branches for eval integrity checking.

Phase 1 of the mutation-augmented eval pipeline. For each repo in a JSONL list,
spawns a Claude Code agent in a Modal sandbox to introduce N small test-breaking
commits. Each commit is stored as a branch (broken-1…broken-N) in a bare git
tarball uploaded to S3.
"""

import contextlib
import json
import logging
import shlex
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
from keystone.modal.modal_runner import run_modal_command

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
You have about 90 seconds. Be fast — don't explore extensively.

Your job: introduce {n} small test-breaking changes to source code in /project.
Language: {language}. Build system: {build_system}. Tests: {tests}. {notes}

IMPORTANT for polyglot projects: if this repo has multiple languages (e.g. Python +
C/C++/Fortran), spread your mutations across different languages. For scipy, that
means Python, C, C++, and Fortran files. Target source files that are likely
exercised by tests — core library code, not build scripts or vendored deps.

For EACH mutation (i=1 to {n}):
  git checkout -b broken-{{i}} main
  # Edit ONE source file (NOT test files) — e.g. insert `raise Exception("mutation")`
  # at the top of an important function, or change `return x` to `return None`,
  # or for C: add `#error "mutation"`, or for Fortran: `STOP "mutation"`
  git add -A && git commit -m "mutation {{i}}"
  git checkout main

Rules:
- Each broken-{{i}} branch diverges from main independently (not chained).
- Only modify SOURCE files, never test files.
- Keep changes tiny — one line per mutation.
- Do NOT run tests or install anything.
- Skip vendored/submodule/third-party directories.

When done: `git branch -v`
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _shell_quote(s: str) -> str:
    """Shell-quote a string for use in a bash -c command."""
    return shlex.quote(s)


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
    use_claude: bool = True,
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

        # Ensure we're on a 'main' branch at the base commit
        # Delete existing main if it points elsewhere, then create at HEAD
        subprocess.run(
            ["git", "branch", "-D", "main"],
            cwd=clone_path,
            capture_output=True,
        )  # ignore errors
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
            if use_claude:
                broken_hashes = _run_mutation_in_modal(clone_path, prompt, n, modal_timeout_seconds)
            else:
                broken_hashes = _run_mutation_locally(clone_path, n, repo_entry.language)
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


def _run_mutation_locally(
    clone_path: Path,
    n: int,
    language: str | None,
) -> list[str]:
    """Create broken branches locally using a simple scripted approach (no Claude)."""
    log.info("Running scripted mutations locally in %s", clone_path)

    # Find candidate source files to mutate (skip test files)
    source_files: list[Path] = []
    for pattern in (
        "**/*.py",
        "**/*.js",
        "**/*.ts",
        "**/*.rb",
        "**/*.go",
        "**/*.rs",
        "**/*.c",
        "**/*.cpp",
        "**/*.java",
    ):
        for f in clone_path.glob(pattern):
            rel = str(f.relative_to(clone_path))
            if "test" in rel.lower() or "spec" in rel.lower() or ".git/" in rel:
                continue
            # Prefer files with some content
            try:
                if f.stat().st_size > 50:
                    source_files.append(f)
            except OSError:
                continue

    # Sort by size descending (larger files are more likely to be core modules)
    source_files.sort(key=lambda f: f.stat().st_size, reverse=True)

    if not source_files:
        log.warning("No source files found to mutate")
        return []

    broken_hashes: list[str] = []
    for i in range(1, n + 1):
        if i - 1 >= len(source_files):
            # Reuse files if we have fewer source files than mutations
            target = source_files[(i - 1) % len(source_files)]
        else:
            target = source_files[i - 1]

        branch_name = f"broken-{i}"
        _run_git(["checkout", "-b", branch_name, "main"], cwd=clone_path)

        # Insert a mutation at the top of the file
        original = target.read_text()
        rel_path = target.relative_to(clone_path)
        if (language and language.lower() in ("python",)) or target.suffix == ".py":
            mutation = f'raise Exception("mutation {i} in {rel_path}")  # MUTATION\n'
        elif target.suffix in (".js", ".ts"):
            mutation = f'throw new Error("mutation {i} in {rel_path}");  // MUTATION\n'
        elif target.suffix == ".go":
            mutation = f'panic("mutation {i} in {rel_path}")  // MUTATION\n'
        elif target.suffix == ".rb":
            mutation = f'raise "mutation {i} in {rel_path}"  # MUTATION\n'
        elif target.suffix in (".c", ".cpp"):
            mutation = f'#error "mutation {i} in {rel_path}"  /* MUTATION */\n'
        elif target.suffix == ".java":
            mutation = f'throw new RuntimeException("mutation {i} in {rel_path}");  // MUTATION\n'
        elif target.suffix == ".rs":
            mutation = f'panic!("mutation {i} in {rel_path}");  // MUTATION\n'
        else:
            mutation = f'raise Exception("mutation {i} in {rel_path}")  # MUTATION\n'

        target.write_text(mutation + original)

        _run_git(["add", "-A"], cwd=clone_path)
        _run_git(
            [
                "-c",
                "user.name=mutation",
                "-c",
                "user.email=m@m",
                "commit",
                "-m",
                f"mutation {i}: {rel_path}",
            ],
            cwd=clone_path,
        )

        # Get commit hash
        result = _run_git(["rev-parse", "HEAD"], cwd=clone_path)
        broken_hashes.append(result.stdout.strip())
        log.info("Created %s: mutated %s", branch_name, rel_path)

        _run_git(["checkout", "main"], cwd=clone_path)

    return broken_hashes


def _run_mutation_in_modal(
    clone_path: Path,
    prompt: str,
    n: int,
    timeout_seconds: int,
) -> list[str]:
    """Upload repo to Modal sandbox, run Claude Code, extract broken branch hashes.

    Uses the same streaming pattern as keystone's ModalAgentRunner: a bash wrapper
    script executed via run_modal_command(capture=True) so stdout/stderr are logged
    in real time through Python's logging system.
    """
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

        run_modal_command(sb, "mkdir", "-p", "/project", name="mutation-setup").wait()
        run_modal_command(
            sb,
            "tar",
            "-xzf",
            "/tmp/project.tar.gz",
            "-C",
            "/project",
            name="mutation-extract",
        ).wait()

        # Init git repo in sandbox (the tarball is a working tree, not a git repo)
        # Mark /project as safe to avoid "dubious ownership" errors (different UID in container)
        run_modal_command(
            sb,
            "git",
            "config",
            "--global",
            "--add",
            "safe.directory",
            "/project",
            name="git-safe",
        ).wait()
        run_modal_command(sb, "git", "-C", "/project", "init", name="git-init").wait()
        run_modal_command(
            sb,
            "git",
            "-C",
            "/project",
            "config",
            "user.name",
            "mutation-agent",
            name="git-config",
        ).wait()
        run_modal_command(
            sb,
            "git",
            "-C",
            "/project",
            "config",
            "user.email",
            "mutation@eval",
            name="git-config",
        ).wait()
        run_modal_command(sb, "git", "-C", "/project", "add", "-A", name="git-add").wait()
        run_modal_command(
            sb,
            "git",
            "-C",
            "/project",
            "commit",
            "-m",
            "base",
            name="git-commit",
        ).wait()
        run_modal_command(
            sb,
            "git",
            "-C",
            "/project",
            "branch",
            "-M",
            "main",
            name="git-branch",
        ).wait()

        # Write prompt to file in sandbox (avoids shell quoting issues)
        with sb.open("/tmp/mutation_prompt.txt", "w") as f:
            f.write(prompt)

        # Write a runner script (same pattern as keystone's /run_agent.sh)
        # --dangerously-skip-permissions prevents Claude from hanging on interactive prompts
        # --output-format stream-json gives us machine-readable output
        runner_script = (
            "#!/bin/bash\n"
            "set -e\n"
            "cd /project\n"
            f"exec timeout {timeout_seconds} claude "
            "--dangerously-skip-permissions "
            "--output-format stream-json "
            "--verbose "
            '-p "$(cat /tmp/mutation_prompt.txt)" '
            "--allowedTools Bash,Read,Write,Edit\n"
        )
        with sb.open("/tmp/run_mutation.sh", "w") as f:
            f.write(runner_script)
        run_modal_command(sb, "chmod", "+x", "/tmp/run_mutation.sh", name="mutation-chmod").wait()

        # Run Claude Code — capture=True streams stdout/stderr to Python logging in real time
        # (ManagedProcess._stream_reader daemon threads handle this, same as keystone's agent)
        log.info("Running Claude Code in sandbox (timeout=%ds)...", timeout_seconds)
        agent_proc = run_modal_command(
            sb,
            "bash",
            "/tmp/run_mutation.sh",
            name="claude-mutation",
            capture=True,
        )
        for _event in agent_proc.stream():
            pass  # All output logged by ManagedProcess._stream_reader
        exit_code = agent_proc.wait()
        log.info("Claude Code exited with code %d", exit_code)

        # Read back branch hashes
        log.info("Reading broken branch hashes...")
        run_modal_command(
            sb,
            "git",
            "-C",
            "/project",
            "branch",
            "-v",
            name="branches",
        ).wait()

        broken_hashes: list[str] = []
        for i in range(1, n + 1):
            branch_name = f"broken-{i}"
            hash_proc = run_modal_command(
                sb,
                "git",
                "-C",
                "/project",
                "rev-parse",
                f"refs/heads/{branch_name}",
                name=f"hash-{branch_name}",
                capture=True,
            )
            captured_hash = ""
            for event in hash_proc.stream():
                captured_hash += event.line
            exit_code = hash_proc.wait()
            if exit_code == 0 and captured_hash.strip():
                broken_hashes.append(captured_hash.strip())
            else:
                log.warning("Branch %s not found", branch_name)

        # Pull the mutated repo back to the local clone
        log.info("Downloading mutated repo from sandbox...")
        run_modal_command(
            sb,
            "tar",
            "-czf",
            "/tmp/mutated.tar.gz",
            "-C",
            "/project",
            ".",
            name="mutation-tar",
        ).wait()
        with sb.open("/tmp/mutated.tar.gz", "rb") as f:
            mutated_data = f.read()

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
    use_claude: bool = True,
) -> list[MutationResult]:
    """Run mutation pipeline for all repos, write amended JSONL."""
    repos = _load_repos(repo_list_path, limit)
    log.info("Loaded %d repos from %s", len(repos), repo_list_path)

    # Run mutations (sequential for now; can be parallelized later)
    results: list[MutationResult] = []
    for repo_entry in repos:
        result = mutate_repo_task(
            repo_entry, s3_prefix, n_mutations, modal_timeout_seconds, use_claude=use_claude
        )
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
    limit_to_first_n_repos: int | None = typer.Option(None, help="Limit to first N repos"),
    modal_timeout_seconds: int = typer.Option(600, help="Modal sandbox timeout"),
    use_claude: bool = typer.Option(
        True, "--use-claude/--no-claude", help="Use Claude Code (Modal) or scripted local mutations"
    ),
) -> None:
    """Run the mutation pipeline."""
    logging.basicConfig(level=logging.INFO)
    mutation_flow(
        repo_list_path=repo_list,
        s3_prefix=s3_prefix,
        n_mutations=n_mutations,
        limit=limit_to_first_n_repos,
        modal_timeout_seconds=modal_timeout_seconds,
        use_claude=use_claude,
    )


if __name__ == "__main__":
    cli_app()
