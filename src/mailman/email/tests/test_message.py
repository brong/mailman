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

"""Test the message API."""

import copy
import pickle
import unittest

from email import message_from_binary_file
from email.header import Header
from email.parser import FeedParser
from email.utils import _has_surrogates
from importlib.resources import files
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.email.message import Message, OwnerNotification, UserNotification
from mailman.testing.helpers import (
    get_queue_messages,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer


class TestUserNotificationMessage(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._msg = UserNotification(
            'aperson@example.com',
            'test@example.com',
            'Something you need to know',
            'I needed to tell you this.')

    def test_one_precedence_header(self):
        # Ensure that when the original message already has a Precedence:
        # header, UserNotification.send(..., add_precedence=True, ...) does
        # not add a second header.
        self.assertEqual(self._msg['precedence'], None)
        self._msg['Precedence'] = 'omg wtf bbq'
        self._msg.send(self._mlist)
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(items[0].msg.get_all('precedence'),
                         ['omg wtf bbq'])

    def test_msgdata(self):
        self._msg.send(self._mlist)
        items = get_queue_messages('virgin', expected_count=1)
        # Notifications should get reduced List-* headers.
        self.assertTrue(items[0].msgdata.get('reduced_list_headers'))
        # Notifications should not apply the decorate handler.
        self.assertTrue(items[0].msgdata.get('nodecorate'))
        # No envelope sender address specified.
        self.assertIsNone(items[0].msgdata.get('sender'))


class TestOwnerNotificationMessage(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._msg = OwnerNotification(
            self._mlist,
            'Something owner notification',
            'I needed to tell you this.'
        )

    def test_owner_notification(self):
        self._msg.send(self._mlist)
        items = get_queue_messages('virgin', expected_count=1)

        self.assertTrue(items[0].msgdata.get('nodecorate'))
        self.assertTrue(items[0].msgdata.get('reduced_list_headers'))
        self.assertEqual(
            items[0].msgdata.get('sender'),
            config.mailman.site_owner
        )
        self.assertEqual(
            items[0].msg.get_all('from'),
            [config.mailman.site_owner]
        )
        self.assertEqual(
            items[0].msg.get_all('to'),
            [config.mailman.site_owner]
        )


class TestMessageSubclass(unittest.TestCase):
    layer = ConfigLayer

    def test_i18n_filenames(self):
        parser = FeedParser(_factory=Message)
        parser.feed("""\
Message-ID: <blah@example.com>
Content-Type: multipart/mixed; boundary="------------050607040206050605060208"

This is a multi-part message in MIME format.
--------------050607040206050605060208
Content-Type: text/plain; charset=UTF-8
Content-Transfer-Encoding: quoted-printable

Test message containing an attachment with an accented filename

--------------050607040206050605060208
Content-Disposition: attachment;
    filename*=UTF-8''d%C3%A9jeuner.txt

Test content
--------------050607040206050605060208--
""")
        msg = parser.close()
        attachment = msg.get_payload(1)
        try:
            filename = attachment.get_filename()
        except TypeError as error:
            self.fail(error)
        self.assertEqual(filename, 'd\xe9jeuner.txt')

    def test_senders_header_instances(self):
        msg = Message()
        msg['From'] = Header('test@example.com')
        # Make sure the senders property does not fail
        self.assertEqual(msg.senders, ['test@example.com'])

    def test_senders_multiple_addresses(self):
        msg = Message()
        msg['From'] = 'Anne <anne@example.com>'
        msg['Reply-To'] = 'Bart <bart@example.com>, Cate <cate@example.com>'
        self.assertEqual(msg.senders,
                         ['anne@example.com',
                          'bart@example.com',
                          'cate@example.com'])

    def test_get_addresses_quoted_comma_and_multiline_display_name(self):
        msg = Message()
        msg['From'] = (
            '"last, first" <comma-in-display-name@example.com>'
        )
        msg['To'] = (
            '"last\r\n first" <cr-lf-in-display-name@example.net>'
        )
        msg['Cc'] = (
            '"last, first" <comma-in-display-name@example.org>, '
            '"last\r\n first" <cr-lf-in-display-name@example.org>'
        )
        self.assertEqual(
            msg.get_addresses('from'),
            [
                ('last, first', 'comma-in-display-name@example.com'),
            ]
        )
        self.assertEqual(
            msg.get_addresses('to'),
            [
                ('last first', 'cr-lf-in-display-name@example.net'),
            ]
        )
        self.assertEqual(
            msg.get_addresses('cc'),
            [
                ('last, first', 'comma-in-display-name@example.org'),
                ('last first', 'cr-lf-in-display-name@example.org'),
            ]
        )

    def test_user_notification_bad_charset(self):
        msg = UserNotification(
            'aperson@example.com',
            'test@example.com',
            'Something you need to know',
            'Non-ascii text é.')
        self.assertEqual(msg.get_payload(decode=True).decode(),
                         'Non-ascii text é.')

    def test_ascii_user_notification_non_ascii_subject(self):
        msg = UserNotification(
            'aperson@example.com',
            'test@example.com',
            'Something you nééd to know',
            'I needed to tell you this.')
        self.assertIn(
                b'Subject: =?utf-8?q?Something_you_n=C3=A9=C3=A9d_to_know?=',
                msg.as_bytes())

    def test_as_string_python_bug_32330(self):
        with (files('mailman.email.tests.data') / 'bad_email_2.eml').open(
                'rb') as fp:
            msg = message_from_binary_file(fp, Message)
            fp.seek(0)
            text = fp.read().decode('ascii', 'replace')
        self.assertEqual(msg.as_string(), text)

    def test_as_string_unicode_surrogates(self):
        with (files('mailman.email.tests.data') / 'bad_email_4.eml').open(
                'rb') as fp:
            msg = message_from_binary_file(fp, Message)
        self.assertFalse(_has_surrogates(msg.as_string()))

    def test_as_bytes_python_bug_41307(self):
        msgstr = """\
To: list@example.com
From: user@example.com
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: 8bit

Message with \u201cNon-ascii\u201d.
"""
        msg = mfs(msgstr)
        self.assertEqual(msg.as_bytes(), msgstr.encode('utf-8'))

    def test_bogus_content_charset(self):
        with (files('mailman.email.tests.data') / 'bad_email_3.eml').open(
                'rb') as fp:
            msg = message_from_binary_file(fp, Message)
            fp.seek(0)
            text = fp.read().decode('ascii', 'replace')
        self.assertEqual(msg.as_string(), text)


class TestHeaderLog(unittest.TestCase):
    """Test the log of original header field values."""

    layer = ConfigLayer

    RAW = """\
From: anne@example.com
To: ant@example.com
Cc: a@example.com
Cc: b@example.com
Subject: hello
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"
Content-Transfer-Encoding: 7bit

Hello.
"""

    def _msg(self):
        msg = mfs(self.RAW)
        msg.start_header_log()
        return msg

    def test_parser_does_not_log(self):
        msg = mfs(self.RAW)
        self.assertIsNone(msg.header_log)

    def test_not_started_records_nothing(self):
        msg = mfs(self.RAW)
        msg['Reply-To'] = 'list@example.com'
        self.assertIsNone(msg.header_log)

    def test_setitem_logs_absent(self):
        msg = self._msg()
        msg['Reply-To'] = 'list@example.com'
        self.assertEqual(msg.header_log, {'reply-to': []})

    def test_setitem_logs_existing(self):
        msg = self._msg()
        msg['Cc'] = 'c@example.com'
        self.assertEqual(
            msg.header_log, {'cc': ['a@example.com', 'b@example.com']})

    def test_delitem_logs_all_values(self):
        msg = self._msg()
        del msg['cc']
        self.assertEqual(
            msg.header_log, {'cc': ['a@example.com', 'b@example.com']})

    def test_first_touch_only(self):
        msg = self._msg()
        msg.replace_header('Subject', 'x')
        msg.replace_header('Subject', 'y')
        del msg['subject']
        self.assertEqual(msg.header_log, {'subject': ['hello']})

    def test_add_header(self):
        msg = self._msg()
        msg.add_header('Content-Disposition', 'inline')
        self.assertEqual(msg.header_log, {'content-disposition': []})

    def test_set_raw(self):
        msg = self._msg()
        msg.set_raw('List-Id', '<ant.example.com>')
        self.assertEqual(msg.header_log, {'list-id': []})

    def test_set_param_logs_content_type(self):
        msg = self._msg()
        msg.set_param('format', 'flowed')
        self.assertEqual(msg.header_log,
                         {'content-type': ['text/plain; charset="us-ascii"']})

    def test_set_boundary_logs_content_type(self):
        msg = mfs("""\
From: anne@example.com
Content-Type: multipart/mixed; boundary="aaa"

--aaa

x
--aaa--
""")
        msg.start_header_log()
        msg.set_boundary('bbb')
        self.assertEqual(msg.header_log,
                         {'content-type': ['multipart/mixed; boundary="aaa"']})

    def test_set_payload_charset_logs(self):
        msg = mfs("""\
From: anne@example.com
Subject: hi

Hello.
""")
        msg.start_header_log()
        msg.set_payload('caf\xe9', 'utf-8')
        # set_charset adds all three, through add_header.
        self.assertEqual(msg.header_log, {
            'mime-version': [], 'content-type': [],
            'content-transfer-encoding': []})

    def test_start_is_idempotent(self):
        msg = self._msg()
        msg['Subject'] = 'changed'
        msg.start_header_log()
        self.assertEqual(msg.header_log, {'subject': ['hello']})

    def test_pickle_and_deepcopy_carry_log(self):
        msg = self._msg()
        del msg['subject']
        for other in (pickle.loads(pickle.dumps(msg)), copy.deepcopy(msg)):
            self.assertEqual(other.header_log, {'subject': ['hello']})
            other['To'] = 'bee@example.com'
            self.assertEqual(other.header_log, {
                'subject': ['hello'], 'to': ['ant@example.com']})

    def test_stop_header_log(self):
        msg = self._msg()
        del msg['subject']
        msg.stop_header_log()
        self.assertIsNone(msg.header_log)
        msg['To'] = 'bee@example.com'
        self.assertIsNone(msg.header_log)

    def test_unpickle_without_log(self):
        # A message pickled before the log existed.
        msg = mfs(self.RAW)
        state = pickle.dumps(msg)
        self.assertIsNone(pickle.loads(state).header_log)

    def testprepend_header(self):
        msg = self._msg()
        msg.prepend_header('Message-Instance', 'm=1; h=x; b=y;')
        self.assertEqual(msg.header_log, {})
        self.assertEqual(msg.keys()[0], 'Message-Instance')
