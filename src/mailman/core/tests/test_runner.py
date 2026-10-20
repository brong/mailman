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

"""Test some Runner base class behavior."""

import os
import signal
import unittest
import warnings
import threading

from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.core.runner import Runner
from mailman.interfaces.member import DeliveryMode
from mailman.interfaces.runner import RunnerCrashEvent
from mailman.model.preferences import Preferences
from mailman.runners.virgin import VirginRunner
from mailman.testing.helpers import (
    configuration,
    event_subscribers,
    get_queue_messages,
    LogFileMark,
    make_digest_messages,
    make_testable_runner,
    specialized_message_from_string as mfs,
    subscribe,
)
from mailman.testing.layers import ConfigLayer
from sqlalchemy import inspect
from sqlalchemy.exc import SAWarning
from unittest import mock


class CrashingRunner(Runner):
    def _dispose(self, mlist, msg, msgdata):
        raise RuntimeError('borked')


class NonQueueRunner(Runner):
    is_queue_runner = False


class TestRunnerSessionReset(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        self.preferences = Preferences(receive_own_postings=True)
        config.db.store.add(self.preferences)
        config.db.commit()

    def test_disabled_by_default(self):
        runner = Runner('in')
        runner.signal_handler(signal.SIGHUP, None)
        with mock.patch('mailman.core.runner.reopen') as reopen:
            runner._process_signals()
        reopen.assert_called_once_with()
        self.assertFalse(runner.reset_db_session_on_sighup)
        self.assertTrue(inspect(self.preferences).persistent)

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_deferred_reset_preserves_data_and_process(self):
        runner = Runner('in')
        log = LogFileMark('mailman.runner')
        runner.signal_handler(signal.SIGHUP, None)
        self.assertTrue(inspect(self.preferences).persistent)
        self.assertEqual(log.read(), '')
        with mock.patch('mailman.core.runner.reopen') as reopen:
            runner._process_signals()
        reopen.assert_called_once_with()
        self.assertTrue(inspect(self.preferences).detached)
        self.assertEqual(len(config.db.store.identity_map), 0)
        self.assertFalse(runner._stop)
        self.assertEqual(runner.status, 0)
        self.assertIn('in runner database session reset after SIGHUP.',
                      log.read())
        self.assertEqual(config.db.store.query(Preferences).count(), 1)

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_idle_runner_resets_session(self):
        runner = Runner('in')
        runner.signal_handler(signal.SIGHUP, None)
        runner._sleep(0)
        self.assertTrue(inspect(self.preferences).detached)
        self.assertFalse(runner._stop)

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_uncommitted_changes_are_discarded(self):
        self.preferences.receive_own_postings = False
        config.db.store.flush()
        runner = Runner('in')
        runner.signal_handler(signal.SIGHUP, None)
        runner._process_signals()
        self.assertTrue(inspect(self.preferences).detached)
        self.assertTrue(config.db.store.query(
            Preferences).one().receive_own_postings)

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_reset_follows_message_commit(self):
        create_list('test@example.com')
        config.db.commit()
        preferences = self.preferences
        attached_during_processing = []

        class SignallingRunner(Runner):
            def _dispose(self, mlist, msg, msgdata):
                preferences.receive_own_postings = False
                os.kill(os.getpid(), signal.SIGHUP)
                attached_during_processing.append(
                    inspect(preferences).persistent)
                return False

        config.switchboards['in'].enqueue(
            mfs('From: anne@example.com\n\n'),
            listid='test.example.com')
        runner = SignallingRunner('in')
        self.addCleanup(
            signal.signal, signal.SIGHUP, signal.getsignal(signal.SIGHUP))
        signal.signal(signal.SIGHUP, runner.signal_handler)
        runner._one_iteration()
        self.assertEqual(attached_during_processing, [True])
        self.assertTrue(inspect(preferences).detached)
        self.assertFalse(config.db.store.query(
            Preferences).one().receive_own_postings)
        get_queue_messages('shunt', expected_count=0)

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_reset_after_processing_failure(self):
        create_list('test@example.com')
        config.db.commit()
        config.switchboards['in'].enqueue(
            mfs('From: anne@example.com\n\n'),
            listid='test.example.com')
        runner = CrashingRunner('in')
        runner.signal_handler(signal.SIGHUP, None)
        runner._one_iteration()
        self.assertTrue(inspect(self.preferences).detached)
        get_queue_messages('shunt', expected_count=1)

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_reset_after_dequeue_failure(self):
        config.switchboards['in'].enqueue(
            mfs('From: anne@example.com\n\n'),
            listid='test.example.com')
        runner = Runner('in')
        runner.signal_handler(signal.SIGHUP, None)
        runner.signal_handler(signal.SIGTERM, None)
        with mock.patch.object(
                runner.switchboard, 'dequeue', side_effect=ValueError('bad')):
            runner.run()
        self.assertTrue(inspect(self.preferences).detached)
        self.assertEqual(len(runner.switchboard.files), 0)

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_reused_primary_key(self):
        previous_id = inspect(self.preferences).identity
        runner = Runner('in')
        runner.signal_handler(signal.SIGHUP, None)
        runner._process_signals()
        # Simulate reset_the_world() in another process: delete the rows and
        # rewind primary key sequences without touching this session.  Keep
        # the old object strongly referenced so the test does not depend on
        # garbage collection.
        with config.db.engine.begin() as connection:
            connection.execute(Preferences.__table__.delete())
            config.db._post_reset(connection)
        # Let the database assign the primary key during flush.  Without the
        # session reset this reuses the stale identity and emits SAWarning.
        current = Preferences()
        with warnings.catch_warnings():
            warnings.simplefilter('error', SAWarning)
            config.db.store.add(current)
            config.db.commit()
        self.assertEqual(inspect(current).identity, previous_id)
        self.assertIsNot(current, self.preferences)
        self.assertEqual(config.db.store.query(Preferences).count(), 1)

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_no_completion_log_on_failure(self):
        runner = Runner('in')
        log = LogFileMark('mailman.runner')
        runner.signal_handler(signal.SIGHUP, None)
        with mock.patch.object(
                config.db, 'close_session',
                side_effect=RuntimeError('reset failed')):
            with self.assertRaisesRegex(RuntimeError, 'reset failed'):
                runner._process_signals()
        self.assertNotIn('database session reset after SIGHUP', log.read())

    @configuration('runner.in', reset_db_session_on_sighup='yes')
    def test_sigterm_does_not_reset_session(self):
        runner = Runner('in')
        runner.signal_handler(signal.SIGTERM, None)
        runner._process_signals()
        self.assertTrue(inspect(self.preferences).persistent)
        self.assertTrue(runner._stop)
        self.assertEqual(runner.status, signal.SIGTERM)


class TestRunner(unittest.TestCase):
    """Test the Runner base class behavior."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('test@example.com')
        self._events = []

    def _got_event(self, event):
        self._events.append(event)

    @configuration('runner.crashing',
                   **{'class': 'mailman.core.tests.CrashingRunner'})
    def test_crash_event(self):
        runner = make_testable_runner(CrashingRunner, 'in')
        # When an exception occurs in Runner._process_one_file(), a zope.event
        # gets triggered containing the exception object.
        msg = mfs("""\
From: anne@example.com
To: test@example.com
Message-ID: <ant>

""")
        config.switchboards['in'].enqueue(msg, listid='test.example.com')
        with event_subscribers(self._got_event):
            runner.run()
        # We should now have exactly one event, which will contain the
        # exception, plus additional metadata containing the mailing list,
        # message, and metadata.
        self.assertEqual(len(self._events), 1)
        event = self._events[0]
        self.assertIsInstance(event, RunnerCrashEvent)
        self.assertEqual(event.mailing_list, self._mlist)
        self.assertEqual(event.message['message-id'], '<ant>')
        self.assertEqual(event.metadata['listid'], 'test.example.com')
        self.assertIsInstance(event.error, RuntimeError)
        self.assertEqual(str(event.error), 'borked')
        self.assertIsInstance(event.runner, CrashingRunner)
        # The message should also have ended up in the shunt queue.
        items = get_queue_messages('shunt', expected_count=1)
        self.assertEqual(items[0].msg['message-id'], '<ant>')

    def test_digest_messages(self):
        # In LP: #1130697, the digest runner creates MIME digests using the
        # stdlib MIMEMutlipart class, however this class does not have the
        # extended attributes we require (e.g. .sender).  The fix is to use a
        # subclass of MIMEMultipart and our own Message subclass; this adds
        # back the required attributes.  (LP: #1130696)
        self._mlist.send_welcome_message = False
        # Subscribe some users receiving digests.
        anne = subscribe(self._mlist, 'Anne')
        anne.preferences.delivery_mode = DeliveryMode.mime_digests
        bart = subscribe(self._mlist, 'Bart')
        bart.preferences.delivery_mode = DeliveryMode.plaintext_digests
        # Start by creating the raw ingredients for the digests.  This also
        # runs the digest runner, thus producing the digest messages into the
        # virgin queue.
        make_digest_messages(self._mlist)
        # Run the virgin queue processor, which runs the cook-headers and
        # to-outgoing handlers.  This should produce no error.
        error_log = LogFileMark('mailman.error')
        runner = make_testable_runner(VirginRunner, 'virgin')
        runner.run()
        error_text = error_log.read()
        self.assertEqual(len(error_text), 0, error_text)
        get_queue_messages('shunt', expected_count=0)
        items = get_queue_messages('out', expected_count=2)
        # Which one is the MIME digest?
        mime_digest = None
        for item in items:
            if item.msg.get_content_type() == 'multipart/mixed':
                assert mime_digest is None, 'Found two MIME digests'
                mime_digest = item.msg
        # The cook-headers handler ran.
        self.assertIn('x-mailman-version', mime_digest)
        self.assertEqual(mime_digest['precedence'], 'list')
        # The list's -request address is the original sender.
        self.assertEqual(item.msgdata['original_sender'],
                         'test-request@example.com')

    @configuration('runner.nonqueue',
                   **{'class': 'mailman.core.tests.NonQueueRunner'})
    def test_non_queue_runner(self):
        # Test that a runner with no queue can run _one_iteration.
        runner = make_testable_runner(NonQueueRunner)
        # This will throw AttributeError on failure.
        runner.run()


class TestRunnerSignal(unittest.TestCase):
    """Test that signal the runner."""

    layer = ConfigLayer

    _MANAGED_SIGNALS = (
        signal.SIGHUP, signal.SIGINT, signal.SIGTERM, signal.SIGUSR1,
    )

    def setUp(self):
        self._saved_handlers = {
            sig: signal.getsignal(sig)
            for sig in self._MANAGED_SIGNALS
        }

    def tearDown(self):
        for sig, handler in self._saved_handlers.items():
            signal.signal(sig, handler)

    @configuration(
        'runner.nonqueue', **{
            'class': 'mailman.core.tests.test_runner.NonQueueRunner',
        }
    )
    def test_sigterm_during_processing(self):
        """SIGTERM received during _one_iteration() must not interrupt it."""
        processing_started = threading.Event()
        finish_processing = threading.Event()
        processing_completed = threading.Event()

        class MockedRunner(Runner):
            is_queue_runner = False

            def _one_iteration(self_inner):
                processing_started.set()
                finish_processing.wait(timeout=10)
                processing_completed.set()
                return 0

        runner = MockedRunner('nonqueue')
        runner.set_signals()

        runner_thread = threading.Thread(target=runner.run)
        runner_thread.start()

        try:
            # Wait for the runner to begin processing.
            self.assertTrue(processing_started.wait(timeout=10),
                            'Runner did not start processing in time')

            # Send SIGTERM while processing is still in progress.
            os.kill(os.getpid(), signal.SIGTERM)

            # Allow the runner to finish its current processing.
            finish_processing.set()

            # Wait for the runner to exit.
            runner_thread.join(timeout=10)
            self.assertFalse(runner_thread.is_alive(),
                             'Runner thread did not exit')

            # Verify that processing ran to completion.
            self.assertTrue(processing_completed.is_set(),
                            'Processing was interrupted by SIGTERM')
            self.assertEqual(runner.status, signal.SIGTERM)
        finally:
            if runner_thread.is_alive():
                runner.stop()
                os.kill(os.getpid(), signal.SIGTERM)
                runner_thread.join(timeout=5)

    @configuration(
        'runner.nonqueue', **{
            'class': 'mailman.core.tests.test_runner.NonQueueRunner',
            'sleep_time': '20s',
        }
    )
    def test_sigterm_during_sleeping(self):
        """SIGTERM received during _one_iteration() must not interrupt it."""
        sleeping_started = threading.Event()
        sleeping_completed = threading.Event()

        class MockedRunner(Runner):
            is_queue_runner = False

            def _sleep(self_inner, timeout=None):
                sleeping_started.set()
                super()._sleep(0)
                sleeping_completed.set()
                return 0

        runner = MockedRunner('nonqueue')
        runner.set_signals()

        runner_thread = threading.Thread(target=runner.run)
        runner_thread.start()

        try:
            # Wait for the runner to begin sleeping.
            self.assertTrue(sleeping_started.wait(timeout=10),
                            'Runner did not start sleeping in time')

            # Send SIGTERM while sleeping is still in progress.
            os.kill(os.getpid(), signal.SIGTERM)

            # Wait for the runner to exit.
            runner_thread.join(timeout=10)
            self.assertFalse(runner_thread.is_alive(),
                             'Runner thread did not exit')

            # Verify that sleeping ran to completion.
            self.assertTrue(sleeping_completed.is_set(),
                            'Sleeping was interrupted by SIGTERM')
            self.assertEqual(runner.status, signal.SIGTERM)
        finally:
            if runner_thread.is_alive():
                runner.stop()
                os.kill(os.getpid(), signal.SIGTERM)
                runner_thread.join(timeout=5)

    @configuration(
        'runner.nonqueue', **{
            'class': 'mailman.core.tests.test_runner.NonQueueRunner'
        }
    )
    def test_process_signals_sighup(self):
        """SIGHUP is processed: logs are reopened."""
        runner = NonQueueRunner('nonqueue')
        runner._signal_queue.put(signal.SIGHUP)
        with mock.patch('mailman.core.runner.reopen') as mock_reopen:
            runner._process_signals()
        mock_reopen.assert_called_once()
        self.assertFalse(runner._stop)
