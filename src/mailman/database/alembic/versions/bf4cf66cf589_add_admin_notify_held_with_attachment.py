# Copyright (C) 2020-2025 by the Free Software Foundation, Inc.
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

"""add_admin_notify_held_with_attachment

Revision ID: bf4cf66cf589
Revises: 8cc1f79f4459
Create Date: 2025-03-04 14:02:37.323219

"""
import sqlalchemy as sa

from alembic import op
from mailman.database.helpers import exists_in_db, is_sqlite


# revision identifiers, used by Alembic.
revision = 'bf4cf66cf589'
down_revision = '8cc1f79f4459'


def upgrade():
    if not exists_in_db(
            op.get_bind(), 'mailinglist',
            'admin_notify_held_with_attachment'):
        op.add_column(                                        # pragma: nocover
            'mailinglist',
            sa.Column('admin_notify_held_with_attachment', sa.BOOLEAN(),
                      nullable=True))
        # Set the default data.
        mlist = sa.sql.table(                                 # pragma: nocover
            'mailinglist',
            sa.sql.column('admin_notify_held_with_attachment',
                          sa.BOOLEAN())
            )
        op.execute(mlist.update().values(                     # pragma: nocover
            {'admin_notify_held_with_attachment': True}))


def downgrade():
    if not is_sqlite(op.get_bind()):
        # SQLite does not support dropping columns.
        op.drop_column(                                       # pragma: nocover
            'mailinglist',
            'admin_notify_held_with_attachment')
