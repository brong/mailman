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
        self._shunt_q = config.switchboards['shunt']
        self._in_q = config.switchboards['in']

    def test_unshunt_all(self):
        filebase1 = self._shunt_q.enqueue(Message(), {})
        filebase2 = self._shunt_q.enqueue(Message(), {})
        self.assertEqual(len(self._shunt_q.files), 2)
        self.assertEqual(len(self._in_q.files), 0)
        result = self._command.invoke(unshunt, ('--verbose'))
        self.assertEqual(
            result.output,
            f'Unshunting to in queue: {filebase1}\n'
            f'Unshunting to in queue: {filebase2}\n'
        )
        self.assertEqual(len(self._shunt_q.files), 0)
        self.assertEqual(len(self._in_q.files), 2)

    def test_unshunt_specified_filebase(self):
        filebase1 = self._shunt_q.enqueue(Message(), {})
        filebase2 = self._shunt_q.enqueue(Message(), {})
        filebase3 = self._shunt_q.enqueue(Message(), {})
        self.assertEqual(len(self._shunt_q.files), 3)
        self.assertEqual(len(self._in_q.files), 0)
        result = self._command.invoke(unshunt, ('--verbose', filebase3))
        self.assertEqual(
            result.output,
            f'Unshunting to in queue: {filebase3}\n'
        )
        self.assertEqual(len(self._shunt_q.files), 2)
        self.assertEqual(len(self._in_q.files), 1)
        result = self._command.invoke(
            unshunt,
            ('--verbose', filebase1, filebase2)
        )
        self.assertEqual(
            result.output,
            f'Unshunting to in queue: {filebase1}\n'
            f'Unshunting to in queue: {filebase2}\n'
        )
        self.assertEqual(len(self._shunt_q.files), 0)
        self.assertEqual(len(self._in_q.files), 3)

    def test_dequeue_fails(self):
        filebase = self._shunt_q.enqueue(Message(), {})
        self.assertEqual(len(self._shunt_q.files), 1)
        self.assertEqual(len(self._in_q.files), 0)
        with patch.object(self._shunt_q, 'dequeue',
                          side_effect=RuntimeError('oops!')):
            result = self._command.invoke(unshunt)
        self.assertEqual(
            result.output,
            f'Cannot unshunt message {filebase}, skipping: oops!\n'
        )
        self.assertEqual(len(self._shunt_q.files), 1)
        self.assertEqual(len(self._in_q.files), 0)

    def test_discard(self):
        filebase = self._shunt_q.enqueue(Message(), {})
        self.assertEqual(len(self._shunt_q.files), 1)
        self.assertEqual(len(self._in_q.files), 0)
        result = self._command.invoke(unshunt, ('--verbose', '--discard'))
        self.assertEqual(
            result.output,
            f'Discarding: {filebase}\n'
        )
        self.assertEqual(len(self._shunt_q.files), 0)
        self.assertEqual(len(self._in_q.files), 0)
