# Copyright (C) 2013-2025 by the Free Software Foundation, Inc.
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

"""Test the conf subcommand."""

import unittest

from click.testing import CliRunner
from mailman.commands.cli_conf import conf
from mailman.config import config
from mailman.testing.layers import ConfigLayer
from tempfile import NamedTemporaryFile


class TestConf(unittest.TestCase):
    """Test the conf subcommand."""

    layer = ConfigLayer

    def setUp(self):
        self._command = CliRunner()

    def test_cannot_access_nonexistent_section(self):
        result = self._command.invoke(conf, ('-s', 'thissectiondoesnotexist'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: conf [OPTIONS]\n'
            'Try \'conf --help\' for help.\n\n'
            'Error: No such section: thissectiondoesnotexist\n')

    def test_cannot_access_nonexistent_section_and_key(self):
        result = self._command.invoke(
            conf, ('-s', 'thissectiondoesnotexist', '-k', 'nosuchkey'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: conf [OPTIONS]\n'
            'Try \'conf --help\' for help.\n\n'
            'Error: No such section: thissectiondoesnotexist\n')

    def test_cannot_access_nonexistent_key(self):
        result = self._command.invoke(
            conf, ('-s', 'mailman', '-k', 'thiskeydoesnotexist'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: conf [OPTIONS]\n'
            'Try \'conf --help\' for help.\n\n'
            'Error: Section mailman: No such key: thiskeydoesnotexist\n')

    def test_pushed_section_is_found(self):
        config.push('test config', """\
[archiver.other]
enable: yes
""")
        result = self._command.invoke(conf, ('-k', 'enable'))
        self.assertIn('[archiver.other] enable: yes', result.output)
        config.pop('test config')

    def test_output_to_explicit_stdout(self):
        result = self._command.invoke(
            conf, ('-o', '-', '-s', 'shell', '-k', 'use_ipython'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(result.output, 'no\n')

    def test_output_to_file(self):
        with NamedTemporaryFile() as outfp:
            result = self._command.invoke(
                conf, ('-o', outfp.name, '-s', 'shell', '-k', 'use_ipython'))
            self.assertEqual(result.exit_code, 0)
            with open(outfp.name, 'r', encoding='utf-8') as infp:
                self.assertEqual(infp.read(), 'no\n')
