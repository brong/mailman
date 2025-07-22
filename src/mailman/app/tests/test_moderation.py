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

"""Moderation tests."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.app.moderator import (
    handle_message,
    handle_unsubscription,
    hold_message,
    hold_unsubscription,
)
from mailman.chains.hold import HeldMessagePendable
from mailman.interfaces.action import Action
from mailman.interfaces.member import MemberRole
from mailman.interfaces.messages import IMessageStore
from mailman.interfaces.pending import IPendings
from mailman.interfaces.requests import IListRequests
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.usermanager import IUserManager
from mailman.runners.incoming import IncomingRunner
from mailman.runners.outgoing import OutgoingRunner
from mailman.runners.pipeline import PipelineRunner
from mailman.testing.helpers import (
    get_queue_messages,
    make_testable_runner,
    set_preferred,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import SMTPLayer
from mailman.utilities.datetime import now
from zope.component import getUtility


class TestModeration(unittest.TestCase):
    """Test moderation functionality."""

    layer = SMTPLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._request_db = IListRequests(self._mlist)
        self._msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: hold me
Message-ID: <alpha>

""")
        self._in = make_testable_runner(IncomingRunner, 'in')
        self._pipeline = make_testable_runner(PipelineRunner, 'pipeline')
        self._out = make_testable_runner(OutgoingRunner, 'out')

    def test_accepted_message_gets_posted(self):
        # A message that is accepted by the moderator should get posted to the
        # mailing list.  LP: #827697
        msgdata = dict(listname='test@example.com',
                       recipients=['bart@example.com'])
        request_id = hold_message(self._mlist, self._msg, msgdata)
        handle_message(self._mlist, request_id, Action.accept)
        self._in.run()
        self._pipeline.run()
        self._out.run()
        messages = list(SMTPLayer.smtpd.messages)
        self.assertEqual(len(messages), 1)
        message = messages[0]
        # We don't need to test the entire posted message, just the bits that
        # prove it got sent out.
        self.assertIn('x-mailman-version', message)
        self.assertIn('x-peer', message)
        # The X-Mailman-Approved-At header has local timezone information in
        # it, so test that separately.
        self.assertEqual(message['x-mailman-approved-at'][:-5],
                         'Mon, 01 Aug 2005 07:49:23 ')
        del message['x-mailman-approved-at']
        # The Message-ID matches the original.
        self.assertEqual(message['message-id'], '<alpha>')
        # Anne sent the message and the mailing list received it.
        self.assertEqual(message['from'], 'anne@example.com')
        self.assertEqual(message['to'], 'test@example.com')
        # The Subject header has the list's prefix.
        self.assertEqual(message['subject'], '[Test] hold me')
        # The list's -bounce address is the actual sender, and Bart is the
        # only actual recipient.  These headers are added by the testing
        # framework and don't show up in production.  They match the RFC 5321
        # envelope.
        self.assertEqual(message['x-mailfrom'], 'test-bounces@example.com')
        self.assertEqual(message['x-rcptto'], 'bart@example.com')

    def test_missing_accepted_message_gets_posted(self):
        # A message that is accepted by the moderator should get posted to the
        # mailing list.  LP: #827697
        msgdata = dict(listname='test@example.com',
                       recipients=['bart@example.com'])
        request_id = hold_message(self._mlist, self._msg, msgdata)
        message_store = getUtility(IMessageStore)
        message_store.delete_message('<alpha>')
        handle_message(self._mlist, request_id, Action.accept)
        self._in.run()
        self._pipeline.run()
        self._out.run()
        messages = list(SMTPLayer.smtpd.messages)
        self.assertEqual(len(messages), 1)
        message = messages[0]
        # We don't need to test the entire posted message, just the bits that
        # prove it got sent out.
        self.assertIn('x-mailman-version', message)
        self.assertIn('x-peer', message)
        # The X-Mailman-Approved-At header has local timezone information in
        # it, so test that separately.
        self.assertEqual(message['x-mailman-approved-at'][:-5],
                         'Mon, 01 Aug 2005 07:49:23 ')
        del message['x-mailman-approved-at']
        # The Message-ID matches the original.
        self.assertEqual(message['message-id'], '<alpha>')
        # Anne sent the message and the mailing list received it.
        self.assertEqual(message['from'], 'anne@example.com')
        self.assertEqual(message['to'], 'test@example.com')
        # The Subject header has the list's prefix.
        self.assertEqual(message['subject'], '[Test] hold me')
        # The list's -bounce address is the actual sender, and Bart is the
        # only actual recipient.  These headers are added by the testing
        # framework and don't show up in production.  They match the RFC 5321
        # envelope.
        self.assertEqual(message['x-mailfrom'], 'test-bounces@example.com')
        self.assertEqual(message['x-rcptto'], 'bart@example.com')

    def test_hold_action_alias_for_defer(self):
        # In handle_message(), the 'hold' action is the same as 'defer' for
        # purposes of this API.
        request_id = hold_message(self._mlist, self._msg)
        handle_message(self._mlist, request_id, Action.defer)
        # The message is still in the pending requests.
        key, data = self._request_db.get_request(request_id)
        self.assertEqual(key, '<alpha>')
        handle_message(self._mlist, request_id, Action.hold)
        key, data = self._request_db.get_request(request_id)
        self.assertEqual(key, '<alpha>')

    def test_lp_1031391(self):
        # LP: #1031391 msgdata['received_time'] gets added by the LMTP server.
        # The value is a datetime.  If this message gets held, it will break
        # pending requests since they require string keys and values.
        received_time = now()
        msgdata = dict(received_time=received_time)
        request_id = hold_message(self._mlist, self._msg, msgdata)
        key, data = self._request_db.get_request(request_id)
        self.assertEqual(data['received_time'], received_time)

    def test_forward(self):
        # We can forward the message to an email address.
        request_id = hold_message(self._mlist, self._msg)
        handle_message(self._mlist, request_id, Action.discard,
                       forward=['zack@example.com'])
        # The forwarded message lives in the virgin queue.
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(str(items[0].msg['subject']),
                         'Forward of moderated message')
        self.assertEqual(list(items[0].msgdata['recipients']),
                         ['zack@example.com'])

    def test_missing_forward(self):
        # We can forward the message to an email address.
        request_id = hold_message(self._mlist, self._msg)
        message_store = getUtility(IMessageStore)
        message_store.delete_message('<alpha>')
        handle_message(self._mlist, request_id, Action.discard,
                       forward=['zack@example.com'])
        # The forwarded message lives in the virgin queue.
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(str(items[0].msg['subject']),
                         'Forward of moderated message')
        self.assertEqual(list(items[0].msgdata['recipients']),
                         ['zack@example.com'])
        payload = items[0].msg.get_payload()[0]
        self.assertIsNotNone(payload)
        self.assertEqual('<alpha>', payload['message-id'])

    def test_survive_a_deleted_message(self):
        # When the message that should be deleted is not found in the store,
        # no error is raised.
        request_id = hold_message(self._mlist, self._msg)
        message_store = getUtility(IMessageStore)
        message_store.delete_message('<alpha>')
        handle_message(self._mlist, request_id, Action.discard)
        self.assertEqual(self._request_db.count, 0)

    def test_handled_message_removed_from_store(self):
        # The message is removed from the store and the pendings db when it's
        # been disposed of.
        request_id = hold_message(self._mlist, self._msg)
        # Get the hash for this pending request.
        hash = list(self._request_db.held_requests)[0].data_hash
        handle_message(self._mlist, request_id, Action.discard)
        self.assertEqual(self._request_db.count, 0)
        message = getUtility(IMessageStore).get_message_by_id('<alpha>')
        self.assertIsNone(message)
        self.assertIsNone(getUtility(IPendings).confirm(hash))

    def test_handled_cross_posted_message_not_removed(self):
        # A cross posted message is not removed when handled on the first list.
        mlist2 = create_list('test2@example.com')
        request_db2 = IListRequests(mlist2)
        request_id = hold_message(self._mlist, self._msg)
        request_id2 = hold_message(mlist2, self._msg)
        # Get the hashes for these pending requests.
        hash0 = list(self._request_db.held_requests)[0].data_hash
        hash1 = list(request_db2.held_requests)[0].data_hash
        # Handle the first list's message.
        handle_message(self._mlist, request_id, Action.discard)
        # There's now only the request for list2.
        self.assertEqual(self._request_db.count, 0)
        self.assertEqual(request_db2.count, 1)
        message = getUtility(IMessageStore).get_message_by_id('<alpha>')
        self.assertIsNotNone(message)
        self.assertIsNone(getUtility(IPendings).confirm(hash0))
        self.assertIsNotNone(getUtility(IPendings).confirm(hash1,
                                                           expunge=False))
        # Handle the second list's message.
        handle_message(mlist2, request_id2, Action.discard)
        # Now the request and message are gone.
        self.assertEqual(request_db2.count, 0)
        message = getUtility(IMessageStore).get_message_by_id('<alpha>')
        self.assertIsNone(message)
        self.assertIsNone(getUtility(IPendings).confirm(hash1))

    def test_all_pendings_removed(self):
        # A held message pends two tokens, One for the moderator and one for
        # the user.  Ensure both are removed when message is handled.
        request_id = hold_message(self._mlist, self._msg)
        # The hold chain does more.
        pendings = getUtility(IPendings)
        user_hash = pendings.add(HeldMessagePendable(
            id=request_id, list_id=self._mlist.list_id))
        # Get the hash for this pending request.
        hash = list(self._request_db.held_requests)[0].data_hash
        handle_message(self._mlist, request_id, Action.discard)
        self.assertEqual(self._request_db.count, 0)
        self.assertIsNone(pendings.confirm(hash))
        self.assertIsNone(pendings.confirm(user_hash))

    def test_all_pendings_removed_old(self):
        # This is different from the testcase above in that it
        # handles the old style HeldMessagePendable where the
        # list_id is not added to pendedkeyvalue. It is only
        # meant so that new versions can handle old pendables.
        request_id = hold_message(self._mlist, self._msg)
        # The hold chain does more.
        pendings = getUtility(IPendings)
        user_hash = pendings.add(HeldMessagePendable(id=request_id))
        # Get the hash for this pending request.
        hash = list(self._request_db.held_requests)[0].data_hash
        handle_message(self._mlist, request_id, Action.discard)
        self.assertEqual(self._request_db.count, 0)
        self.assertIsNone(pendings.confirm(hash))
        self.assertIsNone(pendings.confirm(user_hash))

    def test_rejection_subject_decoded(self):
        # Test that a rejected message with an RFC 2047 encoded Subject: has
        # the Subject: decoded for the rejection
        del self._msg['subject']
        self._msg['Subject'] = '=?utf-8?q?hold_me?='
        request_id = hold_message(self._mlist, self._msg)
        handle_message(self._mlist, request_id, Action.reject)
        # The rejected message lives in the virgin queue.
        items = get_queue_messages('virgin', expected_count=1)
        rejection = items[0].msg
        self.assertEqual(str(rejection['subject']),
                         'Request to mailing list "Test" rejected')
        self.assertIn('Posting of your message titled "hold me"',
                      rejection.get_payload())


