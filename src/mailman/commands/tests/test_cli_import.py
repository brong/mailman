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

"""Test the `mailman import21` subcommand."""

import unittest

from click.testing import CliRunner
from contextlib import ExitStack
from importlib.resources import path
from mailman.app.lifecycle import create_list
from mailman.commands.cli_import import import21
from mailman.testing.layers import ConfigLayer
from mailman.utilities.importer import Import21Error
from pickle import dump
from tempfile import NamedTemporaryFile
from unittest.mock import patch


class TestImport(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._command = CliRunner()
        self.mlist = create_list('ant@example.com')

    @patch('mailman.commands.cli_import.import_config_pck')
    def test_process_pickle_with_bounce_info(self, import_config_pck):
        # The sample data contains Mailman 2 bounce info, represented as
        # _BounceInfo instances.  We throw these away when importing to
        # Mailman 3, but we have to fake the instance's classes, otherwise
        # unpickling the dictionaries will fail.
        with path('mailman.testing', 'config-with-instances.pck') as pckpath:
            pckfile = str(pckpath)
            try:
                self._command.invoke(import21, ('ant.example.com', pckfile))
            except ImportError as error:
                self.fail('The pickle failed loading: {}'.format(error))
        self.assertTrue(import_config_pck.called)

    def test_missing_list_spec(self):
        result = self._command.invoke(import21)
        self.assertEqual(result.exit_code, 2, result.output)
        self.assertEqual(
            result.output,
            'Usage: import21 [OPTIONS] LISTSPEC PICKLE_FILE\n'
            'Try \'import21 --help\' for help.\n\n'
            'Error: Missing argument \'LISTSPEC\'.\n')

    def test_pickle_with_nondict(self):
        with NamedTemporaryFile() as pckfile:
            with open(pckfile.name, 'wb') as fp:
                dump(['not', 'a', 'dict'], fp)
            result = self._command.invoke(
                import21, ('ant.example.com', pckfile.name))
            self.assertIn('Ignoring non-dictionary', result.output)

    def test_pickle_with_bad_language(self):
        with ExitStack() as resources:
            pckfile = str(resources.enter_context(
                path('mailman.testing', 'config.pck')))
            resources.enter_context(
                patch('mailman.utilities.importer.check_language_code',
                      side_effect=Import21Error('Fake bad language code')))
            result = self._command.invoke(
                import21, ('ant.example.com', pckfile))
            self.assertIn('Fake bad language code', result.output)

    def test_pickle_with_non_utf8_string(self):
        with path('mailman.testing', 'config-greek.pck') as pckpath:
            pckfile = str(pckpath)
            self._command.invoke(
                import21, ('--charset=iso-8859-7', 'ant.example.com', pckfile))
        self.assertEqual('Αυτή είναι μια λίστα.', self.mlist.info)
