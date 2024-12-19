# Copyright (C) 2011-2023 by the Free Software Foundation, Inc.
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

"""Test members."""

import unittest

from datetime import timedelta
from mailman.app.lifecycle import create_list
from mailman.interfaces.action import Action
from mailman.interfaces.member import (
    DeliveryStatus,
    MemberRole,
    MembershipError,
    SubscriptionMode,
)
from mailman.interfaces.user import UnverifiedAddressError
from mailman.interfaces.usermanager import IUserManager
from mailman.model.member import Member, MembershipManager
from mailman.testing.helpers import LogFileMark, set_preferred
from mailman.testing.layers import ConfigLayer
from mailman.utilities.datetime import now
from zope.component import getUtility


class TestMember(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._usermanager = getUtility(IUserManager)

    def test_cannot_set_address_with_preferred_address_subscription(self):
        # A user is subscribed to a mailing list with their preferred address.
        # You cannot set the `address` attribute on such IMembers.
        anne = self._usermanager.create_user('anne@example.com')
        set_preferred(anne)
        # Subscribe with the IUser object, not the address.  This makes Anne a
        # member via her preferred address.
        member = self._mlist.subscribe(anne)
        new_address = anne.register('aperson@example.com')
        new_address.verified_on = now()
        self.assertRaises(MembershipError,
                          setattr, member, 'address', new_address)

    def test_cannot_change_to_unverified_address(self):
        # A user is subscribed to a mailing list with an explicit address.
        # You cannot set the `address` attribute to an unverified address.
        anne = self._usermanager.create_user('anne@example.com')
        address = list(anne.addresses)[0]
        member = self._mlist.subscribe(address)
        new_address = anne.register('aperson@example.com')
        # The new address is not verified.
        self.assertRaises(UnverifiedAddressError,
                          setattr, member, 'address', new_address)

    def test_cannot_change_to_address_uncontrolled_address(self):
        # A user tries to change their subscription to an address they do not
        # control.
        anne = self._usermanager.create_user('anne@example.com')
        address = list(anne.addresses)[0]
        member = self._mlist.subscribe(address)
        new_address = self._usermanager.create_address('nobody@example.com')
        new_address.verified_on = now()
        # The new address is not verified.
        self.assertRaises(MembershipError,
                          setattr, member, 'address', new_address)

    def test_cannot_change_to_address_controlled_by_other_user(self):
        # A user tries to change their subscription to an address some other
        # user controls.
        anne = self._usermanager.create_user('anne@example.com')
        anne_address = list(anne.addresses)[0]
        bart = self._usermanager.create_user('bart@example.com')
        bart_address = list(bart.addresses)[0]
        bart_address.verified_on = now()
        member = self._mlist.subscribe(anne_address)
        # The new address is not verified.
        self.assertRaises(MembershipError,
                          setattr, member, 'address', bart_address)

    def test_member_ctor_value_error(self):
        # ValueError when passing in anything but a user or address.
        self.assertRaises(ValueError, Member, MemberRole.member,
                          self._mlist.list_id,
                          'aperson@example.com')

    def test_unsubscribe(self):
        address = self._usermanager.create_address('anne@example.com')
        address.verified_on = now()
        self._mlist.subscribe(address)
        self.assertEqual(len(list(self._mlist.members.members)), 1)
        mark = LogFileMark('mailman.subscribe')
        member = self._mlist.members.get_member('anne@example.com')
        member.unsubscribe()
        self.assertEqual(len(list(self._mlist.members.members)), 0)
        self.assertIn('test@example.com: unsubscribed anne@example.com',
                      mark.read())

    def test_default_moderation_action(self):
        # Owners and moderators have their posts accepted, members and
        # nonmembers default to the mailing list's action for their type.
        anne = self._usermanager.create_user('anne@example.com')
        bart = self._usermanager.create_user('bart@example.com')
        cris = self._usermanager.create_user('cris@example.com')
        dana = self._usermanager.create_user('dana@example.com')
        set_preferred(anne)
        set_preferred(bart)
        set_preferred(cris)
        set_preferred(dana)
        anne_member = self._mlist.subscribe(anne, MemberRole.owner)
        bart_member = self._mlist.subscribe(bart, MemberRole.moderator)
        cris_member = self._mlist.subscribe(cris, MemberRole.member)
        dana_member = self._mlist.subscribe(dana, MemberRole.nonmember)
        self.assertEqual(anne_member.moderation_action, Action.accept)
        self.assertEqual(bart_member.moderation_action, Action.accept)
        self.assertIsNone(cris_member.moderation_action)
        self.assertIsNone(dana_member.moderation_action)

    def test_subscriptin_mode(self):
        # Test subscription mode reflects if a user is subscribed or an address
        # is.
        addr = self._usermanager.create_address('aperson@example.com')
        addr.verified_on = now()
        self._mlist.subscribe(addr)
        member = self._mlist.members.get_member('aperson@example.com')
        self.assertEqual(member.subscription_mode, SubscriptionMode.as_address)

        auser = self._usermanager.create_user('bperson@example.com')
        set_preferred(auser)
        amember = self._mlist.subscribe(auser, MemberRole.member)
        self.assertEqual(amember.subscription_mode, SubscriptionMode.as_user)


class TestMembershipManager(unittest.TestCase):
    """Test MembershipManager. """

    layer = ConfigLayer

    def setUp(self):
        self._bestlist = create_list('best@example.com')
        self._mlist = create_list('test@example.com')

        self._mlist.bounce_score_threshold = 5
        self._mlist.bounce_you_are_disabled_warnings = 2
        self._mlist.bounce_you_are_disabled_warnings_interval = timedelta(
            days=2)

        self._bestlist.bounce_score_threshold = 3
        self._bestlist.bounce_you_are_disabled_warnings = 1
        self._bestlist.bounce_you_are_disabled_warnings_interval = timedelta(
            days=3)

        self._usermanager = getUtility(IUserManager)
        self._mmanager = MembershipManager()

        anne = self._usermanager.create_user('anne@example.com')
        bart = self._usermanager.create_user('bart@example.com')

        set_preferred(anne)
        set_preferred(bart)

        self.anne_member = self._mlist.subscribe(anne)
        self.bart_member = self._mlist.subscribe(bart)

        self.anne_member_best = self._bestlist.subscribe(anne)
        self.bart_member_best = self._bestlist.subscribe(bart)

    def _test_membership_pending_x(
            self,
            anne_score, anne_tot, anne_last, bart_score, bart_tot, bart_last,
            anne2_score, anne2_tot, anne2_last, bart2_score, bart2_tot,
            bart2_last, expected_total, expected_members,
            func):

        self.anne_member.bounce_score = anne_score
        if anne_tot:
            self.anne_member.total_warnings_sent = anne_tot
        if anne_last:
            self.anne_member.last_warning_sent = now() - timedelta(
                days=anne_last)

        self.bart_member.bounce_score = bart_score
        if bart_tot:
            self.bart_member.total_warnings_sent = bart_tot
        if bart_last:
            self.bart_member.last_warning_sent = now() - timedelta(
                days=bart_last)

        self.anne_member_best.bounce_score = anne2_score
        if anne2_tot:
            self.anne_member_best.total_warnings_sent = anne2_tot
        if anne2_last:
            self.anne_member_best.last_warning_sent = now() - timedelta(
                days=anne2_last)

        self.bart_member_best.bounce_score = bart2_score
        if bart2_tot:
            self.bart_member_best.total_warnings_sent = bart2_tot
        if bart2_last:
            self.bart_member_best.last_warning_sent = now() - timedelta(
                days=bart2_last)

        pending_members = list(func())
        self.assertEqual(len(pending_members), expected_total)
        member_ids = list(member.id for member in pending_members)
        self.assertEqual(sorted(member_ids), sorted(expected_members))

    def _test_membership_pending_warning(self, *args):
        self._test_membership_pending_x(
            *args, func=self._mmanager.memberships_pending_warning)

    def _test_membership_pending_removal(self, *args):
        self._test_membership_pending_x(
            *args, func=self._mmanager.memberships_pending_removal)

    def _disable_delivery(self, member):
        member.preferences.delivery_status = DeliveryStatus.by_bounces

    def test_membership_pending_warnings(self):
        # No one gets warnings since everyone's bounce score < threshold.
        self._test_membership_pending_warning(
            2, None, None, 2, None, None,
            2, None, None, None, None, None,
            0, [])

    def test_membership_pending_warnings_bounce_processing_disabled(self):
        # Test that no members of the list whose bounce processing are
        # disabled show up.
        self._bestlist.process_bounces = False
        args = [4, None, None, 4, None, None,
                4, None, None, 4, None, None]
        # We disable delivery for the members.
        self._disable_delivery(self.anne_member_best)
        self._disable_delivery(self.bart_member_best)

        self._test_membership_pending_warning(
            *args,
            0, [])

    def test_warnings_after_threshold_crossed(self):
        # Threshold exceeds on bestlist, but only if delivery is disabled.
        args = [4, None, None, 4, None, None,
                4, None, None, 4, None, None]
        self._test_membership_pending_warning(
            *args,
            0, [])
        # Now if the delivery is disabled for them (only, via bounce processing
        # i.e. DeliveryStatus.by_bounces), they should get the warnings.
        self._disable_delivery(self.anne_member_best)
        self._disable_delivery(self.bart_member_best)

        self._test_membership_pending_warning(
            *args,
            2, [self.anne_member_best.id, self.bart_member_best.id])

    def test_warnings_for_all_lists(self):
        # Threshold exceeds on both lists and warnings pending for everyone.
        self._disable_delivery(self.anne_member_best)
        self._disable_delivery(self.bart_member_best)
        self._disable_delivery(self.anne_member)
        self._disable_delivery(self.bart_member)

        self._test_membership_pending_warning(
            6, None, None, 6, None, None,
            4, None, None, 4, None, None,
            4, [self.anne_member.id, self.bart_member.id,
                self.anne_member_best.id, self.bart_member_best.id])

    def test_warnings_sent_only_after_interval(self):
        # Threshold exceeds but last warning was sent recently.
        self._disable_delivery(self.anne_member_best)
        self._disable_delivery(self.bart_member_best)
        self._disable_delivery(self.anne_member)
        self._disable_delivery(self.bart_member)

        self._test_membership_pending_warning(
            6, 1, 4, 6, 1, 1,
            4, 1, 1, 4, 1, 1,
            1, [self.anne_member.id])

    def test_only_max_warnings(self):
        # Threshold exceeds but total warnings sent equal max for some, so no
        # more warnings for them.
        self._disable_delivery(self.anne_member_best)
        self._disable_delivery(self.bart_member_best)
        self._disable_delivery(self.anne_member)
        self._disable_delivery(self.bart_member)

        self._test_membership_pending_warning(
            6, 2, 4, 6, 1, 3,
            4, 1, 1, 4, None, None,
            2, [self.bart_member.id, self.bart_member_best.id])

    def test_membership_pending_removal_bounce_processing_disabled(self):
        # Test that no members of the list whose bounce processing are
        # disabled show up.
        self._bestlist.process_bounces = False
        args = [5, 2, 0, 6, 1, 0,
                3, 1, 0, 4, 0, 0]
        # We disable delivery for the members.
        self._disable_delivery(self.anne_member_best)
        self._disable_delivery(self.bart_member_best)

        self._test_membership_pending_removal(
            *args,
            0, [])

    def test_removal_only_after_delivery_disabled(self):
        # Test that we get the right list of Members who are supposed to be
        # removed from the MailingList and have received required number of
        # warnings.
        args = [5, 2, 0, 6, 1, 0,
                3, 1, 0, 4, 0, 0]
        # First, people who haven't had delivery disabled first will not be
        # removed from the lists.
        self._test_membership_pending_removal(
            *args,
            0, [])

        # Now, if we disable delivery for them, they should be removed.
        for mem in (self.anne_member_best, self.bart_member_best,
                    self.anne_member, self.bart_member):
            self._disable_delivery(mem)

        self._test_membership_pending_removal(
            *args,
            2, [self.anne_member.id, self.anne_member_best.id])

    def test_total_warnings_sent_maxed_out(self):
        # Test that we remove users only which have max warnings sent out.
        for mem in (self.anne_member_best, self.bart_member_best,
                    self.anne_member, self.bart_member):
            self._disable_delivery(mem)

        self._test_membership_pending_removal(
            6, 1, 0, 6, 2, 0,
            4, 0, 0, 4, 1, 0,
            2, [self.bart_member.id, self.bart_member_best.id])

    def test_total_warnings_sent_more_than_max(self):
        # Warnings more than the threshold should also be removed.
        for mem in (self.anne_member_best, self.bart_member_best,
                    self.anne_member, self.bart_member):
            self._disable_delivery(mem)

        self._test_membership_pending_removal(
            6, 1, 0, 6, 4, 0,
            4, 0, 0, 4, 0, 0,
            1, [self.bart_member.id])
