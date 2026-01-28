import os
import re
from typing import Any

import requests
from loguru import logger
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_exponential

from sculptor.cli.changelog.models import MergeCommit

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_API_VERSION = "2022-06-28"


def get_notion_token() -> str:
    """Get Notion API token from environment."""
    return os.environ["NOTION_API_KEY"]


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
)
def find_existing_notion_page(database_id: str, notion_token: str, page_title: str) -> str | None:
    """Search for an existing page in a Notion database by title."""
    url = f"{NOTION_API_BASE}/databases/{database_id}/query"

    payload = {"filter": {"property": "Name", "title": {"equals": page_title}}}

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        },
        json=payload,
        timeout=30,
    )

    if response.status_code == 200:
        results = response.json().get("results", [])
        if results:
            return results[0]["id"]
    return None


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
)
def delete_notion_page(page_id: str, notion_token: str) -> bool:
    """Delete (archive) a Notion page."""
    logger.info(f"Archiving existing Notion page: {page_id}")

    url = f"{NOTION_API_BASE}/pages/{page_id}"
    response = requests.patch(
        url,
        headers={
            "Authorization": f"Bearer {notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        },
        json={"archived": True},
        timeout=30,
    )

    if response.status_code == 200:
        logger.info("Successfully archived existing page")
        return True
    else:
        logger.warning(f"Failed to archive page: HTTP {response.status_code}")
        raise requests.exceptions.RequestException(f"Failed to archive page: HTTP {response.status_code}")


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
)
def create_notion_page_with_blocks(
    database_id: str,
    notion_token: str,
    properties: dict[str, Any],
    children: list[dict],
) -> dict | None:
    """Create a new Notion page with blocks in batches."""
    url = f"{NOTION_API_BASE}/pages"

    # Create page with first batch of children (max 100)
    batch_size = 100
    first_batch = children[:batch_size]

    payload = {
        "parent": {"database_id": database_id},
        "properties": properties,
        "children": first_batch,
    }

    response = requests.post(
        url,
        headers={
            "Authorization": f"Bearer {notion_token}",
            "Content-Type": "application/json",
            "Notion-Version": NOTION_API_VERSION,
        },
        json=payload,
        timeout=30,
    )

    if response.status_code not in (200, 201):
        logger.error(f"Failed to create page: HTTP {response.status_code} - {response.text}")
        raise requests.exceptions.RequestException(f"Failed to create page: HTTP {response.status_code}")

    page_data = response.json()
    page_id = page_data.get("id")

    # If there are more blocks, append them in batches
    if len(children) > batch_size:
        total_batches = (len(children) + batch_size - 1) // batch_size
        logger.info(f"Created page with first batch. Appending remaining blocks in {total_batches - 1} batch(es)...")

        for i in range(batch_size, len(children), batch_size):
            batch = children[i : i + batch_size]
            batch_num = i // batch_size + 1

            logger.info(f"Appending batch {batch_num}/{total_batches} ({len(batch)} blocks)...")

            append_url = f"{NOTION_API_BASE}/blocks/{page_id}/children"
            append_response = requests.patch(
                append_url,
                headers={
                    "Authorization": f"Bearer {notion_token}",
                    "Content-Type": "application/json",
                    "Notion-Version": NOTION_API_VERSION,
                },
                json={"children": batch},
                timeout=30,
            )

            if append_response.status_code != 200:
                logger.error(
                    f"Failed to append batch {batch_num}: HTTP {append_response.status_code} - {append_response.text}"
                )
                raise requests.exceptions.RequestException(
                    f"Failed to append batch {batch_num}: HTTP {append_response.status_code}"
                )

    return page_data


def categorize_commits(commits: list[MergeCommit]) -> dict[str, list[MergeCommit]]:
    """Categorize commits by Linear labels with priority: devex > improvement > bug > feature."""
    categories = {
        "devex": [],
        "features": [],
        "improvements": [],
        "bugs": [],
        "no_linear_ticket": [],
        "no_label": [],
    }

    for commit in commits:
        if not commit.linear_ticket:
            categories["no_linear_ticket"].append(commit)
            continue

        labels_lower = [label.lower() for label in commit.linear_labels]

        # Priority ordering: devex > improvement > bug > feature
        if "devex" in labels_lower:
            categories["devex"].append(commit)
        elif "improvement" in labels_lower:
            categories["improvements"].append(commit)
        elif "bug" in labels_lower:
            categories["bugs"].append(commit)
        elif "feature" in labels_lower:
            categories["features"].append(commit)
        else:
            # Has a Linear ticket but no recognized label
            categories["no_label"].append(commit)

    return categories


def _clean_description_for_notion(description: str) -> str:
    """Strip markdown headings and blockquote markers for Notion."""
    if not description:
        return ""

    # Strip markdown headings and blockquote markers for Notion
    lines = description.split("\n")
    stripped_lines = []
    for line in lines:
        # Remove heading markers from start of line
        stripped = re.sub(r"^#+\s+", "", line)
        # Remove blockquote markers
        stripped = re.sub(r"^>\s*", "", stripped)
        stripped_lines.append(stripped)
    cleaned_description = "\n".join(stripped_lines)

    # Notion has a 2000 character limit for quote text content
    if len(cleaned_description) > 2000:
        cleaned_description = cleaned_description[:1997] + "..."

    return cleaned_description


def _add_description_block(children: list[dict], description: str) -> None:
    """Add a description block to the children list if description is not empty."""
    if not description:
        return

    cleaned_description = _clean_description_for_notion(description)
    if cleaned_description.strip():
        children.append(
            {
                "object": "block",
                "type": "quote",
                "quote": {"rich_text": [{"type": "text", "text": {"content": cleaned_description}}]},
            }
        )


