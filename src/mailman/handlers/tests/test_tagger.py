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

"""Test the tagger handler."""

import unittest

from email.header import make_header
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer


class TestTagger(unittest.TestCase):
    """Test the tagger handler."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.topics = [('topic_1', 'magic word', 'Test topic', False)]
        self._mlist.topics_enabled = True
        self._mlist.topics_bodylines_limit = 5
        self._msgdata = {}
        self._msg = mfs("""\
Received: from somewhere
To: <ant@example.com>
From: <anne@example.com>
Message-ID: <1234@example.com>

body
""")

    def test_no_hit(self):
        # No hit returns no hits.
        config.handlers['tagger'].process(self._mlist,
                                          self._msg,
                                          self._msgdata)
        self.assertIsNone(self._msg.get('x-topics', None))
        self.assertFalse('topichits' in self._msgdata)

    def test_hit_subject(self):
        # A hit on the Subject: is reported.
        self._msg['Subject'] = 'This contains the magic word'
        config.handlers['tagger'].process(self._mlist,
                                          self._msg,
                                          self._msgdata)
        self.assertEqual('topic_1', self._msg.get('x-topics'))
        self.assertEqual(['topic_1'], self._msgdata['topichits'])

    def test_hit_body(self):
        # A hit on Keywords: in the body is reported.
        self._msg.set_payload("""\
Keywords: magic word
Some blah ...
etc.
""")
        config.handlers['tagger'].process(self._mlist,
                                          self._msg,
                                          self._msgdata)
        self.assertEqual('topic_1', self._msg.get('x-topics'))
        self.assertEqual(['topic_1'], self._msgdata['topichits'])

    def test_no_hit_body_not_searched(self):
        # The body doesn't hit if not searched.
        self._msg.set_payload("""\
Keywords: magic word
Some blah ...
etc.
""")
        self._mlist.topics_bodylines_limit = 0
        config.handlers['tagger'].process(self._mlist,
                                          self._msg,
                                          self._msgdata)
        self.assertIsNone(self._msg.get('x-topics', None))
        self.assertFalse('topichits' in self._msgdata)

    def test_hit_header_instance(self):
        # A hit on a Header instance is reported.
        self._msg['Subject'] = make_header([('This contains the magic word',
                                             'utf-8')])
        config.handlers['tagger'].process(self._mlist,
                                          self._msg,
                                          self._msgdata)
        self.assertEqual('topic_1', self._msg.get('x-topics'))
        self.assertEqual(['topic_1'], self._msgdata['topichits'])
