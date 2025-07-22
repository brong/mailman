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

"""Test the bounce runner."""

import unittest

from datetime import timedelta
from mailman.app.bounces import send_probe
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.database.transaction import transaction
from mailman.interfaces.bounce import (
    BounceContext,
    IBounceProcessor,
    UnrecognizedBounceDisposition,
)
from mailman.interfaces.languages import ILanguageManager
from mailman.interfaces.member import DeliveryStatus, MemberRole
from mailman.interfaces.pending import IPendings
from mailman.interfaces.styles import IStyle, IStyleManager
from mailman.interfaces.usermanager import IUserManager
from mailman.runners.bounce import BounceRunner
from mailman.testing.helpers import (
    get_queue_messages,
    LogFileMark,
    make_testable_runner,
    specialized_message_from_string as message_from_string,
)
from mailman.testing.layers import ConfigLayer
from mailman.utilities.datetime import now
from zope.component import getUtility
from zope.interface import implementer


class TestBounceRunner(unittest.TestCase):
    """Test the bounce runner."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._mlist.send_welcome_message = False
        self._bounceq = config.switchboards['bounces']
        self._runner = make_testable_runner(BounceRunner, 'bounces')
        self._anne = getUtility(IUserManager).create_address(
            'anne@example.com')
        self._member = self._mlist.subscribe(self._anne, MemberRole.member)
        self._msg = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces+anne=example.com@example.com
Message-Id: <first>
Content-Type: multipart/report; report-type=delivery-status; boundary=AAA
MIME-Version: 1.0

--AAA
Content-Type: message/delivery-status

Action: fail
Original-Recipient: rfc822; anne@example.com

--AAA--
""")
        self._msgdata = dict(listid='test.example.com')
        self._processor = getUtility(IBounceProcessor)
        self._pendings = getUtility(IPendings)
        config.push('site owner', """
        [mailman]
        site_owner: postmaster@example.com
        """)
        self.addCleanup(config.pop, 'site owner')

    def test_does_no_processing(self):
        # If the mailing list does no bounce processing, the messages are
        # simply discarded.
        self._mlist.process_bounces = False
        self._bounceq.enqueue(self._msg, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        self.assertEqual(len(list(self._processor.events)), 0)

    def test_verp_detection(self):
        # When we get a VERPd bounce, and we're doing processing, a bounce
        # event will be registered.
        self._bounceq.enqueue(self._msg, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].email, 'anne@example.com')
        self.assertEqual(events[0].list_id, 'test.example.com')
        self.assertEqual(events[0].message_id, '<first>')
        self.assertEqual(events[0].context, BounceContext.normal)
        self.assertEqual(events[0].processed, True)

    def test_verp_non_dsn(self):
        # A VERPed non DSN (vacation response) is not scored.
        nondsn = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces+anne=example.com@example.com
Message-Id: <first>

