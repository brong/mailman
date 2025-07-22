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

import unittest

from mailman.config import config
from mailman.interfaces.configuration import InvalidConfigurationError
from mailman.mta.base import BaseDelivery
from mailman.mta.connection import Connection
from mailman.testing.layers import SMTPLayer, SMTPSLayer, STARTTLSLayer


class BaseDeliveryTester(BaseDelivery):
    @property
    def connection(self):
        return self._connection


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
