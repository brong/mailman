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

"""Test the `max_size` rule."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.rules import max_size
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer


class TestMaximumSize(unittest.TestCase):
    """Test the max_size rule."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')

    def test_max_size_returns_reason(self):
        # Ensure max_size rule returns a reason.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: A Subject
Message-ID: <ant>

A message body.
""")
        rule = max_size.MaximumSize()
        self._mlist.max_message_size = 1
        # Fake the size.
        msg.original_size = 2048
        msgdata = {}
        result = rule.check(self._mlist, msg, msgdata)
        self.assertTrue(result)
        self.assertEqual(msgdata['moderation_reasons'],
                         [('The message is larger than the {} KB maximum size',
                          1)])


class TestiFilteredMaximumSize(unittest.TestCase):
    """Test the max_size rule with filtered content."""

    layer = ConfigLayer

    def setUp(self):
        config.push('filter', """
        [mailman]
        check_max_size_on_filtered_message: yes
        """)
        self._mlist = create_list('test@example.com')
        self._mlist.max_message_size = 1
        self._msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: A Subject
Message-ID: <ant>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="AAAA"

--AAAA
Content-Type: text/plain

Small text plain part

--AAAA
Content-Type: application/octet-stream

This is a big part > 1K bytes
{}

--AAAA--
""".format(('x'*72 + '\n')*15))

    def test_no_filtering(self):
        # Test that unfiltered message is too big.
        self._mlist.filter_content = False
        rule = max_size.MaximumSize()
        msgdata = {}
        result = rule.check(self._mlist, self._msg, msgdata)
        self.assertTrue(result)

    def test_with_filtering(self):
        # Test that removing the big part passes the check.
        self._mlist.filter_content = True
        self._mlist.pass_types = ['multipart', 'text']
        rule = max_size.MaximumSize()
        msgdata = {}
        result = rule.check(self._mlist, self._msg, msgdata)
        self.assertFalse(result)

    def test_with_total_filtering(self):
        # Test that removing the whole message passes the check.
        self._mlist.filter_content = True
        self._mlist.pass_types = ['nothing']
        rule = max_size.MaximumSize()
        msgdata = {}
        result = rule.check(self._mlist, self._msg, msgdata)
        self.assertFalse(result)
