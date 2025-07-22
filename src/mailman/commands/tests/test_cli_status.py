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

"""Test the status command."""

import socket
import unittest

from click.testing import CliRunner
from mailman.bin.master import WatcherState
from mailman.commands.cli_status import status
from mailman.testing.layers import ConfigLayer
from unittest.mock import patch


class FakeLock:
    details = ('localhost', 9999, None)


class TestStatus(unittest.TestCase):
    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._command = CliRunner()

    def test_stale_lock(self):
        with patch('mailman.commands.cli_status.master_state',
                   return_value=(WatcherState.stale_lock, FakeLock())):
            results = self._command.invoke(status)
        self.assertEqual(results.exit_code,
                         WatcherState.stale_lock.value,
                         results.output)
        self.assertEqual(
            results.output,
            'GNU Mailman is stopped (stale pid: 9999)\n',
            results.output)

    def test_unknown_state(self):
        with patch('mailman.commands.cli_status.master_state',
                   return_value=(WatcherState.host_mismatch, FakeLock())):
            results = self._command.invoke(status)
        self.assertEqual(results.exit_code,
                         WatcherState.host_mismatch.value,
                         results.output)
        self.assertEqual(
            results.output,
            'GNU Mailman is in an unexpected state '
            '(localhost != {})\n'.format(socket.getfqdn()),
            results.output)
