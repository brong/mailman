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

"""Test the send-digests subcommand."""

import os
import unittest

from click.testing import CliRunner
from datetime import timedelta
from mailman.app.lifecycle import create_list
from mailman.commands.cli_digests import digests
from mailman.config import config
from mailman.interfaces.digests import DigestFrequency
from mailman.interfaces.member import DeliveryMode
from mailman.runners.digest import DigestRunner
from mailman.testing.helpers import (
    get_queue_messages,
    make_testable_runner,
    specialized_message_from_string as mfs,
    subscribe,
)
from mailman.testing.layers import ConfigLayer
from mailman.utilities.datetime import now as right_now


class TestSendDigests(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.digests_enabled = True
        self._mlist.digest_size_threshold = 100000
        self._mlist.send_welcome_message = False
        self._command = CliRunner()
        self._handler = config.handlers['to-digest']
        self._runner = make_testable_runner(DigestRunner, 'digest')
        # The mailing list needs at least one digest recipient.
        member = subscribe(self._mlist, 'Anne')
        member.preferences.delivery_mode = DeliveryMode.plaintext_digests

    def test_send_one_digest_by_list_id(self):
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # There are no digests already being sent, but the ant mailing list
        # does have a digest mbox collecting messages.
        get_queue_messages('digest', expected_count=0)
        mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        self._command.invoke(digests, ('-s', '-l', 'ant.example.com'))
        self._runner.run()
        # Now, there's no digest mbox and there's a plaintext digest in the
        # outgoing queue.
        self.assertFalse(os.path.exists(mailbox_path))
        items = get_queue_messages('virgin', expected_count=1)
        digest_contents = str(items[0].msg)
        self.assertIn('Subject: message 1', digest_contents)
        self.assertIn('Subject: message 2', digest_contents)

    def test_send_one_digest_by_fqdn_listname(self):
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # There are no digests already being sent, but the ant mailing list
        # does have a digest mbox collecting messages.
        get_queue_messages('digest', expected_count=0)
        mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        self._command.invoke(digests, ('-s', '-l', 'ant@example.com'))
        self._runner.run()
        # Now, there's no digest mbox and there's a plaintext digest in the
        # outgoing queue.
        self.assertFalse(os.path.exists(mailbox_path))
        items = get_queue_messages('virgin', expected_count=1)
        digest_contents = str(items[0].msg)
        self.assertIn('Subject: message 1', digest_contents)
        self.assertIn('Subject: message 2', digest_contents)

    def test_send_one_digest_to_missing_list_id(self):
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # There are no digests already being sent, but the ant mailing list
        # does have a digest mbox collecting messages.
        get_queue_messages('digest', expected_count=0)
        mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        result = self._command.invoke(digests, ('-s', '-l', 'bee.example.com'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.output,
            'No such list found: bee.example.com\n')
        self._runner.run()
        # And no digest was prepared.
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        get_queue_messages('virgin', expected_count=0)

    def test_send_one_digest_to_missing_fqdn_listname(self):
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # There are no digests already being sent, but the ant mailing list
        # does have a digest mbox collecting messages.
        get_queue_messages('digest', expected_count=0)
        mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        result = self._command.invoke(digests, ('-s', '-l', 'bee@example.com'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.output,
            'No such list found: bee@example.com\n')
        self._runner.run()
        # And no digest was prepared.
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        get_queue_messages('virgin', expected_count=0)

    def test_send_digest_to_one_missing_and_one_existing_list(self):
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # There are no digests already being sent, but the ant mailing list
        # does have a digest mbox collecting messages.
        get_queue_messages('digest', expected_count=0)
        mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        result = self._command.invoke(
            digests,
            ('-s', '-l', 'ant.example.com', '-l', 'bee.example.com'))
        self.assertEqual(result.exit_code, 0)
        self.assertEqual(
            result.output,
            'No such list found: bee.example.com\n')
        self._runner.run()
        # But ant's digest was still prepared.
        self.assertFalse(os.path.exists(mailbox_path))
        items = get_queue_messages('virgin', expected_count=1)
        digest_contents = str(items[0].msg)
        self.assertIn('Subject: message 1', digest_contents)
        self.assertIn('Subject: message 2', digest_contents)

    def test_send_digests_for_two_lists(self):
        # Populate ant's digest.
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # Create the second list.
        bee = create_list('bee@example.com')
        bee.digests_enabled = True
        bee.digest_size_threshold = 100000
        bee.send_welcome_message = False
        member = subscribe(bee, 'Bart')
        member.preferences.delivery_mode = DeliveryMode.plaintext_digests
        # Populate bee's digest.
        msg = mfs("""\
To: bee@example.com
From: bart@example.com
Subject: message 3

""")
        self._handler.process(bee, msg, {})
        del msg['subject']
        msg['subject'] = 'message 4'
        self._handler.process(bee, msg, {})
        # There are no digests for either list already being sent, but the
        # mailing lists do have a digest mbox collecting messages.
        ant_mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(ant_mailbox_path), 0)
        # Check bee's digest.
        bee_mailbox_path = os.path.join(bee.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(bee_mailbox_path), 0)
        # Both.
        get_queue_messages('digest', expected_count=0)
        # Process both list's digests.
        self._command.invoke(
            digests, ('-s', '-l', 'ant.example.com', '-l', 'bee@example.com'))
        self._runner.run()
        # Now, neither list has a digest mbox and but there are plaintext
        # digest in the outgoing queue for both.
        self.assertFalse(os.path.exists(ant_mailbox_path))
        self.assertFalse(os.path.exists(bee_mailbox_path))
        items = get_queue_messages('virgin', expected_count=2)
        # Figure out which digest is going to ant and which to bee.
        if items[0].msg['to'] == 'ant@example.com':
            ant = items[0].msg
            bee = items[1].msg
        else:
            assert items[0].msg['to'] == 'bee@example.com'
            ant = items[1].msg
            bee = items[0].msg
        # Check ant's digest.
        digest_contents = str(ant)
        self.assertIn('Subject: message 1', digest_contents)
        self.assertIn('Subject: message 2', digest_contents)
        # Check bee's digest.
        digest_contents = str(bee)
        self.assertIn('Subject: message 3', digest_contents)
        self.assertIn('Subject: message 4', digest_contents)

    def test_send_digests_for_all_lists(self):
        # Populate ant's digest.
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # Create the second list.
        bee = create_list('bee@example.com')
        bee.digests_enabled = True
        bee.digest_size_threshold = 100000
        bee.send_welcome_message = False
        member = subscribe(bee, 'Bart')
        member.preferences.delivery_mode = DeliveryMode.plaintext_digests
        # Populate bee's digest.
        msg = mfs("""\
To: bee@example.com
From: bart@example.com
Subject: message 3

""")
        self._handler.process(bee, msg, {})
        del msg['subject']
        msg['subject'] = 'message 4'
        self._handler.process(bee, msg, {})
        # There are no digests for either list already being sent, but the
        # mailing lists do have a digest mbox collecting messages.
        ant_mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(ant_mailbox_path), 0)
        # Check bee's digest.
        bee_mailbox_path = os.path.join(bee.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(bee_mailbox_path), 0)
        # Both.
        get_queue_messages('digest', expected_count=0)
        # Process all mailing list digests by not setting any arguments.
        self._command.invoke(digests, ('-s',))
        self._runner.run()
        # Now, neither list has a digest mbox and but there are plaintext
        # digest in the outgoing queue for both.
        self.assertFalse(os.path.exists(ant_mailbox_path))
        self.assertFalse(os.path.exists(bee_mailbox_path))
        items = get_queue_messages('virgin', expected_count=2)
        # Figure out which digest is going to ant and which to bee.
        if items[0].msg['to'] == 'ant@example.com':
            ant = items[0].msg
            bee = items[1].msg
        else:
            assert items[0].msg['to'] == 'bee@example.com'
            ant = items[1].msg
            bee = items[0].msg
        # Check ant's digest.
        digest_contents = str(ant)
        self.assertIn('Subject: message 1', digest_contents)
        self.assertIn('Subject: message 2', digest_contents)
        # Check bee's digest.
        digest_contents = str(bee)
        self.assertIn('Subject: message 3', digest_contents)
        self.assertIn('Subject: message 4', digest_contents)

    def test_send_no_digest_ready(self):
        # If no messages have been sent through the mailing list, no digest
        # can be sent.
        mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertFalse(os.path.exists(mailbox_path))
        self._command.invoke(digests, ('-s', '-l', 'ant.example.com'))
        self._runner.run()
        get_queue_messages('virgin', expected_count=0)

    def test_bump_before_send(self):
        self._mlist.digest_volume_frequency = DigestFrequency.monthly
        self._mlist.volume = 7
        self._mlist.next_digest_number = 4
        self._mlist.digest_last_sent_at = right_now() + timedelta(
            days=-32)
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        self._command.invoke(
            digests, ('-s', '--bump', '-l', 'ant.example.com'))
        self._runner.run()
        # The volume is 8 and the digest number is 2 because a digest was sent
        # after the volume/number was bumped.
        self.assertEqual(self._mlist.volume, 8)
        self.assertEqual(self._mlist.next_digest_number, 2)
        self.assertEqual(self._mlist.digest_last_sent_at, right_now())
        items = get_queue_messages('virgin', expected_count=1)
        self.assertEqual(items[0].msg['subject'], 'Ant Digest, Vol 8, Issue 1')

    def test_send_periodic_one_by_listid(self):
        # Test sending digest using --periodic.
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # There are no digests already being sent, but the ant mailing list
        # does have a digest mbox collecting messages.
        get_queue_messages('digest', expected_count=0)
        mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        self._command.invoke(digests, ('-p', '-v', '-l', 'ant.example.com'))
        self._runner.run()
        # Now, there's no digest mbox and there's a plaintext digest in the
        # outgoing queue.
        self.assertFalse(os.path.exists(mailbox_path))
        items = get_queue_messages('virgin', expected_count=1)
        digest_contents = str(items[0].msg)
        self.assertIn('Subject: message 1', digest_contents)
        self.assertIn('Subject: message 2', digest_contents)

    def test_send_periodic_set_false(self):
        # Test sending digest --periodic when the only Mailing List's
        # digest_send_periodic is set to false.
        # Test sending digest using --periodic.
        self._mlist.digest_send_periodic = False
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        # There are no digests already being sent, but the ant mailing list
        # does have a digest mbox collecting messages.
        get_queue_messages('digest', expected_count=0)
        mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(mailbox_path), 0)
        self._command.invoke(digests, ('-p', '-l', 'ant.example.com'))
        self._runner.run()
        # Now, even though the digest command was run, the mailbox should still
        # be there.
        self.assertTrue(os.path.exists(mailbox_path))
        get_queue_messages('virgin', expected_count=0)

    def test_send_periodic_two_lists_one_set_false(self):
        # Test digests --periodic when one of the two lists has
        # digest_send_periodic set to false.
        # Populate ant's digest.
        self._mlist.digest_send_periodic = False
        msg = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: message 1

