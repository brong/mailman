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

"""add dmarc_addresses

Revision ID: 2156fc3f6f7d
Revises: 98224512c9c2
Create Date: 2023-07-07 13:37:43.370623

"""
import sqlalchemy as sa

from alembic import op
from mailman.database.helpers import exists_in_db, is_sqlite
from sqlalchemy import PickleType
from sqlalchemy.ext.mutable import MutableList


# revision identifiers, used by Alembic.
revision = '2156fc3f6f7d'
down_revision = '98224512c9c2'


def upgrade():
    # Update the schema.
    if not exists_in_db(op.get_bind(), 'mailinglist', 'dmarc_addresses'):
        # SQLite may not have removed it when downgrading.
        op.add_column('mailinglist', sa.Column(
            'dmarc_addresses',
            MutableList.as_mutable(PickleType))
            )                                                # pragma: nocover
    # Now migrate the data.  Don't import the table definition from the
    # models, it may break this migration when the model is updated in the
    # future (see the Alembic doc).
    # Use PickleType here just for setting the data.
    mlist = sa.sql.table(
        'mailinglist', sa.sql.column('dmarc_addresses', PickleType)
        )
    # Set the new attribute to the empty list.
    op.execute(mlist.update().values({'dmarc_addresses': []}))


def downgrade():
    if not is_sqlite(op.get_bind()):
        # SQLite does not support dropping columns.
        op.drop_column('mailinglist', 'dmarc_addresses')     # pragma: nocover
