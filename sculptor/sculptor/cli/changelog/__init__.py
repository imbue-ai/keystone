"""
Changelog generation module for Sculptor.

This module extracts merge commits between two version tags and enriches them
with GitLab MR and Linear ticket information. Optionally creates entries in a
Notion database.

Required environment variables:
- GITLAB_TOKEN or GITLAB_ACCESS_TOKEN: GitLab API token with 'read_api' scope
  (Create at https://gitlab.com/-/profile/personal_access_tokens)
- LINEAR_API_KEY: Linear API key (optional, for Linear ticket enrichment)
  (Create at https://linear.app/settings/api)
- NOTION_API_KEY: Notion API token (optional, for creating Notion database entries)
  (Create at https://www.notion.so/my-integrations)
"""
