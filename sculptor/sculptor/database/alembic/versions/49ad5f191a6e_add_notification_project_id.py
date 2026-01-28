"""Add Notification.project_id.

Revision ID: 49ad5f191a6e
Revises: ddd5ef49a520
Create Date: 2025-11-28 16:49:36.634052

"""

from typing import Sequence

import sqlalchemy as sa
from alembic import context
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "49ad5f191a6e"
down_revision: str | None = "ddd5ef49a520"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column("notification", sa.Column("project_id", sa.String(), nullable=True))
    dialect_name = context.get_context().dialect.name
    if dialect_name == "sqlite":
        with op.batch_alter_table("notification") as batch_op:
            batch_op.alter_column("user_reference", existing_type=sa.VARCHAR(), nullable=True)
    else:
        op.alter_column("notification", "user_reference", existing_type=sa.VARCHAR(), nullable=True)


def downgrade() -> None:
    """Downgrade schema."""
    raise NotImplementedError()
