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

"""Test the `news_moderation` rule."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.interfaces.nntp import NewsgroupModeration
from mailman.rules import news_moderation
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer


class TestModeratedNewsgroup(unittest.TestCase):
    """Test the news_moderation rule."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')

    def test_news_moderation_returns_reason(self):
        # Ensure news_moderation rule returns a reason.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: A Subject
Message-ID: <ant>

A message body.
""")
        rule = news_moderation.ModeratedNewsgroup()
        self._mlist.newsgroup_moderation = NewsgroupModeration.moderated
        msgdata = {}
        result = rule.check(self._mlist, msg, msgdata)
        self.assertTrue(result)
        self.assertEqual(msgdata['moderation_reasons'],
                         ['Post to a moderated newsgroup gateway'])