class TestUnsubscription(unittest.TestCase):
    """Test unsubscription requests."""

    layer = SMTPLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._manager = ISubscriptionManager(self._mlist)

    def test_unsubscribe_defer(self):
        # When unsubscriptions must be approved by the moderator, but the
        # moderator defers this decision.
        user_manager = getUtility(IUserManager)
        anne = user_manager.create_address('anne@example.org', 'Anne Person')
        token, token_owner, member = self._manager.register(
            anne, pre_verified=True, pre_confirmed=True, pre_approved=True)
        self.assertIsNone(token)
        self.assertEqual(member.address.email, 'anne@example.org')
        bart = user_manager.create_user('bart@example.com', 'Bart User')
        address = set_preferred(bart)
        self._mlist.subscribe(address, MemberRole.moderator)
        # Now hold and handle an unsubscription request.
        token = hold_unsubscription(self._mlist, 'anne@example.org')
        handle_unsubscription(self._mlist, token, Action.defer)
        items = get_queue_messages('virgin', expected_count=2)
        # Find the moderator message.
        for item in items:
            if item.msg['to'] == 'test-owner@example.com':
                break
        else:
            raise AssertionError('No moderator email found')
        self.assertEqual(
            item.msgdata['recipients'], {'test-owner@example.com'})
        self.assertEqual(
            item.msg['subject'],
            'New unsubscription request from Test by anne@example.org')

    def test_bogus_token(self):
        # Try to handle an unsubscription with a bogus token.
        self.assertRaises(LookupError, self._manager.confirm, None)
