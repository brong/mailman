# Copyright (C) 2017-2025 by the Free Software Foundation, Inc.
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

"""Test for the DMARC chain."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.core.chains import process as process_chain
from mailman.interfaces.chain import DiscardEvent, RejectEvent
from mailman.testing.helpers import (
    event_subscribers,
    get_queue_messages,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer


class TestDMARC(unittest.TestCase):
    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: Ignore

""")

    def test_discard(self):
        msgdata = dict(dmarc_action='discard')
        # When a message is discarded, the only artifacts are a log message
        # and an event.  Catch the event to prove it happened.
        events = []
        def handler(event):                         # noqa: E306
            if isinstance(event, DiscardEvent):
                events.append(event)
        with event_subscribers(handler):
            process_chain(self._mlist, self._msg, msgdata, start_chain='dmarc')
        self.assertEqual(len(events), 1)
        self.assertIs(events[0].msg, self._msg)

    def test_reject(self):
        msgdata = dict(
            dmarc_action='reject',
            moderation_reasons=['DMARC violation'],
            )
        # When a message is reject, an event will be triggered and the message
        # will be bounced.
        events = []
        def handler(event):                         # noqa: E306
            if isinstance(event, RejectEvent):
                events.append(event)
        with event_subscribers(handler):
            process_chain(self._mlist, self._msg, msgdata, start_chain='dmarc')
        self.assertEqual(len(events), 1)
        self.assertIs(events[0].msg, self._msg)
        items = get_queue_messages('virgin', expected_count=1)
        # Unpack the rejection message.
        rejection = items[0].msg.get_payload(0).get_payload()
        self.assertEqual(rejection, """\
Your message to the Ant mailing-list was rejected for the following
reasons:

DMARC violation

The original message as received by Mailman is attached.
""")
