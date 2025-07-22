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

"""Test the `unshunt` command."""

import unittest

from click.testing import CliRunner
from mailman.commands.cli_unshunt import unshunt
from mailman.config import config
from mailman.email.message import Message
from mailman.testing.layers import ConfigLayer
from unittest.mock import patch


class TestUnshunt(unittest.TestCase):
    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._command = CliRunner()
        self._queue = config.switchboards['shunt']

    def test_dequeue_fails(self):
        filebase = self._queue.enqueue(Message(), {})
        with patch.object(self._queue, 'dequeue',
                          side_effect=RuntimeError('oops!')):
            results = self._command.invoke(unshunt)
        self.assertEqual(
            results.output,
            'Cannot unshunt message {}, skipping:\noops!\n'.format(filebase))
