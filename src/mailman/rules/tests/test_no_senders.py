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

"""Test the `no_subject` header rule."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.email.message import Message
from mailman.rules import no_senders
from mailman.testing.layers import ConfigLayer


class TestNoSender(unittest.TestCase):
    """Test the no_senders rule."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._rule = no_senders.NoSenders()

    def test_message_has_no_sender(self):
        msg = Message()
        msgdata = {}
        result = self._rule.check(self._mlist, msg, msgdata)
        self.assertTrue(result)
        self.assertEqual(msgdata['moderation_reasons'],
                         ['The message has no valid senders'])
        self.assertEqual(msgdata['moderation_sender'], 'N/A')

    def test_message_has_sender(self):
        msg = Message()
        msg['From'] = 'anne@example.com'
        msgdata = {}
        result = self._rule.check(self._mlist, msg, msgdata)
        self.assertFalse(result)
        self.assertEqual(msgdata, {})
