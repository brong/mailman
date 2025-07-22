# Copyright (C) 2015-2025 by the Free Software Foundation, Inc.
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

"""Test the `mailman members` command."""

import unittest

from click.testing import CliRunner
from mailman.commands.cli_changeaddress import changeaddress
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.layers import ConfigLayer
from zope.component import getUtility


class TestCLIFindMember(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._um = getUtility(IUserManager)
        self._adr1 = self._um.create_address('anne@example.com', 'Anne Person')
        self._adr2 = self._um.create_address('bart@example.com', 'Bart Person')
        self._command = CliRunner()

    def test_basic_change(self):
        # Test a simple change of address.
        result = self._command.invoke(changeaddress, ('anne@example.com',
                                                      'anne@example.net'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, """\
Address changed from anne@example.com to anne@example.net.
""")
        self.assertIsNone(self._um.get_address('anne@example.com'))
        self.assertIn('<Address: Anne Person <anne@example.net>',
                      repr(self._um.get_address('anne@example.net')))

    def test_change_to_existing_fails(self):
        # Test trying to change to and existing address.
        result = self._command.invoke(changeaddress, ('anne@example.com',
                                                      'bart@example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.output, """\
Usage: changeaddress [OPTIONS] OLD_ADDRESS NEW_ADDRESS
Try 'changeaddress --help' for help.

Error: Address bart@example.com already exists; can't change.
""")

    def test_change_from_nonexisting_fails(self):
        # Test trying to change a nonexistant address.
        result = self._command.invoke(changeaddress, ('anne@example.net',
                                                      'anne@example.edu'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.output, """\
Usage: changeaddress [OPTIONS] OLD_ADDRESS NEW_ADDRESS
Try 'changeaddress --help' for help.

Error: Address anne@example.net not found.
""")

    def test_case_change_only(self):
        # Test that we can change case of existing address.
        result = self._command.invoke(changeaddress, ('anne@example.com',
                                                      'Anne@example.com'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, """\
Address changed from anne@example.com to Anne@example.com.
""")
        self.assertIn('<Address: Anne Person <Anne@example.com>',
                      repr(self._um.get_address('anne@example.com')))

    def test_change_to_invalid_address(self):
        # Test trying to change to an invalid address.
        result = self._command.invoke(changeaddress, ('anne@example.com',
                                                      'invalid_address'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.output, """\
Usage: changeaddress [OPTIONS] OLD_ADDRESS NEW_ADDRESS
Try 'changeaddress --help' for help.

Error: Address invalid_address is not a valid email address.
""")

    def test_change_not_different(self):
        # Test no change.
        result = self._command.invoke(changeaddress, ('anne@example.com',
                                                      'anne@example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(result.output, """\
Usage: changeaddress [OPTIONS] OLD_ADDRESS NEW_ADDRESS
Try 'changeaddress --help' for help.

Error: Addresses are not different.  Nothing to change.
""")
