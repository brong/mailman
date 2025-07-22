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

"""Test the `administrivia` rule."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.rules import administrivia
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer


class TestAdministrivia(unittest.TestCase):
    """Test the administrivia rule."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')

    def test_administrivia_returns_reason(self):
        # Ensure administrivia rule returns a reason.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: unsubscribe
Message-ID: <ant>

A message body.
""")
        rule = administrivia.Administrivia()
        msgdata = {}
        result = rule.check(self._mlist, msg, msgdata)
        self.assertTrue(result)
        self.assertEqual(msgdata['moderation_reasons'],
                         ['Message contains administrivia'])

    def test_encoded_body(self):
        # Test that encoded body is decoded.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: An innocuous subject
Message-ID: <ant>
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: base64
MIMI-Version: 1.0

SSB3YW50IG9mZiB0aGlzIGxpc3QKdW5zdWJzY3JpYmUK
""")
        rule = administrivia.Administrivia()
        msgdata = {}
        result = rule.check(self._mlist, msg, msgdata)
        self.assertTrue(result)
