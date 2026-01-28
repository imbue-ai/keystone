import os
import re

import requests
from loguru import logger
from tenacity import retry
from tenacity import retry_if_exception_type
from tenacity import stop_after_attempt
from tenacity import wait_exponential

LINEAR_API_URL = "https://api.linear.app/graphql"


def get_linear_token() -> str:
    """Get Linear API key from environment."""
    return os.environ["LINEAR_API_KEY"]


def extract_linear_tickets(text: str) -> list[str]:
    """Extract Linear ticket IDs from text (CAP-XXX or PROD-XXX)."""
    tickets = []

    # Extract CAP-XXXX or PROD-XXXX format (case-insensitive)
    # This catches: prod-123, PROD-123, cap-456, CAP-456, sam/prod-123, etc.
    for match in re.finditer(r"\b((?:CAP|PROD)-\d+)\b", text, re.IGNORECASE):
        ticket = match.group(1).upper()  # Normalize to uppercase
        if ticket not in tickets:
            tickets.append(ticket)

    return tickets


def extract_discord_urls(text: str) -> list[str]:
    """Extract Discord URLs from text."""
    urls = []
    for match in re.finditer(r"https://discord\.com/channels/\S+", text):
        url = match.group(0)
        if url not in urls:
            urls.append(url)
    return urls


@retry(
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=2, max=10),
    retry=retry_if_exception_type((requests.exceptions.RequestException, requests.exceptions.Timeout)),
)
def fetch_linear_ticket_api(ticket_id: str, api_key: str) -> dict | None:
    """Fetch Linear ticket information using GraphQL API."""
    try:
        query = """
        query($id: String!) {
            issue(id: $id) {
                id
                title
                description
                url
                assignee {
                    name
                    email
                }
                labels {
                    nodes {
                        name
                    }
                }
                attachments {
                    nodes {
                        url
                        title
                    }
                }
            }
        }
        """

        response = requests.post(
            "https://api.linear.app/graphql",
            headers={
                "Authorization": api_key,
                "Content-Type": "application/json",
            },
            json={"query": query, "variables": {"id": ticket_id}},
            timeout=10,
        )

        if response.status_code == 200:
            data = response.json()
            if "data" in data and "issue" in data["data"]:
                issue = data["data"]["issue"]

                discord_url = None
                for attachment in issue.get("attachments", {}).get("nodes", []):
                    url = attachment.get("url", "")
                    if "discord.com" in url:
                        discord_url = url
                        break

                labels = [label["name"] for label in issue.get("labels", {}).get("nodes", [])]

                assignee = None
                if issue.get("assignee"):
                    assignee = issue["assignee"].get("name") or issue["assignee"].get("email")

                result = {
                    "title": issue.get("title"),
                    "description": issue.get("description"),
                    "url": issue.get("url"),
                    "assignee": assignee,
                    "discord_url": discord_url,
                    "labels": labels,
                }

                return result
        return None
    except Exception as e:
        logger.warning(f"Failed to fetch Linear ticket {ticket_id} via API: {e}")
        return None
