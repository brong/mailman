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

"""Test the `digests` rule."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.rules import digests
from mailman.testing.helpers import (
    configuration,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer


class TestDigestsRule(unittest.TestCase):
    """Test the max_size rule."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')

    @configuration('mailman', hold_digest='yes')
    def test_digest_subject_reason(self):
        # Ensure digests rule returns a reason for subject hit.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: Re: test Digest, Vol 1, Issue 1
Message-ID: <ant>

A message body.
""")
        msgdata = {}
        rule = digests.Digests()
        result = rule.check(self._mlist, msg, msgdata)
        self.assertTrue(result)
        self.assertEqual(msgdata['moderation_reasons'],
                         ['Message has a digest subject'])

    @configuration('mailman', hold_digest='yes')
    def test_digest_masthead_reason(self):
        # Ensure digests rule returns a reason for masthead hit.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: Message Subject
Message-ID: <ant>

Send Test mailing list submissions to
        test@example.com

To subscribe or unsubscribe via email, send a message with subject or body
'help' to
        test-request@example.com

You can reach the person managing the list at
        test-owner@example.com

When replying, please edit your Subject line so it is more specific than
"Re: Contents of $display_name digest..."
""")
        msgdata = {}
        rule = digests.Digests()
        result = rule.check(self._mlist, msg, msgdata)
        self.assertTrue(result)
        self.assertEqual(msgdata['moderation_reasons'],
                         ['Message quotes digest boilerplate'])

    @configuration('mailman', hold_digest='yes')
    def test_miss_on_ok_message(self):
        # Rule should miss if not digest subject or masthead.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: Message Subject
Message-ID: <ant>

A message body.
""")
        msgdata = {}
        rule = digests.Digests()
        result = rule.check(self._mlist, msg, msgdata)
        self.assertFalse(result)

    @configuration('mailman', hold_digest='no')
    def test_no_hit_if_not_configured(self):
        # Ensure rule misses if not configured.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: Re: test Digest, Vol 1, Issue 1
Message-ID: <ant>

Send Test mailing list submissions to
        test@example.com

To subscribe or unsubscribe via email, send a message with subject or body
'help' to
        test-request@example.com

You can reach the person managing the list at
        test-owner@example.com

When replying, please edit your Subject line so it is more specific than
"Re: Contents of Test digest..."
""")
        msgdata = {}
        rule = digests.Digests()
        result = rule.check(self._mlist, msg, msgdata)
        self.assertFalse(result)
