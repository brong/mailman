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

"""MySQL: Extend autoresponse text fields, use utf8mb4

Revision ID: 98224512c9c2
Revises: bc0c49c6dda2
Create Date: 2021-07-06 18:29:14.583453

"""
from alembic import op
from mailman.database.helpers import is_mysql
from mailman.database.types import SAText, SAUnicode4Byte
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = '98224512c9c2'
down_revision = 'bc0c49c6dda2'


def upgrade():  # pragma: nocover
    with op.batch_alter_table('mailinglist', schema=None) as batch_op:
        batch_op.alter_column(
            'autoresponse_owner_text',
            existing_type=SAUnicode4Byte(),
            type_=SAText(),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_postings_text',
            existing_type=SAUnicode4Byte(),
            type_=SAText(),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_request_text',
            existing_type=SAUnicode4Byte(),
            type_=SAText(),
            existing_nullable=True)
        if is_mysql(op.get_bind()):
            # MySQL-only, switch info to utf8mb4 charset
            batch_op.alter_column(
                'info',
                existing_type=mysql.TEXT(charset='utf8', collation='utf8_bin'),
                type_=SAText(),
                existing_nullable=True)


def downgrade():  # pragma: nocover
    with op.batch_alter_table('mailinglist', schema=None) as batch_op:
        if is_mysql(op.get_bind()):
            batch_op.alter_column(
                'info',
                existing_type=SAText(),
                type_=mysql.TEXT(charset='utf8', collation='utf8_bin'),
                existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_request_text',
            existing_type=SAText(),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_postings_text',
            existing_type=SAText(),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_owner_text',
            existing_type=SAText(),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
