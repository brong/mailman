# Copyright (C) 2023 by the Free Software Foundation, Inc.
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

"""Test web url utitlities."""

import unittest

from mailman.testing.layers import ConfigLayer
from mailman.utilities.urls import web_urls


class TestWebUrls(unittest.TestCase):
    layer = ConfigLayer

    # Create a mock MailingList object
    class MockMailingList:

        def __init__(self, domain, list_id, mail_host):
            self.domain = domain
            self.list_id = list_id
            self.mail_host = mail_host

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
        domain_url = "https://example.com/mailman3/domains/lists.example.com"   # noqa: E501
        pending_subscriptions_url = "https://example.com/mailman3/lists/mylist.lists.example.com/subscription_requests"   # noqa: E501
        pending_unsubscriptions_url = "https://example.com/mailman3/lists/mylist.lists.example.com/unsubscription_requests"  # noqa: E501

        domain = self.MockDomain(None)
        mlist = self.MockMailingList(domain, list_id, mail_host)

        # When domain doesn't have a base_url set.
        d = {}
        web_urls(d, mlist)
        self.assertEqual(d, {})

        # Expected URLs are generated.
        domain = self.MockDomain(base_url)
        mlist = self.MockMailingList(domain, list_id, mail_host)
        # Call the web_urls function
        d = {}
        web_urls(d, mlist)
        # Assert the expected values
        self.assertEqual(d['held_message_url'], held_message_url)
        self.assertEqual(d['mailinglist_url'], mailing_list_url)
        self.assertEqual(d['domain_url'], domain_url)
        self.assertEqual(
            d['pending_subscriptions_url'], pending_subscriptions_url)
        self.assertEqual(
            d['pending_unsubscriptions_url'], pending_unsubscriptions_url)
