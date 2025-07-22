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

"""Test various aspects of email delivery."""

import os
import shutil
import tempfile
import unittest

from email.header import make_header
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.interfaces.mailinglist import Personalization
from mailman.interfaces.template import ITemplateManager
from mailman.mta.bulk import BulkDelivery
from mailman.mta.deliver import Deliver
from mailman.testing.helpers import (
    LogFileMark,
    specialized_message_from_string as mfs,
    subscribe,
)
from mailman.testing.layers import ConfigLayer, SMTPLayer
from mailman.utilities.modules import find_name
from unittest.mock import patch
from zope.component import getUtility


# Global test capture.
_deliveries = []


# Derive this from the default individual delivery class.  The point being
# that we don't want to *actually* attempt delivery of the message to the MTA,
# we just want to capture the messages and metadata dictionaries for
# inspection.
class DeliverTester(Deliver):
    def _deliver_to_recipients(self, mlist, msg, msgdata, recipients):
        _deliveries.append((mlist, msg, msgdata, recipients))
        # Nothing gets refused.
        return []


# We also need to test that BulkDeliver does decoration.
class BulkDeliverTester(BulkDelivery):
    def _deliver_to_recipients(self, mlist, msg, msgdata, recipients):
        _deliveries.append((mlist, msg, msgdata, recipients))
        # Nothing gets refused.
        return []


