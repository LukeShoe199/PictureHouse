"""kids approvals and hidden libraries

Titles outside kids libraries approved for child profiles (films and other
single items by a flag, series by folder name), and libraries a profile
chooses not to see.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("media_item", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kids_ok", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    op.create_table(
        "kids_show",
        sa.Column("parsed_show", sa.Text(), nullable=False),
        sa.Column("added_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("parsed_show"),
    )
    op.create_table(
        "hidden_library",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("root_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profile.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["root_id"], ["library_root.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id", "root_id"),
    )


def downgrade() -> None:
    op.drop_table("hidden_library")
    op.drop_table("kids_show")
    with op.batch_alter_table("media_item", schema=None) as batch_op:
        batch_op.drop_column("kids_ok")
