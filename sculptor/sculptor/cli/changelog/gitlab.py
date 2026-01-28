import os
import re

import requests
from loguru import logger
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_exponential

GITLAB_API_BASE = "https://gitlab.com/api/v4"
GITLAB_PROJECT_ID = "generally-intelligent%2Fgenerally_intelligent"


def get_gitlab_token() -> str:
    """Get GitLab token from environment."""
    token = os.environ.get("GITLAB_TOKEN") or os.environ.get("GITLAB_ACCESS_TOKEN")
    if not token:
        raise ValueError("GITLAB_TOKEN or GITLAB_ACCESS_TOKEN environment variable is required. ")
    return token


def extract_mr_number(commit_message: str) -> str | None:
    """Extract GitLab MR number from commit message."""
    match = re.search(r"!(\d+)", commit_message)
    return match.group(1) if match else None


def extract_branch_name(commit_message: str) -> str | None:
    """Extract branch name from merge commit message."""
    match = re.search(r"Merge branch '([^']+)' into", commit_message)
    return match.group(1) if match else None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
)
def fetch_gitlab_mr_info(mr_number: str, gitlab_token: str) -> dict | None:
    """Fetch GitLab MR information using GitLab REST API."""
    try:
        url = f"{GITLAB_API_BASE}/projects/{GITLAB_PROJECT_ID}/merge_requests/{mr_number}"
        response = requests.get(
            url,
            headers={"PRIVATE-TOKEN": gitlab_token},
            timeout=10,
        )

        if response.status_code != 200:
            logger.warning(f"Failed to fetch GitLab MR !{mr_number}: HTTP {response.status_code}")
            return None

        mr_data = response.json()

        result = {
            "title": mr_data.get("title"),
            "description": mr_data.get("description"),
        }
        return result
    except Exception as e:
        logger.warning(f"Failed to fetch GitLab MR !{mr_number}: {e}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
)
def fetch_gitlab_mr_comments(mr_number: str, gitlab_token: str) -> str | None:
    """Fetch GitLab MR comments using GitLab REST API."""
    try:
        url = f"{GITLAB_API_BASE}/projects/{GITLAB_PROJECT_ID}/merge_requests/{mr_number}/notes"
        response = requests.get(
            url,
            headers={"PRIVATE-TOKEN": gitlab_token},
            timeout=10,
        )

        if response.status_code != 200:
            return None

        comments_data = response.json()
        comments = [comment.get("body", "") for comment in comments_data]
        result = " ".join(comments)
        return result
    except Exception as e:
        logger.warning(f"Failed to fetch GitLab MR comments: {e}")
        return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
)
def fetch_gitlab_mr_participants(mr_number: str, gitlab_token: str) -> tuple[str | None, list[str]]:
    """Fetch GitLab MR author and participants using GitLab REST API."""
    try:
        url = f"{GITLAB_API_BASE}/projects/{GITLAB_PROJECT_ID}/merge_requests/{mr_number}"
        response = requests.get(
            url,
            headers={"PRIVATE-TOKEN": gitlab_token},
            timeout=10,
        )

        if response.status_code != 200:
            return None, []

        mr_data = response.json()

        author = mr_data.get("author", {}).get("username")

        participants = set()
        if author:
            participants.add(author)

        for assignee in mr_data.get("assignees", []):
            if "username" in assignee:
                participants.add(assignee["username"])

        for reviewer in mr_data.get("reviewers", []):
            if "username" in reviewer:
                participants.add(reviewer["username"])

        try:
            notes_url = f"{GITLAB_API_BASE}/projects/{GITLAB_PROJECT_ID}/merge_requests/{mr_number}/notes"
            notes_response = requests.get(
                notes_url,
                headers={"PRIVATE-TOKEN": gitlab_token},
                timeout=10,
            )
            if notes_response.status_code == 200:
                comments_data = notes_response.json()
                for comment in comments_data:
                    commenter = comment.get("author", {}).get("username")
                    if commenter:
                        participants.add(commenter)
        except Exception as e:
            logger.warning(f"Failed to fetch GitLab MR notes: {e}")
            pass

        result = (author, sorted(list(participants)))
        return result
    except Exception as e:
        logger.warning(f"Failed to fetch GitLab MR participants: {e}")
        return None, []
