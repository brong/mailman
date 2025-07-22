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

"""Test email validation."""

import unittest

from mailman.interfaces.address import (
    IEmailValidator,
    InvalidEmailAddressError,
)
from mailman.testing.layers import ConfigLayer
from zope.component import getUtility


class TestValidate(unittest.TestCase):
    """Test email validation."""

    layer = ConfigLayer

    def setUp(self):
        self.is_valid = getUtility(IEmailValidator).is_valid
        self.validate = getUtility(IEmailValidator).validate

    def test_no_at(self):
        self.assertFalse(self.is_valid('anne'))

    def test_no_domain(self):
        self.assertFalse(self.is_valid('anne@'))

    def test_valid_email(self):
        self.assertTrue(self.is_valid('anne@example.com'))

    def test_two_ats(self):
        self.assertTrue(self.is_valid('anne@x@example.com'))

    def test_valid_email_quoted_local(self):
        self.assertTrue(self.is_valid('"anne"@example.com'))

    def test_two_ats_quoted_local(self):
        self.assertTrue(self.is_valid('"anne@x"@example.com'))

    def test_validator_exception(self):
        self.assertRaises(InvalidEmailAddressError,
                          self.validate,
                          'bad_address')
