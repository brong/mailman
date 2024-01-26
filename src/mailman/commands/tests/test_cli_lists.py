# Copyright (C) 2015-2023 by the Free Software Foundation, Inc.
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

"""Additional tests for the `lists` command line subcommand."""

import unittest

from click.testing import CliRunner
from mailman.app.lifecycle import create_list
from mailman.commands.cli_lists import lists
from mailman.interfaces.domain import IDomainManager
from mailman.testing.layers import ConfigLayer
from zope.component import getUtility


class TestLists(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._command = CliRunner()

    def test_lists_with_domain_option(self):
        # LP: #1166911 - non-matching lists were returned.
        getUtility(IDomainManager).add(
            'example.net', 'An example domain.')
        create_list('test1@example.com')
        create_list('test2@example.com')
        # Only this one should show up.
        create_list('test3@example.net')
        create_list('test4@example.com')
        result = self._command.invoke(lists, ('--domain', 'example.net'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.output,
            '1 matching mailing lists found:\ntest3@example.net\n')

    def test_lists_with_description(self):
        getUtility(IDomainManager).add(
            'example.net', 'An example domain.')
        mlist1 = create_list('test1@example.com')
        mlist1.description = ('Test list number 1 plus a whole lot more stuff '
                              'and even longer')
        create_list('test2@example.com')
        mlist3 = create_list('test3@example.net')
        mlist3.description = 'Test list number 3'
        create_list('test4@example.com')
        result = self._command.invoke(lists, ('-d'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.output,
            '4 matching mailing lists found:\n'
            'test1@example.com - Test list number 1 plus a whole lot more '
            'stuff and even longer\n'
            'test2@example.com -\n'
            'test3@example.net - Test list number 3\n'
            'test4@example.com -\n')

    def test_lists_without_description(self):
        getUtility(IDomainManager).add(
            'example.net', 'An example domain.')
        mlist1 = create_list('test1@example.com')
        mlist1.description = ('Test list number 1 plus a whole lot more stuff '
                              'and even longer')
        create_list('test2@example.com')
        mlist3 = create_list('test3@example.net')
        mlist3.description = 'Test list number 3'
        create_list('test4@example.com')
        result = self._command.invoke(lists)
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.output,
            '4 matching mailing lists found:\n'
            'test1@example.com\n'
            'test2@example.com\n'
            'test3@example.net\n'
            'test4@example.com\n')

    def test_lists_with_description_and_long_name(self):
        mlist = create_list('a_very_long_list_name_with_more_than_seventy'
                            '_characters_xxxxxxx@example.com')
        mlist.description = ('Test list number 1 plus a whole lot more stuff '
                             'and even longer')
        result = self._command.invoke(lists, '-d')
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.output,
            '1 matching mailing lists found:\n'
            'a_very_long_list_name_with_more_than_seventy'
            '_characters_xxxxxxx@example.com - Test list number 1 '
            'plus a whole lot more stuff and even longer\n')
