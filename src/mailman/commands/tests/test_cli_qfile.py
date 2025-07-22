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

"""Test the qfile command."""

import unittest

from click.testing import CliRunner
from contextlib import ExitStack
from mailman.commands.cli_qfile import qfile
from mailman.testing.layers import ConfigLayer
from pickle import dump
from tempfile import NamedTemporaryFile
from unittest.mock import patch


class TestUnshunt(unittest.TestCase):
    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._command = CliRunner()

    def test_print_str(self):
        with NamedTemporaryFile() as tmp_qfile:
            with open(tmp_qfile.name, 'wb') as fp:
                dump('a simple string', fp)
            results = self._command.invoke(qfile, (tmp_qfile.name,))
            self.assertEqual(results.output, """\
[----- start pickle -----]
<----- start object 1 ----->
a simple string
[----- end pickle -----]
""", results.output)

    def test_interactive(self):
        with ExitStack() as resources:
            tmp_qfile = resources.enter_context(NamedTemporaryFile())
            mock = resources.enter_context(patch(
                'mailman.commands.cli_qfile.interact'))
            with open(tmp_qfile.name, 'wb') as fp:
                dump('a simple string', fp)
            self._command.invoke(qfile, (tmp_qfile.name, '-i'))
            mock.assert_called_once_with(
                banner="Number of objects found (see the variable 'm'): 1")
