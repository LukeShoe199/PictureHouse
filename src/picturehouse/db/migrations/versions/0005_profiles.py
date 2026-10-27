"""profiles: who's watching, kids libraries, progress per profile

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-27 22:30:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "profile",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=40), nullable=False),
        sa.Column("child", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("name"),
    )
    # The house default (profile 1): everything watched so far is its history.
    op.execute(
        "INSERT INTO profile (id, name, child, created_at) VALUES (1, 'Main', 0, CURRENT_TIMESTAMP)"
    )
    op.create_table(
        "device_profile",
        sa.Column("address", sa.String(length=64), nullable=False),
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profile.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("address"),
    )
    with op.batch_alter_table("library_root", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("kids", sa.Boolean(), nullable=False, server_default=sa.false())
        )
    # Progress gains a profile: the primary key changes, so rebuild the table.
    op.create_table(
        "playback_state_new",
        sa.Column("profile_id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Float(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["profile_id"], ["profile.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["media_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("profile_id", "item_id"),
    )
    op.execute(
        "INSERT INTO playback_state_new (profile_id, item_id, position, completed, updated_at) "
        "SELECT 1, item_id, position, completed, updated_at FROM playback_state"
    )
    op.drop_table("playback_state")
    op.rename_table("playback_state_new", "playback_state")


def downgrade() -> None:
    op.create_table(
        "playback_state_old",
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("position", sa.Float(), nullable=False),
        sa.Column("completed", sa.Boolean(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["media_item.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("item_id"),
    )
    op.execute(
        "INSERT INTO playback_state_old (item_id, position, completed, updated_at) "
        "SELECT item_id, position, completed, updated_at FROM playback_state WHERE profile_id = 1"
    )
    op.drop_table("playback_state")
    op.rename_table("playback_state_old", "playback_state")
    with op.batch_alter_table("library_root", schema=None) as batch_op:
        batch_op.drop_column("kids")
    op.drop_table("device_profile")
    op.drop_table("profile")
