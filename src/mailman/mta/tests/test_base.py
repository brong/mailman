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

"""Test BaseDelivery."""

import smtplib
import unittest

from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.interfaces.configuration import InvalidConfigurationError
from mailman.mta.base import BaseDelivery
from mailman.mta.connection import Connection
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import (
    ConfigLayer,
    SMTPLayer,
    SMTPSLayer,
    STARTTLSLayer,
)
from unittest.mock import patch


class BaseDeliveryTester(BaseDelivery):
    @property
    def connection(self):
        return self._connection


class TestBaseDelivery(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._delivery = BaseDeliveryTester()
        self._mlist = create_list('test@example.com')
        self._msg = mfs("""\
From: anne@example.com
To: test@example.com
Message-ID: <msgid>

Testing
""")
        self._recipients = ['cris@example.com', 'bart@example.com']

    def test_return_refused_recipients(self):
        refused = {'bart@example.com': (450, b'Mailbox unavailable')}
        with patch.object(
            self._delivery.connection,
            'sendmail',
            return_value=refused
        ) as sendmail:
            result = self._delivery._deliver_to_recipients(
                self._mlist, self._msg, {}, self._recipients
            )
        self.assertIs(result, refused)
        sendmail.assert_called_once_with(
            self._mlist.bounces_address,
            sorted(self._recipients),
            self._msg
        )

    def test_smtp_recipients_refused(self):
        refused = {
            'bart@example.com': (450, b'Mailbox unavailable'),
            'cris@example.com': (550, b'No such user'),
        }
        with patch.object(
            self._delivery.connection,
            'sendmail',
            side_effect=smtplib.SMTPRecipientsRefused(refused)
        ):
            with self.assertLogs(
                    'mailman.smtp', level='ERROR'
            ) as logs:
                result = self._delivery._deliver_to_recipients(
                    self._mlist, self._msg, {}, self._recipients
                )
        self.assertIs(result, refused)
        self.assertIn('<msgid> recipients refused:', logs.output[0])

    def test_smtp_response_errors_keep_status(self):
        for error in (
            smtplib.SMTPResponseException(451, b'Try again later'),
            smtplib.SMTPDataError(550, b'Message rejected'),
        ):
            with self.subTest(error=error):
                with patch.object(
                    self._delivery.connection, 'sendmail',
                    side_effect=error
                ):
                    with self.assertLogs(
                        'mailman.smtp', level='ERROR'
                    ) as logs:
                        result = self._delivery._deliver_to_recipients(
                            self._mlist, self._msg, {}, self._recipients
                        )
                self.assertEqual(
                    result,
                    {
                        'bart@example.com': (
                            error.smtp_code, error.smtp_error
                        ),
                        'cris@example.com': (
                            error.smtp_code, error.smtp_error
                        ),
                    }
                )
                self.assertIn('<msgid> response exception:', logs.output[0])

    def test_os_and_smtp_errors_are_temporary_failures(self):
        for error in (
            OSError('Cannot connect'),
            ConnectionRefusedError('Connection refused'),
            TimeoutError('Connection timed out'),
            smtplib.SMTPException('SMTP error'),
            smtplib.SMTPServerDisconnected('Connection lost'),
            smtplib.SMTPNotSupportedError('Feature not supported'),
        ):
            with self.subTest(error=error):
                with patch.object(
                    self._delivery.connection,
                    'sendmail',
                    side_effect=error
                ):
                    with self.assertLogs(
                        'mailman.smtp', level='ERROR'
                    ) as logs:
                        result = self._delivery._deliver_to_recipients(
                            self._mlist, self._msg, {}, self._recipients
                        )
                self.assertEqual(
                    result,
                    {
                        'bart@example.com': (444, str(error)),
                        'cris@example.com': (444, str(error)),
                    }
                )
                self.assertEqual(
                    logs.output,
                    [
                        f'ERROR:mailman.smtp:<msgid> low level smtp error: '
                        f'{error}'
                    ]
                )

    def test_unexpected_exception_propagates(self):
        error = RuntimeError('Unexpected delivery error')
        with patch.object(
            self._delivery.connection,
            'sendmail',
            side_effect=error
        ):
            with self.assertRaises(RuntimeError) as caught:
                self._delivery._deliver_to_recipients(
                    self._mlist, self._msg, {}, self._recipients
                )
        self.assertIs(caught.exception, error)


class TestSMTPSDelivery(unittest.TestCase):
    layer = SMTPSLayer

    def test_smtps_config(self):
        config.push('smtps_config', """\
[mta]
smtp_secure_mode: smtps
""")
        delivery = BaseDeliveryTester()
        self.assertIsInstance(delivery.connection, Connection)
        config.pop('smtps_config')


class TestSTARTTLSDelivery(unittest.TestCase):
    layer = STARTTLSLayer

    def test_starttls_config(self):
        config.push('starttls_config', """\
[mta]
smtp_secure_mode: starttls
""")
        delivery = BaseDeliveryTester()
        self.assertIsInstance(delivery.connection, Connection)
        config.pop('starttls_config')


class TestInvalidDelivery(unittest.TestCase):
    layer = SMTPLayer

    def test_invalid_config(self):
        config.push('invalid_config', """\
[mta]
smtp_secure_mode: invalid
""")
        with self.assertRaises(InvalidConfigurationError):
            BaseDeliveryTester()
        config.pop('invalid_config')
