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

"""Test the discard chain."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.core.chains import process as process_chain
from mailman.testing.helpers import (
    LogFileMark,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer


class TestDiscard(unittest.TestCase):
    """Test the discard chain."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._msg = mfs("""\
From: anne@example.com
To: test@example.com
Subject: Ignore
Message-Id: <mid@example.com>

""")

    def test_discard_reasons(self):
        # The log message must contain the moderation reasons.
        msgdata = dict(moderation_reasons=['TEST-REASON-1', 'TEST-REASON-2'])
        log_file = LogFileMark('mailman.vette')
        process_chain(self._mlist, self._msg, msgdata, start_chain='discard')
        log_entry = log_file.read()
        self.assertIn('DISCARD: <mid@example.com>', log_entry)
        self.assertIn('TEST-REASON-1', log_entry)
        self.assertIn('TEST-REASON-2', log_entry)

    def test_discard_no_reasons(self):
        # The log message contains n/a if no moderation reasons.
        msgdata = {}
        log_file = LogFileMark('mailman.vette')
        process_chain(self._mlist, self._msg, msgdata, start_chain='discard')
        log_entry = log_file.read()
        self.assertIn('DISCARD: <mid@example.com>', log_entry)
        self.assertIn('[n/a]', log_entry)
