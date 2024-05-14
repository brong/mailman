# Copyright (C) 2012-2023 by the Free Software Foundation, Inc.
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

"""Tests for the Task runner."""

import os
import unittest

from datetime import timedelta
from lazr.config import as_timedelta
from mailman.app.lifecycle import create_list
from mailman.app.moderator import hold_message
from mailman.config import config
from mailman.database.transaction import dbconnection
from mailman.interfaces.cache import ICacheManager
from mailman.interfaces.messages import IMessageStore
from mailman.interfaces.pending import IPendable, IPendings
from mailman.interfaces.requests import IListRequests
from mailman.interfaces.workflow import IWorkflowStateManager
from mailman.model.bounce import BounceEvent
from mailman.model.message import Message
from mailman.runners.task import TaskRunner
from mailman.testing.helpers import (
    LogFileMark,
    make_testable_runner,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import ConfigLayer
from mailman.utilities.datetime import factory
from zope.component import getUtility
from zope.interface import implementer


@implementer(IPendable)
class MyPendable(dict):
    PEND_TYPE = 'my pended'


class TestTask(unittest.TestCase):
    """Test various aspects of the Task runner."""

    layer = ConfigLayer

    def setUp(self):
        self._pendings = getUtility(IPendings)
        self._events = BounceEvent
        self._wfmanager = getUtility(IWorkflowStateManager)
        self._cachemanager = getUtility(ICacheManager)
        self._messages = getUtility(IMessageStore)
        self._mlist = create_list('ant@example.com')
        self._msg1 = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: A message
Message-ID: <msg1>

first message
""")
        self._msg2 = mfs("""\
To: ant@example.com
From: anne@example.com
Subject: A message
Message-ID: <msg2>

second message
""")
        self._listrequests = IListRequests(self._mlist)
        self._runner = make_testable_runner(TaskRunner)
        self._cachemanager.add('cache1', 'xxx1', lifetime=timedelta(days=1))
        self._cachemanager.add('cache2', 'xxx2', lifetime=timedelta(days=3))
        pendable = MyPendable(id=1)
        self._token1 = self._pendings.add(pendable, lifetime=timedelta(days=1))
        self._token2 = self._pendings.add(pendable, lifetime=timedelta(days=3))
        self._wfmanager.save(self._token1)
        self._wfmanager.save(self._token2)
        self._requestid1 = hold_message(
            self._mlist, self._msg1, reason='Testing')
        self._requestid2 = hold_message(
            self._mlist, self._msg2, reason='Testing')

    def test_task_runner(self):
        # Test that the task runner deletes expired cache, pendings and
        # associated workflows.
        self.assertEqual(self._cachemanager.get('cache1'), 'xxx1')
        self.assertEqual(self._cachemanager.get('cache2'), 'xxx2')
        self.assertEqual(self._pendings.count(), 4)
        self.assertEqual(self._wfmanager.count, 2)
        mark = LogFileMark('mailman.task')
        factory.fast_forward(days=2)
        self._runner.run()
        self.assertIsNone(self._cachemanager.get('cache1'))
        self.assertEqual(self._cachemanager.get('cache2'), 'xxx2')
        self.assertEqual(self._pendings.count(), 3)
        pended = self._pendings.confirm(self._token2, expunge=False)
        self.assertEqual(pended['type'], 'my pended')
        self.assertEqual(list(self._wfmanager.get_all_tokens()),
                         [self._token2])
        log = mark.read()
        self.assertIn('Task runner evicted 1 expired pendings', log)
        self.assertIn('Task runner deleted 1 orphaned workflows', log)
        self.assertIn('Task runner deleted 0 orphaned requests', log)
        self.assertIn('Task runner evicted expired cache entries', log)

    def test_task_runner_request(self):
        # Test that the task runner deletes orphaned requests.
        self.assertEqual(self._pendings.count(), 4)
        self.assertEqual(self._listrequests.count, 2)
        life = as_timedelta(config.mailman.moderator_request_life)
        mark = LogFileMark('mailman.task')
        factory.fast_forward(days=life.days+1)
        self._runner.run()
        self.assertEqual(self._pendings.count(), 0)
        self.assertEqual(self._listrequests.count, 0)
        log = mark.read()
        self.assertIn('Task runner deleted 2 orphaned requests', log)

    def test_task_runner_messages(self):
        # Test that the task runner deletes orphaned messages from the
        # message store.
        # Initially, there are 2 messages in the store and 4 pendings.
        self.assertEqual(len(list(self._messages.messages)), 2)
        self.assertEqual(self._pendings.count(), 4)
        # Deleting the first request removes the pending but not the message.
        self._listrequests.delete_request(self._requestid1)
        self.assertEqual(self._pendings.count(), 3)
        self.assertEqual(len(list(self._messages.messages)), 2)
        mark = LogFileMark('mailman.task')
        self._runner.run()
        # Now there's only msg2.
        self.assertEqual(len(list(self._messages.messages)), 1)
        self.assertIsNotNone(self._messages.get_message_by_id('<msg2>'))
        log = mark.read()
        self.assertIn('Task runner deleted 1 orphaned messages', log)

    @dbconnection
    def test_task_runner_message_files(self, store):
        # Test that task runner deletes message files with no entry in the
        # message store.
        def count_files():
            """ A helper to count the number of saved message files."""
            count = 0
            base_dir = config.MESSAGES_DIR
            for root, dirs, files in os.walk(base_dir):
                if files:
                    count += len(files)
            return count
        # Initally there are two entries in the message store and two files.
        self.assertEqual(len(list(self._messages.messages)), 2)
        self.assertEqual(count_files(), 2)
        # Delete one of the message store entries leaving one but still two
        # files.
        row = store.query(Message).filter_by(message_id='<msg1>').first()
        store.delete(row)
        self.assertEqual(len(list(self._messages.messages)), 1)
        self.assertEqual(count_files(), 2)
        mark = LogFileMark('mailman.task')
        self._runner.run()
        # Now there's only one file.
        self.assertEqual(len(list(self._messages.messages)), 1)
        self.assertEqual(count_files(), 1)
        log = mark.read()
        self.assertIn('Task runner deleted 1 orphaned message files', log)

    @dbconnection
    def test_task_runner_bounce_events_old_unprocessed(self, store):
        # Test that the task runner deletes processed bounce events older than
        # dsn_lifetime, but not newer ones or unprocessed ones.
        # Set one old but unprocessed.
        event = self._events(self._mlist.list_id,
                             'anne@example.com',
                             self._msg1,
                             )
        event.timestamp -= (as_timedelta(config.mailman.dsn_lifetime) +
                            as_timedelta('1d'))
        store.add(event)
        mark = LogFileMark('mailman.task')
        self._runner.run()
        log = mark.read()
        self.assertIn('Task runner evicted 0 expired bounce events', log)

    @dbconnection
    def test_task_runner_bounce_events_old_processed(self, store):
        # Test that the task runner deletes processed bounce events older than
        # dsn_lifetime, but not newer ones or unprocessed ones.
        # Set one old and processed.
        event = self._events(self._mlist.list_id,
                             'anne@example.com',
                             self._msg1,
                             )
        event.timestamp -= (as_timedelta(config.mailman.dsn_lifetime) +
                            as_timedelta('1d'))
        event.processed = True
        store.add(event)
        mark = LogFileMark('mailman.task')
        self._runner.run()
        log = mark.read()
        self.assertIn('Task runner evicted 1 expired bounce events', log)

    @dbconnection
    def test_task_runner_bounce_events_processed(self, store):
        # Test that the task runner deletes processed bounce events older than
        # dsn_lifetime, but not newer ones or unprocessed ones.
        # Set one processed.
        event = self._events(self._mlist.list_id,
                             'anne@example.com',
                             self._msg1,
                             )
        event.processed = True
        store.add(event)
        mark = LogFileMark('mailman.task')
        self._runner.run()
        log = mark.read()
        self.assertIn('Task runner evicted 0 expired bounce events', log)
