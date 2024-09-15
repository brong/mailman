# Copyright (C) 2014-2023 by the Free Software Foundation, Inc.
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

"""Test the avoid_duplicates handler."""

import unittest

from email.header import make_header
from mailman.app.lifecycle import create_list
from mailman.handlers.avoid_duplicates import AvoidDuplicates
from mailman.interfaces.member import MemberRole
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer
from zope.component import getUtility


class TestAvoidDuplicates(unittest.TestCase):
    """Test the avoid_duplicates handler."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.send_welcome_message = False
        self._manager = getUtility(IUserManager)
        anne = self._manager.create_address('anne@example.com')
        bart = self._manager.create_address('bart@example.com')
        self._anne = self._mlist.subscribe(anne, MemberRole.member)
        self._bart = self._mlist.subscribe(bart, MemberRole.member)
        self._anne.preferences.receive_list_copy = True
        self._bart.preferences.receive_list_copy = False

    def test_delete_from_cc_and_recips(self):
        # CC to member with receive_list_copy = False is dropped and member
        # is dropped from recipients.
        msg = mfs("""\
From: anne@example.com
To: ant@example.com
Subject: A subject
Cc: anne@example.com, bart@example.com, other@example.com
X-Mailman-Version: X.Y

More things to say.
""")
        msgdata = dict(recipients=set(['anne@example.com',
                                       'bart@example.com']))
        AvoidDuplicates().process(self._mlist, msg, msgdata)
        self.assertEqual(list(msgdata.get('recipients', [])),
                         ['anne@example.com'])
        # Don't test the whole message. The order in the Cc: varies with
        # Python version.
        ccs = msg.get('cc', 'bogus@example.com')
        self.assertIn('anne@example.com', ccs)
        self.assertIn('other@example.com', ccs)
        self.assertNotIn('bart@example.com', ccs)
        del msg['cc']
        self.assertMultiLineEqual(msg.as_string(), """\
From: anne@example.com
To: ant@example.com
Subject: A subject
X-Mailman-Version: X.Y

More things to say.
""")

    def test_bogus_header_folding(self):
        # We've seen messages with Cc: headers folded inside a quoted string.
        # I.e., a message composed with several Cc addresses of the form
        # 'real name (dept) <user@example.com>', the MUA quotes
        # "real name (dept)" and then folds the header between 'name' and
        # '(dept)' resulting in a header including the entry
        # '"real name\r\n (dept)" <user@example.com>' which parses incorrectly,
        # This tests that we unfold properly.
        msg = mfs("""\
From: anne@example.com
To: ant@example.com
Subject: A subject
Cc: anne@example.com, other@example.com, "real name\r
 (dept)" <user@example.com>
X-Mailman-Version: X.Y

More things to say.
""")
        msgdata = dict(recipients=set(['anne@example.com']))
        AvoidDuplicates().process(self._mlist, msg, msgdata)
        # Don't test the whole message. The order in the Cc: varies with
        # Python version.
        ccs = msg.get('cc')
        self.assertIn('anne@example.com', ccs)
        self.assertIn('other@example.com', ccs)
        self.assertIn('"real name (dept)" <user@example.com>', ccs)
        del msg['cc']
        self.assertMultiLineEqual(msg.as_string(), """\
From: anne@example.com
To: ant@example.com
Subject: A subject
X-Mailman-Version: X.Y

More things to say.
""")

    def test_unfolding_with_quoted_comma(self):
        # Ensure our unfolding doesn't break quoted commas.
        msg = mfs("""\
From: anne@example.com
To: ant@example.com
Subject: A subject
Cc: "last, first" <other@example.com>, "real name\r
 (dept)" <user@example.com>
X-Mailman-Version: X.Y

More things to say.
""")
        msgdata = dict(recipients=set(['anne@example.com']))
        AvoidDuplicates().process(self._mlist, msg, msgdata)
        # Don't test the whole message. The order in the Cc: varies with
        # Python version.
        ccs = msg.get('cc')
        self.assertIn('"last, first" <other@example.com>', ccs)
        self.assertIn('"real name (dept)" <user@example.com>', ccs)
        del msg['cc']
        self.assertMultiLineEqual(msg.as_string(), """\
From: anne@example.com
To: ant@example.com
Subject: A subject
X-Mailman-Version: X.Y

More things to say.
""")

    def test_cc_with_header_instance(self):
        # Ensure we can process header instances.
        msg = mfs("""\
From: anne@example.com
To: ant@example.com
Subject: A subject
X-Mailman-Version: X.Y

More things to say.
""")
        msgdata = dict(recipients=set(['anne@example.com']))
        msg['Cc'] = make_header(
            [('"last, first" <other@example.com>', None),
             ('"real name (dept)" <user@example.com>', None)])
        AvoidDuplicates().process(self._mlist, msg, msgdata)
        # Don't test the whole message. The order in the Cc: varies with
        # Python version.
        ccs = msg.get('cc')
        self.assertIn('"last, first" <other@example.com>', str(ccs))
        self.assertIn('"real name (dept)" <user@example.com>', str(ccs))
        del msg['cc']
        self.assertMultiLineEqual(msg.as_string(), """\
From: anne@example.com
To: ant@example.com
Subject: A subject
X-Mailman-Version: X.Y

More things to say.
""")
