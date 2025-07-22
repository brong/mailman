# Copyright (C) 2017-2025 by the Free Software Foundation, Inc.
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

"""Test the `inject` command."""

import unittest

from click.testing import CliRunner
from io import BytesIO, StringIO
from mailman.app.lifecycle import create_list
from mailman.commands.cli_inject import inject
from mailman.testing.helpers import get_queue_messages
from mailman.testing.layers import ConfigLayer


test_msg = b"""\
To: ant@example.com
From: user@example.com
Message-ID: <some_id@example.com>

body
"""


class InterruptRaisingReader(StringIO):
    def read(self, count=None):
        # Fake enough of the API so click returns this instance unchanged.
        if count is None:
            raise KeyboardInterrupt
        return b''


class TestInject(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._command = CliRunner()
        create_list('ant@example.com')

    def test_inject_keyboard_interrupt(self):
        results = self._command.invoke(
            inject, ('-f', '-', 'ant.example.com'),
            input=InterruptRaisingReader())
        self.assertEqual(results.exit_code, 1)
        self.assertEqual(results.output, 'Interrupted\n')

    def test_inject_no_such_list(self):
        result = self._command.invoke(inject, ('bee.example.com',))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: inject [OPTIONS] LISTSPEC\n'
            'Try \'inject --help\' for help.\n\n'
            'Error: No such list: bee.example.com\n')

    def test_inject_no_such_queue(self):
        result = self._command.invoke(
            inject, ('--queue', 'bogus', 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: inject [OPTIONS] LISTSPEC\n'
            'Try \'inject --help\' for help.\n\n'
            'Error: No such queue: bogus\n')

    def test_inject_no_filename_option(self):
        result = self._command.invoke(
            inject, (('ant.example.com',)),
            input=BytesIO(test_msg))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, '')
        msg = get_queue_messages('in', expected_count=1)[0].msg
        # We can't compare the entire message because of inserted headers.
        self.assertEqual(msg.as_bytes()[:75], test_msg[:75])