I'm out of the office.
""")
        self._bounceq.enqueue(nondsn, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 0)

    def test_nonfatal_verp_detection(self):
        # A VERPd bounce was received, but the error was nonfatal.
        nonfatal = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces+anne=example.com@example.com
Message-Id: <first>
Content-Type: multipart/report; report-type=delivery-status; boundary=AAA
MIME-Version: 1.0

--AAA
Content-Type: message/delivery-status

Action: delayed
Original-Recipient: rfc822; somebody@example.com

--AAA--
""")
        self._bounceq.enqueue(nonfatal, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 0)

    def test_send_probe_non_ascii(self):
        # Send a pobe from an English language list to a user with non-ascii
        # preferred language.
        language_manager = getUtility(ILanguageManager)
        self._mlist.preferred_language = language_manager.get('en')
        # French charset is utf-8, but testing has it as latin-1
        french = language_manager.get('fr')
        french.charset = 'utf-8'
        self._member.address.preferences.preferred_language = french
        send_probe(self._member, self._msg)
        items = get_queue_messages('virgin', expected_count=1)
        msg = items[0].msg
        self.assertIn(b'anne@example.com',
                      msg.get_payload()[0].get_payload(decode=True))

    def test_verp_probe_bounce(self):
        # A VERP probe bounced.  The primary difference here is that the
        # registered bounce event will have a different context.  The
        # Message-Id will be different too, because of the way we're
        # simulating the probe bounce.
        #
        # Start be simulating a probe bounce.
        send_probe(self._member, self._msg)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        bounce = message_from_string("""\
To: {0}
From: mail-daemon@example.com
Message-Id: <second>

""".format(message['From']))
        self._bounceq.enqueue(bounce, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].email, 'anne@example.com')
        self.assertEqual(events[0].list_id, 'test.example.com')
        self.assertEqual(events[0].message_id, '<second>')
        self.assertEqual(events[0].context, BounceContext.probe)
        self.assertEqual(events[0].processed, True)

    def test_nonverp_detectable_fatal_bounce(self):
        # Here's a bounce that is not VERPd, but which has a bouncing address
        # that can be parsed from a known bounce format.  DSN is as good as
        # any, but we'll make the parsed address different for the fun of it.
        dsn = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces@example.com
Message-Id: <first>
Content-Type: multipart/report; report-type=delivery-status; boundary=AAA
MIME-Version: 1.0

--AAA
Content-Type: message/delivery-status

Action: fail
Original-Recipient: rfc822; bart@example.com

--AAA--
""")
        self._bounceq.enqueue(dsn, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].email, 'bart@example.com')
        self.assertEqual(events[0].list_id, 'test.example.com')
        self.assertEqual(events[0].message_id, '<first>')
        self.assertEqual(events[0].context, BounceContext.normal)
        self.assertEqual(events[0].processed, True)

    def test_nonverp_detectable_nonfatal_bounce(self):
        # Here's a bounce that is not VERPd, but which has a bouncing address
        # that can be parsed from a known bounce format.  The bounce is
        # non-fatal so no bounce event is registered and the bounce is not
        # reported as unrecognized.
        self._mlist.forward_unrecognized_bounces_to = (
            UnrecognizedBounceDisposition.site_owner)
        dsn = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces@example.com
Message-Id: <first>
Content-Type: multipart/report; report-type=delivery-status; boundary=AAA
MIME-Version: 1.0

--AAA
Content-Type: message/delivery-status

Action: delayed
Original-Recipient: rfc822; bart@example.com

--AAA--
""")
        self._bounceq.enqueue(dsn, self._msgdata)
        mark = LogFileMark('mailman.bounce')
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 0)
        # There should be nothing in the 'virgin' queue.
        get_queue_messages('virgin', expected_count=0)
        # There should be log event in the log file.
        log_lines = mark.read().splitlines()
        self.assertTrue(len(log_lines) > 0)

    def test_no_detectable_bounce_addresses(self):
        # A bounce message was received, but no addresses could be detected.
        # A message will be logged in the bounce log though, and the message
        # can be forwarded to someone who can do something about it.
        self._mlist.forward_unrecognized_bounces_to = (
            UnrecognizedBounceDisposition.site_owner)
        bogus = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces@example.com
Message-Id: <third>