class TestIndividualDelivery(unittest.TestCase):
    """Test personalized delivery details."""

    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._mlist.personalize = Personalization.individual
        # Make Anne a member of this mailing list.
        self._anne = subscribe(self._mlist, 'Anne', email='anne@example.org')
        # Clear out any results from the previous test.
        del _deliveries[:]
        self._msg = mfs("""\
From: anne@example.org
To: test@example.com
Subject: test

""")
        # Set up a personalized footer for decoration.
        self._template_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._template_dir)
        path = os.path.join(self._template_dir,
                            'site', 'en', 'member-footer.txt')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w', encoding='utf-8') as fp:
            print("""\
address  : $user_address
delivered: $user_delivered_to
language : $user_language
name     : $user_name
""", file=fp)
        config.push('templates', """
        [paths.testing]
        template_dir: {}
        """.format(self._template_dir))
        self.addCleanup(config.pop, 'templates')
        getUtility(ITemplateManager).set(
            'list:member:regular:footer', self._mlist.list_id,
            'mailman:///member-footer.txt')

    def tearDown(self):
        # Free global references.
        del _deliveries[:]

    def test_member_key(self):
        # 'personalize' should end up in the metadata dictionary so that
        # $user_* keys in headers and footers get filled in correctly.
        msgdata = dict(recipients=['anne@example.org'])
        agent = DeliverTester()
        refused = agent.deliver(self._mlist, self._msg, msgdata)
        self.assertEqual(len(refused), 0)
        self.assertEqual(len(_deliveries), 1)
        _mlist, _msg, _msgdata, _recipients = _deliveries[0]
        member = _msgdata.get('member')
        self.assertEqual(member, self._anne)

    def test_arc_sign_called_individual(self):
        # Delivery with arc_sign enabled should call arc_sign.
        msgdata = dict(recipients=['anne@example.org'])
        keyfile = tempfile.NamedTemporaryFile(delete=True)
        keyfile.write(b"""-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDkHlOQoBTzWRiGs5V6NpP3idY6Wk08a5qhdR6wy5bdOKb2jLQi
Y/J16JYi0Qvx/byYzCNb3W91y3FutACDfzwQ/BC/e/8uBsCR+yz1Lxj+PL6lHvqM
KrM3rG4hstT5QjvHO9PzoxZyVYLzBfO2EeC3Ip3G+2kryOTIKT+l/K4w3QIDAQAB
AoGAH0cxOhFZDgzXWhDhnAJDw5s4roOXN4OhjiXa8W7Y3rhX3FJqmJSPuC8N9vQm
6SVbaLAE4SG5mLMueHlh4KXffEpuLEiNp9Ss3O4YfLiQpbRqE7Tm5SxKjvvQoZZe
zHorimOaChRL2it47iuWxzxSiRMv4c+j70GiWdxXnxe4UoECQQDzJB/0U58W7RZy
6enGVj2kWF732CoWFZWzi1FicudrBFoy63QwcowpoCazKtvZGMNlPWnC7x/6o8Gc
uSe0ga2xAkEA8C7PipPm1/1fTRQvj1o/dDmZp243044ZNyxjg+/OPN0oWCbXIGxy
WvmZbXriOWoSALJTjExEgraHEgnXssuk7QJBALl5ICsYMu6hMxO73gnfNayNgPxd
WFV6Z7ULnKyV7HSVYF0hgYOHjeYe9gaMtiJYoo0zGN+L3AAtNP9huqkWlzECQE1a
licIeVlo1e+qJ6Mgqr0Q7Aa7falZ448ccbSFYEPD6oFxiOl9Y9se9iYHZKKfIcst
o7DUw1/hz2Ck4N5JrgUCQQCyKveNvjzkkd8HjYs0SwM0fPjK16//5qDZ2UiDGnOe
uEzxBDAr518Z8VFbR41in3W4Y3yCDgQlLlcETrS+zYcL
-----END RSA PRIVATE KEY-----
""")
        keyfile.flush()
        config.push('arc', """
        [ARC]
        enabled: yes
        authserv_id: lists.example.org
        selector: dummy
        domain: example.org
        sig_headers: mime-version, date, from, to, subject
        privkey: %s
        """ % (keyfile.name))
        with patch.object(DeliverTester, 'arc_sign') as mock:
            DeliverTester().deliver(self._mlist, self._msg, msgdata)
        mock.assert_called_once()
        config.pop('arc')

    def test_decoration(self):
        msgdata = dict(recipients=['anne@example.org'])
        agent = DeliverTester()
        refused = agent.deliver(self._mlist, self._msg, msgdata)
        self.assertEqual(len(refused), 0)
        self.assertEqual(len(_deliveries), 1)
        _mlist, _msg, _msgdata, _recipients = _deliveries[0]
        self.assertMultiLineEqual(_msg.as_string(), """\
From: anne@example.org
To: test@example.com
Subject: test
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"
Content-Transfer-Encoding: 7bit


address  : anne@example.org
delivered: anne@example.org
language : English (USA)
name     : Anne Person

""")

    def test_full_personalization(self):
        self._mlist.personalize = Personalization.full
        msgdata = dict(recipients=['anne@example.org'])
        agent = DeliverTester()
        refused = agent.deliver(self._mlist, self._msg, msgdata)
        self.assertEqual(len(refused), 0)
        self.assertEqual(len(_deliveries), 1)
        _mlist, _msg, _msgdata, _recipients = _deliveries[0]
        self.assertMultiLineEqual(_msg.as_string(), """\
From: anne@example.org
To: Anne Person <anne@example.org>
Subject: test
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"
Content-Transfer-Encoding: 7bit


address  : anne@example.org
delivered: anne@example.org
language : English (USA)
name     : Anne Person

""")

    def test_full_personalization_no_to(self):
        self._mlist.personalize = Personalization.full
        del self._msg['to']
        msgdata = dict(recipients=['anne@example.org'])
        agent = DeliverTester()
        refused = agent.deliver(self._mlist, self._msg, msgdata)
        self.assertEqual(len(refused), 0)
        self.assertEqual(len(_deliveries), 1)
        _mlist, _msg, _msgdata, _recipients = _deliveries[0]
        self.assertMultiLineEqual(_msg.as_string(), """\
From: anne@example.org
Subject: test
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"
Content-Transfer-Encoding: 7bit
To: Anne Person <anne@example.org>


address  : anne@example.org
delivered: anne@example.org
language : English (USA)
name     : Anne Person

""")

    def test_full_personalization_no_user(self):
        self._mlist.personalize = Personalization.full
        msgdata = dict(recipients=['bart@example.org'])
        agent = DeliverTester()
        refused = agent.deliver(self._mlist, self._msg, msgdata)
        self.assertEqual(len(refused), 0)
        self.assertEqual(len(_deliveries), 1)
        _mlist, _msg, _msgdata, _recipients = _deliveries[0]
        self.assertMultiLineEqual(_msg.as_string(), """\
From: anne@example.org
To: bart@example.org
Subject: test
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"
Content-Transfer-Encoding: 7bit


address  : $user_address
delivered: $user_delivered_to
language : $user_language
name     : $user_name

""")

    def test_full_personalization_no_user_no_to(self):
        self._mlist.personalize = Personalization.full
        del self._msg['to']
        msgdata = dict(recipients=['bart@example.org'])
        agent = DeliverTester()
        refused = agent.deliver(self._mlist, self._msg, msgdata)
        self.assertEqual(len(refused), 0)
        self.assertEqual(len(_deliveries), 1)
        _mlist, _msg, _msgdata, _recipients = _deliveries[0]
        self.assertMultiLineEqual(_msg.as_string(), """\
From: anne@example.org
Subject: test
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"
Content-Transfer-Encoding: 7bit
To: bart@example.org


address  : $user_address
delivered: $user_delivered_to
language : $user_language
name     : $user_name

""")


