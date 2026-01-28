from pydantic import BaseModel


class MergeCommit(BaseModel):
    """Represents a merge commit with enriched metadata."""

    commit_hash: str
    commit_message: str
    commit_description: str
    merged_date: str
    branch_name: str | None = None
    mr_number: str | None = None
    gitlab_url: str | None = None
    mr_title: str | None = None
    mr_description: str | None = None
    mr_comments: str | None = None
    mr_author: str | None = None
    mr_participants: list[str] = []
    linear_ticket: str | None = None
    linear_url: str | None = None
    linear_title: str | None = None
    linear_description: str | None = None
    linear_assignee: str | None = None
    upstream_discord_url: str | None = None
    linear_labels: list[str] = []
