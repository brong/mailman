# Copyright (C) 2016-2025 by the Free Software Foundation, Inc.
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

"""Test the withlist/shell command."""

import os
import unittest

from click.testing import CliRunner
from contextlib import ExitStack
from mailman.app.lifecycle import create_list
from mailman.commands.cli_withlist import shell
from mailman.config import config
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.helpers import configuration
from mailman.testing.layers import ConfigLayer
from mailman.utilities.modules import hacked_sys_modules
from types import ModuleType
from unittest.mock import MagicMock, patch


try:
    import readline  # noqa: F401
    has_readline = True
except ImportError:
    has_readline = False


class TestShell(unittest.TestCase):
    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._command = CliRunner()

    def test_namespace(self):
        with patch('mailman.commands.cli_withlist.start_python') as mock:
            self._command.invoke(shell, ('--interactive',))
            self.assertEqual(mock.call_count, 1)
            # Don't test that all names are available, just a few choice ones.
            positional, keywords = mock.call_args
            namespace = positional[0]
            self.assertIn('getUtility', namespace)
            self.assertIn('IArchiver', namespace)
            self.assertEqual(namespace['IUserManager'], IUserManager)

    @configuration('shell', banner='my banner')
    def test_banner(self):
        with patch('mailman.commands.cli_withlist.interact') as mock:
            self._command.invoke(shell, ('--interactive',))
            self.assertEqual(mock.call_count, 1)
            positional, keywords = mock.call_args
            self.assertEqual(keywords['banner'], 'my banner\n')

    @unittest.skipUnless(has_readline, 'readline module is not available')
    @configuration('shell', history_file='$var_dir/history.py')
    def test_history_file(self):
        with patch('mailman.commands.cli_withlist.interact'):
            self._command.invoke(shell, ('--interactive',))
            history_file = os.path.join(config.VAR_DIR, 'history.py')
            self.assertTrue(os.path.exists(history_file))

    @configuration('shell', use_ipython='yes')
    def test_start_ipython4(self):
        mock = MagicMock()
        with hacked_sys_modules('IPython.terminal.embed', mock):
            self._command.invoke(shell, ('--interactive',))
        posargs, kws = mock.InteractiveShellEmbed.instance.call_args
        self.assertEqual(
            kws['banner1'], """Welcome to the GNU Mailman shell
Use commit() to commit changes.
Use abort() to discard changes since the last commit.
Exit with ctrl+D does an implicit commit() but exit() does not.\n""")

    @configuration('shell', use_ipython='yes')
    def test_start_ipython1(self):
        mock = MagicMock()
        with hacked_sys_modules('IPython.frontend.terminal.embed', mock):
            self._command.invoke(shell, ('--interactive',))
        posargs, kws = mock.InteractiveShellEmbed.instance.call_args
        self.assertEqual(
            kws['banner1'], """Welcome to the GNU Mailman shell
Use commit() to commit changes.
Use abort() to discard changes since the last commit.
Exit with ctrl+D does an implicit commit() but exit() does not.\n""")

    @configuration('shell', use_ipython='debug')
    def test_start_ipython_debug(self):
        mock = MagicMock()
        with hacked_sys_modules('IPython.terminal.embed', mock):
            self._command.invoke(shell, ('--interactive',))
        posargs, kws = mock.InteractiveShellEmbed.instance.call_args
        self.assertEqual(
            kws['banner1'], """Welcome to the GNU Mailman shell
Use commit() to commit changes.
Use abort() to discard changes since the last commit.
Exit with ctrl+D does an implicit commit() but exit() does not.\n""")

    @configuration('shell', use_ipython='oops')
    def test_start_ipython_invalid(self):
        mock = MagicMock()
        with hacked_sys_modules('IPython.terminal.embed', mock):
            results = self._command.invoke(shell, ('--interactive',))
        self.assertEqual(
            results.output,
            'Invalid value for [shell]use_python: oops\n')
        # mainloop() never got called.
        self.assertIsNone(
            mock.InteractiveShellEmbed.instance().mainloop.call_args)

    @configuration('shell', use_ipython='yes')
    def test_start_ipython_uninstalled(self):
        with ExitStack() as resources:
            # Pretend iPython isn't available at all.
            resources.enter_context(patch(
                'mailman.commands.cli_withlist.start_ipython1',
                return_value=None))
            resources.enter_context(patch(
                'mailman.commands.cli_withlist.start_ipython4',
                return_value=None))
            results = self._command.invoke(shell, ('--interactive',))
        self.assertEqual(
            results.output,
            'ipython is not available, set use_ipython to no\n')

    def test_regex_without_run(self):
        results = self._command.invoke(shell, ('-l', '^.*example.com'))
        self.assertEqual(results.exit_code, 2)
        self.assertEqual(
            results.output,
            'Usage: shell [OPTIONS] [RUN_ARGS]...\n'
            'Try \'shell --help\' for help.\n\n'
            'Error: Regular expression requires --run\n')

    def test_listspec_without_run(self):
        create_list('ant@example.com')
        mock = MagicMock()
        with ExitStack() as resources:
            resources.enter_context(
                hacked_sys_modules('IPython.terminal.embed', mock))
            interactive_mock = resources.enter_context(patch(
                'mailman.commands.cli_withlist.do_interactive'))
            self._command.invoke(shell, ('-l', 'ant.example.com'))
        posargs, kws = interactive_mock.call_args
        self.assertEqual(
            posargs[1],
            'The variable \'m\' is the ant.example.com mailing list')

    def test_listspec_without_run_no_such_list(self):
        results = self._command.invoke(shell, ('-l', 'ant.example.com'))
        self.assertEqual(results.exit_code, 2)
        self.assertEqual(
            results.output,
            'Usage: shell [OPTIONS] [RUN_ARGS]...\n'
            'Try \'shell --help\' for help.\n\n'
            'Error: No such list: ant.example.com\n')

    def test_run_without_listspec(self):
        something = ModuleType('something')
        something.something = lambda: print('I am a something!')
        with hacked_sys_modules('something', something):
            results = self._command.invoke(shell, ('--run', 'something'))
        self.assertEqual(results.exit_code, 0)
        self.assertEqual(results.output, 'I am a something!\n')

    def test_run_bogus_listspec(self):
        results = self._command.invoke(
            shell, ('-l', 'bee.example.com', '--run', 'something'))
        self.assertEqual(results.exit_code, 2)
        self.assertEqual(
            results.output,
            'Usage: shell [OPTIONS] [RUN_ARGS]...\n'
            'Try \'shell --help\' for help.\n\n'
            'Error: No such list: bee.example.com\n')
