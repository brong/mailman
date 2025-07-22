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

"""increase_workflowstate_data_column_size

Revision ID: 2b73fbcc97c9
Revises: 9735f5e5dbdb
Create Date: 2020-11-14 12:25:50.833363

"""

from alembic import op
from mailman.database.types import SAUnicode, SAUnicodeLarge


# revision identifiers, used by Alembic.
revision = '2b73fbcc97c9'
down_revision = '9735f5e5dbdb'


def upgrade():
    # Adding the invitation parameter can make the data value too long for
    # MySQL SaUnicode.
    with op.batch_alter_table('workflowstate') as batch_op:
        batch_op.alter_column('data', type_=SAUnicodeLarge)


def downgrade():
    with op.batch_alter_table('workflowstate') as batch_op:
        batch_op.alter_column('data', type_=SAUnicode)