""")
        self._bounceq.enqueue(bogus, self._msgdata)
        mark = LogFileMark('mailman.bounce')
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 0)
        line = mark.readline()
        self.assertEqual(
            line[-51:-1],
            'Bounce message w/no discernable addresses: <third>')
        # Here's the forwarded message to the site owners.
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(items[0].msg['to'], 'postmaster@example.com')

    def test_saving_dsn_pends_msgid(self):
        # When we save a DSN for notices, we have to pend its msgid so task
        # Runner won't remove it as an orphan.
        self._bounceq.enqueue(self._msg, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].email, 'anne@example.com')
        self.assertEqual(events[0].list_id, 'test.example.com')
        self.assertEqual(events[0].message_id, '<first>')
        self.assertEqual(events[0].context, BounceContext.normal)
        self.assertEqual(events[0].processed, True)
        # Check for a pended item with the msgid.
        self.assertEqual(self._pendings.count(), 1)
        token, pended = list(self._pendings)[0]
        self.assertEqual(pended['_mod_message_id'], '<first>')

    def test_bounce_increment_notice_sent_with_dsn(self):
        # Test that bounce_notify_owner_on_bounce_increment sends a notice
        # and the notice contains the DSN.
        self._mlist.bounce_notify_owner_on_bounce_increment = True
        self._bounceq.enqueue(self._msg, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].email, 'anne@example.com')
        self.assertEqual(events[0].list_id, 'test.example.com')
        self.assertEqual(events[0].message_id, '<first>')
        self.assertEqual(events[0].context, BounceContext.normal)
        self.assertEqual(events[0].processed, True)
        # Anne's delivery should still be enabled.
        self.assertEqual(DeliveryStatus.enabled, self._member.delivery_status)
        # There should be a message to the admin.
        items = get_queue_messages('virgin', expected_count=1)
        msg = items[0].msg
        self.assertEqual("anne@example.com's bounce score incremented on Test",
                         msg['subject'])
        # The message should be multipart withe the DSN attached.
        self.assertEqual('multipart/mixed', msg.get_content_type())
        dsn = msg.get_payload(1)
        self.assertEqual('message/rfc822', dsn.get_content_type())
        self.assertIn('From: mail-daemon@example.com\n'
                      'To: test-bounces+anne=example.com@example.com\n'
                      'Message-Id: <first>', dsn.as_string())

    def test_bounce_disabled_notice_sent_with_dsn(self):
        # Test that bounce_notify_owner_on_disable sends a notice
        # and the notice contains the DSN.
        self._mlist.bounce_score_threshold = 1
        self._bounceq.enqueue(self._msg, self._msgdata)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].email, 'anne@example.com')
        self.assertEqual(events[0].list_id, 'test.example.com')
        self.assertEqual(events[0].message_id, '<first>')
        self.assertEqual(events[0].context, BounceContext.normal)
        self.assertEqual(events[0].processed, True)
        # Anne's delivery should be disabled.
        self.assertEqual(DeliveryStatus.by_bounces,
                         self._member.delivery_status)
        # There should be a messages to the admin and user.
        items = get_queue_messages('virgin', expected_count=2)
        # Get the one to the admin.
        if items[0].msg['to'] == 'test-owner@example.com':
            msg = items[0].msg
        else:
            msg = items[1].msg
        self.assertEqual("anne@example.com's subscription disabled on Test",
                         msg['subject'])
        # The message should be multipart withe the DSN attached.
        self.assertEqual('multipart/mixed', msg.get_content_type())
        dsn = msg.get_payload(1)
        self.assertEqual('message/rfc822', dsn.get_content_type())
        self.assertIn('From: mail-daemon@example.com\n'
                      'To: test-bounces+anne=example.com@example.com\n'
                      'Message-Id: <first>', dsn.as_string())


# Create a style for the mailing list which sets the absolute minimum
# attributes.  In particular, this will not set the bogus `bounce_processing`
# attribute which the default style set (before LP: #876774 was fixed).

@implementer(IStyle)
class TestStyle:
    """See `IStyle`."""

    name = 'test'
    description = 'A test style.'

    def apply(self, mailing_list):
        """See `IStyle`."""
        mailing_list.preferred_language = 'en'


class TestBounceRunnerBug876774(unittest.TestCase):
    """Test LP: #876774.

    Quoting:

    It seems that bounce_processing is defined in src/mailman/styles/default.py
    The style are applied at mailing-list creation, but bounce_processing
    attribute is not persisted, the src/mailman/database/mailman.sql file
    doesn't define it.
    """
    layer = ConfigLayer

    def setUp(self):
        self._style = TestStyle()
        self._style_manager = getUtility(IStyleManager)
        self._style_manager.register(self._style)
        self.addCleanup(self._style_manager.unregister, self._style)
        # Now we can create the mailing list.
        self._mlist = create_list('test@example.com', style_name='test')
        self._bounceq = config.switchboards['bounces']
        self._processor = getUtility(IBounceProcessor)
        self._runner = make_testable_runner(BounceRunner, 'bounces')

    def test_bug876774(self):
        # LP: #876774, see above.
        bounce = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces+anne=example.com@example.com
Message-Id: <first>

""")
        self._bounceq.enqueue(bounce, dict(listid='test.example.com'))
        self.assertEqual(len(self._bounceq.files), 1)
        self._runner.run()
        get_queue_messages('bounces', expected_count=0)
        events = list(self._processor.events)
        self.assertEqual(len(events), 0)


