# Copyright (C) 2014-2023 by the Free Software Foundation, Inc.
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

"""Test the check_dmarc handler.

We only need one test to ensure the handler invokes the rule.  The rule is
thoroughly tested elsewhere."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.handlers.check_dmarc import checkDMARC
from mailman.interfaces.mailinglist import DMARCMitigateAction
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer


class TestDMARC(unittest.TestCase):
    """Test the check_dmarc handler."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._msg = mfs("""\
From: anne@gmail.com
To: test-owner@example.com
Subject: A disposable message
Message-ID: <ant>

""")
        self._msgdata = {}
        self._handler = checkDMARC()
        self._mlist.dmarc_mitigate_action = DMARCMitigateAction.munge_from
        self._mlist.dmarc_mitigate_unconditionally = False
        self._mlist.dmarc_addresses = [r'^.*@gmail\.com']

    def test_dmarc(self):
        self._handler.process(self._mlist, self._msg, self._msgdata)
        self.assertTrue(self._msgdata['dmarc'])
