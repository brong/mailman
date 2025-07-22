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

"""increase_info

Revision ID: ec5fe422e27c
Revises: 472e6d713047
Create Date: 2021-04-28 17:46:28.879085

"""

from alembic import op
from mailman.database.types import SAText, SAUnicode


# revision identifiers, used by Alembic.
revision = 'ec5fe422e27c'
down_revision = '472e6d713047'


def upgrade():
    # Mailing List info column needs to be bigger.
    with op.batch_alter_table('mailinglist') as batch_op:
        batch_op.alter_column('info', type_=SAText)


def downgrade():
    with op.batch_alter_table('mailinglist') as batch_op:
        batch_op.alter_column('info', type_=SAUnicode)
