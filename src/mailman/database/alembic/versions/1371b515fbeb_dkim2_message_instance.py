# Copyright (C) 2026 by the Free Software Foundation, Inc.
#
# This file is part of GNU Mailman.
#
# GNU Mailman is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# GNU Mailman is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# GNU Mailman.  If not, see <https://www.gnu.org/licenses/>.

"""add_dkim2_message_instance

Revision ID: 1371b515fbeb
Revises: 98224512c9c2
Create Date: 2026-11-13 22:46:19.000000

"""
import sqlalchemy as sa

from alembic import op
from mailman.database.helpers import exists_in_db, is_sqlite


# revision identifiers, used by Alembic.
revision = '1371b515fbeb'
down_revision = '98224512c9c2'


def upgrade():
    if not exists_in_db(
            op.get_bind(), 'mailinglist', 'dkim2_message_instance'):
        op.add_column(                                        # pragma: nocover
            'mailinglist',
            sa.Column('dkim2_message_instance', sa.BOOLEAN(),
                      nullable=True))
        # Set the default data.
        mlist = sa.sql.table(                                 # pragma: nocover
            'mailinglist',
            sa.sql.column('dkim2_message_instance', sa.BOOLEAN())
            )
        op.execute(mlist.update().values(                     # pragma: nocover
            {'dkim2_message_instance': True}))


def downgrade():
    if not is_sqlite(op.get_bind()):
        # SQLite does not support dropping columns.
        op.drop_column(                                       # pragma: nocover
            'mailinglist',
            'dkim2_message_instance')
