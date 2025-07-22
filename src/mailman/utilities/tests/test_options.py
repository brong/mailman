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

"""Test option utilities."""

import click
import unittest

from mailman.utilities.options import validate_runner_spec


class TestValidateRunnerSpec(unittest.TestCase):
    def test_false_value(self):
        self.assertIsNone(validate_runner_spec(None, None, None))

    def test_runner_only(self):
        specs = validate_runner_spec(None, None, 'incoming')
        self.assertEqual(specs, ('incoming', 1, 1))

    def test_full_runner_spec(self):
        specs = validate_runner_spec(None, None, 'incoming:2:4')
        self.assertEqual(specs, ('incoming', 2, 4))

    def test_bad_runner_spec(self):
        with self.assertRaises(click.BadParameter) as cm:
            validate_runner_spec(None, None, 'incoming:not:int')
        self.assertEqual(
            cm.exception.message,
            'slice and range must be integers: incoming:not:int')

    def test_bad_runner_spec_parts(self):
        with self.assertRaises(click.UsageError) as cm:
            validate_runner_spec(None, None, 'incoming:2')
        self.assertEqual(
            cm.exception.message,
            'Bad runner spec: incoming:2')
