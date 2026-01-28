"""updated imbue cli tool content

Revision ID: ddd5ef49a520
Revises: 8b836c9b525b
Create Date: 2025-11-06 09:23:26.556177

This migration transforms ImbueCLI tool content from the old format to the new format:
- Transforms ImbueCLIActionToolContent to ActionOutput format
- Renames 'check' field to 'command'
- Renames 'issues' field to 'outputs'
- Removes 'summary' field
- Transforms 'user_display' from string to structured object
- Removes 'content_type' from individual action outputs
"""

import json
from typing import Any
from typing import Sequence

import sqlalchemy as sa
from alembic import op
from loguru import logger

# revision identifiers, used by Alembic.
revision: str = "ddd5ef49a520"
down_revision: str | None = "8b836c9b525b"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TABLE_NAME = "saved_agent_message"
PRIMARY_KEY = "snapshot_id"


def _select_rows(dialect: str, table_name: str, primary_key: str) -> sa.TextClause:
    """Select rows that contain ResponseBlockAgentMessage with ImbueCLI content."""
    logger.info(
        f"Selecting rows from {table_name} with {primary_key} and object_type = 'ResponseBlockAgentMessage' using {dialect} dialect"
    )
    if dialect == "postgresql":
        return sa.text(
            f"""
            SELECT {primary_key}, message
            FROM {table_name}
            WHERE message ->> 'object_type' = "ResponseBlockAgentMessage"
        """
        )
    elif dialect == "sqlite":
        return sa.text(
            f"""
            SELECT {primary_key}, message
            FROM {table_name}
            WHERE json_extract(message, '$.object_type') = "ResponseBlockAgentMessage"
        """
        )
    else:
        raise ValueError(f"Unsupported dialect: {dialect}")


def _transform_user_display(user_display: Any) -> dict[str, Any]:
    """Transform user_display from old format to new format.

    Old format: str | None (HTML string or None)
    New format: UserDisplayOutputUnion (ErroredOutput | CommandTextOutput)
    """
    # If it's already a dict with object_type, it's already migrated or in new format
    if isinstance(user_display, dict) and "object_type" in user_display:
        return user_display

    # If it's None or empty string, create a default CommandTextOutput
    if user_display is None or user_display == "":
        return {"object_type": "CommandTextOutput", "output": ""}

    # If it's a string, wrap it in CommandTextOutput
    if isinstance(user_display, str):
        return {"object_type": "CommandTextOutput", "output": user_display}

    # Fallback: create empty CommandTextOutput
    return {"object_type": "CommandTextOutput", "output": str(user_display)}


def _transform_action_output(action_output: dict[str, Any]) -> dict[str, Any]:
    """Transform a single action output from old format to new format.

    Old format (ImbueCLIActionToolContent):
    {
        "content_type": "imbue_cli_action",
        "check": "verify",
        "summary": "...",
        "user_display": "<html>...</html>",
        "issues": [...]
    }

    New format (ActionOutput):
    {
        "command": "verify",
        "outputs": [...],
        "user_display": {"object_type": "CommandTextOutput", "output": "..."}
    }
    """
    # Check if already migrated (has 'command' and 'outputs' instead of 'check' and 'issues')
    if "command" in action_output and "outputs" in action_output:
        # Already migrated, just ensure summary is removed if present
        action_output.pop("summary", None)
        action_output.pop("content_type", None)
        return action_output

    new_action_output: dict[str, Any] = {}

    # Rename 'check' to 'command'
    if "check" in action_output:
        new_action_output["command"] = action_output["check"]
    elif "command" in action_output:
        new_action_output["command"] = action_output["command"]
    else:
        # Default if neither exists
        new_action_output["command"] = "unknown"

    # Rename 'issues' to 'outputs'
    if "outputs" in action_output:
        new_action_output["outputs"] = action_output["outputs"]
    else:
        # If we can't find 'outputs' then just use an empty list
        # Migrating all the different old types of action outputs to the new format, is
        # too trecharous and they can always be re-generated as needed.
        new_action_output["outputs"] = []

    # Transform user_display
    user_display = action_output.get("user_display")
    new_action_output["user_display"] = _transform_user_display(user_display)

    # Note: 'summary' and 'content_type' are intentionally omitted (removed)
    return new_action_output


