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

"""Test the `mailman members` command."""

import unittest

from click.testing import CliRunner
from mailman.app.lifecycle import create_list
from mailman.commands.cli_delmembers import delmembers
from mailman.testing.helpers import get_queue_messages, subscribe
from mailman.testing.layers import ConfigLayer
from tempfile import NamedTemporaryFile


class TestCLIDelMembers(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        # Default to no messages.
        self._mlist.send_welcome_message = False
        self._mlist.send_goodbye_message = False
        self._mlist.admin_notify_mchanges = False
        self._command = CliRunner()

    def test_no_such_list(self):
        result = self._command.invoke(delmembers, (
            '-f', '-', '-l', 'bee.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: delmembers [OPTIONS]\n'
            'Try \'delmembers --help\' for help.\n\n'
            'Error: No such list: bee.example.com\n')

    def test_bad_filename(self):
        result = self._command.invoke(delmembers, (
            '-f', 'bad', '-l', 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: delmembers [OPTIONS]\n'
            'Try \'delmembers --help\' for help.\n\n'
            'Error: Invalid value for \'--file\' / \'-f\': '
            '\'bad\': No such file or directory\n')

    def test_not_subscribed_without_display_name(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('aperson@example.com', file=infp)
            result = self._command.invoke(delmembers, (
                '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(
           result.output,
           'Member not subscribed (skipping): aperson@example.com\n'
           )

    def test_not_subscribed_with_display_name(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(delmembers, (
                '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(
           result.output,
           'Member not subscribed (skipping): '
           'Anne Person <aperson@example.com>\n'
           )

    def test_deletion_blank_lines(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        subscribe(self._mlist, 'Cate')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            print('', file=infp)
            print('   ', file=infp)
            print('\t', file=infp)
            print('Bart Person <bperson@example.com>', file=infp)
            result = self._command.invoke(delmembers, (
                '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(str(members[0].address),
                         'Cate Person <cperson@example.com>')

    def test_deletion_commented_lines(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            print('#Bart Person <bperson@example.com>', file=infp)
            result = self._command.invoke(delmembers, (
                '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(str(members[0].address),
                         'Bart Person <bperson@example.com>')

    def test_deletion_input_stdin(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        result = self._command.invoke(delmembers, (
            '-f', '-', '-l', 'ant.example.com'),
            input='Anne Person <aperson@example.com>\n')
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(str(members[0].address),
                         'Bart Person <bperson@example.com>')

    def test_deletion_all_members(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        result = self._command.invoke(delmembers, (
            '-a', '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)

    def test_deletion_from_all_lists(self):
        self._mlist2 = create_list('bee@example.com')
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        subscribe(self._mlist2, 'Bart')
        subscribe(self._mlist2, 'Cate')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Bart Person <bperson@example.com>', file=infp)
            result = self._command.invoke(delmembers, (
                '--fromall', '-f', infp.name))
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(str(members[0].address),
                         'Anne Person <aperson@example.com>')
        members2 = list(self._mlist2.members.members)
        self.assertEqual(len(members2), 1)
        self.assertEqual(str(members2[0].address),
                         'Cate Person <cperson@example.com>')

    def test_deletion_from_all_lists_not_member_of_all(self):
        self._mlist2 = create_list('bee@example.com')
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist2, 'Bart')
        subscribe(self._mlist2, 'Cate')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Bart Person <bperson@example.com>', file=infp)
            result = self._command.invoke(delmembers, (
                '--fromall', '-f', infp.name))
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(str(members[0].address),
                         'Anne Person <aperson@example.com>')
        members2 = list(self._mlist2.members.members)
        self.assertEqual(len(members2), 1)
        self.assertEqual(str(members2[0].address),
                         'Cate Person <cperson@example.com>')

    def test_deletion_dash_member(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        result = self._command.invoke(delmembers, (
            '-m', 'aperson@example.com', '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(str(members[0].address),
                         'Bart Person <bperson@example.com>')

    def test_deletion_multiple_dash_member(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        result = self._command.invoke(delmembers, (
            '-m', 'aperson@example.com', '-l', 'ant.example.com',
            '-m', 'bperson@example.com'))
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)

    def test_deletion_file_and_dash_member(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Bart Person <bperson@example.com>', file=infp)
            result = self._command.invoke(delmembers, (
                '-m', 'aperson@example.com', '-l', 'ant.example.com',
                '-f', infp.name))
        self.assertEqual(result.output, '')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)

    def test_invalid_fromall_list(self):
        result = self._command.invoke(delmembers, (
            '-f', '-', '--fromall', '-l', 'bee.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: delmembers [OPTIONS]\n'
            'Try \'delmembers --help\' for help.\n\n'
            'Error: --fromall may not be specified with -l/--list, '
            'or -a/--all\n')

    def test_invalid_fromall_all(self):
        result = self._command.invoke(delmembers, (
            '--fromall', '-a'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: delmembers [OPTIONS]\n'
            'Try \'delmembers --help\' for help.\n\n'
            'Error: --fromall may not be specified with -l/--list, '
            'or -a/--all\n')

    def test_invalid_file_all(self):
        result = self._command.invoke(delmembers, (
            '-f', '-', '-a'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: delmembers [OPTIONS]\n'
            'Try \'delmembers --help\' for help.\n\n'
            'Error: -a/--all must not be specified with '
            '-f/--file or -m/--member.\n')

    def test_invalid_member_all(self):
        result = self._command.invoke(delmembers, (
            '-m', 'aperson@example.com', '-a'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: delmembers [OPTIONS]\n'
            'Try \'delmembers --help\' for help.\n\n'
            'Error: -a/--all must not be specified with '
            '-f/--file or -m/--member.\n')

    def test_invalid_not_fromall_not_list(self):
        result = self._command.invoke(delmembers, (
            '-f', '-'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: delmembers [OPTIONS]\n'
            'Try \'delmembers --help\' for help.\n\n'
            'Error: Without --fromall, -l/--list is required.\n')

    def test_invalid_no_all_file_or_member(self):
        result = self._command.invoke(delmembers, (
            '-l', 'ant@example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: delmembers [OPTIONS]\n'
            'Try \'delmembers --help\' for help.\n\n'
            'Error: At least one of -a/--all, -f/--file or -m/--member '
            'is required.\n')

    def test_override_no_goodbye(self):
        self._mlist.send_goodbye_message = False
        subscribe(self._mlist, 'Anne')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('aperson@example.com', file=infp)
            result = self._command.invoke(delmembers, (
                '-g', '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('You have been unsubscribed',
                      str(items[0].msg['subject']))

    def test_override_yes_goodbye(self):
        self._mlist.send_goodbye_message = True
        subscribe(self._mlist, 'Anne')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('aperson@example.com', file=infp)
            result = self._command.invoke(delmembers, (
                '-G', '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        get_queue_messages('virgin', expected_count=0)

    def test_no_override_goodbye(self):
        self._mlist.send_goodbye_message = True
        subscribe(self._mlist, 'Anne')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('aperson@example.com', file=infp)
            result = self._command.invoke(delmembers, (
                '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('You have been unsubscribed',
                      str(items[0].msg['subject']))

    def test_override_no_admin_notify(self):
        subscribe(self._mlist, 'Anne')
        self._mlist.admin_notify_mchanges = False
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('aperson@example.com', file=infp)
            result = self._command.invoke(delmembers, (
                '-n', '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('Ant unsubscription notification',
                      str(items[0].msg['subject']))
        self.assertIn('Anne Person <aperson@example.com> has been removed',
                      str(items[0].msg))

    def test_override_yes_admin_notify(self):
        subscribe(self._mlist, 'Anne')
        self._mlist.admin_notify_mchanges = True
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('aperson@example.com', file=infp)
            result = self._command.invoke(delmembers, (
                '-N', '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        get_queue_messages('virgin', expected_count=0)

    def test_no_override_admin_notify(self):
        subscribe(self._mlist, 'Anne')
        self._mlist.admin_notify_mchanges = True
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('aperson@example.com', file=infp)
            result = self._command.invoke(delmembers, (
                '-f', infp.name, '-l', 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('Ant unsubscription notification',
                      str(items[0].msg['subject']))
        self.assertIn('Anne Person <aperson@example.com> has been removed',
                      str(items[0].msg))
