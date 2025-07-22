# Copyright (C) 2012-2025 by the Free Software Foundation, Inc.
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

"""Test the `request` address."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.database.transaction import transaction
from mailman.interfaces.autorespond import ResponseAction
from mailman.runners.command import CommandRunner
from mailman.testing.helpers import (
    get_queue_messages,
    make_testable_runner,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer


class TestRequest(unittest.TestCase):
    """Test messages to -request."""

    layer = ConfigLayer

    def setUp(self):
        self._commandq = config.switchboards['command']
        self._runner = make_testable_runner(CommandRunner, 'command')
        with transaction():
            # Register a subscription requiring confirmation.
            self._mlist = create_list('test@example.com')

    def test_respond_and_continue(self):
        msg = mfs("""\
From: anne@example.org
To: test-request@example.com
Subject: help

""")
        self._mlist.autorespond_requests = ResponseAction.respond_and_continue
        self._mlist.autoresponse_request_text = 'Autoresponse'
        self._commandq.enqueue(msg, dict(listid='test.example.com',
                                         to_request=True))
        self._runner.run()
        items = get_queue_messages('virgin', expected_count=2)
        self.assertEqual(items[0].msg.get_payload(), 'Autoresponse')
        self.assertIn('results of your email command',
                      items[1].msg.get_payload())

    def test_respond_and_discard(self):
        msg = mfs("""\
From: anne@example.org
To: test-request@example.com
Subject: help

""")
        self._mlist.autorespond_requests = ResponseAction.respond_and_discard
        self._mlist.autoresponse_request_text = 'Autoresponse'
        self._commandq.enqueue(msg, dict(listid='test.example.com',
                                         to_request=True))
        self._runner.run()
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(items[0].msg.get_payload(), 'Autoresponse')
