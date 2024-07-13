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

"""Test the `mailman members` command."""

import unittest

from click.testing import CliRunner
from mailman.app.lifecycle import create_list
from mailman.commands.cli_findmember import findmember
from mailman.interfaces.member import MemberRole
from mailman.testing.helpers import subscribe
from mailman.testing.layers import ConfigLayer


class TestCLIFindMember(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlistb = create_list('bee@example.com')
        self._command = CliRunner()

    def test_basic_find(self):
        # Test a simple find of one membership.
        subscribe(self._mlist, 'Anne')
        result = self._command.invoke(findmember, ('.',))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, """\
Email: aperson@example.com
    List: ant.example.com
        MemberRole.member
""")

    def test_basic_find_with_more_complex_pattern(self):
        # Test a find of one membership with a more complex pattern.
        subscribe(self._mlist, 'Anne')
        result = self._command.invoke(findmember, ('^.*$',))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, """\
Email: aperson@example.com
    List: ant.example.com
        MemberRole.member
""")

    def test_no_role(self):
        # Test for no matching roles.
        subscribe(self._mlist, 'Anne')
        result = self._command.invoke(findmember, ('--role', 'owner', '.',))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, '')

    def test_no_pattern_match(self):
        # test for no address matching pattern.
        subscribe(self._mlist, 'Anne')
        result = self._command.invoke(findmember, ('doesnt_match',))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, '')

    def test_case_insensitive_pattern(self):
        # Test that patterns are case insensitive.
        subscribe(self._mlist, 'Anne')
        result = self._command.invoke(findmember, ('APerson',))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, """\
Email: aperson@example.com
    List: ant.example.com
        MemberRole.member
""")

    def test_user_and_address(self):
        # Test finding members subscribed as user and as address.
        subscribe(self._mlist, 'Anne', as_user=True)
        subscribe(self._mlistb, 'Bart', as_user=False)
        result = self._command.invoke(findmember, ('.'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, """\
Email: aperson@example.com
    List: ant.example.com
        MemberRole.member
Email: bperson@example.com
    List: bee.example.com
        MemberRole.member
""")

    def test_only_role(self):
        # Test only finding requested role.
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlistb, 'Bart', role=MemberRole.owner)
        result = self._command.invoke(findmember, ('--role', 'owner', '.'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, """\
Email: bperson@example.com
    List: bee.example.com
        MemberRole.owner
""")

    def test_find_only_admins(self):
        # Test the administrators role.
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlistb, 'Bart', role=MemberRole.owner)
        subscribe(self._mlist, 'Cate', role=MemberRole.moderator)
        subscribe(self._mlistb, 'Doug', role=MemberRole.nonmember)
        result = self._command.invoke(findmember, ('--role',
                                                   'administrator',
                                                   '.'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, """\
Email: bperson@example.com
    List: bee.example.com
        MemberRole.owner
Email: cperson@example.com
    List: ant.example.com
        MemberRole.moderator
""")