""")
        self._handler.process(self._mlist, msg, {})
        del msg['subject']
        msg['subject'] = 'message 2'
        self._handler.process(self._mlist, msg, {})
        # Create the second list.
        bee = create_list('bee@example.com')
        bee.digests_enabled = True
        bee.digest_size_threshold = 100000
        bee.send_welcome_message = False
        member = subscribe(bee, 'Bart')
        member.preferences.delivery_mode = DeliveryMode.plaintext_digests
        # Populate bee's digest.
        msg = mfs("""\
To: bee@example.com
From: bart@example.com
Subject: message 3

""")
        self._handler.process(bee, msg, {})
        del msg['subject']
        msg['subject'] = 'message 4'
        self._handler.process(bee, msg, {})
        # There are no digests for either list already being sent, but the
        # mailing lists do have a digest mbox collecting messages.
        ant_mailbox_path = os.path.join(self._mlist.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(ant_mailbox_path), 0)
        # Check bee's digest.
        bee_mailbox_path = os.path.join(bee.data_path, 'digest.mmdf')
        self.assertGreater(os.path.getsize(bee_mailbox_path), 0)
        # Both.
        get_queue_messages('digest', expected_count=0)
        # Process both list's digests.
        self._command.invoke(
            digests, ('-p', '-l', 'ant.example.com', '-l', 'bee@example.com'))
        self._runner.run()
        # Now, ant should still have it's mailbox file, but bee shouldn't.
        # Also, bee's message should be in the outgoing queue.
        self.assertTrue(os.path.exists(ant_mailbox_path))
        self.assertFalse(os.path.exists(bee_mailbox_path))
        items = get_queue_messages('virgin', expected_count=1)
        # Figure out which digest is going to ant and which to bee.
        assert items[0].msg['to'] == 'bee@example.com'
        # Check bee's digest.
        digest_contents = str(items[0].msg)
        self.assertIn('Subject: message 3', digest_contents)
        self.assertIn('Subject: message 4', digest_contents)


class TestBumpVolume(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.digest_volume_frequency = DigestFrequency.monthly
        self._mlist.volume = 7
        self._mlist.next_digest_number = 4
        self.right_now = right_now()
        self._command = CliRunner()

    def test_bump_one_list(self):
        self._mlist.digest_last_sent_at = self.right_now + timedelta(
            days=-32)
        self._command.invoke(digests, ('-b', '-l', 'ant.example.com'))
        self.assertEqual(self._mlist.volume, 8)
        self.assertEqual(self._mlist.next_digest_number, 1)
        self.assertEqual(self._mlist.digest_last_sent_at, self.right_now)

    def test_bump_two_lists(self):
        self._mlist.digest_last_sent_at = self.right_now + timedelta(
            days=-32)
        # Create the second list.
        bee = create_list('bee@example.com')
        bee.digest_volume_frequency = DigestFrequency.monthly
        bee.volume = 7
        bee.next_digest_number = 4
        bee.digest_last_sent_at = self.right_now + timedelta(
            days=-32)
        self._command.invoke(
            digests, ('-b', '-l', 'ant.example.com', '-l', 'bee.example.com'))
        self.assertEqual(self._mlist.volume, 8)
        self.assertEqual(self._mlist.next_digest_number, 1)
        self.assertEqual(self._mlist.digest_last_sent_at, self.right_now)

    def test_bump_verbose(self):
        result = self._command.invoke(
            digests, ('-v', '-b', '-l', 'ant.example.com'))
        self.assertMultiLineEqual(result.output, """\
ant.example.com is at volume 7, number 4
ant.example.com bumped to volume 7, number 5
""")

    def test_send_verbose(self):
        result = self._command.invoke(
            digests, ('-v', '-s', '-n', '-l', 'ant.example.com'))
        self.assertMultiLineEqual(result.output, """\
ant.example.com sent volume 7, number 4
""")


class TestDigestCommand(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._command = CliRunner()

    def test_send_and_periodic_options_exclusive(self):
        result = self._command.invoke(digests,
                                      ('-s', '-p', '-l', 'ant@example.com'))
        self.assertEqual(result.exit_code, 1)
        self.assertMultiLineEqual(result.output, """\
--send and --periodic flags cannot be used together
""")
