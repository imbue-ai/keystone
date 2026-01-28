import re

from loguru import logger

from imbue_core.processes.local_process import run_blocking
from sculptor.cli.changelog.gitlab import extract_branch_name
from sculptor.cli.changelog.gitlab import extract_mr_number
from sculptor.cli.changelog.models import MergeCommit


def fetch_release_branches_from_remote() -> bool:
    """Fetch release branches from remote."""
    try:
        logger.info("Fetching release branches from remote...")
        result = run_blocking(["git", "fetch", "origin", "automated/bump*:refs/remotes/origin/automated/bump*"])
        if result.returncode == 0:
            logger.info("Successfully fetched release branches from remote")
            return True
        else:
            logger.warning("Failed to fetch release branches from remote")
            return False
    except Exception as e:
        logger.warning(f"Error fetching release branches from remote: {e}")
        return False


def get_versions() -> list[str]:
    """Get all versions from automated bump commits, sorted from newest to oldest."""

    logger.info("Looking for all versions...")

    fetch_release_branches_from_remote()
    result = run_blocking(["git", "log", "--all", "--merges", "--oneline", "--grep=automated/bump"])
    lines = result.stdout.strip().split("\n")

    if not lines or not lines[0]:
        logger.error("Could not find any automated bump commits")
        return []

    # Extract versions from bump commits (deduplicated and in order)
    # Format: "Merge branch 'automated/bump-0.2.23' into 'main'"
    versions = []
    seen_versions = set()

    for line in lines:
        match = re.search(r"automated/bump[_-]\w*[_-]v(\d+\.\d+\.\d+)", line)
        if match:
            version = match.group(1)
            if version not in seen_versions:
                versions.append(version)
                seen_versions.add(version)

    if versions:
        logger.info(f"Found {len(versions)} versions: {', '.join(versions[:5])}{'...' if len(versions) > 5 else ''}")
    else:
        logger.error("Could not extract any versions from bump commits")

    return versions


def get_commit_timestamp(version: str) -> str | None:
    """Get the timestamp for when a version was cut."""
    bump_commit = find_bump_commit(version)
    if not bump_commit:
        return None

    result = run_blocking(["git", "log", "-1", "--format=%cI", bump_commit])
    return result.stdout.strip() if result.stdout.strip() else None


def find_bump_commit(from_version: str) -> str | None:
    """Find the automated bump commit for a given version.

    Searches using two patterns to support both cherry-picked and non-cherry-picked commits:
    1. Merge commit branch name: "automated/bump.*{version}" (non-cherry-picked)
    2. Explicit bump message: "Bumping Sculptor Version to v{version}" (cherry-picked)
    """
    logger.info(f"Looking for automated bump commit for version {from_version}...")

    result_picks = run_blocking(["git", "log", "--oneline", f"--grep=Bumping Sculptor Version to v{from_version}"])
    result_merges = run_blocking(["git", "log", "--merges", "--oneline", f"--grep=automated/bump.*{from_version}"])
    lines = result_picks.stdout.strip().split("\n") + result_merges.stdout.strip().split("\n")
    lines = [l for l in lines if l]
    if lines and lines[0]:
        commit_hash = lines[0].split()[0]
        logger.info(f"Found bump commit: {commit_hash}")
        return commit_hash

    logger.error(f"Could not find automated bump commit for version {from_version}")
    return None


def get_merge_commits(from_version: str, to_version: str = "HEAD") -> list[MergeCommit]:
    """Get all merge commits between two versions."""
    first_bump_commit = find_bump_commit(from_version)
    second_bump_commit = find_bump_commit(to_version)

    if not first_bump_commit or not second_bump_commit:
        return []

    logger.info(
        "Finding commits between {}@{} and {}@{}", from_version, first_bump_commit, to_version, second_bump_commit
    )

    result = run_blocking(
        ["git", "log", "--format=%H", f"{first_bump_commit}..{second_bump_commit}"],
    )

    merge_commits = []
    for commit_hash in result.stdout.strip().split("\n"):
        if not commit_hash:
            continue

        subject_result = run_blocking(["git", "log", "-1", "--format=%s", commit_hash])
        commit_message = subject_result.stdout.strip()

        if "into 'main'" not in commit_message and "into 'release/sculptor-v'" not in commit_message:
            # it's not a relevant merge into main or release
            continue

        body_result = run_blocking(["git", "log", "-1", "--format=%B", commit_hash])
        commit_description = body_result.stdout.strip()

        date_result = run_blocking(["git", "log", "-1", "--format=%ci", commit_hash])
        merged_date = date_result.stdout.strip()

        try:
            mr_number = extract_mr_number(commit_description)
            branch_name = extract_branch_name(commit_message)
        except Exception as e:
            continue

        merge_commit = MergeCommit(
            commit_hash=commit_hash[:11],
            commit_message=commit_message,
            commit_description=commit_description,
            merged_date=merged_date,
            branch_name=branch_name,
            mr_number=mr_number,
        )

        merge_commits.append(merge_commit)

    logger.info(f"Found {len(merge_commits)} merge commits")
    return merge_commits