class TestBounceRunnerPeriodicRun(unittest.TestCase):
    """Test the bounce runner's periodic function.."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._mlist.send_welcome_message = False
        self._runner = make_testable_runner(BounceRunner, 'bounces')
        self._anne = getUtility(IUserManager).create_address(
            'anne@example.com')
        self._member = self._mlist.subscribe(self._anne, MemberRole.member)
        self._msg = message_from_string("""\
From: mail-daemon@example.com
To: test-bounces+anne=example.com@example.com
Message-Id: <first>

""")
        self._msgdata = dict(listid='test.example.com')
        self._processor = getUtility(IBounceProcessor)
        config.push('site owner', """
        [mailman]
        site_owner: postmaster@example.com
        """)
        self.addCleanup(config.pop, 'site owner')

    def _subscribe_and_add_bounce_event(
            self, addr, subscribe=True, create=True, context=None, count=1):
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

    def test_periodic_bounce_event_processing(self):
        anne = self._subscribe_and_add_bounce_event(
            'anne@example.com', subscribe=False, create=False)
        bart = self._subscribe_and_add_bounce_event('bart@example.com')
        # Since MailingList has process_bounces set to False, nothing happens
        # with the events.
        self._runner.run()
        self.assertEqual(anne.bounce_score, 1.0)
        self.assertEqual(bart.bounce_score, 1.0)
        for event in self._processor.events:
            self.assertEqual(event.processed, True)

    def test_events_disable_delivery(self):
        self._mlist.bounce_score_threshold = 3
        anne = self._subscribe_and_add_bounce_event(
            'anne@example.com', subscribe=False, create=False)
        anne.bounce_score = 2
        anne.last_bounce_received = now() - timedelta(days=2)
        self._runner.run()
        # Score is reset when delovery is disabled.
        self.assertEqual(anne.bounce_score, 0)
        self.assertEqual(
            anne.preferences.delivery_status, DeliveryStatus.by_bounces)
        # There should also be a pending notification for the list
        # administrator.
        items = get_queue_messages('virgin', expected_count=2)
        if items[0].msg['to'] == 'test-owner@example.com':
            owner_notif, disable_notice = items
        else:
            disable_notice, owner_notif = items
        self.assertEqual(owner_notif.msg['Subject'],
                         "anne@example.com's subscription disabled on Test")
        self.assertEqual(disable_notice.msg['to'], 'anne@example.com')
        self.assertEqual(
            str(disable_notice.msg['subject']),
            'Your subscription for Test mailing list has been disabled')

    def test_events_send_warning(self):
        self._mlist.bounce_you_are_disabled_warnings = 3
        self._mlist.bounce_you_are_disabled_warnings_interval = timedelta(
            days=2)

        anne = self._mlist.members.get_member(self._anne.email)
        anne.preferences.delivery_status = DeliveryStatus.by_bounces
        anne.total_warnings_sent = 1
        anne.last_warning_sent = now() - timedelta(days=3)

        self._runner.run()
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(str(items[0].msg['to']), 'anne@example.com')
        self.assertEqual(
            str(items[0].msg['subject']),
            'Your subscription for Test mailing list has been disabled')
        self.assertEqual(anne.total_warnings_sent, 2)
        self.assertEqual(anne.last_warning_sent.day, now().day)

    def test_events_bounce_already_disabled(self):
        # A bounce received for an already disabled member is only logged.
        anne = self._subscribe_and_add_bounce_event(
            'anne@example.com', subscribe=False, create=False)
        self._mlist.bounce_score_threshold = 3
        anne.bounce_score = 3
        anne.preferences.delivery_status = DeliveryStatus.by_bounces
        anne.total_warnings_sent = 1
        anne.last_warning_sent = now() - timedelta(days=3)
        mark = LogFileMark('mailman.bounce')
        self._runner.run()
        get_queue_messages('virgin', expected_count=0)
        self.assertEqual(anne.total_warnings_sent, 1)
        self.assertIn(
           'Residual bounce received for member anne@example.com '
           'on list test.example.com.', mark.read()
           )

    def test_events_membership_removal(self):
        self._mlist.bounce_notify_owner_on_removal = True
        self._mlist.bounce_you_are_disabled_warnings = 3
        self._mlist.bounce_you_are_disabled_warnings_interval = timedelta(
            days=2)

        anne = self._mlist.members.get_member(self._anne.email)
        anne.preferences.delivery_status = DeliveryStatus.by_bounces
        anne.total_warnings_sent = 3
        # Don't remove immediately.
        anne.last_warning_sent = now() - timedelta(days=2)

        self._runner.run()
        items = get_queue_messages('virgin', expected_count=2)
        if items[0].msg['to'] == 'test-owner@example.com':
            owner_notif, user_notif = items
        else:
            user_notif, owner_notif = items
        self.assertEqual(user_notif.msg['to'], 'anne@example.com')
        self.assertEqual(
            user_notif.msg['subject'],
            'You have been unsubscribed from the Test mailing list')

        self.assertEqual(
            str(owner_notif.msg['subject']),
            'anne@example.com unsubscribed from Test mailing '
            'list due to bounces')
        # The membership should no longer exist.
        self.assertIsNone(
            self._mlist.members.get_member(self._anne.email))

    def test_events_membership_removal_no_warnings(self):
        self._mlist.bounce_notify_owner_on_removal = True
        self._mlist.bounce_you_are_disabled_warnings = 0
        self._mlist.bounce_you_are_disabled_warnings_interval = timedelta(
            days=2)

        anne = self._mlist.members.get_member(self._anne.email)
        anne.preferences.delivery_status = DeliveryStatus.by_bounces
        anne.total_warnings_sent = 0
        # Remove immediately.
        anne.last_warning_sent = now()

        self._runner.run()
        items = get_queue_messages('virgin', expected_count=2)
        if items[0].msg['to'] == 'test-owner@example.com':
            owner_notif, user_notif = items
        else:
            user_notif, owner_notif = items
        self.assertEqual(user_notif.msg['to'], 'anne@example.com')
        self.assertEqual(
            user_notif.msg['subject'],
            'You have been unsubscribed from the Test mailing list')

        self.assertEqual(
            str(owner_notif.msg['subject']),
            'anne@example.com unsubscribed from Test mailing '
            'list due to bounces')
        # The membership should no longer exist.
        self.assertIsNone(
            self._mlist.members.get_member(self._anne.email))

    def test_events_membership_removal_not_immediate(self):
        self._mlist.bounce_notify_owner_on_removal = True
        self._mlist.bounce_you_are_disabled_warnings = 3
        self._mlist.bounce_you_are_disabled_warnings_interval = timedelta(
            days=2)

        anne = self._mlist.members.get_member(self._anne.email)
        anne.preferences.delivery_status = DeliveryStatus.by_bounces
        anne.total_warnings_sent = 3
        # Don't remove immediately.
        anne.last_warning_sent = now()

        self._runner.run()
        get_queue_messages('virgin', expected_count=0)
        # The membership should still exist.
        self.assertIsNotNone(
            self._mlist.members.get_member(self._anne.email))
