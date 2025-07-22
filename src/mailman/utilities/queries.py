# Copyright (C) 2016-2025 by the Free Software Foundation, Inc.
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

"""Some helpers for queries."""

from collections.abc import Sequence
from public import public
from sqlalchemy import func, select


@public
class QuerySequence(Sequence):
    """A simple wrapper class around database query results.

    Use this to provide a sequence-like API around query results, such as
    being able to use len() and slicing, where the results objects don't
    natively provide them.

    :param session: SQLAlchemy session object that will be used to query
        the provided query with sequence like behavior.
    :param query: The raw Select query from SQLAlchemy.
    """
    def __init__(self, session, query=None):
        super().__init__()
        self._query = query
        self.session = session

    def __len__(self):
        return (
            0 if self._query is None
            else (
                self
                .session
                .scalars(select(func.count()).select_from(self._query))
                .one())
            )

    def __getitem__(self, index):
        if self._query is None:
            raise IndexError('index out of range')
        if isinstance(index, slice):
            if index.step is not None:
                raise IndexError(
                    'Steps are not supported for QuerySequence')
            return (
                self
                .session
                .scalars(self._query.slice(index.start, index.stop))
                .all()
                )
        else:
            return (
                self
                .session
                .scalars(self._query.offset(index).limit(1))
                .one()
                )

    def __iter__(self):
        if self._query is None:
            return []
        yield from self.session.execute(self._query).scalars()
