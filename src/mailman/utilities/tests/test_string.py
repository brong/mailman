# Copyright (C) 2015-2023 by the Free Software Foundation, Inc.
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

"""Test the string utilities."""

import unittest

from mailman.testing.layers import ConfigLayer
from mailman.utilities import string


class TestString(unittest.TestCase):
    def test_oneline_bogus_charset(self):
        self.assertEqual(string.oneline('foo', 'bogus'), 'foo')

    def test_wrap_blank_paragraph(self):
        self.assertEqual(string.wrap('\n\n'), '\n\n')


class TestWebSubstitutions(unittest.TestCase):
    """Test that all the web substitutions are available."""

    layer = ConfigLayer

    # Create a mock MailingList object

    class MockMailingList:

        def __init__(self, domain, list_id, mail_host):

            class preferred_language:

                def __init__(self, code):
                    self.code = code

            self.domain = domain
            self.list_id = list_id
            self.mail_host = mail_host
            self.fqdn_listname = list_id.replace('.', '@', 1)
            self.display_name = 'MyList'
            self.list_name = 'mylist'
            self.short_listname = 'mylist'
            self.description = 'description'
            self.info = 'info'
            self.request_address = list_id.replace('.', '-request@', 1)
            self.owner_address = list_id.replace('.', '-owner@', 1)
            self.preferred_language = preferred_language('en')

    # Create a mock domain object
    class MockDomain:

        def __init__(self, base_url):
            self.base_url = base_url

    def test_web_urls(self):
        base_url = "https://example.com/mailman3"
        list_id = "mylist.lists.example.com"
        mail_host = "lists.example.com"
        held_message_url = "https://example.com/mailman3/lists/mylist.lists.example.com/held_messages"  # noqa: E501
        mailing_list_url = "https://example.com/mailman3/lists/mylist.lists.example.com"  # noqa: E501
        domain_url = "https://example.com/mailman3/domains/lists.example.com"
        pending_subscriptions_url = "https://example.com/mailman3/lists/mylist.lists.example.com/subscription_requests"   # noqa: E501
        pending_unsubscriptions_url = "https://example.com/mailman3/lists/mylist.lists.example.com/unsubscription_requests"  # noqa: E501

        # Expected URLs are generated.
        domain = self.MockDomain(base_url)
        mlist = self.MockMailingList(domain, list_id, mail_host)
        # Create a template with all the substitutions.
        template = """\
$base_url
$list_id
$mail_host
$held_message_url
$mailing_list_url
$domain_url
$pending_subscriptions_url
$pending_unsubscriptions_url
"""
        template = string.expand(template, mlist)
        # Assert the expected values
        self.assertIn(base_url, template)
        self.assertIn(list_id, template)
        self.assertIn(mail_host, template)
        self.assertIn(held_message_url, template)
        self.assertIn(mailing_list_url, template)
        self.assertIn(domain_url, template)
        self.assertIn(pending_subscriptions_url, template)
        self.assertIn(pending_unsubscriptions_url, template)
