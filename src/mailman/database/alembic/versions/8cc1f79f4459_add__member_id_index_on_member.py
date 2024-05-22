"""add _member_id index on member

Revision ID: 8cc1f79f4459
Revises: 729a8f2045da
Create Date: 2024-05-20 11:45:37.682640

"""

from alembic import op


# revision identifiers, used by Alembic.
revision = "8cc1f79f4459"
down_revision = "729a8f2045da"


def upgrade():
    op.create_index(op.f("ix_member__member_id"), "member", ["_member_id"], unique=False)  # noqa: E501


def downgrade():
    op.drop_index(op.f("ix_member__member_id"), table_name="member")
