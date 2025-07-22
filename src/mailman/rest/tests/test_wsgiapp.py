# Copyright (C) 2019-2025 by the Free Software Foundation, Inc.
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

"""Test behaviors of the REST API"""

import requests
import unittest

from mailman.config import config
from mailman.interfaces.domain import IDomainManager
from mailman.testing.helpers import call_api
from mailman.testing.layers import RESTLayer
from zope.component import getUtility


class TestSupportedContentType(unittest.TestCase):
    layer = RESTLayer

    def test_api_supports_json_input(self):
        # Test that input parameters can be sent as json encoded body of the
        # request.
        url = 'http://localhost:9001/3.1/domains'
        json, response = call_api(url, json=dict(mail_host='example.org'))
        self.assertEqual(response.status_code, 201)

    def test_api_returns_json_error_with_json_input(self):
        # Test that API returns error in JSON if we call API with Content-Type
        # set to application/json.
        url = 'http://localhost:9001/3.1/domains'
        # Skipping JSON input should cause a parse error, but return that as
        # error.
        basic_auth = (config.webservice.admin_user,
                      config.webservice.admin_pass)
        response = requests.post(
            url,
            headers={'Content-Type': 'application/json'},
            json=dict(),
            auth=basic_auth)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers.get('content-type', None),
                         'application/json; charset=UTF-8')
        self.assertEqual(
            response.json(),
            {'title': '400 Bad Request',
             'description': 'Missing Parameter: mail_host'}
            )
        # Now, let's try to send in json valid json but cause a missing
        # required parameter error.
        response = requests.post(
            url,
            json={'description': 'A fun mailing list.'},
            headers={'Content-Type': 'application/json'},
            auth=basic_auth
            )
        self.assertEqual(response.headers.get('content-type', None),
                         'application/json; charset=UTF-8')
        self.assertEqual(
            response.json(),
            {'title': '400 Bad Request',
             'description': 'Missing Parameter: mail_host'}
            )
        # We are algo going to test the call to
        # mailman.rest.helpers.BadRequest() just to make sure that it also
        # formats the errors correctly. We can't test every single site, but
        # atleast the ones we know about.
        # First, create a Domain.
        url = 'http://localhost:9001/3.1/domains'
        json, response = call_api(url, json=dict(mail_host='example.org'))
        self.assertEqual(response.status_code, 201)
        # Then, try to get bad URL
        url = 'http://localhost:9001/3.1/domains/example.org/lists/random'
        response = requests.get(
            url,
            headers={'Content-Type': 'application/json'},
            auth=basic_auth
            )
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers.get('content-type', None),
                         'application/json')
        self.assertEqual(response.json(),
                         {'title': '400 Bad Request'})
        # Now, let's try to call somewhere mailman.rest.helpers.bad_request()
        # is used.
        url = 'http://localhost:9001/3.1/domains'
        response = requests.post(url,
                                 json=dict(mail_host='example.org'),
                                 auth=basic_auth)
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.headers.get('content-type', None),
                         'application/json; charset=UTF-8')

    def test_json_input_with_list(self):
        # Test create a domain with a list of owners.
        url = 'http://localhost:9001/3.1/domains'
        json, response = call_api(
            url,
            json=dict(mail_host='example.org',
                      owner=['aperson@example.org', 'bperson@example.org']))
        self.assertEqual(response.status_code, 201)
        domain = getUtility(IDomainManager).get('example.org')
        owners = sorted([user.addresses[0].email for user in domain.owners])
        self.assertEqual(owners,
                         ['aperson@example.org', 'bperson@example.org'])

    def test_error_response_is_unformly_formatted(self):
        pass
