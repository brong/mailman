# Copyright (C) 2014-2025 by the Free Software Foundation, Inc.
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

"""Test MTA connections."""

import unittest

from mailman.config import config
from mailman.mta.connection import Connection, SecureMode
from mailman.testing.helpers import (
    LogFileMark,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import SMTPLayer, SMTPSLayer, STARTTLSLayer
from smtplib import SMTPAuthenticationError, SMTPNotSupportedError
from unittest.mock import patch


class TestConnection(unittest.TestCase):
    layer = SMTPLayer

    def test_authentication_error(self):
        # Logging in to the MTA with a bad user name and password produces a
        # 571 Bad Authentication error.
        with self.assertRaises(SMTPAuthenticationError) as cm:
            connection = Connection(
                config.mta.smtp_host, int(config.mta.smtp_port), 0,
                'baduser', 'badpass')
            connection.sendmail('anne@example.com', ['bart@example.com'], """\
From: anne@example.com
To: bart@example.com
Subject: aardvarks

""")
        self.assertEqual(cm.exception.smtp_code, 571)
        self.assertEqual(cm.exception.smtp_error, b'Bad authentication')

    def test_authentication_good_path(self):
        # Logging in with the correct user name and password succeeds.
        connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0,
            'testuser', 'testpass')
        connection.sendmail('anne@example.com', ['bart@example.com'], """\
From: anne@example.com
To: bart@example.com
Subject: aardvarks

""")
        self.assertEqual(self.layer.smtpd.get_authentication_credentials(),
                         'AHRlc3R1c2VyAHRlc3RwYXNz')

    def test_sendmail_accepts_string(self):
        # The sendmail method accepts bytes.
        connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0)
        connection.sendmail('anne@example.com', ['bart@example.com'], """\
From: anne@example.com
To: bart@example.com
Subject: aardvarks

""")
        messages = list(self.layer.smtpd.messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['subject'], 'aardvarks')

    def test_sendmail_accepts_bytes(self):
        # The sendmail method accepts bytes.
        connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0)
        connection.sendmail('anne@example.com', ['bart@example.com'], b"""\
From: anne@example.com
To: bart@example.com
Subject: aardvarks

""")
        messages = list(self.layer.smtpd.messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['subject'], 'aardvarks')

    def test_sendmail_accepts_message(self):
        # The sendmail method accepts a message object.
        connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0)
        connection.sendmail('anne@example.com', ['bart@example.com'], mfs("""\
From: anne@example.com
To: bart@example.com
Subject: aardvarks

"""))
        messages = list(self.layer.smtpd.messages)
        self.assertEqual(len(messages), 1)
        self.assertEqual(messages[0]['subject'], 'aardvarks')


class _ConnectionCounter:
    layer = None

    def setUp(self):
        self.connection = None
        self.msg_text = """\
From: anne@example.com
To: bart@example.com
Subject: aardvarks
"""

    def test_count_0(self):
        # So far, no connections.
        self.assertEqual(self.layer.smtpd.get_connection_count(), 0)

    def test_count_1(self):
        self.connection.sendmail(
            'anne@example.com', ['bart@example.com'], self.msg_text)
        self.assertEqual(self.layer.smtpd.get_connection_count(), 1)

    def test_count_2(self):
        self.connection.sendmail(
            'anne@example.com', ['bart@example.com'], self.msg_text)
        self.connection.quit()
        self.connection.sendmail(
            'cate@example.com', ['dave@example.com'], self.msg_text)
        self.connection.quit()
        self.assertEqual(self.layer.smtpd.get_connection_count(), 2)

    def test_count_2_no_quit(self):
        self.connection.sendmail(
            'anne@example.com', ['bart@example.com'], self.msg_text)
        self.connection.sendmail(
            'cate@example.com', ['dave@example.com'], self.msg_text)
        self.connection.quit()
        self.assertEqual(self.layer.smtpd.get_connection_count(), 1)

    def test_count_reset(self):
        self.connection.sendmail(
            'anne@example.com', ['bart@example.com'], self.msg_text)
        self.connection.quit()
        self.connection.sendmail(
            'cate@example.com', ['dave@example.com'], self.msg_text)
        self.connection.quit()
        # Issue the fake SMTP command to reset the count.
        self.layer.smtpd.reset()
        self.assertEqual(self.layer.smtpd.get_connection_count(), 0)


class TestSMTPConnectionCount(_ConnectionCounter, unittest.TestCase):

    layer = SMTPLayer

    def setUp(self):
        super().setUp()
        self.connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0)

    @patch('mailman.mta.connection.smtplib.SMTP', side_effect=Exception)
    def test_exception_is_raised_from_connect(self, mocked_SMTP):
        connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0)
        with self.assertRaises(Exception):
            connection._connect()
        self.assertTrue(mocked_SMTP.called)


class TestSMTPSConnectionCount(_ConnectionCounter, unittest.TestCase):

    layer = SMTPSLayer

    def setUp(self):
        super().setUp()
        self.connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0,
            secure_mode=SecureMode.IMPLICIT, verify_cert=False
            )


class TestSTARTTLSConnectionCount(_ConnectionCounter, unittest.TestCase):

    layer = STARTTLSLayer

    def setUp(self):
        super().setUp()
        self.connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0,
            secure_mode=SecureMode.STARTTLS, verify_cert=False
            )


class TestSTARTTLSNotSupported(unittest.TestCase):
    layer = SMTPLayer

    def test_not_supported(self):
        connection = Connection(
            config.mta.smtp_host, int(config.mta.smtp_port), 0,
            secure_mode=SecureMode.STARTTLS, verify_cert=False
            )
        msg_text = """\
From: anne@example.com
To: bart@example.com
Subject: aardvarks
"""
        LogFileMark('mailman.smtp')
        with self.assertRaises(SMTPNotSupportedError):
            connection.sendmail(
                'anne@example.com', ['bart@example.com'], msg_text)
