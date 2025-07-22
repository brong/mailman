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

"""Test the `emergency` rule."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.rules import emergency
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer


class TestEmergency(unittest.TestCase):
    """Test the emergency rule."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')

    def test_emergency_returns_reason(self):
        # Ensure emergency rule returns a reason.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: A Subject
Message-ID: <ant>

A message body.
""")
        rule = emergency.Emergency()
        self._mlist.emergency = True
        msgdata = {}
        result = rule.check(self._mlist, msg, msgdata)
        self.assertTrue(result)
        self.assertEqual(msgdata['moderation_reasons'],
                         ['Emergency moderation is in effect for this list'])
