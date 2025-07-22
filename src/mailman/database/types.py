# Copyright (C) 2007-2025 by the Free Software Foundation, Inc.
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

"""Database type conversions."""

import uuid

from public import public
from sqlalchemy import Integer
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.types import CHAR, Text, TypeDecorator, Unicode


@public
class Enum(TypeDecorator):
    """Handle Python 3.4 style enums.

    Stores an integer-based Enum as an integer in the database, and
    converts it on-the-fly.
    """
    impl = Integer
    cache_ok = True

    def __init__(self, enum, *args, **kw):
        super().__init__(*args, **kw)
        self.enum = enum

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return value.value

    def process_result_value(self, value, dialect):
        if value is None:
            return None
        return self.enum(value)


@public
class UUID(TypeDecorator):
    """Platform-independent GUID type.

    Uses Postgresql's UUID type, otherwise uses
    CHAR(32), storing as stringified hex values.

    """
    impl = CHAR
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == 'postgresql':
            return dialect.type_descriptor(postgresql.UUID())
        else:
            return dialect.type_descriptor(CHAR(32))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        elif dialect.name == 'postgresql':
            return str(value)
        else:
            if not isinstance(value, uuid.UUID):
                value = uuid.UUID(value)
            return '%.32x' % value.int

    def process_result_value(self, value, dialect):
        # postgres returns UUID object by default in SQLAlchemy 2.0.0
        # and str value on sqlalchemy < 2.0.0.
        if (value is None) or (isinstance(value, uuid.UUID)):
            return value
        else:
            return uuid.UUID(value)


@public
class SAUnicode(TypeDecorator):
    """Unicode datatype to support fixed length VARCHAR in MySQL.

    This type compiles to VARCHAR(255) in case of MySQL, and in case of
    other dialects defaults to the Unicode type.  This was created so
    that we don't have to alter the output of the default Unicode data
    type and it can still be used if needed in the codebase.

    WARNING: On MySQL it will not be possible to insert emojis or other
    4-byte utf-8 encoded characters. This should only be used if the entire
    column needs to be indexed, otherwise use SAUnicode4Byte.
    """
    impl = Unicode
    cache_ok = True


@compiles(SAUnicode)
def default_sa_unicode(element, compiler, **kw):
    return compiler.visit_unicode(element, **kw)


@compiles(SAUnicode, 'mysql')
def compile_sa_unicode(element, compiler, **kw):
    # We hardcode the collate here to make string comparison case sensitive.
    return 'VARCHAR(255) COLLATE utf8_bin'


@public
class SAUnicode4Byte(TypeDecorator):
    """Unicode datatype to support fixed length VARCHAR in MySQL.

    This type compiles to VARCHAR(255) in case of MySQL, and in case of
    other dialects defaults to the Unicode type.  This was created so
    that we don't have to alter the output of the default Unicode data
    type and it can still be used if needed in the codebase.
    """
    impl = Unicode
    cache_ok = True


@compiles(SAUnicode4Byte)
def default_sa_unicode_4byte(element, compiler, **kw):
    return compiler.visit_unicode(element, **kw)


@compiles(SAUnicode4Byte, 'mysql')
def compile_sa_unicode_4byte(element, compiler, **kw):  # pragma: nocover
    # We hardcode the collate here to make string comparison case sensitive.
    return 'VARCHAR(255) COLLATE utf8mb4_bin'


@public
class SAUnicodeLarge(TypeDecorator):
    """Similar to SAUnicode type, but compiles to VARCHAR(510).

    This is double size of SAUnicode defined above.
    """
    impl = Unicode
    cache_ok = True


@compiles(SAUnicodeLarge, 'mysql')
def compile_sa_unicode_large(element, compiler, **kw):  # pragma: nocover
    # We hardcode the collate here to make string comparison case sensitive.
    return 'VARCHAR(510) COLLATE utf8mb4_bin'


@compiles(SAUnicodeLarge)
def default_sa_unicode_large(element, compiler, **kw):
    return compiler.visit_unicode(element, **kw)


@public
class SAUnicodeXL(TypeDecorator):
    """Similar to SAUnicode type, but compiles to VARCHAR(20000).

    This allows very long values up to 20,000 bytes for cases where
    SAUnicodeLarge is not enough.

    Because of MySQL issues columns of this type should either not be indexed
    or should be indexed with an Index() function specifying mysql_length=N
    where N is (probably much) less than 767.
    See https://docs.sqlalchemy.org/en/latest/dialects/mysql.html#index-length
    """
    impl = Unicode
    cache_ok = True


@compiles(SAUnicodeXL, 'mysql')
def compile_sa_unicode_xl(element, compiler, **kw):
    # We hardcode the collate here to make string comparison case sensitive.
    return 'VARCHAR(20000) COLLATE utf8_bin'              # pragma: nocover


@compiles(SAUnicodeXL)
def default_sa_unicode_xl(element, compiler, **kw):
    return compiler.visit_unicode(element, **kw)


@public
class SAText(TypeDecorator):
    """Text datatype to support large text content in MySQL.

    This type compiles to TEXT COLLATE utf8mb4_bin in case of MySQL, and in
    case of other dialects defaults to the Text type.
    """
    impl = Text
    cache_ok = True


@compiles(SAText)
def default_sa_text(element, compiler, **kw):
    return compiler.visit_text(element, **kw)


@compiles(SAText, 'mysql')
def compile_sa_text(element, compiler, **kw):
    # We hardcode the collate here to make string comparison case sensitive.
    return 'TEXT COLLATE utf8mb4_bin'                     # pragma: nocover
