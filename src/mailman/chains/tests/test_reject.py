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

"""Test the reject chain."""

import unittest

from mailman.app.bounces import bounce_message
from mailman.app.lifecycle import create_list
from mailman.core.chains import process as process_chain
from mailman.interfaces.languages import ILanguageManager
from mailman.interfaces.pipeline import RejectMessage
from mailman.testing.helpers import (
    get_queue_messages,
    LogFileMark,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer
from zope.component import getUtility


class TestReject(unittest.TestCase):
    """Test the reject chain."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: Ignore
Message-Id: <mid@example.com>

""")

    def test_reject_reasons(self):
        # The bounce message must contain the moderation reasons.
        msgdata = dict(moderation_reasons=[
            'TEST-REASON-1',
            'TEST-REASON-2',
            ('TEST-{}-REASON-{}', 'FORMAT', 3),
            ])
        logfile = LogFileMark('mailman.vette')
        process_chain(self._mlist, self._msg, msgdata, start_chain='reject')
        bounces = get_queue_messages('virgin', expected_count=1)
        payload = bounces[0].msg.get_payload(0).as_string()
        self.assertIn('TEST-REASON-1', payload)
        self.assertIn('TEST-REASON-2', payload)
        self.assertIn('TEST-FORMAT-REASON-3', payload)
        logged = logfile.read()
        self.assertIn('REJECT: <mid@example.com>', logged)
        self.assertIn('TEST-REASON-1', logged)
        self.assertIn('TEST-REASON-2', logged)
        self.assertIn('TEST-FORMAT-REASON-3', logged)

    def test_bounce_message_bad_charset(self):
        # Force the list to use us-ascii charset to trigger UnicodeError
        language_manager = getUtility(ILanguageManager)
        en_language = language_manager.get('en')
        self._mlist.preferred_language = en_language
        error = RejectMessage('Non-ascii text é.')
        bounce_message(self._mlist, self._msg, error)
        items = get_queue_messages('virgin', expected_count=1)
        notice = items[0].msg.get_payload(0)
        self.assertEqual(
            notice.get_payload(decode=True).decode(),
            'Non-ascii text é.'
        )
        self.assertEqual(notice.get_content_charset(), 'utf-8')

    def test_no_reason(self):
        # There may be no moderation reasons.
        process_chain(self._mlist, self._msg, {}, start_chain='reject')
        bounces = get_queue_messages('virgin', expected_count=1)
        payload = bounces[0].msg.get_payload(0).as_string()
        self.assertIn('No bounce details are available', payload)
