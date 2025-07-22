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

"""Use utf8mb4 in MySQL

Revision ID: bc0c49c6dda2
Revises: ec5fe422e27c
Create Date: 2021-05-17 15:07:36.808010

"""
from alembic import op
from mailman.database.helpers import is_mysql
from mailman.database.types import SAUnicode4Byte, SAUnicodeLarge
from sqlalchemy.dialects import mysql


# revision identifiers, used by Alembic.
revision = 'bc0c49c6dda2'
down_revision = 'ec5fe422e27c'


def upgrade():  # pragma: nocover
    if not is_mysql(op.get_bind()):
        return
    with op.batch_alter_table('address', schema=None) as batch_op:
        batch_op.alter_column(
            'display_name',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)

    with op.batch_alter_table('domain', schema=None) as batch_op:
        batch_op.alter_column(
            'description',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)

    with op.batch_alter_table('headermatch', schema=None) as batch_op:
        batch_op.alter_column(
            'pattern',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)

    with op.batch_alter_table('mailinglist', schema=None) as batch_op:
        batch_op.alter_column(
            'autoresponse_owner_text',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_postings_text',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_request_text',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'bounce_matching_headers',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'dmarc_moderation_notice',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=510),
            type_=SAUnicodeLarge(),
            existing_nullable=True)
        batch_op.alter_column(
            'dmarc_wrapped_message_text',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=510),
            type_=SAUnicodeLarge(),
            existing_nullable=True)
        batch_op.alter_column(
            'description',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'member_moderation_notice',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'nonmember_rejection_notice',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'display_name',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'subject_prefix',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column(
            'display_name',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)
        batch_op.alter_column(
            'password',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=255),
            type_=SAUnicode4Byte(),
            existing_nullable=True)

    with op.batch_alter_table('workflowstate', schema=None) as batch_op:
        batch_op.alter_column(
            'data',
            existing_type=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                        length=510),
            type_=SAUnicodeLarge(),
            existing_nullable=True)


def downgrade():  # pragma: nocover
    if not is_mysql(op.get_bind()):
        return
    with op.batch_alter_table('workflowstate', schema=None) as batch_op:
        batch_op.alter_column(
            'data',
            existing_type=SAUnicodeLarge(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=510),
            existing_nullable=True)

    with op.batch_alter_table('user', schema=None) as batch_op:
        batch_op.alter_column(
            'password',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'display_name',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)

    with op.batch_alter_table('mailinglist', schema=None) as batch_op:
        batch_op.alter_column(
            'subject_prefix',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'display_name',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'nonmember_rejection_notice',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'member_moderation_notice',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'description',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'dmarc_wrapped_message_text',
            existing_type=SAUnicodeLarge(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=510),
            existing_nullable=True)
        batch_op.alter_column(
            'dmarc_moderation_notice',
            existing_type=SAUnicodeLarge(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=510),
            existing_nullable=True)
        batch_op.alter_column(
            'bounce_matching_headers',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_request_text',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_postings_text',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
        batch_op.alter_column(
            'autoresponse_owner_text',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)

    with op.batch_alter_table('headermatch', schema=None) as batch_op:
        batch_op.alter_column(
            'pattern',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)

    with op.batch_alter_table('domain', schema=None) as batch_op:
        batch_op.alter_column(
            'description',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)

    with op.batch_alter_table('address', schema=None) as batch_op:
        batch_op.alter_column(
            'display_name',
            existing_type=SAUnicode4Byte(),
            type_=mysql.VARCHAR(charset='utf8', collation='utf8_bin',
                                length=255),
            existing_nullable=True)
