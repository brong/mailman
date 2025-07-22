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

"""Test the `mailman addmembers` command."""

import re
import unittest

from click.testing import CliRunner
from mailman.app.lifecycle import create_list
from mailman.commands.cli_addmembers import addmembers
from mailman.interfaces.bans import IBanManager
from mailman.interfaces.mailinglist import SubscriptionPolicy
from mailman.interfaces.member import DeliveryMode, DeliveryStatus, MemberRole
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.helpers import get_queue_messages, subscribe
from mailman.testing.layers import ConfigLayer
from tempfile import NamedTemporaryFile
from zope.component import getUtility


class TestCLIAddMembers(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._command = CliRunner()

    def test_no_such_list(self):
        result = self._command.invoke(addmembers, ('-', 'bee.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: addmembers [OPTIONS] FILENAME LISTSPEC\n'
            'Try \'addmembers --help\' for help.\n\n'
            'Error: No such list: bee.example.com\n')

    def test_bad_filename(self):
        result = self._command.invoke(addmembers, ('bad', 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: addmembers [OPTIONS] FILENAME LISTSPEC\n'
            'Try \'addmembers --help\' for help.\n\n'
            'Error: Invalid value for \'FILENAME\': '
            '\'bad\': No such file or directory\n')

    def test_already_subscribed_with_display_name(self):
        subscribe(self._mlist, 'Anne')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           'Already subscribed (skipping): Anne Person <aperson@example.com>\n'
           )

    def test_banned_address(self):
        IBanManager(self._mlist).ban('aperson@example.com')
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           'Membership is banned (skipping): '
           'Anne Person <aperson@example.com>\n'
           )
        self.assertEqual(len(list(self._mlist.members.members)), 0)

    def test_subscription_pending(self):
        # Create an address.
        address = getUtility(IUserManager).create_address(
            'aperson@example.com', 'Anne Person')
        # Pend a subscription.
        self._mlist.subscription_policy = SubscriptionPolicy.confirm
        ISubscriptionManager(self._mlist).register(address)
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           'Subscription already pending (skipping): '
           'Anne Person <aperson@example.com>\n'
           )
        self.assertEqual(len(list(self._mlist.members.members)), 0)

    def test_add_invalid_email(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('foobar@', file=infp)
            result = self._command.invoke(addmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(
           result.output,
           'Cannot parse as valid email address (skipping): foobar@\n'
           )
        self.assertEqual(len(list(self._mlist.members.members)), 0)

    def test_add_delivery_default(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_add_input_stdin(self):
        result = self._command.invoke(addmembers, (
            '-', 'ant.example.com'),
            input='Anne Person <aperson@example.com>\n')
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_add_existing_address(self):
        getUtility(IUserManager).create_address('aperson@example.com',
                                                'A Person')
        result = self._command.invoke(addmembers, (
            '-', 'ant.example.com'),
            input='Anne Person <aperson@example.com>\n')
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'A Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_add_blanks_and_comments(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            print('   ', file=infp)
            print('#Bart Person <bperson@example.com>', file=infp)
            print('Cate Person <cperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 2)
        addresses = [member.address.original_email for member in members]
        self.assertIn('aperson@example.com', addresses)
        self.assertIn('cperson@example.com', addresses)

    def test_add_delivery_regular(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                '-d', 'regular', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_add_delivery_disabled(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                '-d', 'disabled', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.regular)
        self.assertEqual(members[0].delivery_status,
                         DeliveryStatus.by_moderator)

    def test_add_delivery_mime(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                '-d', 'mime', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode, DeliveryMode.mime_digests)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_add_delivery_plain(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                '-d', 'plain', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode,
                         DeliveryMode.plaintext_digests)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_add_delivery_summary(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                '-d', 'summary', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        self.assertEqual(members[0].display_name, 'Anne Person')
        self.assertEqual(members[0].role, MemberRole.member)
        self.assertEqual(members[0].delivery_mode,
                         DeliveryMode.summary_digests)
        self.assertEqual(members[0].delivery_status, DeliveryStatus.enabled)

    def test_add_delivery_bogus(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                '-d', 'bogus', infp.name, 'ant.example.com'))
        self.assertEqual(result.exit_code, 2)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 0)
        self.assertEqual(
            result.output,
            'Usage: addmembers [OPTIONS] FILENAME LISTSPEC\n'
            'Try \'addmembers --help\' for help.\n\n'
            'Error: Invalid value for \'--delivery\' / \'-d\': '
            '\'bogus\' is not one of \'regular\', \'mime\', '
            '\'plain\', \'summary\', \'disabled\'.\n')

    def test_invite_member(self):
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                '-i', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        # Anne is not subscribed.
        self.assertEqual(len(members), 0)
        # But there is an invitation.
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('invited', str(items[0].msg['subject']))
        self.assertIn('aperson@example.com', str(items[0].msg['to']))
        token = re.sub(r'^.*\+([^+@]*)@.*$', r'\1', str(items[0].msg['from']))
        self.assertIn("""\
Your address "aperson@example.com" has been invited to join the ant
mailing list at example.com by the ant mailing list owner.
You may accept the invitation by simply replying to this message.

Or you should include the following line -- and only the following
line -- in a message to ant-request@example.com:

    confirm {}

Note that simply sending a `reply' to this message should work from
most mail readers.

If you want to decline this invitation, please simply disregard this
message.  If you have any questions, please send them to
ant-owner@example.com.""".format(token),
                      str(items[0].msg))

    def test_override_no_welcome(self):
        self._mlist.send_welcome_message = False
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                '-w', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
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
            result = self._command.invoke(addmembers, (
                '-W', infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        get_queue_messages('virgin', expected_count=0)

    def test_no_override_welcome(self):
        self._mlist.send_welcome_message = True
        with NamedTemporaryFile('w', buffering=1, encoding='utf-8') as infp:
            print('Anne Person <aperson@example.com>', file=infp)
            result = self._command.invoke(addmembers, (
                infp.name, 'ant.example.com'))
        self.assertEqual(result.output, '')
        self.assertEqual(result.exit_code, 0)
        members = list(self._mlist.members.members)
        self.assertEqual(len(members), 1)
        self.assertEqual(members[0].address.email, 'aperson@example.com')
        items = get_queue_messages('virgin', expected_count=1)
        self.assertIn('Welcome', str(items[0].msg['subject']))
        self.assertIn('aperson@example.com', str(items[0].msg['to']))
