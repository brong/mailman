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

"""Test queries."""

import unittest

from mailman.utilities.queries import QuerySequence
from operator import getitem


class TestQueries(unittest.TestCase):

    def test_index_error(self):
        query = QuerySequence(None, None)
        self.assertRaises(IndexError, getitem, query, 1)

    def test_iterate_with_none(self):
        query = QuerySequence(None, None)
        self.assertEqual(list(query), [])

    def test_getitem_with_steps(self):
        # True is definitely a bad input value here,
        # but we are passing it here only to not have
        # to be stuck by None check on query.
        query = QuerySequence(None, unittest.mock.Mock())
        self.assertRaises(IndexError, getitem, query, slice(1, 4, -1))

    def test_getitem_with_slice(self):
        query = unittest.mock.Mock()
        session = unittest.mock.Mock()
        seq = QuerySequence(session, query)
        seq[5]
        self.assertTrue(query.offset.called)
        self.assertTrue(query.offset.asseert_called_with(5))
        self.assertTrue(query.offset(5).limit.called)
        query.offset(5).limit.assert_called_with(1)
        self.assertTrue(session.scalars.called)
        query.reset_mock()
        session.reset_mock()
        seq[5:10]
        self.assertTrue(query.slice.called)
        query.slice.assert_called_with(5, 10)
        self.assertTrue(session.scalars.called)
