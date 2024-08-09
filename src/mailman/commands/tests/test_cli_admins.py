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

"""Test the `mailman admins` command."""

import unittest

from click.testing import CliRunner
from mailman.app.lifecycle import create_list
from mailman.commands.cli_admins import admins
from mailman.testing.layers import ConfigLayer


class TestCLIAdmins(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._command = CliRunner()

    def test_no_such_list(self):
        result = self._command.invoke(admins, ('-a', 'aperson@example.com',
                                               'bee.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: admins [OPTIONS] LISTSPEC\n'
            'Try \'admins --help\' for help.\n\n'
            'Error: No such list: bee.example.com\n')

    def test_bad_email_add(self):
        result = self._command.invoke(admins, ('-a', 'bad', 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: admins [OPTIONS] LISTSPEC\n'
            'Try \'admins --help\' for help.\n\n'
            'Error: Invalid email address: bad\n')

    def test_bad_email_with_dn_add(self):
        result = self._command.invoke(admins, ('-a', 'Dn <bad@>',
                                               'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: admins [OPTIONS] LISTSPEC\n'
            'Try \'admins --help\' for help.\n\n'
            'Error: Invalid email address: Dn <bad@>\n')

    def test_bad_email_delete(self):
        result = self._command.invoke(admins, ('-d', 'bad', 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: admins [OPTIONS] LISTSPEC\n'
            'Try \'admins --help\' for help.\n\n'
            'Error: Invalid email address: bad\n')

    def test_no_add_delete(self):
        result = self._command.invoke(admins, ('ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: admins [OPTIONS] LISTSPEC\n'
            'Try \'admins --help\' for help.\n\n'
            'Error: Nothing to add or delete.\n')

    def test_bad_role(self):
        result = self._command.invoke(admins, ('-a', 'aperson@example.com',
                                               '-r' 'bad', 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: admins [OPTIONS] LISTSPEC\n'
            'Try \'admins --help\' for help.\n\n'
            'Error: Invalid value for \'--role\' / \'-r\': \'bad\' is not one '
            'of \'owner\', \'moderator\'.\n')
