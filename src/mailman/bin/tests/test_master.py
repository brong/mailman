# Copyright (C) 2010-2025 by the Free Software Foundation, Inc.
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
from unittest.mock import call, Mock, patch


class FakeLock:
    details = ('host.example.com', 9999, '/tmp/whatever')

    def unlock(self):
        pass


class TestMasterBase(unittest.TestCase):
    """Shared shutdown and cleanup helpers for master tests."""

    def assert_master_stopped(self, m):
        m.thread.join(timeout=10)
        self.assertFalse(
            m.thread.is_alive(),
            f'Master thread did not stop; runners: {list(m._kids)}')
        self.assertEqual(list(m._kids), [])

    def cleanup_master(self, m):
        m._restartable = False
        deadline = time.monotonic() + 10
        while True:
            # A restart already in progress may still add a new PID.
            for pid in m._kids:
                with suppress(ProcessLookupError, PermissionError):
                    os.kill(pid, signal.SIGKILL)
                # Only reap here if the watcher is no longer doing so.
                if not m.thread.is_alive():
                    try:
                        waited_pid, _ = os.waitpid(pid, os.WNOHANG)
                    except ChildProcessError:
                        m._kids.drop(pid)
                    else:
                        if waited_pid:
                            m._kids.drop(waited_pid)
            m.thread.join(timeout=0.1)
            if not m.thread.is_alive():
                if not list(m._kids):
                    return
                time.sleep(0.1)
            if time.monotonic() >= deadline:
                self.fail(
                    'Could not clean up master; remaining runners: '
                    f'{list(m._kids)}')


