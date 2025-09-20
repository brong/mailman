# Copyright (C) 2014-2025 by the Free Software Foundation, Inc.
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

"""Test the cleanse handler."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.testing.helpers import (
    LogFileMark,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer


class TestCleanse(unittest.TestCase):
    """Test the cleanse handler."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.description = 'Test List'
        self._mlist.anonymous_list = True
        self._msg = mfs("""\
Received: from somewhere
Approved: pw
Urgent: pw
To: <ant@example.com>
From: <anne@example.com>
Reply-To: <anne@example.com>
Sender: <bart@example.com>
Organization: not much
Return-Path: <bounce@example.com>
X-Originating-Email: <anne@example.com>
Message-ID: <1234@example.com>
X-MailFrom: <anne@example.com>
X-Envelope-From: <anne@example.com>
X-Unknown: some value
Return-Receipt-To: <anne@example.com>
Authentication-Results: example.com; dkim=pass
ARC-Authentication-Results: i=1; example.com
ARC-Message-Signature:  i=1; d=example.com
DKIM-Signature: d=example.com

body
""")
        self._headers = set(self._msg.keys())

    def test_non_anonymous(self):
        # Non anonymous lists remove a few headers
        self._mlist.anonymous_list = False
        config.handlers['cleanse'].process(self._mlist, self._msg, {})
        removals = set(('Approved', 'Urgent', 'Return-Receipt-To'))
        self.assertEqual(set(self._msg.keys()), self._headers - removals)

    def test_anonymous(self):
        # Test removals and logging for an anonymous list
        mark = LogFileMark('mailman.smtp')
        config.handlers['cleanse'].process(self._mlist, self._msg, {})
        removals = set(('Approved', 'Urgent', 'Return-Receipt-To', 'Received',
                        'Sender', 'Organization', 'Return-Path',
                        'X-Originating-Email', 'X-MailFrom', 'X-Envelope-From',
                        'X-Unknown', 'Authentication-Results',
                        'ARC-Authentication-Results', 'ARC-Message-Signature',
                        'DKIM-Signature'))
        self.assertEqual(set(self._msg.keys()), self._headers - removals)
        self.assertNotEqual('<1234@example.com>', self._msg['message-id'])
        self.assertEqual('ant@example.com', self._msg['reply-to'])
        self.assertEqual('Test List <ant@example.com>', self._msg['from'])
        self.assertIn(
            'post to ant@example.com from <anne@example.com> anonymized',
            mark.read())

    def test_anonymous_all_keepers(self):
        # Test removals for an anonymous list where anonymous_list_keep_headers
        # keeps all.  X-Unknown should not be removed.
        config.push('keep_all', """
        [mailman]
        anonymous_list_keep_headers: ^.
        """)
        self.addCleanup(config.pop, 'keep_all')
        config.handlers['cleanse'].process(self._mlist, self._msg, {})
        removals = set(('Approved', 'Urgent', 'Return-Receipt-To', 'Received',
                        'Sender', 'Organization', 'Return-Path',
                        'X-Originating-Email', 'X-MailFrom',
                        'X-Envelope-From', 'Authentication-Results',
                        'ARC-Authentication-Results', 'ARC-Message-Signature',
                        'DKIM-Signature'))
        self.assertEqual(set(self._msg.keys()), self._headers - removals)

    def test_anonymous_bad_regexp(self):
        # Test that a bad regexp is logged and otherwise ignored.
        config.push('bad_regexp', """
        [mailman]
        anonymous_list_keep_headers: ^(?!x-) ?.
        """)
        # In the above, ^(?!x-) keeps all non X- and ?. is invalid.
        self.addCleanup(config.pop, 'bad_regexp')
        mark = LogFileMark('mailman.error')
        config.handlers['cleanse'].process(self._mlist, self._msg, {})
        removals = set(('Approved', 'Urgent', 'Return-Receipt-To', 'Received',
                        'Sender', 'Organization', 'Return-Path',
                        'X-Originating-Email', 'X-MailFrom', 'X-Envelope-From',
                        'X-Unknown', 'Authentication-Results',
                        'ARC-Authentication-Results', 'ARC-Message-Signature',
                        'DKIM-Signature'))
        self.assertEqual(set(self._msg.keys()), self._headers - removals)
        self.assertIn('Ignored bad anonymous_list_keep_headers regexp ?.: '
                      'nothing to repeat at position 0', mark.read())