class TestBulkDelivery(unittest.TestCase):
    """Test bulk delivery decoration."""

    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._mlist = create_list('test@example.com')
        # Make Anne a member of this mailing list.
        self._anne = subscribe(self._mlist, 'Anne', email='anne@example.org')
        # Clear out any results from the previous test.
        del _deliveries[:]
        self._msg = mfs("""\
From: anne@example.org
To: test@example.com
Subject: test

""")
        # Set up a footer for decoration.
        self._template_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self._template_dir)
        path = os.path.join(self._template_dir,
                            'site', 'en', 'member-footer.txt')
        os.makedirs(os.path.dirname(path))
        with open(path, 'w', encoding='utf-8') as fp:
            print("""\
list: $display_name
Footer
""", file=fp)
        config.push('templates', """
        [paths.testing]
        template_dir: {}
        """.format(self._template_dir))
        self.addCleanup(config.pop, 'templates')
        getUtility(ITemplateManager).set(
            'list:member:regular:footer', self._mlist.list_id,
            'mailman:///member-footer.txt')

    def tearDown(self):
        # Free global references.
        del _deliveries[:]

    def test_bulk_decoration(self):
        msgdata = dict(recipients=['anne@example.org'])
        agent = BulkDeliverTester()
        refused = agent.deliver(self._mlist, self._msg, msgdata)
        self.assertEqual(len(refused), 0)
        self.assertEqual(len(_deliveries), 1)
        _mlist, _msg, _msgdata, _recipients = _deliveries[0]
        self.assertMultiLineEqual(_msg.as_string(), """\
From: anne@example.org
To: test@example.com
Subject: test
MIME-Version: 1.0
Content-Type: text/plain; charset="us-ascii"
Content-Transfer-Encoding: 7bit


list: Test
Footer

""")

    def test_arc_sign_called_bulk(self):
        # Delivery with arc_sign enabled should call arc_sign.
        msgdata = dict(recipients=['anne@example.org'])
        keyfile = tempfile.NamedTemporaryFile(delete=True)
        keyfile.write(b"""-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDkHlOQoBTzWRiGs5V6NpP3idY6Wk08a5qhdR6wy5bdOKb2jLQi
Y/J16JYi0Qvx/byYzCNb3W91y3FutACDfzwQ/BC/e/8uBsCR+yz1Lxj+PL6lHvqM
KrM3rG4hstT5QjvHO9PzoxZyVYLzBfO2EeC3Ip3G+2kryOTIKT+l/K4w3QIDAQAB
AoGAH0cxOhFZDgzXWhDhnAJDw5s4roOXN4OhjiXa8W7Y3rhX3FJqmJSPuC8N9vQm
6SVbaLAE4SG5mLMueHlh4KXffEpuLEiNp9Ss3O4YfLiQpbRqE7Tm5SxKjvvQoZZe
zHorimOaChRL2it47iuWxzxSiRMv4c+j70GiWdxXnxe4UoECQQDzJB/0U58W7RZy
6enGVj2kWF732CoWFZWzi1FicudrBFoy63QwcowpoCazKtvZGMNlPWnC7x/6o8Gc
uSe0ga2xAkEA8C7PipPm1/1fTRQvj1o/dDmZp243044ZNyxjg+/OPN0oWCbXIGxy
WvmZbXriOWoSALJTjExEgraHEgnXssuk7QJBALl5ICsYMu6hMxO73gnfNayNgPxd
WFV6Z7ULnKyV7HSVYF0hgYOHjeYe9gaMtiJYoo0zGN+L3AAtNP9huqkWlzECQE1a
licIeVlo1e+qJ6Mgqr0Q7Aa7falZ448ccbSFYEPD6oFxiOl9Y9se9iYHZKKfIcst
o7DUw1/hz2Ck4N5JrgUCQQCyKveNvjzkkd8HjYs0SwM0fPjK16//5qDZ2UiDGnOe
uEzxBDAr518Z8VFbR41in3W4Y3yCDgQlLlcETrS+zYcL
-----END RSA PRIVATE KEY-----
""")
        keyfile.flush()
        config.push('arc', """
        [ARC]
        enabled: yes
        authserv_id: lists.example.org
        selector: dummy
        domain: example.org
        sig_headers: mime-version, date, from, to, subject
        privkey: %s
        """ % (keyfile.name))
        with patch.object(BulkDeliverTester, 'arc_sign') as mock:
            BulkDeliverTester().deliver(self._mlist, self._msg, msgdata)
        mock.assert_called_with(self._mlist, self._msg, msgdata)
        config.pop('arc')


