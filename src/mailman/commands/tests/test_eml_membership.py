# Copyright (C) 2016-2023 by the Free Software Foundation, Inc.
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
from mailman.commands.eml_membership import Join, Leave
from mailman.email.message import Message
from mailman.interfaces.bans import IBanManager
from mailman.interfaces.mailinglist import SubscriptionPolicy
from mailman.interfaces.member import DeliveryMode
from mailman.interfaces.pending import IPendings
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.usermanager import IUserManager
from mailman.runners.command import Results
from mailman.testing.helpers import set_preferred
from mailman.testing.layers import ConfigLayer
from zope.component import getUtility


class TestLeave(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._command = Leave()

    def test_confirm_leave_not_a_member(self):
        self._mlist.unsubscription_policy = SubscriptionPolicy.confirm
        # Try to unsubscribe someone who is not a member.  Anne is a real
        # user, with a validated address, but she is not a member of the
        # mailing list.
        anne = getUtility(IUserManager).create_user('anne@example.com')
        set_preferred(anne)
        # Initiate an unsubscription.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertEqual(
            str(results).splitlines()[-1],
            'leave: anne@example.com is not a member of ant@example.com')

    def test_leave_no_sender(self):
        # Initiate an unsubscription with no sender in the message.
        msg = Message()
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertEqual(
            str(results).splitlines()[-1],
            'leave: No valid email address found to unsubscribe')

    def test_leave_no_user(self):
        # Initiate an unsubscription for a non-user.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertEqual(
            str(results).splitlines()[-1],
            'No registered user for email address: anne@example.com')


class TestJoin(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._command = Join()

    def test_join_successful(self):
        # Subscribe a member via join.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertIn('Confirmation email sent to anne@example.com',
                      str(results))

    def test_join_rfc2047_display(self):
        # Subscribe a member with RFC 2047 encoded display name via join.
        msg = Message()
        msg['From'] = '=?utf-8?q?Anne?= <anne@example.com>'
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertIn('Confirmation email sent to Anne <anne@example.com>',
                      str(results))
        # Check the pending confirmation.
        pendings = list(getUtility(IPendings).find(self._mlist,
                                                   'subscription',
                                                   confirm=False))
        self.assertEqual(1, len(pendings))
        token = pendings[0][0]
        pended = getUtility(IPendings).confirm(token, expunge=False)
        self.assertEqual('Anne', pended['display_name'])
        self.assertEqual('anne@example.com', pended['email'])

    def test_join_rfc2047_display_with_comma(self):
        # Subscribe a member with RFC 2047 encoded display name containing a
        # comma and non-ascii via join.
        msg = Message()
        msg['From'] = '=?utf-8?q?J=C3=BCnk=2C_Anne?= <anne@example.com>'
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertIn('Confirmation email sent to =?utf-8?b?SsO8bmssIEFubmU=?='
                      ' <anne@example.com>', str(results))
        # Check the pending confirmation.
        pendings = list(getUtility(IPendings).find(self._mlist,
                                                   'subscription',
                                                   confirm=False))
        self.assertEqual(1, len(pendings))
        token = pendings[0][0]
        pended = getUtility(IPendings).confirm(token, expunge=False)
        self.assertEqual('Jünk, Anne', pended['display_name'])
        self.assertEqual('anne@example.com', pended['email'])

    def test_join_digest(self):
        # Subscribe a member to digest via join.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, ('digest=mime',), results)
        self.assertIn('Confirmation email sent to anne@example.com',
                      str(results))

    def test_join_digest_works(self):
        # Subscribe a member to digest via join and verify it works.
        # Set things so the subscribe works now.
        self._mlist.subscription_policy = SubscriptionPolicy.open
        # Create a verified address for Anne.
        user = getUtility(IUserManager).make_user('anne@example.com', 'Anne')
        address = user.addresses[0]
        address.verified_on = address.registered_on
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, ('digest=mime',), results)
        # Anne is a member.
        members = list(self._mlist.members.members)
        self.assertEqual(1, len(members))
        self.assertEqual('anne@example.com', members[0].address.email)
        self.assertEqual(DeliveryMode.mime_digests, members[0].delivery_mode)

    def test_join_other(self):
        # Subscribe a different address via join.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {},
                              ('address=bob@example.com',), results)
        self.assertIn('Confirmation email sent to bob@example.com',
                      str(results))

    def test_join_other_bogus(self):
        # Try to subscribe a bogus different address via join.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {},
                              ('address=bogus',), results)
        self.assertIn('Invalid email address: bogus', str(results))

    def test_join_bad_argument(self):
        # Try to subscribe a member with a bad argument via join.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, ('digest=bogus',), results)
        self.assertIn('bad argument: digest=bogus', str(results))

    def test_join_bad_argument_name(self):
        # Try to subscribe a member with a bad argument via join.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, ('reg=bogus',), results)
        self.assertIn('bad argument: reg=bogus', str(results))

    def test_join_bad_argument_no_equal(self):
        # Try to subscribe a member with a bad argument via join.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, ('digest',), results)
        self.assertIn('bad argument: digest', str(results))

    def test_join_already_a_member(self):
        # Try to subscribe someone who is already a member.  Anne is a real
        # user, with a validated address, but she is not a member of the
        # mailing list yet.
        anne = getUtility(IUserManager).create_user('anne@example.com')
        set_preferred(anne)
        # First subscribe anne.
        ISubscriptionManager(self._mlist).register(anne, pre_verified=True,
                                                   pre_confirmed=True,
                                                   pre_approved=True)
        # Then initiate a subscription.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertEqual(
            str(results).splitlines()[-1],
            'anne@example.com is already a member of '
            'mailing list ant@example.com')

    def test_join_banned(self):
        # Try to subscribe someone who is banned.  Anne is a real
        # user, with a validated address, but she is not a member of the
        # mailing list and is banned from joining.
        # Add anne to the ban list.
        IBanManager(self._mlist).ban('anne@example.com')
        # Then initiate a subscription.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertEqual(
            str(results).splitlines()[-1],
            'anne@example.com is not allowed to subscribe to ant@example.com')

    def test_join_pending(self):
        self._mlist.subscription_policy = SubscriptionPolicy.confirm
        # Try to subscribe someone who already has a subscription pending.
        # Anne is a real user, with a validated address, who already has a
        # pending subscription for this mailing list.
        anne = getUtility(IUserManager).create_user('anne@example.com')
        set_preferred(anne)
        # Initiate a subscription.
        ISubscriptionManager(self._mlist).register(anne)
        # And try to subscribe.
        msg = Message()
        msg['From'] = 'anne@example.com'
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertEqual(
            str(results).splitlines()[-1],
            'anne@example.com has a pending subscription for ant@example.com')

    def test_join_posting_address(self):
        # Try to subscribe the list posting address.
        msg = Message()
        msg['From'] = self._mlist.posting_address
        results = Results()
        self._command.process(self._mlist, msg, {}, (), results)
        self.assertEqual(
            str(results).splitlines()[-1],
            'List posting address not allowed')
