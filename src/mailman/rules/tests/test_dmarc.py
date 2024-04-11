# Copyright (C) 2016-2023 by the Free Software Foundation, Inc.
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

"""Tests and mocks for DMARC rule."""

import os
import threading

from contextlib import ExitStack
from datetime import timedelta
from dns.exception import DNSException
from dns.rdatatype import CNAME, TXT
from dns.resolver import NoAnswer, NoNameservers, NXDOMAIN
from email import message_from_bytes
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import path
from lazr.config import as_timedelta
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.interfaces.mailinglist import DMARCMitigateAction
from mailman.rules import dmarc
from mailman.testing.helpers import (
    configuration,
    LogFileMark,
    specialized_message_from_string as mfs,
    wait_for_webservice,
)
from mailman.testing.layers import ConfigLayer
from mailman.utilities.datetime import now
from public import public
from unittest import TestCase
from unittest.mock import patch


@public
def get_dns_resolver(
        rtype=TXT,
        rdata=b'v=DMARC1; p=reject;',
        rmult=False,
        cmult=False,
        cloop=False,
        cmiss=False,
        ucase=False,
        cnmok=False,
        cnuc=False):
    """Create a dns.resolver.Resolver mock.

    This is used to return a predictable response to a _dmarc query.  It
    returns p=reject for the example.biz domain and raises an exception for
    other examples.

    It only implements those classes and attributes used by the dmarc rule.
    """
    class Name:
        # Mock answer.name.
        def __init__(self, name='_dmarc.example.biz.'):
            self.name = name

        def to_text(self):
            return self.name

    class Item:
        # Mock answer.items.
        def __init__(self, rdata=rdata, cname='_dmarc.example.com.'):
            self.strings = [rdata]
            # For CNAMEs.
            self.target = Name(cname)

    class Ans_e:
        # Mock answer element.
        def __init__(
                self,
                rtype=rtype,
                rdata=rdata,
                cname='_dmarc.example.com.',
                name='_dmarc.example.biz.'):
            self.rdtype = rtype
            self.items = [Item(rdata, cname)]
            self.name = Name(name)

    class Answer:
        # Mock answer.
        def __init__(self):
            if cloop:
                self.answer = [
                    Ans_e(
                        rtype=CNAME,
                        name='_dmarc.example.biz.',
                        cname='_dmarc.example.org.'
                        ),
                    Ans_e(
                        rtype=CNAME,
                        name='_dmarc.example.org.',
                        cname='_dmarc.example.biz.'
                        ),
                    Ans_e(
                        rtype=TXT,
                        name='_dmarc.example.org.',
                        rdata=b'v=DMARC1; p=reject;'
                        ),
                    ]
            elif cmult:
                self.answer = [
                    Ans_e(
                        rtype=CNAME,
                        name='_dmarc.example.biz.',
                        cname='_dmarc.example.net.'
                        ),
                    Ans_e(
                        rtype=CNAME,
                        name='_dmarc.example.net.',
                        cname='_dmarc.example.com.'
                        ),
                    Ans_e(
                        rtype=TXT,
                        name='_dmarc.example.com.',
                        rdata=b'v=DMARC1; p=quarantine;'
                        ),
                    ]
            elif cmiss:
                self.answer = [
                    Ans_e(
                        rtype=CNAME,
                        name='_dmarc.example.biz.',
                        cname='_dmarc.example.net.'
                        ),
                    Ans_e(
                        rtype=TXT,
                        name='_dmarc.example.biz.',
                        rdata=b'v=DMARC1; p=reject;'
                        ),
                    ]
            elif rmult:
                self.answer = [Ans_e(), Ans_e(rdata=b'v=DMARC1; p=none;')]
            elif ucase:
                self.answer = [Ans_e(name='_dmarc.EXAMPLE.biz.')]
            elif cnmok:
                self.answer = [
                    Ans_e(
                        rtype=CNAME,
                        name='_dmarc.example.biz.',
                        cname='_dmarc.example.net.'
                        ),
                    Ans_e(
                        rtype=TXT,
                        name='_dmarc.example.net.',
                        rdata=b'v=DMARC1; p=reject;'
                        ),
                    ]
            elif cnuc:
                self.answer = [
                    Ans_e(
                        rtype=CNAME,
                        name='_dmarc.EXAMPLE.biz.',
                        cname='_dmarc.example.net.'
                        ),
                    Ans_e(
                        rtype=TXT,
                        name='_dmarc.example.net.',
                        rdata=b'v=DMARC1; p=reject;'
                        ),
                    ]
            else:
                self.answer = [Ans_e()]

    class Resolver:
        # Mock dns.resolver.Resolver class.
        def query(self, domain, data_type):
            if data_type != TXT:
                raise NoAnswer
            dparts = domain.split('.')
            if len(dparts) < 3:
                raise NXDOMAIN
            if len(dparts) > 3:
                raise NoAnswer
            if dparts[0] != '_dmarc':
                raise NoAnswer
            if dparts[2] == 'info':
                raise DNSException('no internet')
            if dparts[2] == 'home':
                raise NoNameservers
            if dparts[1] != 'example' or dparts[2] != 'biz':
                raise NXDOMAIN
            self.response = Answer()
            return self
    patcher = patch('dns.resolver.Resolver', Resolver)
    return patcher