def _add_commit_section(
    children: list[dict],
    title: str,
    commit_list: list[MergeCommit],
    section_description: str | None = None,
) -> None:
    """Add a section of commits to the children list."""
    if not commit_list:
        return

    # Add section heading
    children.append(
        {
            "object": "block",
            "type": "heading_2",
            "heading_2": {"rich_text": [{"type": "text", "text": {"content": title}}]},
        }
    )

    # Add section description if provided
    if section_description:
        children.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": section_description}}]},
            }
        )

    for commit in commit_list:
        # Add Linear ticket link and title if available
        if commit.linear_title:
            title_parts = []
            if commit.linear_ticket and commit.linear_url:
                title_parts.append(
                    {
                        "type": "text",
                        "text": {"content": commit.linear_ticket, "link": {"url": commit.linear_url}},
                        "annotations": {"bold": True},
                    }
                )
                title_parts.append(
                    {
                        "type": "text",
                        "text": {"content": f": {commit.linear_title}"},
                        "annotations": {"bold": True},
                    }
                )
            else:
                title_parts.append(
                    {
                        "type": "text",
                        "text": {"content": commit.linear_title},
                        "annotations": {"bold": True},
                    }
                )

            children.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": title_parts},
                }
            )

            # Add assignee if available
            if commit.linear_assignee:
                children.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {"content": f"Assignee: {commit.linear_assignee}"},
                                    "annotations": {"italic": True},
                                }
                            ]
                        },
                    }
                )

            # Add Linear description if available
            if commit.linear_description:
                _add_description_block(children, commit.linear_description)

        # Add MR link and title if available
        if commit.mr_title:
            title_parts = []
            if commit.mr_number and commit.gitlab_url:
                title_parts.append(
                    {
                        "type": "text",
                        "text": {"content": f"MR{commit.mr_number}", "link": {"url": commit.gitlab_url}},
                        "annotations": {"bold": True},
                    }
                )
                title_parts.append(
                    {
                        "type": "text",
                        "text": {"content": f": {commit.mr_title}"},
                        "annotations": {"bold": True},
                    }
                )
            else:
                title_parts.append(
                    {
                        "type": "text",
                        "text": {"content": commit.mr_title},
                        "annotations": {"bold": True},
                    }
                )

            children.append(
                {
                    "object": "block",
                    "type": "paragraph",
                    "paragraph": {"rich_text": title_parts},
                }
            )

            # Add author if available
            if commit.mr_author:
                children.append(
                    {
                        "object": "block",
                        "type": "paragraph",
                        "paragraph": {
                            "rich_text": [
                                {
                                    "type": "text",
                                    "text": {"content": f"Author: {commit.mr_author}"},
                                    "annotations": {"italic": True},
                                }
                            ]
                        },
                    }
                )

            # Add MR description if available
            if commit.mr_description:
                _add_description_block(children, commit.mr_description)

        # Add extra blank line after each entry
        children.append(
            {
                "object": "block",
                "type": "paragraph",
                "paragraph": {"rich_text": [{"type": "text", "text": {"content": ""}}]},
            }
        )


def create_notion_changelog_entry(
    commits: list[MergeCommit],
    database_id: str,
    notion_token: str,
    from_version: str,
    to_version: str,
    cut_time: str | None = None,
) -> dict | None:
    """Create or update a Notion database entry representing a full changelog."""
    try:
        page_title = f"v{from_version} to v{to_version}"

        existing_page_id = find_existing_notion_page(database_id, notion_token, page_title)

        if existing_page_id:
            logger.info(f"Found existing page {existing_page_id}, will archive and recreate it")
            delete_notion_page(existing_page_id, notion_token)

        categories = categorize_commits(commits)

        properties: dict[str, Any] = {
            "Name": {"title": [{"text": {"content": f"v{from_version} to v{to_version}"}}]},
            "From version": {"rich_text": [{"text": {"content": from_version}}]},
            "To version": {"rich_text": [{"text": {"content": to_version}}]},
        }

        # Add Cut time if available
        if cut_time:
            properties["Cut time"] = {"date": {"start": cut_time}}

        # Build the changelog content as Notion blocks
        children = []

        # Add sections for each category
        _add_commit_section(children, "Features", categories["features"])
        _add_commit_section(children, "Improvements", categories["improvements"])
        _add_commit_section(children, "Bugs", categories["bugs"])
        _add_commit_section(children, "Devex", categories["devex"])
        _add_commit_section(
            children,
            "No Label",
            categories["no_label"],
            "Linear tickets without a recognized label (Devex, Feature, Improvement, or Bug).\n",
        )
        _add_commit_section(
            children,
            "No Linear Ticket",
            categories["no_linear_ticket"],
            "Anything without a linear ticket is listed below.\n",
        )

        # Create new page with all blocks
        page_data = create_notion_page_with_blocks(database_id, notion_token, properties, children)
        logger.success(f"Created Notion changelog entry for {from_version} to {to_version}")
        return page_data

    except Exception as e:
        logger.error(f"Failed to create Notion entry: {e}")
        return None


def create_notion_changelog(
    commits: list[MergeCommit],
    database_id: str,
    from_version: str,
    to_version: str,
    cut_time: str | None = None,
) -> bool:
    """Create a Notion database entry for the changelog."""
    try:
        notion_token = get_notion_token()
    except ValueError as e:
        logger.warning(f"Notion API key not available: {e}")
        logger.warning("Skipping Notion changelog creation")
        return False

    logger.info(f"Creating Notion changelog entry for {from_version} to {to_version}...")

    result = create_notion_changelog_entry(commits, database_id, notion_token, from_version, to_version, cut_time)

    return result is not None