class TestMaster(TestMasterBase):
    """Test the Mailman Core master runner."""

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

    def assertIn_with_timeout(
        self, needle, container_callable, timeout=10.0, interval=0.1
    ):
        """
        A custom assertion that waits at specified intervals until a given
        element is found in the container.

        :param needle: The element to look for.
        :param container_callable: A callable that returns the target container
         (list, string, etc.) to evaluate dynamically.
        :param timeout: Number of seconds before timing out.
        :param interval: Interval between checks in seconds.
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Retrieve the latest state on each iteration
            current_container = container_callable()

            if needle in current_container:
                break

            time.sleep(interval)

        self.assertIn(
            needle,
            current_container,
            f"Waited for {timeout} seconds, but '{needle}' was not found.",
        )

    def assertNotIn_with_timeout(
        self, needle, container_callable, timeout=10.0, interval=0.1
    ):
        """
        A custom assertion that waits at specified intervals until a given
        element is NO LONGER found in the container.

        :param needle: The element that should be absent.
        :param container_callable: A callable that returns the target container
         to evaluate dynamically.
        :param timeout: Number of seconds before timing out.
        :param interval: Interval between checks in seconds.
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            # Retrieve the latest state on each iteration
            current_container = container_callable()

            if needle not in current_container:
                break

            time.sleep(interval)

        self.assertNotIn(
            needle,
            current_container,
            f"Waited for {timeout} seconds, but '{needle}' is still present.",
        )

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
            signal_mock = resources.enter_context(patch.object(
                master.Loop, 'install_signal_handlers'))
            loop_mock = resources.enter_context(patch.object(
                master.Loop, 'loop'))
            result = command.invoke(
                master.main,
                ('-C', config_file,
                 '--no-restart', '--force',
                 '-r', 'in:1:1', '--verbose'))
            self.assertEqual(result.exit_code, 0, result.output)
            # We got initialized with the custom configuration file and the
            # verbose flag.
            init_mock.assert_called_once_with(config_file, True)
            # We returned a lock that was force-acquired.
            lock_mock.assert_called_once_with(True)
            # We created a non-restartable loop.
            start_mock.assert_called_once_with([('in', 1, 1)])
            signal_mock.assert_called_once_with()
            loop_mock.assert_called_once_with()

    def test_install_signal_handlers(self):
        """Verify Loop's signal registration and lock-refresh alarm.

        Cover the production registration that test_main_cli mocks out,
        including lock refresh and alarm rearming.  Mock the signal APIs
        to avoid changing process-wide handlers or scheduling a real alarm.
        """
        loop = master.Loop(lock=Mock())
        with ExitStack() as resources:
            signal_mock = resources.enter_context(patch(
                'mailman.bin.master.signal.signal'))
            alarm_mock = resources.enter_context(patch(
                'mailman.bin.master.signal.alarm'))
            loop.install_signal_handlers()
            self.assertEqual(signal_mock.call_count, 5)
            alarm_handler = signal_mock.call_args_list[0].args[1]
            self.assertEqual(signal_mock.call_args_list, [
                call(signal.SIGALRM, alarm_handler),
                call(signal.SIGHUP, loop._sighup_handler),
                call(signal.SIGUSR1, loop._sigusr1_handler),
                call(signal.SIGTERM, loop._sigterm_handler),
                call(signal.SIGINT, loop._sigint_handler),
                ])
            alarm_mock.assert_called_once_with(master.SECONDS_IN_A_DAY)
            alarm_handler(signal.SIGALRM, None)
            loop._lock.refresh.assert_called_once_with()
            self.assertEqual(alarm_mock.call_args_list, [
                call(master.SECONDS_IN_A_DAY),
                call(master.SECONDS_IN_A_DAY),
                ])

    def test_sighup_handler(self):
        """Invokes the SIGHUP handler.

        This sends SIGHUPs to the runners.  Unfortunately, there is no
        easy way to test whether that actually happens.  We'd need a
        specialized runner for that.

        """
        m = TestableMaster()
        mark = LogFileMark('mailman.runner')
        m.start('command')
        self.addCleanup(self.cleanup_master, m)
        self.assertEqual(len(list(m._kids)), 1)

        # We need to give the runner some time to install the signal
        # handler.  If we send SIGHUP before it does, the default
        # action is to terminate the process.  We wait until we see
        # that the runner has done so by inspecting the log file.
        self.assertIn_with_timeout("runner started.", mark.read)

        mark = LogFileMark('mailman.runner')
        m._sighup_handler(None, None)

        # Check if the runner reopened the log.
        needle = "command runner caught SIGHUP.  Reopening logs."
        self.assertIn_with_timeout(needle, mark.read)

        # Just to make sure it didn't die.
        self.assertEqual(len(list(m._kids)), 1)
        m._sigterm_handler(None, None)
        self.assert_master_stopped(m)

    def test_sigusr1_handler(self):
        """Invokes the SIGUSR1 handler.

        We then check whether the runners restart.
        """
        m = TestableMaster()
        m._restartable = True
        mark = LogFileMark('mailman.runner')
        m.start('command')
        self.addCleanup(self.cleanup_master, m)
        kids = list(m._kids)
        self.assertEqual(len(kids), 1)
        old_kid = kids[0]

        # We need to give the runner some time to install the signal
        # handler.  If we send SIGUSR1 before it does, the default
        # action is to terminate the process.  We wait until we see
        # that the runner has done so by inspecting the log file.
        self.assertIn_with_timeout("runner started.", mark.read)

        mark = LogFileMark('mailman.runner')
        m._sigusr1_handler(None, None)
        self.assertNotIn_with_timeout(old_kid, lambda: set(m._kids))

        # Check if the runner got the signal.
        needle = "command runner caught SIGUSR1.  Stopping."
        self.assertIn_with_timeout(needle, mark.read)

        # A new PID does not imply that exec and signal setup have completed.
        self.assertIn_with_timeout("runner started.", mark.read)
        new_kids = list(m._kids)
        self.assertEqual(len(new_kids), 1)
        self.assertTrue(kids[0] != new_kids[0])
        m._sigterm_handler(None, None)
        self.assert_master_stopped(m)

    def test_sigterm_handler(self):
        """Invokes the SIGTERM handler.

        We then check whether the runners are actually stopped.
        """
        m = TestableMaster()
        mark = LogFileMark('mailman.runner')
        m.start('command')
        self.addCleanup(self.cleanup_master, m)
        kids = list(m._kids)
        self.assertEqual(len(kids), 1)
        old_kid = kids[0]

        # We need to give the runner some time to install the signal
        # handler.  If we send SIGTERM before it does, the default
        # action is to terminate the process and will return a
        # slightly different status code.  We wait until we see that
        # the runner has done so by inspecting the log file.
        self.assertIn_with_timeout("runner started.", mark.read)

        mark = LogFileMark('mailman.runner')
        m._sigterm_handler(None, None)
        self.assertNotIn_with_timeout(old_kid, lambda: set(m._kids))

        # Check if the runner got the signal.
        needle = "command runner caught SIGTERM.  Stopping."
        self.assertIn_with_timeout(needle, mark.read)

        self.assert_master_stopped(m)

    def test_sigint_handler(self):
        """Invokes the SIGINT handler.

        We then check whether the runners are actually stopped.
        """
        m = TestableMaster()
        mark = LogFileMark('mailman.runner')
        m.start('command')
        self.addCleanup(self.cleanup_master, m)
        self.assertEqual(len(list(m._kids)), 1)

        kids = list(m._kids)
        self.assertEqual(len(kids), 1)
        old_kid = kids[0]

        # We need to give the runner some time to install the signal
        # handler.  If we send SIGINT before it does, the default
        # action is to terminate the process and will return a
        # slightly different status code.  We wait until we see that
        # the runner has done so by inspecting the log file.
        self.assertIn_with_timeout("runner started.", mark.read)

        mark = LogFileMark('mailman.runner')
        m._sigint_handler(None, None)
        self.assertNotIn_with_timeout(old_kid, lambda: set(m._kids))

        # Check if the runner got the signal.
        needle = "command runner caught SIGINT.  Stopping."
        self.assertIn_with_timeout(needle, mark.read)

        self.assert_master_stopped(m)

    def test_runner_restart_on_sigkill(self):
        """Kills a runner with SIGKILL and see if it restarted."""
        m = TestableMaster()
        m._restartable = True
        mark = LogFileMark('mailman.runner')
        m.start('command')
        self.addCleanup(self.cleanup_master, m)
        kids = list(m._kids)
        self.assertEqual(len(kids), 1)
        old_kid = kids[0]
        self.assertIn_with_timeout("runner started.", mark.read)

        mark = LogFileMark('mailman.runner')
        os.kill(old_kid, signal.SIGKILL)

        # But, we need to wait for the master to collect it.
        self.assertNotIn_with_timeout(old_kid, lambda: set(m._kids))

        self.assertIn_with_timeout("runner started.", mark.read)
        new_kids = list(m._kids)
        self.assertEqual(len(new_kids), 1)
        self.assertTrue(old_kid != new_kids[0])
        m._sigterm_handler(None, None)
        self.assert_master_stopped(m)


