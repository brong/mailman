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

"""Test mailman command utilities."""

import unittest

from click.testing import CliRunner
from datetime import timedelta
from importlib.resources import path
from mailman.app.lifecycle import create_list
from mailman.bin.mailman import main
from mailman.config import config
from mailman.database.transaction import transaction
from mailman.interfaces.command import ICLISubCommand
from mailman.testing.layers import ConfigLayer
from mailman.utilities.datetime import now
from mailman.utilities.modules import add_components
from unittest.mock import patch


def mock_euid():
    return 0


class TestMailmanCommand(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._command = CliRunner()

    def test_mailman_command_config(self):
        with path('mailman.testing', 'testing.cfg') as config_path:
            with patch('mailman.bin.mailman.initialize') as init:
                self._command.invoke(main, ('-C', str(config_path), 'info'))
            init.assert_called_once_with(str(config_path))

    def test_mailman_command_no_config(self):
        with patch('mailman.bin.mailman.initialize') as init:
            self._command.invoke(main, ('info',))
        init.assert_called_once_with(None)

    @patch('mailman.bin.mailman.os')
    @patch('mailman.bin.mailman.initialize')
    def test_mailman_command_without_subcommand_prints_help(
            self, mock_os, mock):
        mock_os.geteuid.return_value = 1000
        # Issue #137: Running `mailman` without a subcommand raises an
        # AttributeError.
        result = self._command.invoke(main, [])
        lines = result.output.splitlines()
        # "main" instead of "mailman" because of the way the click runner
        # works.  It does actually show the correct program when run from the
        # command line.
        self.assertEqual(lines[0], 'Usage: main [OPTIONS] COMMAND [ARGS]...')
        # The help output includes a list of subcommands, in sorted order.
        commands = {}
        add_components('commands', ICLISubCommand, commands)
        help_commands = list(
            line.split()[0].strip()
            for line in lines[-len(commands):]
            )
        self.assertEqual(sorted(commands), help_commands)

    @patch('mailman.bin.mailman.initialize')
    def test_mailman_command_with_bad_subcommand_prints_help(self, mock):
        # Issue #137: Running `mailman` without a subcommand raises an
        # AttributeError.
        result = self._command.invoke(main, ('not-a-subcommand',))
        lines = result.output.splitlines()
        # "main" instead of "mailman" because of the way the click runner
        # works.  It does actually show the correct program when run from the
        # command line.
        self.assertEqual(lines[0], 'Usage: main [OPTIONS] COMMAND [ARGS]...')

    @patch('mailman.bin.mailman.os')
    @patch('mailman.bin.mailman.initialize')
    def test_transaction_commit_after_successful_subcommand(
            self, mock_os, mock):
        mock_os.geteuid.return_value = 0
        # Issue #223: Subcommands which change the database need to commit or
        # abort the transaction.
        with transaction():
            mlist = create_list('ant@example.com')
            mlist.volume = 5
            mlist.next_digest_number = 3
            mlist.digest_last_sent_at = now() - timedelta(days=60)
        self._command.invoke(main, ('digests', '-b', '-l', 'ant@example.com'))
        # Clear the current transaction to force a database reload.
        config.db.abort()
        self.assertEqual(mlist.volume, 6)
        self.assertEqual(mlist.next_digest_number, 1)

    @patch('mailman.bin.mailman.initialize')
    @patch('mailman.commands.cli_digests.maybe_send_digest_now',
           side_effect=RuntimeError)
    def test_transaction_abort_after_failing_subcommand(self, mock1, mock2):
        with transaction():
            mlist = create_list('ant@example.com')
            mlist.volume = 5
            mlist.next_digest_number = 3
            mlist.digest_last_sent_at = now() - timedelta(days=60)
        self._command.invoke(
            main, ('digests', '-b', '-l', 'ant@example.com', '--send'))
        # Clear the current transaction to force a database reload.
        config.db.abort()
        # The volume and number haven't changed.
        self.assertEqual(mlist.volume, 5)
        self.assertEqual(mlist.next_digest_number, 3)

    @patch('mailman.bin.mailman.initialize')
    @patch('os.geteuid', mock_euid)
    def test_wont_run_as_root(self, mock):
        result = self._command.invoke(main)
        self.assertIn(
            'Error: If you are sure you want to run as root, '
            'specify --run-as-root.',
            result.output)
        self.assertNotEqual(result.exit_code, 0)

    @patch('mailman.bin.mailman.initialize')
    @patch('os.geteuid', mock_euid)
    def test_will_run_as_root_with_option(self, mock):
        result = self._command.invoke(main, ('--run-as-root'))
        self.assertNotIn('Error:', result.output)
        self.assertEqual(result.exit_code, 0)
