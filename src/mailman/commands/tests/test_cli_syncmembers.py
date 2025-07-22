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
from mailman.commands.cli_syncmembers import syncmembers
from mailman.interfaces.bans import IBanManager
from mailman.interfaces.mailinglist import SubscriptionPolicy
from mailman.interfaces.member import DeliveryMode, DeliveryStatus, MemberRole
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.helpers import get_queue_messages, subscribe
from mailman.testing.layers import ConfigLayer
from tempfile import NamedTemporaryFile
from zope.component import getUtility


class TestCLISyncMembers(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        # Default to no messages.
        self._mlist.send_welcome_message = False
        self._mlist.send_goodbye_message = False
        self._mlist.admin_notify_mchanges = False
        self._command = CliRunner()

    def test_no_such_list(self):
        result = self._command.invoke(syncmembers, ('-', 'bee.example.com',))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: syncmembers [OPTIONS] FILENAME LISTSPEC\n'
            'Try \'syncmembers --help\' for help.\n\n'
            'Error: No such list: bee.example.com\n')

    def test_bad_filename(self):
        result = self._command.invoke(syncmembers, ('bad', 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: syncmembers [OPTIONS] FILENAME LISTSPEC\n'
            'Try \'syncmembers --help\' for help.\n\n'
            'Error: Invalid value for \'FILENAME\': '
            '\'bad\': No such file or directory\n')

    def test_sync_invalid_email(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Dont Subscribe <not-a-valid-email>', file=infp)
            print('not-a-valid@email', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           'Cannot parse as valid email address'
           ' (skipping): Dont Subscribe <not-a-valid-email>\n'
           'Cannot parse as valid email address'
           ' (skipping): not-a-valid@email\n'
           'Nothing to do\n')

    def test_sync_no_change(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '--no-change', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '[DEL] Bart Person'
                                        ' <bperson@example.com>\n')
        self.assertEqual(len(list(self._mlist.members.members)), 2)

    def test_sync_empty_tuple(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            print('""', file=infp)
            result = self._command.invoke(syncmembers, (
                '--no-change', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, "Cannot parse as valid email "
                                        "address (skipping): \"\"\n"
                                        "[DEL] Bart Person "
                                        "<bperson@example.com>\n")
        self.assertEqual(len(list(self._mlist.members.members)), 2)

    def test_banned_address(self):
        IBanManager(self._mlist).ban('aperson@example.com')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           '[ADD] Anne Person <aperson@example.com>\n'
           'Membership is banned (skipping): '
           'Anne Person <aperson@example.com>\n'
           )
        self.assertEqual(len(list(self._mlist.members.members)), 0)

    def test_subscription_pending(self):
        # Pend a subscription for Anne.
        self._mlist.subscription_policy = SubscriptionPolicy.confirm
        address = getUtility(IUserManager).create_address(
            'aperson@example.com', 'Anne Person')
        ISubscriptionManager(self._mlist).register(address)
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            print('Bart Person <bperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person'
                         ' <aperson@example.com>\n'
                         'Subscription already pending (skipping): '
                         'Anne Person <aperson@example.com>\n'
                         '[ADD] Bart Person'
                         ' <bperson@example.com>\n')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'bperson@example.com')

    def test_sync_commented_lines(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            print('#Bart Person <bperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '[DEL] Bart Person'
                                        ' <bperson@example.com>\n')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')

    def test_sync_blank_lines(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            print('', file=infp)
            print('', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '[DEL] Bart Person'
                                        ' <bperson@example.com>\n')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')

    def test_sync_nothing_to_do(self):
        subscribe(self._mlist, 'Anne')
        subscribe(self._mlist, 'Bart')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            print('Bart Person <bperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output, "Nothing to do\n")
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 2)
        addresses = [member.address.original_email for member in members]
        self.assertIn('bperson@example.com', addresses)
        self.assertIn('aperson@example.com', addresses)

    def test_sync_no_display_name(self):
        subscribe(self._mlist, 'Bart')
        subscribe(self._mlist, 'Cate', role=MemberRole.nonmember)
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('<aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           '[ADD] aperson@example.com\n'
           '[DEL] Bart Person <bperson@example.com>\n')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')

    def test_sync_del_no_display_name(self):
        subscribe(self._mlist, 'Bart')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('<aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           '[ADD] aperson@example.com\n'
           '[DEL] Bart Person <bperson@example.com>\n')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')

    def test_sync_del_upper_case_email(self):
        subscribe(self._mlist, 'Bart', email='BART@example.com')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('<aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           '[ADD] aperson@example.com\n'
           '[DEL] Bart Person <BART@example.com>\n')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')

    def test_sync_no_del_upper_case_email(self):
        subscribe(self._mlist, 'Bart', email='BART@example.com')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('<aperson@example.com>', file=infp)
            print('bart@example.com', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           '[ADD] aperson@example.com\n')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 2)
        addresses = [member.address.original_email for member in members]
        self.assertIn('BART@example.com', addresses)
        self.assertIn('aperson@example.com', addresses)

    def test_sync_add_upper_case_email(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <ANNE@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           '[ADD] Anne Person <ANNE@example.com>\n')
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual('ANNE@example.com',
                         members[0].address.original_email)

    def test_sync_delivery_default(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_sync_input_stdin(self):
        result = self._command.invoke(syncmembers, (
            '-', 'ant.example.com'),
            input='Anne Person <aperson@example.com>\n')
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_sync_add_existing_address(self):
        getUtility(IUserManager).create_address('aperson@example.com',
                                                'A Person')
        result = self._command.invoke(syncmembers, (
            '-', 'ant.example.com'),
            input='Anne Person <aperson@example.com>\n')
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'A Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_sync_delivery_regular(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '-d', 'regular', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_sync_delivery_disabled(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '-d', 'disabled', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status,
                         DeliveryStatus.by_moderator)

    def test_sync_delivery_mime(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '-d', 'mime', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.mime_digests)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_sync_delivery_plain(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '-d', 'plain', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode,
                         DeliveryMode.plaintext_digests)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_sync_delivery_summary(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '-d', 'summary', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode,
                         DeliveryMode.summary_digests)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_sync_delivery_bogus(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '-d', 'bogus', infp.name, 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        self.assertEqual(
            result.output,
            'Usage: syncmembers [OPTIONS] FILENAME LISTSPEC\n'
            'Try \'syncmembers --help\' for help.\n\n'
            'Error: Invalid value for \'--delivery\' / \'-d\': '
            '\'bogus\' is not one of \'regular\', \'mime\', '
            '\'plain\', \'summary\', \'disabled\'.\n')

    def test_override_no_welcome(self):
        self._mlist.send_welcome_message = False
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '-w', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('Welcome', str(items[0].msg['subject']))
        self.assertIn('aperson@example.com', str(items[0].msg['to']))

    def test_override_yes_welcome(self):
        self._mlist.send_welcome_message = True
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                '-W', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        get_queue_messages('virgin', expected_count=0)

    def test_no_override_welcome(self):
        self._mlist.send_welcome_message = True
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[ADD] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('Welcome', str(items[0].msg['subject']))
        self.assertIn('aperson@example.com', str(items[0].msg['to']))

    def test_override_no_goodbye(self):
        self._mlist.send_goodbye_message = False
        subscribe(self._mlist, 'Anne')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('', file=infp)
            result = self._command.invoke(syncmembers, (
                '-g', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[DEL] Anne Person <aperson@example.com>\n')
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
            print('', file=infp)
            result = self._command.invoke(syncmembers, (
                '-G', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[DEL] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        get_queue_messages('virgin', expected_count=0)

    def test_no_override_goodbye(self):
        self._mlist.send_goodbye_message = True
        subscribe(self._mlist, 'Anne')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[DEL] Anne Person <aperson@example.com>\n')
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
            print('', file=infp)
            result = self._command.invoke(syncmembers, (
                '-a', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[DEL] Anne Person <aperson@example.com>\n')
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
            print('', file=infp)
            result = self._command.invoke(syncmembers, (
                '-A', infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[DEL] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        get_queue_messages('virgin', expected_count=0)

    def test_no_override_admin_notify(self):
        subscribe(self._mlist, 'Anne')
        self._mlist.admin_notify_mchanges = True
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('', file=infp)
            result = self._command.invoke(syncmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output,
                         '[DEL] Anne Person <aperson@example.com>\n')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('Ant unsubscription notification',
                      str(items[0].msg['subject']))
        self.assertIn('Anne Person <aperson@example.com> has been removed',
                      str(items[0].msg))
