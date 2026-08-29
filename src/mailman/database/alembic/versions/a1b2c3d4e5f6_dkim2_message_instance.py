"""dkim2_message_instance per-list flag

Revision ID: a1b2c3d4e5f6
Revises: bf4cf66cf589
Create Date: 2026-03-23 00:00:00.000000

"""

import sqlalchemy as sa

from alembic import op
from mailman.database.helpers import exists_in_db


# revision identifiers, used by Alembic.
revision = 'a1b2c3d4e5f6'
down_revision = 'bf4cf66cf589'


def upgrade():
    if not exists_in_db(op.get_bind(), 'mailinglist', 'dkim2_message_instance'):
        op.add_column('mailinglist', sa.Column(
            'dkim2_message_instance',
            sa.Boolean,
            nullable=True))
    mlist = sa.sql.table(
        'mailinglist',
        sa.sql.column('dkim2_message_instance', sa.Boolean),
    )
    op.execute(mlist.update().values(
        dkim2_message_instance=op.inline_literal(True)))


def downgrade():
    with op.batch_alter_table('mailinglist') as batch_op:
        batch_op.drop_column('dkim2_message_instance')
