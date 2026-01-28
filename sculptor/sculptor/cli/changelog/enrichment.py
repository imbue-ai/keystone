from loguru import logger

from sculptor.cli.changelog.gitlab import fetch_gitlab_mr_comments
from sculptor.cli.changelog.gitlab import fetch_gitlab_mr_info
from sculptor.cli.changelog.gitlab import fetch_gitlab_mr_participants
from sculptor.cli.changelog.gitlab import get_gitlab_token
from sculptor.cli.changelog.linear import extract_discord_urls
from sculptor.cli.changelog.linear import extract_linear_tickets
from sculptor.cli.changelog.linear import fetch_linear_ticket_api
from sculptor.cli.changelog.linear import get_linear_token
from sculptor.cli.changelog.models import MergeCommit


def enrich_merge_commits(commits: list[MergeCommit]) -> list[MergeCommit]:
    """Enrich merge commits with GitLab MR and Linear ticket information."""

    gitlab_token = get_gitlab_token()
    linear_token = get_linear_token()

    enriched_commits = []
    for i, commit in enumerate(commits, 1):
        logger.info(f"Processing commit {i}/{len(commits)}: {commit.commit_hash} (MR: {commit.mr_number or 'none'})")

        if commit.mr_number:
            mr_number = commit.mr_number  # Type narrowing: now str, not str | None
            commit.gitlab_url = (
                f"https://gitlab.com/generally-intelligent/generally_intelligent/-/merge_requests/{mr_number}"
            )

            logger.info(f"  Fetching GitLab MR !{mr_number}...")
            mr_info = fetch_gitlab_mr_info(mr_number, gitlab_token)
            if mr_info:
                commit.mr_title = mr_info.get("title")
                commit.mr_description = mr_info.get("description")
                logger.info("    GitLab MR fetched successfully")
            else:
                logger.warning(f"    Failed to fetch GitLab MR !{mr_number}")

            logger.info("  Fetching GitLab MR comments...")
            commit.mr_comments = fetch_gitlab_mr_comments(mr_number, gitlab_token)
            if commit.mr_comments:
                logger.info("    GitLab MR comments fetched successfully")

            logger.info("  Fetching GitLab MR participants...")
            author, participants = fetch_gitlab_mr_participants(mr_number, gitlab_token)
            commit.mr_author = author
            commit.mr_participants = participants
            if participants:
                logger.info("    GitLab MR participants fetched successfully")

        # Search for Linear tickets in all sources
        logger.info("  Searching for Linear tickets...")
        all_sources = " ".join(
            filter(
                None,
                [
                    commit.commit_description,
                    commit.branch_name,
                    commit.mr_title,
                    commit.mr_description,
                    commit.mr_comments,
                ],
            )
        )

        linear_tickets = extract_linear_tickets(all_sources)
        if linear_tickets:
            logger.info(f"    Found Linear tickets: {', '.join(linear_tickets)}")
            commit.linear_ticket = linear_tickets[0]
            linear_ticket = commit.linear_ticket  # Type narrowing: now str, not str | None

            if linear_token:
                logger.info(f"  Fetching Linear ticket {linear_ticket}...")
                ticket_info = fetch_linear_ticket_api(linear_ticket, linear_token)

                if ticket_info:
                    commit.linear_title = ticket_info.get("title")
                    commit.linear_description = ticket_info.get("description")
                    commit.linear_url = ticket_info.get("url")
                    commit.linear_assignee = ticket_info.get("assignee")
                    commit.linear_labels = ticket_info.get("labels", [])
                    if ticket_info.get("discord_url"):
                        commit.upstream_discord_url = ticket_info["discord_url"]
                    logger.info("    Linear ticket fetched successfully")

        # Search for Discord URLs if not found in Linear ticket
        if not commit.upstream_discord_url:
            logger.info("  Searching for Discord URLs...")
            discord_urls = extract_discord_urls(all_sources)
            if discord_urls:
                commit.upstream_discord_url = discord_urls[0]
                logger.info(f"    Found Discord URL: {commit.upstream_discord_url}")

        logger.info("  Commit processed successfully\n")
        enriched_commits.append(commit)

    logger.success(f"Processed {len(enriched_commits)} commits total.")

    return enriched_commits
