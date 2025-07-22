# Copyright (C) 2011-2025 by the Free Software Foundation, Inc.
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

"""REST membership tests."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.database.transaction import transaction
from mailman.interfaces.bans import IBanManager
from mailman.interfaces.mailinglist import SubscriptionPolicy
from mailman.interfaces.member import DeliveryMode, DeliveryStatus, MemberRole
from mailman.interfaces.subscriptions import ISubscriptionManager, TokenOwner
from mailman.interfaces.usermanager import IUserManager
from mailman.runners.incoming import IncomingRunner
from mailman.testing.helpers import (
    call_api,
    get_lmtp_client,
    get_queue_messages,
    make_testable_runner,
    set_preferred,
    subscribe,
    TestableMaster,
    wait_for_webservice,
)
from mailman.testing.layers import ConfigLayer, RESTLayer
from mailman.utilities.datetime import now
from urllib.error import HTTPError
from zope.component import getUtility


class TestMembership(unittest.TestCase):
    layer = RESTLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('test@example.com')
        self._usermanager = getUtility(IUserManager)

    def test_try_to_join_missing_list(self):
        # A user tries to join a non-existent list.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'missing.example.com',
                'subscriber': 'nobody@example.com',
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'No such list')

    def test_try_to_leave_missing_list(self):
        # A user tries to leave a non-existent list.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/missing@example.com'
                     '/member/nobody@example.com',
                     method='DELETE')
        self.assertEqual(cm.exception.code, 404)

    def test_try_to_leave_list_with_bogus_address(self):
        # Try to leave a mailing list using an invalid membership address.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1', method='DELETE')
        self.assertEqual(cm.exception.code, 404)

    def test_try_to_leave_a_list_twice(self):
        with transaction():
            anne = self._usermanager.create_user('anne@example.com')
            set_preferred(anne)
            self._mlist.subscribe(anne)
        url = 'http://localhost:9001/3.0/members/1'
        json, response = call_api(url, method='DELETE')
        # For a successful DELETE, the response code is 204 and there is no
        # content.
        self.assertEqual(json, None)
        self.assertEqual(response.status_code, 204)
        with self.assertRaises(HTTPError) as cm:
            call_api(url, method='DELETE')
        self.assertEqual(cm.exception.code, 404)

    def test_leave_mlist_sends_notice_to_user(self):
        with transaction():
            anne = self._usermanager.create_user('anne@example.com')
            set_preferred(anne)
            self._mlist.subscribe(anne)
        # Empty the virgin queue.
        get_queue_messages('virgin', expected_count=1)
        url = 'http://localhost:9001/3.0/members/1'
        # Calling the DELETE api should send user a notice.
        json, response = call_api(url, method='DELETE')
        self.assertEqual(json, None)
        self.assertEqual(response.status_code, 204)
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(str(items[0].msg['to']), 'anne@example.com')
        self.assertEqual(
            str(items[0].msg['subject']),
            'You have been unsubscribed from the Test mailing list')

    def test_leave_mlist_moderate_policy(self):
        with transaction():
            self._mlist.unsubscription_policy = SubscriptionPolicy.moderate
            anne = self._usermanager.create_user('anne@example.com')
            set_preferred(anne)
            self._mlist.subscribe(anne)
        # Empty the virgin queue.
        get_queue_messages('virgin', expected_count=1)
        url = 'http://localhost:9001/3.0/members/1'
        # Try unsubscribing the user.
        with transaction():
            json, resp = call_api(
                url,
                data=dict(pre_confirmed=True),
                method='DELETE')
        self.assertEqual(resp.status_code, 202)
        self.assertEqual(json.get('token_owner'), 'moderator')
        token = json.get('token')
        # The member should not have been deleted.
        json, resp = call_api(url)
        self.assertEqual(resp.status_code, 200)
        # Now, approve the un-subscription request.
        json, resp = call_api(
            'http://localhost:9001/3.0/lists/test.example.com/'
            'requests/{}'.format(token),
            data={'action': 'accept'})
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(json, None)
        # Finally, the user should be unsubscribed.
        with self.assertRaises(HTTPError) as cm:
            resp = call_api(url)
        self.assertEqual(cm.exception.code, 404)

    def test_leave_mlist_confirm_policy(self):
        with transaction():
            anne = self._usermanager.create_user('anne@example.com')
            set_preferred(anne)
            self._mlist.subscribe(anne)
        # Empty the virgin queue.
        get_queue_messages('virgin', expected_count=1)
        url = 'http://localhost:9001/3.0/members/1'
        # The default policy is to confirm, so an un-confirmed request would be
        # held for user confirmation.
        json, response = call_api(url, data={'pre_confirmed': False},
                                  method='DELETE')
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(str(items[0].msg['to']), 'anne@example.com')
        self.assertEqual(
            str(items[0].msg['subject']),
            'Your confirmation is needed to leave the test@example.com'
            ' mailing list.')

    def test_try_to_join_a_list_twice(self):
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            self._mlist.subscribe(anne)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                'pre_verified': True,
                'pre_confirmed': True,
                'pre_approved': True,
                })
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(cm.exception.reason, 'Member already subscribed')

    def test_try_to_join_a_list_twice_issue260(self):
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            self._mlist.subscribe(anne)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                'pre_verified': False,
                'pre_confirmed': False,
                'pre_approved': False,
                })
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(cm.exception.reason, 'Member already subscribed')

    def test_invite_new_member(self):
        # Invite an email address to join a list.
        json, response = call_api('http://localhost:9001/3.0/members', {
            'list_id': 'test.example.com',
            'subscriber': 'anne@example.com',
            'display_name': 'Anne Person',
            'invitation': True,
            })
        self.assertEqual(response.status_code, 202)
        self.assertEqual(len(json), 3)

        # Response will always add http_etag.
        fields = ['http_etag', 'token', 'token_owner']
        self.assertEqual(sorted(json.keys()), fields)
        self.assertEqual(json['token_owner'], 'subscriber')

    def test_invite_existing_member(self):
        # Invite an existing member's email address to join a list.
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            self._mlist.subscribe(anne)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                'display_name': 'Anne Person',
                'invitation': True,
                })
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(cm.exception.reason, 'Member already subscribed')

    def test_subscribe_user_without_preferred_address(self):
        with transaction():
            getUtility(IUserManager).create_user('anne@example.com')
        # Subscribe the user to the mailing list by hex UUID.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.1/members', {
                'list_id': 'test.example.com',
                'subscriber': '00000000000000000000000000000001',
                'pre_verified': True,
                'pre_confirmed': True,
                'pre_approved': True,
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'User has no preferred address')

    def test_subscribe_bogus_user_by_uid(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.1/members', {
                'list_id': 'test.example.com',
                'subscriber': '00000000000000000000000000000801',
                'pre_verified': True,
                'pre_confirmed': True,
                'pre_approved': True,
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'No such user')

    def test_add_member_with_mixed_case_email(self):
        # LP: #1425359 - Mailman is case-perserving, case-insensitive.  This
        # test subscribes the lower case address and ensures the original mixed
        # case address can't be subscribed.
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            self._mlist.subscribe(anne)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'ANNE@example.com',
                'pre_verified': True,
                'pre_confirmed': True,
                'pre_approved': True,
                })
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(cm.exception.reason, 'Member already subscribed')

    def test_add_member_with_lower_case_email(self):
        # LP: #1425359 - Mailman is case-perserving, case-insensitive.  This
        # test subscribes the mixed case address and ensures the lower cased
        # address can't be added.
        with transaction():
            anne = self._usermanager.create_address('ANNE@example.com')
            self._mlist.subscribe(anne)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                'pre_verified': True,
                'pre_confirmed': True,
                'pre_approved': True,
                })
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(cm.exception.reason, 'Member already subscribed')

    def test_join_with_invalid_delivery_mode(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                'display_name': 'Anne Person',
                'delivery_mode': 'invalid-mode',
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(
            cm.exception.reason,
            'Invalid Parameter "delivery_mode": Accepted Values are: '
            'regular, plaintext_digests, mime_digests, summary_digests.')

    def test_join_email_contains_slash(self):
        json, response = call_api('http://localhost:9001/3.0/members', {
            'list_id': 'test.example.com',
            'subscriber': 'hugh/person@example.com',
            'display_name': 'Hugh Person',
            'pre_verified': True,
            'pre_confirmed': True,
            'pre_approved': True,
            })
        self.assertEqual(json, None)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.headers['location'],
                         'http://localhost:9001/3.0/members/1')
        # Reset any current transaction.
        config.db.abort()
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'hugh/person@example.com')

    def test_join_as_user_with_preferred_address(self):
        with transaction():
            anne = self._usermanager.create_user('anne@example.com')
            set_preferred(anne)
            self._mlist.subscribe(anne)
        json, response = call_api('http://localhost:9001/3.0/members')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(int(json['total_size']), 1)
        entry_0 = json['entries'][0]
        self.assertEqual(entry_0['self_link'],
                         'http://localhost:9001/3.0/members/1')
        self.assertEqual(entry_0['role'], 'member')
        self.assertEqual(entry_0['user'], 'http://localhost:9001/3.0/users/1')
        self.assertEqual(entry_0['email'], 'anne@example.com')
        self.assertEqual(
            entry_0['address'],
            'http://localhost:9001/3.0/addresses/anne@example.com')
        self.assertEqual(entry_0['list_id'], 'test.example.com')

    def test_duplicate_pending_subscription(self):
        # Issue #199 - a member's subscription is already pending and they try
        # to subscribe again.
        registrar = ISubscriptionManager(self._mlist)
        with transaction():
            self._mlist.subscription_policy = SubscriptionPolicy.moderate
            anne = self._usermanager.create_address('anne@example.com')
            token, token_owner, member = registrar.register(
                anne, pre_verified=True, pre_confirmed=True)
            self.assertEqual(token_owner, TokenOwner.moderator)
            self.assertIsNone(member)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                'pre_verified': True,
                'pre_confirmed': True,
                })
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(cm.exception.reason,
                         'Subscription request already pending')

    def test_duplicate_other_pending_subscription(self):
        # Issue #199 - a member's subscription is already pending and they try
        # to subscribe again.  Unlike above, this pend is waiting for the user
        # to confirm their subscription.
        registrar = ISubscriptionManager(self._mlist)
        with transaction():
            self._mlist.subscription_policy = (
                SubscriptionPolicy.confirm_then_moderate)
            anne = self._usermanager.create_address('anne@example.com')
            token, token_owner, member = registrar.register(
                anne, pre_verified=True)
            self.assertEqual(token_owner, TokenOwner.subscriber)
            self.assertIsNone(member)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                'pre_verified': True,
                'pre_confirmed': True,
                })
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(cm.exception.reason,
                         'Subscription request already pending')

    def test_member_changes_preferred_address(self):
        with transaction():
            anne = self._usermanager.create_user('anne@example.com')
            set_preferred(anne)
            self._mlist.subscribe(anne)
        # Take a look at Anne's current membership.
        json, response = call_api('http://localhost:9001/3.0/members')
        self.assertEqual(int(json['total_size']), 1)
        entry_0 = json['entries'][0]
        self.assertEqual(entry_0['email'], 'anne@example.com')
        self.assertEqual(
            entry_0['address'],
            'http://localhost:9001/3.0/addresses/anne@example.com')
        # Anne registers a new address and makes it her preferred address.
        # There are no changes to her membership.
        with transaction():
            new_preferred = anne.register('aperson@example.com')
            new_preferred.verified_on = now()
            anne.preferred_address = new_preferred
        # Take another look at Anne's current membership.
        json, response = call_api('http://localhost:9001/3.0/members')
        self.assertEqual(int(json['total_size']), 1)
        entry_0 = json['entries'][0]
        self.assertEqual(entry_0['email'], 'aperson@example.com')
        self.assertEqual(
            entry_0['address'],
            'http://localhost:9001/3.0/addresses/aperson@example.com')

    def test_get_nonexistent_member(self):
        # /members/<bogus> returns 404
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/bogus')
        self.assertEqual(cm.exception.code, 404)

    def test_patch_nonexistent_member(self):
        # /members/<missing> PATCH returns 404
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/801',
                     {}, method='PATCH')
        self.assertEqual(cm.exception.code, 404)

    def test_patch_membership_with_bogus_address(self):
        # Try to change a subscription address to one that does not yet exist.
        with transaction():
            subscribe(self._mlist, 'Anne')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1', {
                'address': 'bogus@example.com',
                }, method='PATCH')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'Address not registered')

    def test_patch_membership_with_unverified_address(self):
        # Try to change a subscription address to one that is not yet verified.
        with transaction():
            subscribe(self._mlist, 'Anne')
            self._usermanager.create_address('anne.person@example.com')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1', {
                'address': 'anne.person@example.com',
                }, method='PATCH')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'Unverified address')

    def test_patch_membership_of_preferred_address(self):
        # Try to change a subscription to an address when the user is
        # subscribed via their preferred address.
        with transaction():
            subscribe(self._mlist, 'Anne')
            anne = self._usermanager.create_address('anne.person@example.com')
            anne.verified_on = now()
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1', {
                'address': 'anne.person@example.com',
                }, method='PATCH')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         'Address is not controlled by user')

    def test_patch_member_bogus_attribute(self):
        # /members/<id> PATCH 'bogus' returns 400
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            self._mlist.subscribe(anne)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1', {
                     'powers': 'super',
                     }, method='PATCH')
        self.assertEqual(cm.exception.code, 400)
        self.assertTrue('Unexpected parameters: powers' in cm.exception.reason)

    def test_member_all_without_preferences(self):
        # /members/<id>/all should return a 404 when it isn't trailed by
        # `preferences`
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1/all')
        self.assertEqual(cm.exception.code, 404)

    def test_patch_member_invalid_moderation_action(self):
        # /members/<id> PATCH with invalid 'moderation_action' returns 400.
        with transaction():
            anne = self._usermanager.create_address('anne@example.com')
            self._mlist.subscribe(anne)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1', {
                     'moderation_action': 'invalid',
                     }, method='PATCH')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(
            cm.exception.reason,
            'Invalid Parameter "moderation_action": Accepted Values are: '
            'hold, reject, discard, accept, defer.')

    def test_bad_preferences_url(self):
        with transaction():
            subscribe(self._mlist, 'Anne')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1/preferences/bogus')
        self.assertEqual(cm.exception.code, 404)

    def test_not_a_member_preferences(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1/preferences')
        self.assertEqual(cm.exception.code, 404)

    def test_not_a_member_all_preferences(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members/1/all/preferences')
        self.assertEqual(cm.exception.code, 404)

    def test_delete_other_role(self):
        with transaction():
            subscribe(self._mlist, 'Anne', MemberRole.moderator)
        json, response = call_api(
            'http://localhost:9001/3.0/members/1',
            method='DELETE')
        self.assertEqual(response.status_code, 204)
        self.assertEqual(len(list(self._mlist.moderators.members)), 0)

    def test_banned_member_tries_to_join(self):
        # A user tries to join a list they are banned from.
        with transaction():
            IBanManager(self._mlist).ban('anne@example.com')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'Membership is banned')

    def test_globally_banned_member_tries_to_join(self):
        # A user tries to join a list they are banned from.
        with transaction():
            IBanManager(None).ban('anne@example.com')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': 'anne@example.com',
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'Membership is banned')

    def test_list_posting_address_as_member(self):
        # The list posting address tries to subscribe.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': self._mlist.posting_address,
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         'List posting address cannot subscribe')

    def test_list_posting_address_as_member_approved(self):
        # The list posting address tries to subscribe.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': self._mlist.posting_address,
                'pre_verified': True,
                'pre_confirmed': True,
                'pre_approved': True,
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         'List posting address cannot subscribe')

    def test_list_posting_address_as_owner(self):
        # The liust posting address tries to subscribe.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': self._mlist.posting_address,
                'role': 'owner',
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         'List posting address cannot be added')

    def test_list_posting_address_as_moderator(self):
        # The liust posting address tries to subscribe.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'test.example.com',
                'subscriber': self._mlist.posting_address,
                'role': 'moderator'
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         'List posting address cannot be added')

    def test_subscribe_supress_welcome_message(self):
        # Test subscription of a new member is able to supress welcome message,
        # even if Mailinglist is configured to do so.
        self._mlist.send_welcome_message = True
        content, response = call_api('http://localhost:9001/3.0/members', {
            'list_id': 'test.example.com',
            'subscriber': 'ANNE@example.com',
            'pre_verified': True,
            'pre_confirmed': True,
            'pre_approved': True,
            'send_welcome_message': False,
            })
        self.assertEqual(response.status_code, 201)
        member = self._mlist.members.get_member('anne@example.com')
        self.assertIsNotNone(member)
        get_queue_messages('virgin', expected_count=0)

    def test_send_welcome_message(self):
        # Test that the send_welcome_message flag sends welcome message, even
        # when the mlist isn't configured to do so.
        self._mlist.send_welcome_message = False
        content, response = call_api('http://localhost:9001/3.0/members', {
            'list_id': 'test.example.com',
            'subscriber': 'ANNE@example.com',
            'pre_verified': True,
            'pre_confirmed': True,
            'pre_approved': True,
            'send_welcome_message': True,
            })
        self.assertEqual(response.status_code, 201)
        member = self._mlist.members.get_member('anne@example.com')
        self.assertIsNotNone(member)
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(str(items[0].msg['to']), 'anne@example.com')
        self.assertEqual(
            str(items[0].msg['subject']), 'Welcome to the "Test" mailing list')

    def test_subscribe_with_digests_nomail(self):
        content, response = call_api('http://localhost:9001/3.1/members', {
            'list_id': 'test.example.com',
            'subscriber': 'ANNE@example.com',
            'pre_verified': True,
            'pre_confirmed': True,
            'delivery_status': 'by_user',
            'delivery_mode': 'plaintext_digests',
            })
        self.assertEqual(response.status_code, 201)
        member = self._mlist.members.get_member('anne@example.com')
        self.assertIsNotNone(member)
        self.assertEqual(member.delivery_mode, DeliveryMode.plaintext_digests)
        self.assertEqual(member.delivery_status, DeliveryStatus.by_user)

    def test_add_owner_with_options(self):
        content, response = call_api('http://localhost:9001/3.1/members', {
            'list_id': 'test.example.com',
            'subscriber': 'ANNE@example.com',
            'role': 'owner',
            'pre_verified': True,
            'pre_confirmed': True,
            'delivery_status': 'by_user',
            'delivery_mode': 'plaintext_digests',
            })
        self.assertEqual(response.status_code, 201)
        member = self._mlist.owners.get_member('anne@example.com')
        self.assertIsNotNone(member)
        self.assertEqual(member.delivery_mode, DeliveryMode.plaintext_digests)
        self.assertEqual(member.delivery_status, DeliveryStatus.by_user)


class CustomLayer(ConfigLayer):
    """Custom layer which starts both the REST and LMTP servers."""

    server = None
    client = None

    @classmethod
    def _wait_for_both(cls):
        cls.client = get_lmtp_client(quiet=True)
        wait_for_webservice()

    @classmethod
    def setUp(cls):
        assert cls.server is None, 'Layer already set up'
        cls.server = TestableMaster(cls._wait_for_both)
        cls.server.start('lmtp', 'rest')

    @classmethod
    def tearDown(cls):
        assert cls.server is not None, 'Layer is not set up'
        cls.server.stop()
        cls.server = None


class TestNonmembership(unittest.TestCase):
    layer = CustomLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('test@example.com')
        self._usermanager = getUtility(IUserManager)

    def _go(self, message):
        lmtp = get_lmtp_client(quiet=True)
        lmtp.lhlo('remote.example.org')
        lmtp.sendmail('nonmember@example.com', ['test@example.com'], message)
        lmtp.close()
        # The message will now be sitting in the `in` queue.  Run the incoming
        # runner once to process it, which should result in the nonmember
        # showing up.
        inq = make_testable_runner(IncomingRunner, 'in')
        inq.run()

    def test_nonmember_findable_after_posting(self):
        # A nonmember we have never seen before posts a message to the mailing
        # list.  They are findable through the /members/find API using a role
        # of nonmember.
        self._go("""\
