# Copyright (C) 2015-2025 by the Free Software Foundation, Inc.
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

"""Test the admin-notify subcommand."""

import unittest

from click.testing import CliRunner
from mailman.app.lifecycle import create_list
from mailman.app.moderator import hold_message
from mailman.chains.hold import HoldChain
from mailman.commands.cli_notify import notify
from mailman.interfaces.mailinglist import SubscriptionPolicy
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.helpers import (
    get_queue_messages,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer
from zope.component import getUtility


class TestNotifyCommand(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist2 = create_list('bee@example.com')
        self._mlist.subscription_policy = SubscriptionPolicy.moderate
        self._mlist.unsubscription_policy = SubscriptionPolicy.moderate
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        # Hold this message.
        hold_message(self._mlist, msg, {}, 'Non-member post')
        # And a second one too.
        msg2 = mfs("""\
To: ant@example.com
From: bob@example.com
Subject: =?utf-8?q?message_2?=

""")
        hold_message(self._mlist, msg2, {}, 'Some other reason')
        usermanager = getUtility(IUserManager)
        submanager = ISubscriptionManager(self._mlist)
        # Generate held subscription.
        usera = usermanager.make_user('anne@example.com')
        usera.addresses[0].verified_on = usera.addresses[0].registered_on
        usera.preferred_address = usera.addresses[0]
        submanager.register(usera)
        # Generate a held unsubscription.
        userb = usermanager.make_user('bob@example.com')
        userb.addresses[0].verified_on = userb.addresses[0].registered_on
        userb.preferred_address = userb.addresses[0]
        submanager.register(userb, pre_verified=True, pre_confirmed=True,
                            pre_approved=True)
        submanager.unregister(userb)
        self._command = CliRunner()

    def test_notify_dry_run_verbose(self):
        # Clear messages from setup.
        get_queue_messages('virgin')
        result = self._command.invoke(notify,
                                      ('-n', '-v', '-l', 'ant@example.com'))
        self.assertMultiLineEqual(result.output, """\
The ant@example.com list has 4 moderation requests waiting.
""")
        get_queue_messages('virgin', expected_count=0)

    def test_notify_dry_run_verbose_list_id(self):
        # Clear messages from setup.
        get_queue_messages('virgin')
        result = self._command.invoke(notify,
                                      ('-n', '-v', '-l', 'ant.example.com'))
        self.assertMultiLineEqual(result.output, """\
The ant@example.com list has 4 moderation requests waiting.
""")
        get_queue_messages('virgin', expected_count=0)

    def test_notify_one_list(self):
        # Clear messages from setup.
        get_queue_messages('virgin')
        result = self._command.invoke(notify, ('-v', '-l', 'ant@example.com'))
        self.assertMultiLineEqual(result.output, """\
The ant@example.com list has 4 moderation requests waiting.
""")
        msg = get_queue_messages('virgin', expected_count=1)[0].msg
        self.assertMultiLineEqual(msg.get_payload(), """\
The ant@example.com list has 4 moderation requests waiting.


Held Subscriptions:
    User: anne@example.com

Held Unsubscriptions:
    User: bob@example.com

Held Messages:
    Sender: anne@example.com
    Subject: message 1
    Reason: Non-member post

    Sender: bob@example.com
    Subject: message 2
    Reason: Some other reason


Please attend to this at your earliest convenience.""")

    def test_notify_bogus_list(self):
        result = self._command.invoke(notify,
                                      ('-v', '-l', 'bogus@example.com'))
        self.assertMultiLineEqual(result.output, """\
No such list found: bogus@example.com
""")

    def test_notify_all_lists(self):
        # Clear messages from setup.
        get_queue_messages('virgin')
        result = self._command.invoke(notify, ('-v',))
        self.assertMultiLineEqual(result.output, """\
The ant@example.com list has 4 moderation requests waiting.
The bee@example.com list has 0 moderation requests waiting.
""")
        msg = get_queue_messages('virgin', expected_count=1)[0].msg
        self.assertMultiLineEqual(msg.get_payload(), """\
The ant@example.com list has 4 moderation requests waiting.


Held Subscriptions:
    User: anne@example.com

Held Unsubscriptions:
    User: bob@example.com

Held Messages:
    Sender: anne@example.com
    Subject: message 1
    Reason: Non-member post

    Sender: bob@example.com
    Subject: message 2
    Reason: Some other reason


Please attend to this at your earliest convenience.""")


class TestBogusRFC2047(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: =?gb2312?q?=E9=FF_message_1?=

""")
        # Hold this message.
        hold_message(self._mlist, msg, {}, 'Non-member post')
        self._command = CliRunner()

    def test_bogus_rfc2047(self):
        # Clear messages from setup.
        get_queue_messages('virgin')
        result = self._command.invoke(notify, ('-v',))
        self.assertMultiLineEqual(result.output, """\
The ant@example.com list has 1 moderation requests waiting.
""")
        msg = get_queue_messages('virgin', expected_count=1)[0].msg
        self.assertMultiLineEqual(msg.get_payload(), """\
The ant@example.com list has 1 moderation requests waiting.


Held Messages:
    Sender: anne@example.com
    Subject: =?gb2312?q?=E9=FF_message_1?=
    Reason: Non-member post


Please attend to this at your earliest convenience.""")


class TestChainsHold(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        # Hold this message using chains.hold. Supply moderation_reasons and
        # set fromusenet to suppress user notice.
        msgdata = {'moderation_reasons': ['Non-member post'],
                   'fromusenet': True}
        # Suppress owner notice too.
        self._mlist.admin_immed_notify = False
        HoldChain()._process(self._mlist, msg, msgdata)
        self._command = CliRunner()

    def test_notify_with_hold_from_hold_chain(self):
        # Clear messages from setup.
        get_queue_messages('virgin')
        result = self._command.invoke(notify, ('-v',))
        self.assertMultiLineEqual(result.output, """\
The ant@example.com list has 1 moderation requests waiting.
""")
        msg = get_queue_messages('virgin', expected_count=1)[0].msg
        self.assertMultiLineEqual(msg.get_payload(), """\
The ant@example.com list has 1 moderation requests waiting.


Held Messages:
    Sender: anne@example.com
    Subject: message 1
    Reason: Non-member post


Please attend to this at your earliest convenience.""")
