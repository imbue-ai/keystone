"""move system prompt onto task

Revision ID: d121a5ed0b3d
Revises: 0a8cd671469e
Create Date: 2025-10-07 16:03:26.672534

"""

import json
from typing import Sequence

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d121a5ed0b3d"
down_revision: str | None = "0a8cd671469e"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    connection = op.get_bind()
    dialect = connection.dialect.name

    # Step 1: For each task, find the last UpdateSystemPromptUserMessage and update task input_data
    if dialect == "postgresql":
        # PostgreSQL query
        tasks_with_messages = (
            connection.execute(
                sa.text("""
                WITH last_system_prompts AS (
                    SELECT
                        sam.task_id,
                        sam.message->>'text' as prompt_text,
                        ROW_NUMBER() OVER (PARTITION BY sam.task_id ORDER BY sam.created_at DESC) as rn
                    FROM saved_agent_message sam
                    WHERE sam.message->>'object_type' = 'UpdateSystemPromptUserMessage'
                )
                SELECT task_id, prompt_text
                FROM last_system_prompts
                WHERE rn = 1
            """)
            )
            .mappings()
            .all()
        )
    else:  # SQLite
        tasks_with_messages = (
            connection.execute(
                sa.text("""
                WITH last_system_prompts AS (
                    SELECT
                        sam.task_id,
                        json_extract(sam.message, '$.text') as prompt_text,
                        ROW_NUMBER() OVER (PARTITION BY sam.task_id ORDER BY sam.created_at DESC) as rn
                    FROM saved_agent_message sam
                    WHERE json_extract(sam.message, '$.object_type') = 'UpdateSystemPromptUserMessage'
                )
                SELECT task_id, prompt_text
                FROM last_system_prompts
                WHERE rn = 1
            """)
            )
            .mappings()
            .all()
        )

    # Step 2: Update each task's input_data to set systemPrompt
    for row in tasks_with_messages:
        task_id = row["task_id"]
        prompt_text = row["prompt_text"]

        # Fetch the current input_data
        if dialect == "postgresql":
            result = connection.execute(
                sa.text("SELECT input_data FROM task_latest WHERE object_id = :task_id"), {"task_id": task_id}
            ).fetchone()
        else:  # SQLite
            result = connection.execute(
                sa.text("SELECT input_data FROM task_latest WHERE object_id = :task_id"), {"task_id": task_id}
            ).fetchone()

        if result:
            input_data = json.loads(result[0]) if isinstance(result[0], str) else result[0]

            # Set the systemPrompt field
            input_data["systemPrompt"] = prompt_text

            # Update both task and task_latest tables
            connection.execute(
                sa.text("UPDATE task SET input_data = :input_data WHERE object_id = :task_id"),
                {"input_data": json.dumps(input_data), "task_id": task_id},
            )
            connection.execute(
                sa.text("UPDATE task_latest SET input_data = :input_data WHERE object_id = :task_id"),
                {"input_data": json.dumps(input_data), "task_id": task_id},
            )

    # Step 3: Delete UpdateSystemPromptUserMessage and StopAgentUserMessage messages
    if dialect == "postgresql":
        connection.execute(
            sa.text("""
                DELETE FROM saved_agent_message
                WHERE message->>'object_type' IN ('UpdateSystemPromptUserMessage', 'StopAgentUserMessage')
            """)
        )
    else:  # SQLite
        connection.execute(
            sa.text("""
                DELETE FROM saved_agent_message
                WHERE json_extract(message, '$.object_type') IN ('UpdateSystemPromptUserMessage', 'StopAgentUserMessage')
            """)
        )


def downgrade() -> None:
    """Downgrade schema."""
    pass
