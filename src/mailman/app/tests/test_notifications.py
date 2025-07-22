
# Copyright (C) 2012-2025 by the Free Software Foundation, Inc.
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

"""Test notifications."""

import os
import re
import unittest

from contextlib import ExitStack
from mailman.app.lifecycle import create_list
from mailman.app.notifications import send_goodbye_message
from mailman.config import config
from mailman.interfaces.languages import ILanguageManager
from mailman.interfaces.member import MemberRole
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.template import ITemplateManager
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.helpers import (
    get_queue_messages,
    set_preferred,
    subscribe,
)
from mailman.testing.layers import ConfigLayer
from mailman.utilities.datetime import now
from tempfile import TemporaryDirectory
from zope.component import getUtility


class TestNotifications(unittest.TestCase):
    """Test notifications."""

    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        resources = ExitStack()
        self.addCleanup(resources.close)
        self.var_dir = resources.enter_context(TemporaryDirectory())
        self._mlist = create_list('test@example.com')
        self._mlist.display_name = 'Test List'
        getUtility(ITemplateManager).set(
            'list:user:notice:welcome', self._mlist.list_id,
            'mailman:///welcome.txt')
        config.push('template config', """\
        [paths.testing]
        template_dir: {}/templates
        """.format(self.var_dir))
        resources.callback(config.pop, 'template config')
        # Populate the template directories with a few fake templates.
        path = os.path.join(self.var_dir, 'templates', 'site', 'en')
        os.makedirs(path)
        full_path = os.path.join(path, 'list:user:notice:welcome.txt')
        with open(full_path, 'w', encoding='utf-8') as fp:
            print("""\
Welcome to the $list_name mailing list.

    Posting address: $fqdn_listname
    Help and other requests: $list_requests
    Your name: $user_name
    Your address: $user_address""", file=fp)
        # Write a goodbye message.
        full_path = os.path.join(path, 'list:user:notice:goodbye.txt')
        with open(full_path, 'w', encoding='utf-8') as fp:
            print('$user_email just left the $list_name mailing list!',
                  file=fp)
        # Write a list-specific welcome message.
        path = os.path.join(self.var_dir, 'templates', 'lists',
                            'test@example.com', 'xx')
        os.makedirs(path)
        full_path = os.path.join(path, 'list:user:notice:welcome.txt')
        with open(full_path, 'w', encoding='utf-8') as fp:
            print('You just joined the $list_name mailing list!', file=fp)
        # Write a list-specific welcome message with non-ascii.
        path = os.path.join(self.var_dir, 'templates', 'lists',
                            'test@example.com', 'yy')
        os.makedirs(path)
        full_path = os.path.join(path, 'list:user:notice:welcome.txt')
        with open(full_path, 'w', encoding='utf-8') as fp:
            print('Yöu just joined the $list_name mailing list!', file=fp)
        # Write a list-specific address confirmation message with non-ascii.
        full_path = os.path.join(path, 'list:user:action:subscribe.txt')
        with open(full_path, 'w', encoding='utf-8') as fp:
            print('Wé need your confirmation', file=fp)

    def test_welcome_message(self):
        subscribe(self._mlist, 'Anne', email='anne@example.com')
        # Now there's one message in the virgin queue.
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(str(message['subject']),
                         'Welcome to the "Test List" mailing list')
        self.assertMultiLineEqual(message.get_payload(), """\
Welcome to the Test List mailing list.

    Posting address: test@example.com
    Help and other requests: test-request@example.com
    Your name: Anne Person
    Your address: anne@example.com
""")

    def test_more_specific_welcome_message_nonenglish(self):
        # The welcome message url can contain placeholders for the fqdn list
        # name and language.
        getUtility(ITemplateManager).set(
            'list:user:notice:welcome', self._mlist.list_id,
            'mailman:///$listname/$language/welcome.txt')
        # Add the xx language and subscribe Anne using it.
        manager = getUtility(ILanguageManager)
        manager.add('xx', 'us-ascii', 'Xlandia')
        # We can't use the subscribe() helper because that would send the
        # welcome message before we set the member's preferred language.
        address = getUtility(IUserManager).create_address(
            'anne@example.com', 'Anne Person')
        address.preferences.preferred_language = 'xx'
        self._mlist.subscribe(address)
        # Now there's one message in the virgin queue.
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(str(message['subject']),
                         'Welcome to the "Test List" mailing list')
        self.assertMultiLineEqual(
            message.get_payload(),
            'You just joined the Test List mailing list!')

    def test_more_specific_messages_nonascii(self):
        # The welcome message url can contain placeholders for the fqdn list
        # name and language.
        getUtility(ITemplateManager).set(
            'list:user:notice:welcome', self._mlist.list_id,
            'mailman:///$listname/$language/welcome.txt')
        # Add the yy language and subscribe Anne using it.
        getUtility(ILanguageManager).add('yy', 'utf-8', 'Ylandia')
        # We can't use the subscribe() helper because that would send the
        # welcome message before we set the member's preferred language.
        address = getUtility(IUserManager).create_address(
            'anne@example.com', 'Anné Person')
        address.preferences.preferred_language = 'yy'
        # Get the admin notice too.
        self._mlist.admin_notify_mchanges = True
        # Make another non-ascii replacement.
        self._mlist.display_name = 'Tést List'
        # And set the list's language.
        self._mlist.preferred_language = 'yy'
        self._mlist.subscribe(address)
        # Now there are two messages in the virgin queue.
        items = get_queue_messages('virgin', expected_count=2)
        if str(items[0].msg['subject']).startswith('Welcome'):
            welcome = items[0].msg
            admin_notice = items[1].msg
        else:
            welcome = items[1].msg
            admin_notice = items[0].msg
        self.assertEqual(str(welcome['subject']),
                         'Welcome to the "Tést List" mailing list')
        self.assertMultiLineEqual(
            welcome.get_payload(decode=True).decode('utf-8'),
            'Yöu just joined the Tést List mailing list!')
        # Ensure the message is single part and properly encoded.
        raw_payload = welcome.get_payload()
        self.assertEqual(
            raw_payload.encode('us-ascii', 'replace').decode('us-ascii'),
            raw_payload)
        self.assertEqual(str(admin_notice['subject']),
                         'Tést List subscription notification')
        self.assertMultiLineEqual(
            admin_notice.get_payload(decode=True).decode('utf-8'),
            '=?utf-8?q?Ann=C3=A9_Person?= <anne@example.com> has been'
            ' successfully subscribed to Tést List.\n')
        # Ensure the message is single part and properly encoded.
        raw_payload = admin_notice.get_payload()
        self.assertEqual(
            raw_payload.encode('us-ascii', 'replace').decode('us-ascii'),
            raw_payload)

    def test_confirmation_message(self):
        # Create an address to subscribe.
        address = getUtility(IUserManager).create_address(
            'anne@example.com', 'Anne Person')
        # Register the address with the list to create a confirmation notice.
        ISubscriptionManager(self._mlist).register(address)
        # Now there's one message in the virgin queue.
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertTrue(str(message['subject']).startswith('Your confirm'))
        token = re.sub(r'^.*\+([^+@]*)@.*$', r'\1', str(message['from']))
        self.assertMultiLineEqual(
            message.get_payload(), """\
Email Address Registration Confirmation

Hello, this is the GNU Mailman server at example.com.

We have received a registration request for the email address

    anne@example.com

Before you can start using GNU Mailman at this site, you must first confirm
that this is your email address.  You can do this by replying to this message.

Or you should include the following line -- and only the following
line -- in a message to test-request@example.com:

    confirm {}

Note that simply sending a `reply' to this message should work from
most mail readers.

If you do not wish to register this email address, simply disregard this
message.  If you think you are being maliciously subscribed to the list, or
have any other questions, you may contact

    test-owner@example.com
""".format(token))

    def test_nonascii_confirmation_message(self):
        # Add the 'yy' language and set it
        getUtility(ILanguageManager).add('yy', 'utf-8', 'Ylandia')
        self._mlist.preferred_language = 'yy'
        # Create an address to subscribe.
        address = getUtility(IUserManager).create_address(
            'anne@example.com', 'Anne Person')
        # Register the address with the list to create a confirmation notice.
        ISubscriptionManager(self._mlist).register(address)
        # Now there's one message in the virgin queue.
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertTrue(str(message['subject']).startswith('Your confirm'))
        self.assertMultiLineEqual(
            message.get_payload(decode=True).decode('utf-8'),
            'Wé need your confirmation\n')

    def test_goodbye_message(self):
        member = subscribe(self._mlist, 'Anne', email='anne@example.com')
        # Now there's one message in the virgin queue; get it and clear it.
        items = get_queue_messages('virgin', expected_count=1)
        # Send anne an unsubscribe message.
        language = getUtility(ILanguageManager).get('en')
        send_goodbye_message(self._mlist, member.address.email, language)
        # There's a new message in the virgin queue.
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(str(message['subject']),
                         'You have been unsubscribed from the Test List '
                         'mailing list')
        self.assertMultiLineEqual(
            message.get_payload(),
            'anne@example.com just left the Test List mailing list!')

    def test_no_welcome_message_to_owners(self):
        # Welcome messages go only to mailing list members, not to owners.
        subscribe(self._mlist, 'Anne', MemberRole.owner, 'anne@example.com')
        # There is no welcome message in the virgin queue.
        get_queue_messages('virgin', expected_count=0)

    def test_no_welcome_message_to_nonmembers(self):
        # Welcome messages go only to mailing list members, not to nonmembers.
        subscribe(self._mlist, 'Anne', MemberRole.nonmember,
                  'anne@example.com')
        # There is no welcome message in the virgin queue.
        get_queue_messages('virgin', expected_count=0)

    def test_no_welcome_message_to_moderators(self):
        # Welcome messages go only to mailing list members, not to moderators.
        subscribe(self._mlist, 'Anne', MemberRole.moderator,
                  'anne@example.com')
        # There is no welcome message in the virgin queue.
        get_queue_messages('virgin', expected_count=0)

    def test_member_susbcribed_address_has_display_name(self):
        address = getUtility(IUserManager).create_address(
            'anne@example.com', 'Anne Person')
        address.verified_on = now()
        self._mlist.subscribe(address)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(message['to'], 'Anne Person <anne@example.com>')

    def test_member_susbcribed_address_has_display_name_not_msgdata(self):
        address = getUtility(IUserManager).create_address(
            'anne@example.com', 'Anne Person')
        address.verified_on = now()
        self._mlist.subscribe(address)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        msgdata = items[0].msgdata
        self.assertEqual(message['to'], 'Anne Person <anne@example.com>')
        self.assertEqual(list(msgdata['recipients']), ['anne@example.com'])

    def test_member_subscribed_address_has_no_display_name(self):
        address = getUtility(IUserManager).create_address('anne@example.com')
        address.verified_on = now()
        self._mlist.subscribe(address)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(message['to'], 'anne@example.com')

    def test_member_is_user_and_has_display_name(self):
        user = getUtility(IUserManager).create_user(
            'anne@example.com', 'Anne Person')
        set_preferred(user)
        self._mlist.subscribe(user)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(message['to'], 'Anne Person <anne@example.com>')

    def test_member_is_user_and_has_no_display_name(self):
        user = getUtility(IUserManager).create_user('anne@example.com')
        set_preferred(user)
        self._mlist.subscribe(user)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(message['to'], 'anne@example.com')

    def test_member_has_linked_user_display_name(self):
        user = getUtility(IUserManager).create_user(
            'anne@example.com', 'Anne Person')
        set_preferred(user)
        address = getUtility(IUserManager).create_address('anne2@example.com')
        address.verified_on = now()
        user.link(address)
        self._mlist.subscribe(address)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(message['to'], 'Anne Person <anne2@example.com>')

    def test_member_has_no_linked_display_name(self):
        user = getUtility(IUserManager).create_user('anne@example.com')
        set_preferred(user)
        address = getUtility(IUserManager).create_address('anne2@example.com')
        address.verified_on = now()
        user.link(address)
        self._mlist.subscribe(address)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(message['to'], 'anne2@example.com')

    def test_member_has_address_and_user_display_name(self):
        user = getUtility(IUserManager).create_user(
            'anne@example.com', 'Anne Person')
        set_preferred(user)
        address = getUtility(IUserManager).create_address(
            'anne2@example.com', 'Anne X Person')
        address.verified_on = now()
        user.link(address)
        self._mlist.subscribe(address)
        items = get_queue_messages('virgin', expected_count=1)
        message = items[0].msg
        self.assertEqual(message['to'], 'Anne X Person <anne2@example.com>')
