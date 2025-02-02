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

"""Test bounce model objects."""

import unittest

from datetime import datetime, timedelta
from mailman.app.lifecycle import create_list, remove_list
from mailman.database.transaction import transaction
from mailman.interfaces.bounce import (
    BounceContext,
    IBounceProcessor,
    InvalidBounceEvent,
)
from mailman.interfaces.member import DeliveryStatus
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.helpers import (
    configuration,
    get_queue_messages,
    LogFileMark,
    specialized_message_from_string as message_from_string,
)
from mailman.testing.layers import ConfigLayer
from mailman.utilities.datetime import now
from zope.component import getUtility


class TestBounceEvents(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._processor = getUtility(IBounceProcessor)
        with transaction():
            self._mlist = create_list('test@example.com')
        self._msg = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces@example.com
Message-Id: <first>

""")

    def _subscribe_and_add_bounce_event(
            self, addr, subscribe=True, create=True, context=None):
        user_mgr = getUtility(IUserManager)
        with transaction():
            if create:
                anne = user_mgr.create_address(addr)
            else:
                anne = user_mgr.get_address(addr)
            if subscribe:
                self._mlist.subscribe(anne)
            self._processor.register(
                self._mlist, addr, self._msg, where=context)
        return self._mlist.members.get_member(addr)

    def _process_pending_events(self):
        events = list(self._processor.unprocessed)
        for event in events:
            self._processor.process_event(event)
        return events

    def test_events_iterator(self):
        self._subscribe_and_add_bounce_event('anne@example.com')
        events = list(self._processor.events)
        self.assertEqual(len(events), 1)
        event = events[0]
        self.assertEqual(event.list_id, 'test.example.com')
        self.assertEqual(event.email, 'anne@example.com')
        self.assertEqual(event.timestamp, datetime(2005, 8, 1, 7, 49, 23))
        self.assertEqual(event.message_id, '<first>')
        self.assertEqual(event.context, BounceContext.normal)
        self.assertEqual(event.processed, False)
        # The unprocessed list will be exactly the same right now.
        unprocessed = list(self._processor.unprocessed)
        self.assertEqual(len(unprocessed), 1)
        event = unprocessed[0]
        self.assertEqual(event.list_id, 'test.example.com')
        self.assertEqual(event.email, 'anne@example.com')
        self.assertEqual(event.timestamp, datetime(2005, 8, 1, 7, 49, 23))
        self.assertEqual(event.message_id, '<first>')
        self.assertEqual(event.context, BounceContext.normal)
        self.assertFalse(event.processed)

    def test_unprocessed_events_iterator(self):
        self._subscribe_and_add_bounce_event('anne@example.com')
        self._subscribe_and_add_bounce_event('bartanne@example.com')

        events = list(self._processor.events)
        self.assertEqual(len(events), 2)
        unprocessed = list(self._processor.unprocessed)
        # The unprocessed list will be exactly the same right now.
        self.assertEqual(len(unprocessed), 2)
        # Process one of the events.
        with transaction():
            events[0].processed = True
        # Now there will be only one unprocessed event.
        unprocessed = list(self._processor.unprocessed)
        self.assertEqual(len(unprocessed), 1)
        # Process the other event.
        with transaction():
            events[1].processed = True
        # Now there will be no unprocessed events.
        unprocessed = list(self._processor.unprocessed)
        self.assertEqual(len(unprocessed), 0)

    def test_process_bounce_event(self):
        # Test that we are able to process bounce events.
        self._subscribe_and_add_bounce_event(
            'anne@example.com', subscribe=False)
        events = list(self._processor.unprocessed)
        self.assertEqual(len(events), 1)
        # If the associated email with the event is not a member of the
        # MailingList, an InvalidBounceEvent exception is raised.
        with self.assertRaises(InvalidBounceEvent):
            self._processor.process_event(events[0])
        # Now, we will subscribe the user and see if we can process the event
        # further and add another bounce event for anne.
        self._subscribe_and_add_bounce_event('anne@example.com', create=False)
        events = list(self._processor.unprocessed)
        self.assertEqual(len(events), 1)

        member = self._mlist.members.get_member('anne@example.com')
        self.assertTrue(member is not None)

        self._processor.process_event(events[0])
        # Now, we should be able to check the bounce score of anne.
        self.assertEqual(member.bounce_score, 1)
        self.assertIsNotNone(member.last_bounce_received)
        # Also, the delivery should be unset, the default.
        self.assertIsNone(member.preferences.delivery_status)

        # Now create another event for Anne.
        self._subscribe_and_add_bounce_event('anne@example.com', create=False,
                                             subscribe=False)
        # Now delete the list and process the bounce for the non-existent list.
        remove_list(self._mlist)
        events = list(self._processor.unprocessed)
        self.assertEqual(len(events), 1)
        # If the MailingList has been deleted, an InvalidBounceEvent exception
        # is raised.
        with self.assertRaises(InvalidBounceEvent):
            self._processor.process_event(events[0])

    def test_bounce_score_increases_once_everyday(self):
        # Test only the bounce events more than a day apart can increase the
        # bounce score of a member.
        # Add two events, for the same day.
        self._subscribe_and_add_bounce_event('anne@example.com')
        member = self._subscribe_and_add_bounce_event(
            'anne@example.com', create=False, subscribe=False)
        events = list(self._processor.unprocessed)
        self.assertEqual(len(events), 2)
        for event in events:
            self._processor.process_event(event)
        self.assertEqual(member.bounce_score, 1)

    def test_stale_bounce_score_is_reset(self):
        # Test that the bounce score is reset after
        # mlist.bounce_info_stale_after number of days.
        member = self._subscribe_and_add_bounce_event('anne@example.com')
        member.bounce_score = 10
        # Set the last bouce received to be 2 days before the threshold.
        member.last_bounce_received = (
            now() - self._mlist.bounce_info_stale_after - timedelta(days=2))
        events = list(self._processor.unprocessed)
        self.assertEqual(len(events), 1)
        self._processor.process_event(events[0])
        self.assertEqual(member.bounce_score, 1)

    def test_bounce_score_over_threshold_disables_delivery(
            self, expected_count=1):
        # Test that the bounce score higher than thereshold disbales delivery
        # for the member.
        self._mlist.bounce_score_threshold = 1
        # Disable welcome message so we can assert admin notice later.
        self._mlist.send_welcome_message = False

        self._subscribe_and_add_bounce_event('anne@example.com')
        member = self._subscribe_and_add_bounce_event(
            'anne@example.com', create=False, subscribe=False)

        # We need to make sure that events are not on same date to have them
        # increase the bounce score.
        events = list(self._processor.unprocessed)
        events[0].timestamp = events[0].timestamp - timedelta(days=2)

        # Now, process the events and check that user is disabled.
        for event in events:
            self._processor.process_event(event)
        # The first event scores 1 and disables delivery.  Disabling delivery
        # resets the score. The second is not processed because delivery is
        # already disabled.
        self.assertEqual(member.bounce_score, 0)
        self.assertEqual(
            member.preferences.delivery_status, DeliveryStatus.by_bounces)

        # There should be an admin notice about the disabled subscription.
        messages = get_queue_messages('virgin', expected_count=expected_count)
        if expected_count > 0:
            msg = messages[0].msg
            self.assertEqual(
                str(msg['Subject']),
                'anne@example.com\'s subscription disabled on Test')

    def test_bounce_disable_skips_admin_notice(self):
        # Test that when a subscription is disabled, the admin is notified if
        # the mailing list is configured to send notices.
        self._mlist.bounce_notify_owner_on_disable = False
        self.test_bounce_score_over_threshold_disables_delivery(
            expected_count=0)

    @configuration('mta', verp_probes='yes')
    def test_bounce_score_over_threshold_sends_probe(self):
        # Test that bounce score over threshold does not disables delivery if
        # the MailingList is configured to send probes first.
        # Sending probe also resets bounce_score.
        # Disable welcome message so we can assert admin notice later.
        self._mlist.send_welcome_message = False
        self._mlist.bounce_score_threshold = 0
        member = self._subscribe_and_add_bounce_event('anne@example.com')
        member.bounce_score = 1
        # Process events.
        self._process_pending_events()
        self.assertEqual(member.bounce_score, 0)
        self.assertIsNone(member.preferences.delivery_status)
        messages = get_queue_messages('virgin', expected_count=1)
        msg = messages[0].msg
        self.assertEqual(str(msg['subject']),
                         'Test mailing list probe message')

    @configuration('mta', verp_probes='yes', probe_add_dsn='yes')
    def test_probe_has_dsn_attached(self):
        # Test that the probe has DSN attached.
        self._mlist.send_welcome_message = False
        self._mlist.bounce_score_threshold = 0
        member = self._subscribe_and_add_bounce_event('anne@example.com')
        member.bounce_score = 1
        # Process events.
        self._process_pending_events()
        messages = get_queue_messages('virgin', expected_count=1)
        msg = messages[0].msg
        self.assertEqual(str(msg['subject']),
                         'Test mailing list probe message')
        self.assertTrue(msg.is_multipart())
        self.assertEqual(str(msg.get_payload(1).get_payload()[0]),
                         self._msg.as_string())

    @configuration('mta', verp_probes='yes', probe_add_dsn='no')
    def test_probe_has_dsn_not_attached(self):
        # Test that the probe doesn't have DSN attached.
        self._mlist.send_welcome_message = False
        self._mlist.bounce_score_threshold = 0
        member = self._subscribe_and_add_bounce_event('anne@example.com')
        member.bounce_score = 1
        # Process events.
        self._process_pending_events()
        messages = get_queue_messages('virgin', expected_count=1)
        msg = messages[0].msg
        self.assertEqual(str(msg['subject']),
                         'Test mailing list probe message')
        self.assertEqual(len(msg.get_payload()), 1)

    def test_bounce_event_probe_disables_delivery(self):
        # That that bounce probe disables delivery immidiately.
        member = self._subscribe_and_add_bounce_event(
            'anne@example.com', context=BounceContext.probe)
        self._process_pending_events()
        self.assertEqual(
            member.preferences.delivery_status, DeliveryStatus.by_bounces)

    def test_disable_delivery_already_disabled(self):
        # Attempting to disable delivery for an already disabled member does
        # nothing.
        self._mlist.send_welcome_message = False
        member = self._subscribe_and_add_bounce_event('anne@example.com')
        events = list(self._processor.events)
        self.assertEqual(len(events), 1)
        member.total_warnings_sent = 3
        member.last_warning_sent = now() - timedelta(days=2)
        member.preferences.delivery_status = DeliveryStatus.by_bounces
        mark = LogFileMark('mailman.bounce')
        self._processor._disable_delivery(self._mlist, member, events[0])
        self.assertEqual(mark.read(), '')
        self.assertEqual(member.total_warnings_sent, 3)
        self.assertEqual(member.last_warning_sent, now() - timedelta(days=2))
        get_queue_messages('virgin', expected_count=0)

    def test_residual_bounce_marked_processed(self):
        # A bounce received after delivery is disabled should be marked as
        # processed.
        member = self._subscribe_and_add_bounce_event('anne@example.com')
        events = list(self._processor.unprocessed)
        self.assertEqual(len(events), 1)
        member.preferences.delivery_status = DeliveryStatus.by_bounces
        self._processor.process_event(events[0])
        events = list(self._processor.unprocessed)
        self.assertEqual(len(events), 0)

    def test_send_warnings_after_disable(self):
        # Test that required number of warnings are sent after the delivery is
        # disabled.
        self._mlist.bounce_notify_owner_on_disable = False
        self._mlist.bounce_you_are_disabled_warnings = 1
        self._mlist.bounce_you_are_disabled_warnings_interval = timedelta(
            days=1)
        self._mlist.bounce_score_threshold = 3
        self._mlist.send_welcome_message = False

        member = self._subscribe_and_add_bounce_event('anne@example.com')
        member.bounce_score = 3
        member.last_bounce_received = now() - timedelta(days=2)
        # We will process all events now.
        self._process_pending_events()
        self.assertEqual(member.preferences.delivery_status,
                         DeliveryStatus.by_bounces)
        # Disabling delivery resets the score.
        self.assertEqual(member.bounce_score, 0)

        self._processor._send_warnings()
        self.assertEqual(member.last_warning_sent.day, now().day)
        self.assertEqual(member.total_warnings_sent, 1)
        msgs = get_queue_messages('virgin', expected_count=1)
        msg = msgs[0].msg
        self.assertEqual(str(msg['Subject']),
                         'Your subscription for Test mailing list has'
                         ' been disabled')

    def test_send_warnings_and_remove_membership(self):
        # Test that required number of warnings are sent and then the
        # membership is removed.
        self._mlist.bounce_notify_owner_on_disable = False
        self._mlist.bounce_notify_owner_on_removal = True
        self._mlist.bounce_you_are_disabled_warnings = 1
        self._mlist.bounce_you_are_disabled_warnings_interval = timedelta(
            days=1)
        self._mlist.bounce_score_threshold = 3
        self._mlist.send_welcome_message = False

        member = self._subscribe_and_add_bounce_event('anne@example.com')
        member.bounce_score = 4
        member.last_bounce_received = now() - timedelta(days=2)
        member.total_warnings_sent = 1
        member.last_warning_sent = now() - timedelta(days=2)
        member.preferences.delivery_status = DeliveryStatus.by_bounces

        # Now make sure that we send the warnings.
        with transaction():
            self._processor.send_warnings_and_remove()

        member = self._mlist.members.get_member('anne@example.com')
        self.assertIsNone(member)
        # There should be only 2 messages in the queue. One notifying the user
        # of their removal and other notifying the admin about the removal.
        msgs = get_queue_messages('virgin', expected_count=2)
        if msgs[0].msg['to'] == self._mlist.owner_address:
            owner_notif, user_notice = msgs
        else:
            user_notice, owner_notif = msgs
        self.assertEqual(
            user_notice.msg['subject'],
            'You have been unsubscribed from the Test mailing list')
        self.assertEqual(
            owner_notif.msg['subject'],
            'anne@example.com unsubscribed from Test mailing list due '
            'to bounces')
