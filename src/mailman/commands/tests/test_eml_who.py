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

"""Test the Join and Leave commands."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.commands.eml_who import Who
from mailman.email.message import Message
from mailman.interfaces.command import ContinueProcessing
from mailman.interfaces.member import DeliveryMode, DeliveryStatus, MemberRole
from mailman.model.roster import RosterVisibility
from mailman.runners.command import Results
from mailman.testing.helpers import subscribe
from mailman.testing.layers import ConfigLayer


class TestWho(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._command = Who()
        self._member = subscribe(self._mlist, 'Cate')
        subscribe(self._mlist, 'Doug')
        subscribe(self._mlist, 'Anne')
        bmember = subscribe(self._mlist, 'Bart')
        emember = subscribe(self._mlist, 'Elly')
        subscribe(self._mlist, 'Fred')
        bmember.preferences.delivery_status = DeliveryStatus.by_moderator
        emember.preferences.delivery_mode = DeliveryMode.mime_digests
        self._moderator = subscribe(self._mlist, 'Greg',
                                    role=MemberRole.moderator)
        self._owner = subscribe(self._mlist, 'Irma', role=MemberRole.owner)
        self._expected = """\
The results of your email command are provided below.

Members of the ant@example.com mailing list:
    Anne Person <aperson@example.com>
    Bart Person <bperson@example.com>
    Cate Person <cperson@example.com>
    Doug Person <dperson@example.com>
    Elly Person <eperson@example.com>
    Fred Person <fperson@example.com>
"""
        self._noperm = """\
The results of your email command are provided below.

You are not authorized to see the membership list.
"""

    def test_public_roster(self):
        self._mlist.member_roster_visibility = RosterVisibility.public
        # An unknown address should be able to get a public roster.
        msg = Message()
        msg['From'] = 'unknown@example.com'
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(self._expected, results._output.getvalue())

    def test_non_public_roster(self):
        self._mlist.member_roster_visibility = RosterVisibility.members
        # An unknown address should not be able to get a non-public roster.
        msg = Message()
        msg['From'] = 'unknown@example.com'
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.no, ret)
        self.assertEqual(self._noperm, results._output.getvalue())

    def test_member_visible_roster(self):
        self._mlist.member_roster_visibility = RosterVisibility.members
        # A member should be able to get a member visible roster.
        msg = Message()
        msg['From'] = self._member.address.email
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(self._expected, results._output.getvalue())

    def test_member_visible_roster_owner(self):
        self._mlist.member_roster_visibility = RosterVisibility.members
        # An owner should be able to get a member visible roster.
        msg = Message()
        msg['From'] = self._owner.address.email
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(self._expected, results._output.getvalue())

    def test_member_visible_roster_moderator(self):
        self._mlist.member_roster_visibility = RosterVisibility.members
        # A moderator should be able to get a member visible roster.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(self._expected, results._output.getvalue())

    def test_moderator_visible_roster(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # A member should not be able to get a moderator visible roster.
        msg = Message()
        msg['From'] = self._member.address.email
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.no, ret)
        self.assertEqual(self._noperm, results._output.getvalue())

    def test_moderator_visible_roster_nonmember(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # An unknown address can't get a moderator visible roster.
        msg = Message()
        msg['From'] = 'unknown@example.com'
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.no, ret)
        self.assertEqual(self._noperm, results._output.getvalue())

    def test_moderaror_visible_roster_owner(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # An owner should be able to get a moderator visible roster.
        msg = Message()
        msg['From'] = self._owner.address.email
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(self._expected, results._output.getvalue())

    def test_moderaror_visible_roster_moderator(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # A moderator should be able to get a moderator visible roster.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        ret = self._command.process(self._mlist, msg, {}, (), results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(self._expected, results._output.getvalue())

    def test_args_delivery_no(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # Test delivery=disabled.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        args = ['delivery=disabled']
        ret = self._command.process(self._mlist, msg, {}, args, results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(results._output.getvalue(), """\
The results of your email command are provided below.

Members of the ant@example.com mailing list:
    Bart Person <bperson@example.com>
""")

    def test_args_delivery_yes(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # Test delivery=enabled.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        args = ['delivery=enabled']
        ret = self._command.process(self._mlist, msg, {}, args, results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(results._output.getvalue(), """\
The results of your email command are provided below.

Members of the ant@example.com mailing list:
    Anne Person <aperson@example.com>
    Cate Person <cperson@example.com>
    Doug Person <dperson@example.com>
    Elly Person <eperson@example.com>
    Fred Person <fperson@example.com>
""")

    def test_args_delivery_bogus(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # Test delivery=bogus.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        args = ['delivery=bogus']
        ret = self._command.process(self._mlist, msg, {}, args, results)
        self.assertIs(ContinueProcessing.no, ret)
        self.assertEqual(results._output.getvalue(), """\
The results of your email command are provided below.

Unrecognized or invalid argument(s):
delivery=bogus
""")

    def test_args_mode_yes(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # Test mode=digest.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        args = ['mode=digest']
        ret = self._command.process(self._mlist, msg, {}, args, results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(results._output.getvalue(), """\
The results of your email command are provided below.

Members of the ant@example.com mailing list:
    Elly Person <eperson@example.com>
""")

    def test_args_mode_no(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # Test mode=regular.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        args = ['mode=regular']
        ret = self._command.process(self._mlist, msg, {}, args, results)
        self.assertIs(ContinueProcessing.yes, ret)
        self.assertEqual(results._output.getvalue(), """\
The results of your email command are provided below.

Members of the ant@example.com mailing list:
    Anne Person <aperson@example.com>
    Bart Person <bperson@example.com>
    Cate Person <cperson@example.com>
    Doug Person <dperson@example.com>
    Fred Person <fperson@example.com>
""")

    def test_args_mode_bogus(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # Test mode=bogus.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        args = ['mode=bogus']
        ret = self._command.process(self._mlist, msg, {}, args, results)
        self.assertIs(ContinueProcessing.no, ret)
        self.assertEqual(results._output.getvalue(), """\
The results of your email command are provided below.

Unrecognized or invalid argument(s):
mode=bogus
""")

    def test_args_multi_bogus(self):
        self._mlist.member_roster_visibility = RosterVisibility.moderators
        # Test multiple args, some bogus.
        msg = Message()
        msg['From'] = self._moderator.address.email
        results = Results()
        args = ['mode=regular', 'digest=bogus', 'mode=bogus', 'delivery=none']
        ret = self._command.process(self._mlist, msg, {}, args, results)
        self.assertIs(ContinueProcessing.no, ret)
        self.assertEqual(results._output.getvalue(), """\
The results of your email command are provided below.

Unrecognized or invalid argument(s):
digest=bogus
mode=bogus
delivery=none
""")