class TestMasterTestHelpers(TestMasterBase):
    """Test the shutdown and cleanup helpers used by TestMaster."""

    layer = ConfigLayer

    def test_master_join_timeout(self):
        """Verify the test helper reports a watcher shutdown timeout.

        Exercise assert_master_stopped with a mock thread that stays alive.
        Check that it uses a bounded join and raises an assertion rather
        than treating an unfinished shutdown as success.
        """
        m = Mock(_kids=[])
        m.thread.is_alive.return_value = True
        with self.assertRaisesRegex(
                AssertionError, 'Master thread did not stop'):
            self.assert_master_stopped(m)
        m.thread.join.assert_called_once_with(timeout=10)

    def test_master_cleanup_after_failure(self):
        """Verify cleanup_master releases runners after a test failure.

        Register the test helper with addCleanup on a deliberately failing
        test.  Check that its real runner and watcher stop without adding
        a cleanup error or losing the original failure.
        """
        m = TestableMaster()
        m._restartable = True
        m.start('command')
        self.addCleanup(self.cleanup_master, m)

        def fail():
            self.fail('Expected test failure')

        case = unittest.FunctionTestCase(fail)
        case.addCleanup(self.cleanup_master, m)
        result = unittest.TestResult()
        case.run(result)
        self.assertEqual(len(result.failures), 1)
        self.assertEqual(result.errors, [])
        self.assert_master_stopped(m)

    def test_master_cleanup_without_watcher(self):
        """Verify cleanup_master reaps runners without a live watcher.

        Let the watcher thread exit before starting a real runner, then
        check that the test helper terminates and reaps that child itself.
        """
        with patch.object(TestableMaster, 'loop'):
            m = TestableMaster()
        m.thread.start()
        self.addCleanup(self.cleanup_master, m)
        self.assert_master_stopped(m)
        m.start_runners(('command',))
        self.cleanup_master(m)
        self.assertEqual(list(m._kids), [])

    def test_master_cleanup_during_restart(self):
        """Verify cleanup_master handles a PID added during shutdown.

        Model an in-progress restart by adding a PID during a mocked join.
        Check that the test helper disables restarts and requests SIGKILL
        for the new PID on a subsequent pass, without spawning a process.
        """
        m = Mock(_kids=master.PIDWatcher())
        m.thread.is_alive.side_effect = (True, True, False)
        pid = 12345

        def join(timeout):
            if pid in m._kids:
                m._kids.drop(pid)
            else:
                m._kids.add(pid, ('command', 1, 1, 1))

        m.thread.join.side_effect = join
        with patch('mailman.bin.tests.test_master.os.kill') as kill_mock:
            self.cleanup_master(m)
        kill_mock.assert_called_once_with(pid, signal.SIGKILL)
        self.assertEqual(m.thread.join.call_args_list, [
            call(timeout=0.1),
            call(timeout=0.1),
            ])
        self.assertFalse(m._restartable)

    def test_master_cleanup_timeout(self):
        """Verify cleanup_master fails when its deadline expires.

        Keep a mock watcher alive and advance a mocked clock to the
        deadline.  Check that the test helper reports cleanup failure
        rather than retrying indefinitely.
        """
        m = Mock(_kids=[])
        m.thread.is_alive.return_value = True
        with patch('mailman.bin.tests.test_master.time.monotonic',
                   side_effect=(0, 10)):
            with self.assertRaisesRegex(
                    AssertionError, 'Could not clean up master'):
                self.cleanup_master(m)
        m.thread.join.assert_called_once_with(timeout=0.1)