From: nonmember@example.com
To: test@example.com
Subject: Nonmember post
Message-ID: <alpha>

Some text.
""")
        # Now use the REST API to try to find the nonmember.
        json, response = call_api(
            'http://localhost:9001/3.0/members/find', {
                # 'list_id': 'test.example.com',
                'role': 'nonmember',
                })
        self.assertEqual(json['total_size'], 1)
        nonmember = json['entries'][0]
        self.assertEqual(nonmember['role'], 'nonmember')
        self.assertEqual(nonmember['email'], 'nonmember@example.com')
        self.assertEqual(
            nonmember['address'],
            'http://localhost:9001/3.0/addresses/nonmember@example.com')
        # There is no user key in the JSON data because there is no user
        # record associated with the address record.
        self.assertNotIn('user', nonmember)

    def test_linked_nonmember_findable_after_posting(self):
        # Like above, a nonmember posts a message to the mailing list.  In
        # this case though, the nonmember already has a user record.  They are
        # findable through the /members/find API using a role of nonmember.
        with transaction():
            self._usermanager.create_user('nonmember@example.com')
        self._go("""\
From: nonmember@example.com
To: test@example.com
Subject: Nonmember post
Message-ID: <alpha>

Some text.
""")
        # Now use the REST API to try to find the nonmember.
        json, response = call_api(
            'http://localhost:9001/3.0/members/find', {
                # 'list_id': 'test.example.com',
                'role': 'nonmember',
                })
        self.assertEqual(json['total_size'], 1)
        nonmember = json['entries'][0]
        self.assertEqual(nonmember['role'], 'nonmember')
        self.assertEqual(nonmember['email'], 'nonmember@example.com')
        self.assertEqual(
            nonmember['address'],
            'http://localhost:9001/3.0/addresses/nonmember@example.com')
        # There is a user key in the JSON data because the address had
        # previously been linked to a user record.
        self.assertEqual(nonmember['user'],
                         'http://localhost:9001/3.0/users/1')


class TestAPI31Members(unittest.TestCase):
    layer = RESTLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('ant@example.com')

    def test_member_ids_are_hex(self):
        with transaction():
            subscribe(self._mlist, 'Anne')
            subscribe(self._mlist, 'Bart')
        json, response = call_api('http://localhost:9001/3.1/members')
        entries = json['entries']
        self.assertEqual(len(entries), 2)
        self.assertEqual(
          entries[0]['self_link'],
          'http://localhost:9001/3.1/members/00000000000000000000000000000001')
        self.assertEqual(
            entries[0]['member_id'],
            '00000000000000000000000000000001')
        self.assertEqual(
            entries[0]['user'],
            'http://localhost:9001/3.1/users/00000000000000000000000000000001')
        self.assertEqual(
          entries[1]['self_link'],
          'http://localhost:9001/3.1/members/00000000000000000000000000000002')
        self.assertEqual(
            entries[1]['member_id'],
            '00000000000000000000000000000002')
        self.assertEqual(
            entries[1]['user'],
            'http://localhost:9001/3.1/users/00000000000000000000000000000002')

    def test_get_member_id_by_hex(self):
        with transaction():
            subscribe(self._mlist, 'Anne')
        json, response = call_api(
          'http://localhost:9001/3.1/members/00000000000000000000000000000001')
        self.assertEqual(
            json['member_id'],
            '00000000000000000000000000000001')
        self.assertEqual(
          json['self_link'],
          'http://localhost:9001/3.1/members/00000000000000000000000000000001')
        self.assertEqual(
            json['user'],
            'http://localhost:9001/3.1/users/00000000000000000000000000000001')
        self.assertEqual(
            json['address'],
            'http://localhost:9001/3.1/addresses/aperson@example.com')

    def test_get_list_member_id_by_email(self):
        with transaction():
            subscribe(self._mlist, 'Anne', email="aperson@example.com")
        json, response = call_api(
            'http://localhost:9001/3.1/lists/ant.example.com/member'
            '/aperson@example.com')
        self.assertEqual(
            json['member_id'],
            '00000000000000000000000000000001')
        self.assertEqual(
          json['self_link'],
          'http://localhost:9001/3.1/members/00000000000000000000000000000001')
        self.assertEqual(
            json['user'],
            'http://localhost:9001/3.1/users/00000000000000000000000000000001')
        self.assertEqual(
            json['address'],
            'http://localhost:9001/3.1/addresses/aperson@example.com')

    def test_cannot_get_member_id_by_int(self):
        with transaction():
            subscribe(self._mlist, 'Anne')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.1/members/1')
        self.assertEqual(cm.exception.code, 404)

    def test_preferences(self):
        with transaction():
            member = subscribe(self._mlist, 'Anne')
            member.preferences.delivery_mode = DeliveryMode.summary_digests
        json, response = call_api(
            'http://localhost:9001/3.1/members'
            '/00000000000000000000000000000001/preferences')
        self.assertEqual(json['delivery_mode'], 'summary_digests')

    def test_all_preferences(self):
        with transaction():
            member = subscribe(self._mlist, 'Anne')
            member.preferences.delivery_mode = DeliveryMode.summary_digests
        json, response = call_api(
            'http://localhost:9001/3.1/members'
            '/00000000000000000000000000000001/all/preferences')
        self.assertEqual(json['delivery_mode'], 'summary_digests')

    def test_create_new_membership_by_hex(self):
        with transaction():
            user = getUtility(IUserManager).create_user('anne@example.com')
            set_preferred(user)
        # Subscribe the user to the mailing list by hex UUID.
        json, response = call_api(
            'http://localhost:9001/3.1/members', {
                'list_id': 'ant.example.com',
                'subscriber': '00000000000000000000000000000001',
                'pre_verified': True,
                'pre_confirmed': True,
                'pre_approved': True,
                })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
           response.headers['location'],
           'http://localhost:9001/3.1/members/00000000000000000000000000000001'
           )

    def test_create_new_owner_by_hex(self):
        with transaction():
            user = getUtility(IUserManager).create_user('anne@example.com')
            set_preferred(user)
        # Subscribe the user to the mailing list by hex UUID.
        json, response = call_api(
            'http://localhost:9001/3.1/members', {
                'list_id': 'ant.example.com',
                'subscriber': '00000000000000000000000000000001',
                'role': 'owner',
                })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(
           response.headers['location'],
           'http://localhost:9001/3.1/members/00000000000000000000000000000001'
           )

    def test_cannot_create_new_membership_by_int(self):
        with transaction():
            user = getUtility(IUserManager).create_user('anne@example.com')
            set_preferred(user)
        # We can't use the int representation of the UUID with API 3.1.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.1/members', {
                'list_id': 'ant.example.com',
                'subscriber': '1',
                'pre_verified': True,
                'pre_confirmed': True,
                'pre_approved': True,
                })
        # This is a bad request because the `subscriber` value isn't something
        # that's known to the system, in API 3.1.  It's not technically a 404
        # because that's reserved for URL lookups.
        self.assertEqual(cm.exception.code, 400)

    def test_duplicate_owner(self):
        # Server failure when someone is already an owner.
        with transaction():
            anne = getUtility(IUserManager).create_address('anne@example.com')
            self._mlist.subscribe(anne, MemberRole.owner)
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.1/members', {
                'list_id': 'ant.example.com',
                'subscriber': anne.email,
                'role': 'owner',
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(
            cm.exception.reason,
            'anne@example.com is already an owner of ant@example.com')


class TestMemberFields(unittest.TestCase):
    layer = RESTLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('test@example.com')
        self._usermanager = getUtility(IUserManager)

    def _test_get_member_roster_with_specific_fields(
            self, url, total_size, data=None):
        if data:
            data['fields'] = ['member_id', 'role', 'address']
        else:
            url = url + '?fields=member_id&fields=role&fields=address'
        json, response = call_api(url, data=data)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['total_size'], total_size)
        entries = json['entries']
        self.assertEqual(len(entries), total_size)

        # Response will always add http_etag.
        fields = ['address', 'http_etag', 'member_id', 'role']
        self.assertEqual(sorted(entries[0].keys()), fields)

    def _test_get_member_roster_invalid_fields(self, url, data=None):
        if data:
            data['fields'] = ['bogus']
        else:
            url = url + '?fields=bogus'
        with self.assertRaises(HTTPError) as cm:
            call_api(url, data=data)
        self.assertEqual(cm.exception.code, 400)
        self.assertIn(
            'Unknown field "bogus" for Member resource. Allowed fields are: ',
            cm.exception.reason
            )

    def test_get_top_level_members_with_fields(self):
        with transaction():
            subscribe(self._mlist, 'Anne')
            subscribe(self._mlist, 'Bart')
        self._test_get_member_roster_with_specific_fields(
            'http://localhost:9001/3.1/members', total_size=2)
        self._test_get_member_roster_invalid_fields(
            'http://localhost:9001/3.1/members')

    def test_get_members_roster_with_fields(self):
        with transaction():
            subscribe(self._mlist, 'Anne')
            subscribe(self._mlist, 'Bart')
        self._test_get_member_roster_with_specific_fields(
            'http://localhost:9001/3.1/lists/{}/roster/member'.format(
                self._mlist.list_id), total_size=2)
        self._test_get_member_roster_invalid_fields(
            'http://localhost:9001/3.1/lists/{}/roster/member'.format(
                self._mlist.list_id))

    def test_get_owners_roster_fields(self):
        with transaction():
            subscribe(self._mlist, 'Anne', role=MemberRole.owner)
            subscribe(self._mlist, 'Bart', role=MemberRole.owner)
        self._test_get_member_roster_with_specific_fields(
            'http://localhost:9001/3.1/lists/{}/roster/owner'.format(
                self._mlist.list_id), total_size=2)
        self._test_get_member_roster_invalid_fields(
            'http://localhost:9001/3.1/lists/{}/roster/owner'.format(
                self._mlist.list_id))

    def test_get_moderator_roster_fields(self):
        with transaction():
            subscribe(self._mlist, 'Anne', role=MemberRole.moderator)
            subscribe(self._mlist, 'Bart', role=MemberRole.moderator)
        self._test_get_member_roster_with_specific_fields(
            'http://localhost:9001/3.1/lists/{}/roster/moderator'.format(
                self._mlist.list_id), total_size=2)
        self._test_get_member_roster_invalid_fields(
            'http://localhost:9001/3.1/lists/{}/roster/moderator'.format(
                self._mlist.list_id))

    def test_find_members_with_fields(self):
        with transaction():
            subscribe(self._mlist, 'Anne')
        self._test_get_member_roster_with_specific_fields(
            'http://localhost:9001/3.1/members/find',
            data={'list_id': self._mlist.list_id},
            total_size=1)
        self._test_get_member_roster_invalid_fields(
            'http://localhost:9001/3.1/members/find',
            data={'list_id': self._mlist.list_id})