def _migrate_message_content(message: dict[str, Any]) -> bool:
    """Migrate ImbueCLI content in a message. Returns True if any changes were made."""
    if message.get("object_type") != "ResponseBlockAgentMessage":
        return False

    content = message.get("content", [])
    if not isinstance(content, list):
        return False

    modified = False
    for content_block in content:
        if not isinstance(content_block, dict):
            continue

        # Look for ToolResultBlock or ToolResultBlockSimple
        if content_block.get("type") not in ["tool_result", "tool_result_simple"]:
            continue

        tool_content = content_block.get("content")
        if not isinstance(tool_content, dict):
            continue

        # Check if this is ImbueCLI content
        if tool_content.get("content_type") != "imbue_cli":
            continue

        # Transform action_outputs
        action_outputs = tool_content.get("action_outputs", [])
        if not isinstance(action_outputs, list):
            continue

        transformed_outputs = []
        for action_output in action_outputs:
            if isinstance(action_output, dict):
                try:
                    transformed = _transform_action_output(action_output)
                    transformed_outputs.append(transformed)
                    modified = True
                except Exception as e:
                    # We just discard any action outputs that we can't transform, they are mainly used to
                    # render thing tool use blocks in the UI. They can always be re-generated as needed.
                    logger.error(f"Failed to transform action output: {e}, discarding action output")
                    continue
            else:
                transformed_outputs.append(action_output)

        tool_content["action_outputs"] = transformed_outputs

    return modified


def _table_exists(connection: Any, table_name: str) -> bool:
    """Check if a table exists in the database."""
    dialect = connection.dialect.name
    if dialect == "postgresql":
        result = connection.execute(
            sa.text("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = :table_name)").bindparams(
                sa.bindparam("table_name", table_name)
            )
        ).scalar()
        return bool(result)
    elif dialect == "sqlite":
        result = connection.execute(
            sa.text("SELECT name FROM sqlite_master WHERE type='table' AND name=:table_name").bindparams(
                sa.bindparam("table_name", table_name)
            )
        ).fetchone()
        return result is not None
    else:
        raise ValueError(f"Unsupported dialect: {dialect}")


def upgrade() -> None:
    """Upgrade schema - transform ImbueCLI content to new format."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    if not _table_exists(connection, TABLE_NAME):
        logger.info(f"Skipping table {TABLE_NAME} - does not exist yet")
        raise ValueError(f"Table {TABLE_NAME} does not exist")

    logger.info(f"Processing table: {TABLE_NAME}")
    select_statement = _select_rows(dialect, TABLE_NAME, PRIMARY_KEY)
    logger.info(f"Select statement: {select_statement}")
    rows = connection.execute(select_statement).mappings().all()

    logger.info(f"Found {len(rows)} rows to process in {TABLE_NAME}")

    update_data = []
    for row in rows:
        message = json.loads(row.message) if isinstance(row.message, str) else row.message

        # Update the message and check if changes were made
        if _migrate_message_content(message):
            update_data.append({PRIMARY_KEY: row[PRIMARY_KEY], "message": json.dumps(message)})

    if len(update_data) > 0:
        logger.info(f"Updating {len(update_data)} rows in {TABLE_NAME}")
        connection.execute(
            sa.text(
                f"""
                UPDATE {TABLE_NAME}
                SET message = :message
                WHERE {PRIMARY_KEY} = :{PRIMARY_KEY}
            """
            ).bindparams(
                sa.bindparam(PRIMARY_KEY, type_=sa.String),
                sa.bindparam("message", type_=sa.Text),
            ),
            update_data,
        )
    else:
        logger.info(f"No updates needed for {TABLE_NAME}")


def downgrade() -> None:
    """Downgrade schema - not implemented."""
    # Downgrade is not implemented as requested
    pass