@public
def use_test_organizational_data(resources):
    # Point the organizational URL to our test data.
    filename = str(resources.enter_context(
        path('mailman.rules.tests.data', 'org_domain.txt')))
    url = 'file:///{}'.format(filename)
    return resources.enter_context(
        configuration('dmarc', org_domain_data_url=url))


class TestDMARCRules(TestCase):
    """Test organizational domain determination."""

    layer = ConfigLayer

    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        # Make sure every test has a clean cache.
        self.cache = {}
        self.resources.enter_context(
            patch('mailman.rules.dmarc.suffix_cache', self.cache))
        use_test_organizational_data(self.resources)

    def test_no_data_for_domain(self):
        self.assertEqual(
            dmarc.get_organizational_domain('sub.dom.example.nxtld'),
            'example.nxtld')

    def test_domain_with_wild_card(self):
        self.assertEqual(
            dmarc.get_organizational_domain('ssub.sub.foo.kobe.jp'),
            'sub.foo.kobe.jp')

    def test_exception_to_wild_card(self):
        self.assertEqual(
            dmarc.get_organizational_domain('ssub.sub.city.kobe.jp'),
            'city.kobe.jp')

    def test_straightforward_cname(self):
        # Test that we can recognize an answer with case mismatch in the
        # domain.
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(cnmok=True):
            self.assertTrue(rule.check(mlist, msg, {}))

    def test_uppercase_in_returned_domain(self):
        # Test that we can recognize an answer with case mismatch in the
        # domain.
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(ucase=True):
            self.assertTrue(rule.check(mlist, msg, {}))

    def test_uppercase_in_returned_cname(self):
        # Test that we can recognize an answer with case mismatch in the
        # domain.
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(cnuc=True):
            self.assertTrue(rule.check(mlist, msg, {}))

    def test_no_at_sign_in_from_address(self):
        # If there's no @ sign in the From: address, the rule can't hit.
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver():
            self.assertFalse(rule.check(mlist, msg, {}))

    def test_non_ascii_in_from_address(self):
        # Non-ascii headers with message_from_bytes can return a Header
        # objest on msg.get().
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = message_from_bytes(b"""\
From: Ann\xe9 <anne@example.com>
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver():
            self.assertFalse(rule.check(mlist, msg, {}))

    def test_dmarc_dns_exception(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.info
To: ant@example.com

""")
        mark = LogFileMark('mailman.error')
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver():
            self.assertTrue(rule.check(mlist, msg, {}))
        line = mark.readline()
        self.assertEqual(
            line[-144:],
            'DNSException: Unable to query DMARC policy for '
            'anne@example.info (_dmarc.example.info). '
            'Abstract base class shared by all dnspython exceptions.\n')

    def test_dmarc_nonameservers_exception(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.home
To: ant@example.com

""")
        mark = LogFileMark('mailman.error')
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver():
            self.assertTrue(rule.check(mlist, msg, {}))
        line = mark.readline()
        self.assertEqual(
            line[-84:],
            'DNSException: No Nameservers available for '
            'anne@example.home (_dmarc.example.home).\n')

    def test_cname_wrong_txt_name(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(cmiss=True):
            self.assertFalse(rule.check(mlist, msg, {}))

    def test_domain_with_subdomain_policy(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(rdata=b'v=DMARC1; sp=quarantine;'):
            self.assertFalse(rule.check(mlist, msg, {}))

    def test_org_domain_with_subdomain_policy(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@sub.domain.example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(rdata=b'v=DMARC1; sp=quarantine;'):
            self.assertTrue(rule.check(mlist, msg, {}))

    def test_wrong_dmarc_version(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(rdata=b'v=DMARC01; p=reject;'):
            self.assertFalse(rule.check(mlist, msg, {}))

    def test_multiple_records(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        mark = LogFileMark('mailman.error')
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(rmult=True):
            self.assertFalse(rule.check(mlist, msg, {}))
        line = mark.readline()
        self.assertEqual(
            line[-95:],
            'RRset of TXT records for _dmarc.example.biz has 2 '
            'v=DMARC1 entries; ignoring them per RFC 7849\n')

    def test_multiple_cnames(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        mark = LogFileMark('mailman.vette')
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(cmult=True):
            self.assertTrue(rule.check(mlist, msg, {}))
        line = mark.readline()
        self.assertEqual(
            line[-128:],
            'ant: DMARC lookup for anne@example.biz (_dmarc.example.biz) '
            'found p=quarantine in _dmarc.example.com. = v=DMARC1; '
            'p=quarantine;\n')

    def test_looping_cnames(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        mark = LogFileMark('mailman.vette')
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(cloop=True):
            self.assertTrue(rule.check(mlist, msg, {}))
        line = mark.readline()
        self.assertEqual(
            line[-120:],
            'ant: DMARC lookup for anne@example.biz (_dmarc.example.biz) '
            'found p=reject in _dmarc.example.org. = v=DMARC1; p=reject;\n')

    def test_no_policy(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(rdata=b'v=DMARC1; pct=100;'):
            self.assertFalse(rule.check(mlist, msg, {}))

    def test_address_in_dmarc_addresses(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        mlist.dmarc_addresses = ['anne@example.biz']
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(rdata=b'v=DMARC1; p=none;'):
            self.assertTrue(rule.check(mlist, msg, {}))

    def test_address_in_dmarc_addresses_regex(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        mlist.dmarc_addresses = ['^.*@example.biz']
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(rdata=b'v=DMARC1; p=none;'):
            self.assertTrue(rule.check(mlist, msg, {}))

    def test_address_in_dmarc_addresses_no_hit(self):
        mlist = create_list('ant@example.com')
        # Use action reject.  The rule only hits on reject and discard.
        mlist.dmarc_mitigate_action = DMARCMitigateAction.reject
        mlist.dmarc_addresses = ['anne@example.com']
        msg = mfs("""\
From: anne@example.biz
To: ant@example.com

""")
        rule = dmarc.DMARCMitigation()
        with get_dns_resolver(rdata=b'v=DMARC1; p=none;'):
            self.assertFalse(rule.check(mlist, msg, {}))

    def test_parser(self):
        data_file = str(self.resources.enter_context(
            path('mailman.rules.tests.data', 'org_domain.txt')))
        dmarc.parse_suffix_list(data_file)
        # There is no entry for example.biz because that line starts with
        # whitespace.
        self.assertNotIn('biz.example', self.cache)
        # The file had !city.kobe.jp so the flag says there's an exception.
        self.assertTrue(self.cache['jp.kobe.city'])
        # The file had *.kobe.jp so there's no exception.
        self.assertFalse(self.cache['jp.kobe.*'])


# New in Python 3.5.
try:
    from http import HTTPStatus
except ImportError:
    class HTTPStatus:
        FORBIDDEN = 403
        NOT_FOUND = 404
        OK = 200


# We need a web server to vend non-mailman: urls.
class TestableHandler(BaseHTTPRequestHandler):
    # Be quiet.
    def log_request(*args, **kws):
        pass

    log_error = log_request

    def do_GET(self):
        if self.path == '/public_suffix_list.dat':
            self.send_response(HTTPStatus.OK)
            self.send_header('Content-Type', 'UTF-8')
            self.end_headers()
            self.wfile.write(b'abc')
        elif self.path == '/internal-server-error':
            self.send_error(HTTPStatus.INTERNAL_SERVER_ERROR)
        else:
            self.send_error(HTTPStatus.NOT_FOUND)


class HTTPLayer(ConfigLayer):
    httpd = None

    @classmethod
    def setUp(cls):
        assert cls.httpd is None, 'Layer already set up'
        cls.httpd = HTTPServer(('localhost', 8180), TestableHandler)
        cls._thread = threading.Thread(target=cls.httpd.serve_forever)
        cls._thread.daemon = True
        cls._thread.start()
        wait_for_webservice('localhost', 8180)

    @classmethod
    def tearDown(cls):
        assert cls.httpd is not None, 'Layer not set up'
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls._thread.join()


class TestSuffixList(TestCase):
    layer = HTTPLayer

    def test_cached_copy_is_good(self):
        cache_path = os.path.join(config.VAR_DIR, dmarc.LOCAL_FILE_NAME)
        with open(cache_path, 'w', encoding='utf-8') as fp:
            print('xyz', end='', file=fp)
        # The cache expires a day from now.
        expires = (now() + timedelta(days=1)).timestamp()
        os.utime(cache_path, (expires, expires))
        new_path = dmarc.ensure_current_suffix_list()
        self.assertEqual(cache_path, new_path)
        with open(cache_path, 'r', encoding='utf-8') as fp:
            contents = fp.read()
        self.assertEqual(contents, 'xyz')
        self.assertEqual(os.stat(new_path).st_mtime, expires)

    @configuration(
        'dmarc',
        org_domain_data_url='http://localhost:8180/public_suffix_list.dat')
    def test_cached_copy_is_expired(self):
        cache_path = os.path.join(config.VAR_DIR, dmarc.LOCAL_FILE_NAME)
        with open(cache_path, 'w', encoding='utf-8') as fp:
            print('xyz', end='', file=fp)
        # Expire the cache file.  That way the current cached file will be
        # invalid and a new one will be downloaded.
        expires = (now() - timedelta(days=1)).timestamp()
        os.utime(cache_path, (expires, expires))
        new_path = dmarc.ensure_current_suffix_list()
        self.assertEqual(cache_path, new_path)
        with open(cache_path, 'r', encoding='utf-8') as fp:
            contents = fp.read()
        self.assertEqual(contents, 'abc')
        self.assertEqual(
            os.stat(new_path).st_mtime,
            (now() + as_timedelta(config.dmarc.cache_lifetime)).timestamp())

    @configuration(
        'dmarc',
        org_domain_data_url='http://localhost:8180/public_suffix_list.dat')
    def test_cached_copy_is_missing(self):
        cache_path = os.path.join(config.VAR_DIR, dmarc.LOCAL_FILE_NAME)
        self.assertFalse(os.path.exists(cache_path))
        new_path = dmarc.ensure_current_suffix_list()
        self.assertEqual(cache_path, new_path)
        with open(cache_path, 'r', encoding='utf-8') as fp:
            contents = fp.read()
        self.assertEqual(contents, 'abc')
        self.assertEqual(
            os.stat(new_path).st_mtime,
            (now() + as_timedelta(config.dmarc.cache_lifetime)).timestamp())

    @configuration(
        'dmarc',
        org_domain_data_url='http://localhost:8180/not-found')
    def test_cached_copy_is_missing_download_not_found(self):
        # There's no cached file and we'll get a 404 (Not Found) so
        # we'll have to fall back to our internal copy.
        cache_path = os.path.join(config.VAR_DIR, dmarc.LOCAL_FILE_NAME)
        self.assertFalse(os.path.exists(cache_path))
        new_path = dmarc.ensure_current_suffix_list()
        self.assertEqual(cache_path, new_path)
        with open(cache_path, 'r', encoding='utf-8') as fp:
            contents = fp.read()
        # The contents is *not* equal to our dummy test data, but don't tie it
        # too closely to the in-tree file contents since that might change
        # when and if we update that.
        self.assertNotEqual(contents, 'abc')
        self.assertEqual(
            os.stat(new_path).st_mtime,
            (now() + as_timedelta(config.dmarc.cache_lifetime)).timestamp())

    @configuration(
        'dmarc',
        org_domain_data_url='http://localhost:8180/not-found')
    def test_cached_copy_is_expired_download_not_found(self):
        # Because the cached copy is out of date, we try to download the new
        # version.  But that 404s so we end up continuing to use the cached
        # copy.
        cache_path = os.path.join(config.VAR_DIR, dmarc.LOCAL_FILE_NAME)
        with open(cache_path, 'w', encoding='utf-8') as fp:
            print('xyz', end='', file=fp)
        # Expire the cache file.  That way the current cached file will be
        # invalid and a new one will be downloaded.
        expires = (now() - timedelta(days=1)).timestamp()
        os.utime(cache_path, (expires, expires))
        new_path = dmarc.ensure_current_suffix_list()
        self.assertEqual(cache_path, new_path)
        with open(cache_path, 'r', encoding='utf-8') as fp:
            contents = fp.read()
        # The contents are from the cached file.
        self.assertEqual(contents, 'xyz')
        # The cached file timestamp doesn't change.
        self.assertEqual(os.stat(new_path).st_mtime, expires)

    @configuration(
        'dmarc',
        org_domain_data_url='http://localhost:8180/internal-server-error')
    def test_download_internal_server_error(self):
        # https://gitlab.com/mailman/mailman/-/issues/1140
        cache_path = os.path.join(config.VAR_DIR, dmarc.LOCAL_FILE_NAME)
        self.assertFalse(os.path.exists(cache_path))
        new_path = dmarc.ensure_current_suffix_list()
        self.assertEqual(cache_path, new_path)
