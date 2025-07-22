# Copyright (C) 2016-2025 by the Free Software Foundation, Inc.
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

"""Test REST header matches."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.database.transaction import transaction
from mailman.interfaces.mailinglist import IHeaderMatchList
from mailman.testing.helpers import call_api
from mailman.testing.layers import RESTLayer
from urllib.error import HTTPError


class TestHeaderMatches(unittest.TestCase):
    layer = RESTLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('ant@example.com')

    def test_get_missing_header_match(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/header-matches/0')
        self.assertEqual(cm.exception.code, 404)
        self.assertEqual(cm.exception.reason,
                         'No header match at this position: 0')

    def test_delete_missing_header_match(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/header-matches/0',
                     method='DELETE')
        self.assertEqual(cm.exception.code, 404)
        self.assertEqual(cm.exception.reason,
                         'No header match at this position: 0')

    def test_add_duplicate(self):
        header_matches = IHeaderMatchList(self._mlist)
        with transaction():
            header_matches.append('header', 'pattern')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/header-matches', {
                         'header': 'header',
                         'pattern': 'pattern',
                        }, method='POST')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason,
                         'This header match already exists')

    def test_header_match_on_missing_list(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/bee.example.com'
                     '/header-matches/')
        self.assertEqual(cm.exception.code, 404)

    def test_add_bad_regexp(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/header-matches', {
                         'header': 'header',
                         'pattern': '+invalid',
                        })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(
            cm.exception.reason,
            'Invalid Parameter "pattern":'
            ' Expected a valid regexp, got +invalid.')

    def test_patch_bad_regexp(self):
        header_matches = IHeaderMatchList(self._mlist)
        with transaction():
            header_matches.append('header', 'pattern')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/header-matches/0', {
                         'header': 'header',
                         'pattern': '+invalid',
                        }, method='PATCH')
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(
            cm.exception.reason,
            'Invalid Parameter "pattern":'
            ' Expected a valid regexp, got +invalid.')
        self.assertEqual(cm.exception.reason,
                         'Invalid Parameter "pattern": '
                         'Expected a valid regexp, got +invalid.')

    def test_add_header_match(self):
        _, resp = call_api('http://localhost:9001/3.0/lists/ant.example.com'
                           '/header-matches', {
                               'header': 'header-1',
                               'pattern': '^Yes',
                               'action': 'hold',
                               'tag': 'tag1',
                               },
                           method='POST')
        self.assertEqual(resp.status_code, 201)
        header_matches = IHeaderMatchList(self._mlist)
        self.assertEqual(
            [(match.header, match.pattern, match.chain, match.tag)
             for match in header_matches],
            [('header-1', '^Yes', 'hold', 'tag1')])

    def test_add_header_match_with_no_action(self):
        _, resp = call_api('http://localhost:9001/3.0/lists/ant.example.com'
                           '/header-matches', {
                               'header': 'header-1',
                               'pattern': '^Yes',
                               'action': '',
                               'tag': 'tag1',
                                },
                               method='POST')
        self.assertEqual(resp.status_code, 201)
        header_matches = IHeaderMatchList(self._mlist)
        self.assertEqual(
            [(match.header, match.pattern, match.chain, match.tag)
             for match in header_matches],
            [('header-1', '^Yes', None, 'tag1')])

    def test_update_header_match_with_action(self):
        header_matches = IHeaderMatchList(self._mlist)
        with transaction():
            header_matches.append('header-1', '^Yes', 'hold', 'tag1')
        _, resp = call_api('http://localhost:9001/3.0/lists/ant.example.com'
                           '/header-matches/0', {
                               'action': ''
                               },
                           method='PATCH')
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            [(match.header, match.pattern, match.chain, match.tag)
             for match in header_matches],
            [('header-1', '^Yes', None, 'tag1')])

    def test_update_header_match_with_no_action(self):
        header_matches = IHeaderMatchList(self._mlist)
        with transaction():
            header_matches.append('header-1', '^Yes', 'hold', 'tag1')
        _, resp = call_api('http://localhost:9001/3.0/lists/ant.example.com'
                           '/header-matches/0', {
                               'pattern': '^No'
                               },
                           method='PATCH')
        self.assertEqual(resp.status_code, 204)
        self.assertEqual(
            [(match.header, match.pattern, match.chain, match.tag)
             for match in header_matches],
            [('header-1', '^No', 'hold', 'tag1')])

    def test_get_header_match_by_tag(self):
        header_matches = IHeaderMatchList(self._mlist)
        with transaction():
            header_matches.append('header-1', 'pattern-1')
            header_matches.append(
                'header-2', 'pattern-2', chain='hold', tag='tag')
            header_matches.append('header-3', 'pattern-3', chain='accept')

        content, resp = call_api(
            'http://localhost:9001/3.0/lists/ant.example.com'
            '/header-matches/find', {'tag': 'tag'}
            )
        self.assertEqual(resp.status_code, 200)
        self.assertIsNotNone(content)
        self.assertEqual(len(content['entries']), 1)
        self.assertEqual(content['entries'][0]['header'], 'header-2')
        self.assertEqual(content['entries'][0]['pattern'], 'pattern-2')
        self.assertEqual(content['entries'][0]['action'], 'hold')

    def test_get_header_match_empty(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/header-matches/find', {'tag': 'tag'})
        self.assertEqual(cm.exception.code, 404)
        self.assertEqual(
            cm.exception.reason,
            'Cound not find any HeaderMatch for provided search options.')