class TestCloseAfterDelivery(unittest.TestCase):
    """Test that connections close after delivery."""

    layer = SMTPLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        # Set personalized delivery.
        self._mlist.personalize = Personalization.individual
        # Make Anne a member of this mailing list.
        self._anne = subscribe(self._mlist, 'Anne', email='anne@example.org')
        self._msg = mfs("""\
From: anne@example.org
To: test@example.com
Subject: test

""")
        self._deliverer = find_name(config.mta.outgoing)
        # Set the maximum transactions per connection.
        config.push('maxtrans', """
        [mta]
        max_sessions_per_connection: 3
        """)
        self.addCleanup(config.pop, 'maxtrans')

    def test_two_messages(self):
        msgdata = dict(recipients=['anne@example.org'])
        # Send a message.
        self._deliverer(self._mlist, self._msg, msgdata)
        # We should have made one SMTP connection.
        self.assertEqual(SMTPLayer.smtpd.get_connection_count(), 1)
        # Send a second message.
        msgdata = dict(recipients=['bart@example.org'])
        self._deliverer(self._mlist, self._msg, msgdata)
        # This should result in a second connection.
        self.assertEqual(SMTPLayer.smtpd.get_connection_count(), 2)

    def test_one_message_two_recip(self):
        msgdata = dict(recipients=['anne@example.org', 'bart@example.org'])
        # Send the message.
        self._deliverer(self._mlist, self._msg, msgdata)
        # Sending to 2 recips sends 2 messages because of personalization.
        # That's fewer than max_sessions_per_connection so there's only
        # one connection.
        self.assertEqual(SMTPLayer.smtpd.get_connection_count(), 1)

    def test_one_message_four_recip(self):
        msgdata = dict(recipients=['anne@example.org',
                                   'bart@example.org',
                                   'cate@example.com',
                                   'dave@example.com'])
        # Send the message.
        self._deliverer(self._mlist, self._msg, msgdata)
        # Since max_sessions_per_connection is 3, sending 4 personalized
        # messages creates 2 connections.
        self.assertEqual(SMTPLayer.smtpd.get_connection_count(), 2)


class TestDeliveryLogging(unittest.TestCase):
    """Test that logging doesn't split on folded Message-IDs."""

    layer = SMTPLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        # Make Anne a member of this mailing list.
        self._anne = subscribe(self._mlist, 'Anne', email='anne@example.org')
        self._msg = mfs("""\
From: anne@example.org
To: test@example.com
Subject: test
Message-ID:
 <AM6PR09MB347488BB9A684F6A23A1D375966C9@AM6PR09MB34.eurprd09.prod.outlook.com>

""")
        self._deliverer = find_name(config.mta.outgoing)

    def test_logging_with_folded_messageid(self):
        # Test that folded Message-ID header doesn't fold the log messages.
        mark = LogFileMark('mailman.smtp')
        msgdata = dict(recipients=['anne@example.org'], to_list=True)
        self._deliverer(self._mlist, self._msg, msgdata)
        logs = mark.read()
        self.assertRegex(logs, r' \(\d+\) <AM6PR09MB347488.*smtp')
        self.assertRegex(logs, r' \(\d+\) <AM6PR09MB347488.*post')

    def test_logging_with_header_instance(self):
        msgdata = dict(recipients=['test@example.com', 'bart@example.org'])
        self._mlist.personalize = Personalization.none
        msgdata['recipients'] = ['test@example.com', 'bart@example.org']
        self._msg['CC'] = make_header([('bart@example.org', None)])
        config.push('logging', """
        [logging.smtp]
        success: post for $recip recips
        """)
        self.addCleanup(config.pop, 'logging')
        mark = LogFileMark('mailman.smtp')
        self._deliverer(self._mlist, self._msg, msgdata)
        self.assertIn(
            'post for test@example.com,bart@example.org recips',
            mark.read())
