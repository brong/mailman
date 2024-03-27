# Copyright (C) 2011-2023 by the Free Software Foundation, Inc.
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

"""Test the `mailman create` subcommand."""

import unittest

from click.testing import CliRunner
from mailman.app.lifecycle import create_list
from mailman.commands.cli_lists import create, remove
from mailman.interfaces.domain import IDomainManager
from mailman.testing.layers import ConfigLayer
from zope.component import getUtility


class TestCreate(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._command = CliRunner()

    def test_cannot_create_duplicate_list(self):
        # Cannot create a mailing list if it already exists.
        create_list('ant@example.com')
        result = self._command.invoke(create, ('ant@example.com',))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: create [OPTIONS] LISTNAME\n'
            'Try \'create --help\' for help.\n\n'
            'Error: List already exists: ant@example.com\n')

    def test_invalid_posting_address(self):
        # Cannot create a mailing list with an invalid posting address.
        result = self._command.invoke(create, ('foo',))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: create [OPTIONS] LISTNAME\n'
            'Try \'create --help\' for help.\n\n'
            'Error: Illegal list name: foo\n')

    def test_invalid_owner_addresses(self):
        # Cannot create a list with invalid owner addresses.  LP: #778687
        result = self._command.invoke(
            create, ('-o', 'invalid', 'ant@example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: create [OPTIONS] LISTNAME\n'
            'Try \'create --help\' for help.\n\n'
            'Error: Illegal owner addresses: invalid\n')

    def test_create_with_style_name(self):
        # Cannot create a list with a unknown style name.
        result = self._command.invoke(
            create, ('--style-name', 'private-default', 'ant@example.com'))
        self.assertEqual(result.exit_code, 0)

    def test_create_with_unknown_style_name(self):
        # Cannot create a list with a unknown style name.
        result = self._command.invoke(
            create, ('--style-name', 'invalid', 'ant@example.com'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: create [OPTIONS] LISTNAME\n'
            'Try \'create --help\' for help.\n\n'
            'Error: Unknown list style name: invalid\n')

    def test_create_without_domain_option(self):
        # The domain will be created if no domain options are specified.  Use
        # the example.org domain since example.com is created by the test
        # suite so it would always already exist.
        result = self._command.invoke(create, ('ant@example.org',))
        self.assertEqual(result.exit_code, 0)
        domain = getUtility(IDomainManager)['example.org']
        self.assertEqual(domain.mail_host, 'example.org')

    def test_create_with_d(self):
        result = self._command.invoke(create, ('ant@example.org', '-d'))
        self.assertEqual(result.exit_code, 0)
        domain = getUtility(IDomainManager)['example.org']
        self.assertEqual(domain.mail_host, 'example.org')

    def test_create_with_domain(self):
        result = self._command.invoke(create, ('ant@example.org', '--domain'))
        self.assertEqual(result.exit_code, 0)
        domain = getUtility(IDomainManager)['example.org']
        self.assertEqual(domain.mail_host, 'example.org')

    def test_create_with_D(self):
        result = self._command.invoke(create, ('ant@example.org', '-D'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: create [OPTIONS] LISTNAME\n'
            'Try \'create --help\' for help.\n\n'
            'Error: Undefined domain: example.org\n')

    def test_create_with_nodomain(self):
        result = self._command.invoke(
            create, ('ant@example.org', '--no-domain'))
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: create [OPTIONS] LISTNAME\n'
            'Try \'create --help\' for help.\n\n'
            'Error: Undefined domain: example.org\n')


class TestRemove(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._command = CliRunner()

    def test_remove_not_quiet_no_such_list(self):
        results = self._command.invoke(remove, ('ant@example.com',))
        # It's not an error to try to remove a nonexistent list.
        self.assertEqual(results.exit_code, 0)
        self.assertEqual(
            results.output,
            'No such list matching spec: ant@example.com\n')
