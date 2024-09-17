# Copyright (C) 2010-2023 by the Free Software Foundation, Inc.
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

"""Test master watcher utilities."""

import os
import time
import signal
import socket
import tempfile
import unittest

from click.testing import CliRunner
from contextlib import ExitStack, suppress
from datetime import timedelta
from flufl.lock import Lock, TimeOutError
from importlib.resources import path
from io import StringIO
from mailman.bin import master
from mailman.config import config
from mailman.testing.helpers import LogFileMark, TestableMaster
from mailman.testing.layers import ConfigLayer
from unittest.mock import patch


class FakeLock:
    details = ('host.example.com', 9999, '/tmp/whatever')

    def unlock(self):
        pass


class TestMaster(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        fd, self.lock_file = tempfile.mkstemp()
        os.close(fd)
        # The lock file should not exist before we try to acquire it.
        os.remove(self.lock_file)

    def tearDown(self):
        # Unlocking removes the lock file, but just to be safe (i.e. in case
        # of errors).
        with suppress(FileNotFoundError):
            os.remove(self.lock_file)

    def test_acquire_lock_1(self):
        lock = master.acquire_lock_1(False, self.lock_file)
        is_locked = lock.is_locked
        lock.unlock()
        self.assertTrue(is_locked)

    def test_acquire_lock_1_force(self):
        # Create the lock and lock it.
        my_lock = Lock(self.lock_file)
        my_lock.lock(timedelta(seconds=60))
        # Try to aquire it again with force.
        lock = master.acquire_lock_1(True, self.lock_file)
        self.assertTrue(lock.is_locked)
        lock.unlock()

    def test_master_state(self):
        my_lock = Lock(self.lock_file)
        # Mailman is not running.
        state, lock = master.master_state(self.lock_file)
        self.assertEqual(state, master.WatcherState.none)
        # Acquire the lock as if another process had already started the
        # master.  Use a timeout to avoid this test deadlocking.
        my_lock.lock(timedelta(seconds=60))
        try:
            state, lock = master.master_state(self.lock_file)
        finally:
            my_lock.unlock()
        self.assertEqual(state, master.WatcherState.conflict)

    def test_master_state_stale(self):
        # Create a lock file with non-existent pid.
        with open(self.lock_file, 'w') as fp:
            fp.write(f'{self.lock_file}|{socket.getfqdn()}|9999999|junk')
        # Try to acquire the lock.
        Lock(self.lock_file)
        state, lock = master.master_state(self.lock_file)
        self.assertEqual(state, master.WatcherState.stale_lock)

    def test_acquire_lock_timeout_reason_unknown(self):
        stderr = StringIO()
        with ExitStack() as resources:
            resources.enter_context(patch(
                'mailman.bin.master.acquire_lock_1',
                side_effect=TimeOutError))
            resources.enter_context(patch(
                'mailman.bin.master.master_state',
                return_value=(master.WatcherState.none, FakeLock())))
            resources.enter_context(patch(
                'mailman.bin.master.sys.stderr', stderr))
            with self.assertRaises(SystemExit) as cm:
                master.acquire_lock(False)
            self.assertEqual(cm.exception.code, 1)
            self.assertEqual(stderr.getvalue(), """\
For unknown reasons, the master lock could not be acquired.

Lock file: {}
Lock host: host.example.com

Exiting.
""".format(config.LOCK_FILE))

    def test_main_cli(self):
        command = CliRunner()
        fake_lock = FakeLock()
        with ExitStack() as resources:
            config_file = str(resources.enter_context(
                path('mailman.testing', 'testing.cfg')))
            init_mock = resources.enter_context(patch(
                'mailman.bin.master.initialize'))
            lock_mock = resources.enter_context(patch(
                'mailman.bin.master.acquire_lock',
                return_value=fake_lock))
            start_mock = resources.enter_context(patch.object(
                master.Loop, 'start_runners'))
            loop_mock = resources.enter_context(patch.object(
                master.Loop, 'loop'))
            command.invoke(
                master.main,
                ('-C', config_file,
                 '--no-restart', '--force',
                 '-r', 'in:1:1', '--verbose'))
            # We got initialized with the custom configuration file and the
            # verbose flag.
            init_mock.assert_called_once_with(config_file, True)
            # We returned a lock that was force-acquired.
            lock_mock.assert_called_once_with(True)
            # We created a non-restartable loop.
            start_mock.assert_called_once_with([('in', 1, 1)])
            loop_mock.assert_called_once_with()

    def test_sighup_handler(self):
        """Invokes the SIGHUP handler.

        This sends SIGHUPs to the runners.  Unfortunately, there is no
        easy way to test whether that actually happens.  We'd need a
        specialized runner for that.

        """
        m = TestableMaster()
        mark = LogFileMark('mailman.runner')
        m.start('command')
        self.assertEqual(len(list(m._kids)), 1)

        # We need to give the runner some time to install the signal
        # handler.  If we send SIGHUP before it does, the default
        # action is to terminate the process.  We wait until we see
        # that the runner has done so by inspecting the log file.
        # This is race free, and bounded in time.
        start = time.time()
        while ("runner started." not in mark.read()
               and time.time() - start < 10):
            time.sleep(0.1)

        mark = LogFileMark('mailman.runner')
        m._sighup_handler(None, None)

        # Check if the runner reopened the log.
        start = time.time()
        needle = "command runner caught SIGHUP.  Reopening logs."
        while (needle not in mark.read()
               and time.time() - start < 10):
            time.sleep(0.1)
        self.assertIn(needle, mark.read())

        # Just to make sure it didn't die.
        self.assertEqual(len(list(m._kids)), 1)
        m.stop()

    def test_sigusr1_handler(self):
        """Invokes the SIGUSR1 handler.

        We then check whether the runners restart.
        """
        m = TestableMaster()
        m._restartable = True
        mark = LogFileMark('mailman.runner')
        m.start('command')
        kids = list(m._kids)
        self.assertEqual(len(kids), 1)
        old_kid = kids[0]

        # We need to give the runner some time to install the signal
        # handler.  If we send SIGUSR1 before it does, the default
        # action is to terminate the process.  We wait until we see
        # that the runner has done so by inspecting the log file.
        # This is race free, and bounded in time.
        start = time.time()
        while ("runner started." not in mark.read()
               and time.time() - start < 10):
            time.sleep(0.1)

        # Invoke the handler in a loop.  This is race free, and
        # bounded in time.
        start = time.time()
        mark = LogFileMark('mailman.runner')
        while old_kid in set(m._kids) and time.time() - start < 10:
            # We must not send signals in rapid succession, because
            # the behavior of signals arriving while the process is in
            # the signal handler varies.  Linux implements System V
            # semantics, which means the default signal action is
            # restored for the duration of the signal handler.  In
            # this case it means to terminate the process.
            time.sleep(1)
            m._sigusr1_handler(None, None)

        # Check if the runner got the signal.
        start = time.time()
        needle = "command runner caught SIGUSR1.  Stopping."
        while (needle not in mark.read()
               and time.time() - start < 10):
            time.sleep(0.1)
        self.assertIn(needle, mark.read())

        new_kids = list(m._kids)
        self.assertEqual(len(new_kids), 1)
        self.assertTrue(kids[0] != new_kids[0])
        m._restartable = False
        for pid in new_kids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        m.thread.join()
        m.cleanup()

    def test_sigterm_handler(self):
        """Invokes the SIGTERM handler.

        We then check whether the runners are actually stopped.
        """
        m = TestableMaster()
        mark = LogFileMark('mailman.runner')
        m.start('command')
        kids = list(m._kids)
        self.assertEqual(len(kids), 1)
        old_kid = kids[0]

        # We need to give the runner some time to install the signal
        # handler.  If we send SIGTERM before it does, the default
        # action is to terminate the process and will return a
        # slightly different status code.  We wait until we see that
        # the runner has done so by inspecting the log file.  This is
        # race free, and bounded in time.
        start = time.time()
        while ("runner started." not in mark.read()
               and time.time() - start < 10):
            time.sleep(0.1)

        # Invoke the handler in a loop.  This is race free, and
        # bounded in time.
        start = time.time()
        mark = LogFileMark('mailman.runner')
        while old_kid in set(m._kids) and time.time() - start < 10:
            time.sleep(0.1)
            m._sigterm_handler(None, None)

        # Check if the runner got the signal.
        start = time.time()
        needle = "command runner caught SIGTERM.  Stopping."
        while (needle not in mark.read()
               and time.time() - start < 10):
            time.sleep(0.1)
        self.assertIn(needle, mark.read())

        m.thread.join()
        self.assertEqual(len(list(m._kids)), 0)
        m.cleanup()

    def test_sigint_handler(self):
        """Invokes the SIGINT handler.

        We then check whether the runners are actually stopped.
        """
        m = TestableMaster()
        mark = LogFileMark('mailman.runner')
        m.start('command')
        self.assertEqual(len(list(m._kids)), 1)

        kids = list(m._kids)
        self.assertEqual(len(kids), 1)
        old_kid = kids[0]

        # We need to give the runner some time to install the signal
        # handler.  If we send SIGINT before it does, the default
        # action is to terminate the process and will return a
        # slightly different status code.  We wait until we see that
        # the runner has done so by inspecting the log file.  This is
        # race free, and bounded in time.
        start = time.time()
        while ("runner started." not in mark.read()
               and time.time() - start < 10):
            time.sleep(0.1)

        # Invoke the handler in a loop.  This is race free, and
        # bounded in time.
        start = time.time()
        mark = LogFileMark('mailman.runner')
        while old_kid in set(m._kids) and time.time() - start < 10:
            time.sleep(0.1)
            m._sigint_handler(None, None)

        # Check if the runner got the signal.
        start = time.time()
        needle = "command runner caught SIGINT.  Stopping."
        while (needle not in mark.read()
               and time.time() - start < 10):
            time.sleep(0.1)
        self.assertIn(needle, mark.read())

        m.thread.join()
        self.assertEqual(len(list(m._kids)), 0)
        m.cleanup()

    def test_runner_restart_on_sigkill(self):
        """Kills a runner with SIGKILL and see if it restarted."""
        m = TestableMaster()
        m._restartable = True
        m.start('command')
        kids = list(m._kids)
        self.assertEqual(len(kids), 1)
        old_kid = kids[0]

        # Kill it.  No need to wait for anything as this cannot be
        # caught anyway.
        os.kill(old_kid, signal.SIGKILL)

        # But, we need to wait for the master to collect it.
        start = time.time()
        while old_kid in set(m._kids) and time.time() - start < 10:
            time.sleep(0.1)

        new_kids = list(m._kids)
        self.assertEqual(len(new_kids), 1)
        self.assertTrue(old_kid != new_kids[0])
        m._restartable = False
        for pid in new_kids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        m.thread.join()
        m.cleanup()
