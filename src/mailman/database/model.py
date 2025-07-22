# Copyright (C) 2006-2025 by the Free Software Foundation, Inc.
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

"""Base class for all database classes."""

from contextlib import closing
from mailman.config import config
from public import public
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import close_all_sessions


class ModelMeta:
    """The custom metaclass for all model base classes.

    This is used in the test suite to quickly reset the database after each
    test.  It works by iterating over all the tables, deleting each.  The test
    suite will then recreate the tables before each test.
    """
    @staticmethod
    def _reset(db):
        config.db.store.expunge_all()
        close_all_sessions()
        # Make sure we delete/expunge all objects before we drop tables.
        with closing(config.db.engine.connect()) as connection:
            _ = connection.begin()

            try:
                # Delete all the tables in reverse foreign key dependency
                # order.
                # https://docs.sqlalchemy.org/en/latest/core/metadata.html \
                # #accessing-tables-and-columns
                for table in reversed(Model.metadata.sorted_tables):
                    connection.execute(table.delete())
            except:                             # noqa: E722 pragma: nocover
                connection.rollback()
                raise
            else:
                connection.commit()


Model = declarative_base(cls=ModelMeta)
public(Model=Model)
