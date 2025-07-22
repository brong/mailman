# Copyright (C) 2011-2025 by the Free Software Foundation, Inc.
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

"""Test some additional corner cases for starting/stopping."""

import os
import sys
import time
import shutil
import signal
import socket
import unittest

from click.testing import CliRunner
from contextlib import ExitStack, suppress
from datetime import datetime, timedelta
from flufl.lock import SEP
from importlib.resources import path
from mailman.bin.master import WatcherState
from mailman.commands.cli_control import reopen, restart, start
from mailman.config import config
from mailman.testing.helpers import configuration
from mailman.testing.layers import ConfigLayer
from public import public
from tempfile import TemporaryDirectory
from unittest.mock import patch


# For ../docs/control.rst
@public
def make_config(resources):
    cfg_path = resources.enter_context(
        path('mailman.commands.tests.data', 'no-runners.cfg'))
    # We have to patch the global config's filename attribute.  The problem
    # here is that click does not support setting the -C option on the
    # parent command (i.e. `master`).
    # https://github.com/pallets/click/issues/831
    resources.enter_context(patch.object(config, 'filename', str(cfg_path)))


# For ../docs/control.rst
@public
def find_master():
    # See if the master process is still running.
    until = timedelta(seconds=10) + datetime.now()
    while datetime.now() < until:
        time.sleep(0.1)
        with suppress(FileNotFoundError, ValueError, ProcessLookupError):
            with open(config.PID_FILE) as fp:
                pid = int(fp.read().strip())
                os.kill(pid, 0)
                return pid
    return None


@public
def claim_lock():
    # Fake an acquisition of the master lock by another process, which
    # subsequently goes stale.  Start by finding a free process id.  Yes,
    # this could race, but given that we're starting with our own PID and
    # searching downward, it's less likely.
    fake_pid = os.getpid() - 1
    while fake_pid > 1:
        try:
            os.kill(fake_pid, 0)
        except ProcessLookupError:
            break
        fake_pid -= 1
    else:
        raise RuntimeError('Cannot find free PID')
    # Lock acquisition logic taken from flufl.lock.
    claim_file = SEP.join((
        config.LOCK_FILE,
        socket.getfqdn(),
        str(fake_pid),
        '0'))
    with open(config.LOCK_FILE, 'w') as fp:
        fp.write(claim_file)
    os.link(config.LOCK_FILE, claim_file)
    expiration_date = datetime.now() - timedelta(minutes=5)
    t = time.mktime(expiration_date.timetuple())
    os.utime(claim_file, (t, t))
    return claim_file


@public
def kill_with_extreme_prejudice(pid_or_pidfile=None):
    # 2016-12-03 barry: We have intermittent hangs during both local and CI
    # test suite runs where killing a runner or master process doesn't
    # terminate the process.  In those cases, wait()ing on the child can
    # suspend the test process indefinitely.  Locally, you have to C-c the
    # test process, but that still doesn't kill it; the process continues to
    # run in the background.  If you then search for the process's pid and
    # SIGTERM it, it will usually exit, which is why I don't understand why
    # the above SIGTERM doesn't kill it sometimes.  However, when run under
    # CI, the test suite will just hang until the CI runner times it out.  It
    # would be better to figure out the underlying cause, because we have
    # definitely seen other situations where a runner process won't exit, but
    # for testing purposes we're just trying to clean up some resources so
    # after a brief attempt at SIGTERMing it, let's SIGKILL it and warn.
    if isinstance(pid_or_pidfile, str):
        try:
            with open(pid_or_pidfile, 'r') as fp:
                pid = int(fp.read())
        except FileNotFoundError:
            # There's nothing to kill.
            return
    else:
        pid = pid_or_pidfile
    if pid is not None:
        os.kill(pid, signal.SIGTERM)
    until = timedelta(seconds=10) + datetime.now()
    while datetime.now() < until:
        try:
            if pid is None:
                os.wait3(os.WNOHANG)
            else:
                os.waitpid(pid, os.WNOHANG)
        except ChildProcessError:
            # This basically means we went one too many times around the
            # loop.  The previous iteration successfully reaped the child.
            # Because the return status of wait3() and waitpid() are different
            # in those cases, it's easier just to catch the exception for
            # either call and exit.
            return
        time.sleep(0.1)
    else:
        if pid is None:
            # There's really not much more we can do because we have no pid to
            # SIGKILL.  Just report the problem and continue.
            print('WARNING: NO CHANGE IN CHILD PROCESS STATES',
                  file=sys.stderr)
            return
        print('WARNING: SIGTERM DID NOT EXIT PROCESS; SIGKILLing',
              file=sys.stderr)
        if pid is not None:
            os.kill(pid, signal.SIGKILL)
        until = timedelta(seconds=10) + datetime.now()
        while datetime.now() < until:
            try:
                os.waitpid(pid, os.WNOHANG)
            except ChildProcessError:
                # 2016-03-10 maxking: We are seeing ChildProcessError very
                # often in CI due to the os.waitpid on L155 above. This is
                # raised when there is no child process left. We are clearly in
                # the arena of a race condition where the process was killed
                # somewhere after we checked and before we tried to wait on
                # it. TOCTTOU problem.
                return
            time.sleep(0.1)
        else:
            print('WARNING: SIGKILL DID NOT EXIT PROCESS!', file=sys.stderr)


@public
def clean_stale_locks():
    """Cleanup the master.pid and master.lck file, if they exist."""
    # If the master process was force-killed during the test suite run, it is
    # possible that the stale pid file was left. Clean that file up.
    if os.path.exists(config.PID_FILE):
        os.unlink(config.PID_FILE)
    if os.path.exists(config.LOCK_FILE):
        os.unlink(config.LOCK_FILE)


class TestControl(unittest.TestCase):
    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._command = CliRunner()
        self._tmpdir = TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        # Specify where to put the pid file; and make sure that the master
        # gets killed regardless of whether it gets started or not.
        self._pid_file = os.path.join(self._tmpdir.name, 'master-test.pid')
        self.addCleanup(kill_with_extreme_prejudice, self._pid_file)
        # Patch cli_control so that 1) it doesn't actually do a fork, since
        # that makes it impossible to avoid race conditions in the test; 2)
        # doesn't actually os.execl().
        with ExitStack() as resources:
            resources.enter_context(patch(
                'mailman.commands.cli_control.os.fork',
                # Pretend to be the child.
                return_value=0
                ))
            self._execl = resources.enter_context(patch(
                'mailman.commands.cli_control.os.execl'))
            resources.enter_context(patch(
                'mailman.commands.cli_control.os.setsid'))
            resources.enter_context(patch(
                'mailman.commands.cli_control.os.chdir'))
            resources.enter_context(patch(
                'mailman.commands.cli_control.os.environ',
                os.environ.copy()))
            # Arrange for the mocks to be reverted when the test is over.
            self.addCleanup(resources.pop_all().close)

    def test_master_is_elsewhere_and_missing(self):
        with ExitStack() as resources:
            bin_dir = resources.enter_context(TemporaryDirectory())
            old_master = os.path.join(config.BIN_DIR, 'master')
            new_master = os.path.join(bin_dir, 'master')
            shutil.move(old_master, new_master)
            resources.callback(shutil.move, new_master, old_master)
            results = self._command.invoke(start)
            # Argument #2 to the execl() call should be the path to the master
            # program, and the path should not exist.
            self.assertEqual(
                len(self._execl.call_args_list), 1, results.output)
            posargs, kws = self._execl.call_args_list[0]
            master_path = posargs[2]
            self.assertEqual(os.path.basename(master_path), 'master')
            self.assertFalse(os.path.exists(master_path), master_path)

    def test_master_is_elsewhere_and_findable(self):
        with ExitStack() as resources:
            bin_dir = resources.enter_context(TemporaryDirectory())
            old_master = os.path.join(config.BIN_DIR, 'master')
            new_master = os.path.join(bin_dir, 'master')
            shutil.move(old_master, new_master)
            resources.callback(shutil.move, new_master, old_master)
            with configuration('paths.testing', bin_dir=bin_dir):
                results = self._command.invoke(start)
            # Argument #2 to the execl() call should be the path to the master
            # program, and the path should exist.
            self.assertEqual(
                len(self._execl.call_args_list), 1, results.output)
            posargs, kws = self._execl.call_args_list[0]
            master_path = posargs[2]
            self.assertEqual(os.path.basename(master_path), 'master')
            self.assertTrue(os.path.exists(master_path), master_path)

    def test_stale_lock_no_force(self):
        claim_file = claim_lock()
        self.addCleanup(os.remove, claim_file)
        self.addCleanup(os.remove, config.LOCK_FILE)
        result = self._command.invoke(start)
        self.assertEqual(result.exit_code, 2)
        self.assertEqual(
            result.output,
            'Usage: start [OPTIONS]\n'
            'Try \'start --help\' for help.\n\n'
            'Error: A previous run of GNU Mailman did not exit cleanly '
            '(stale_lock).  Try using --force\n')

    def test_stale_lock_force(self):
        claim_file = claim_lock()
        self.addCleanup(os.remove, claim_file)
        self.addCleanup(os.remove, config.LOCK_FILE)
        # Don't test the results of this command.  Because we're mocking
        # os.execl(), we'll end up raising the RuntimeError at the end of the
        # start() method, child branch.
        self._command.invoke(start, ('--force',))
        self.assertEqual(len(self._execl.call_args_list), 1)
        posargs, kws = self._execl.call_args_list[0]
        self.assertIn('--force', posargs)

    def test_generate_aliases_file_on_start(self):
        # Test that 'aliases' command is called when 'start' is called.
        with ExitStack() as resources:
            # To be able to get the output from aliases command, we need to
            # capture the output from parent command, which invokes the aliases
            # command.
            resources.enter_context(patch(
                'mailman.commands.cli_control.os.fork',
                # Pretend to be the parent.
                return_value=1))
            mock_regenerate = resources.enter_context(
                patch('{}.regenerate'.format(config.mta.incoming)))
            result = self._command.invoke(start)
            self.assertTrue('Generating MTA alias maps' in result.output)
            # At some point, this should be moved to assert_called_once() when
            # we drop support for Python 3.5.
            self.assertTrue(mock_regenerate.called)


class TestControlSimple(unittest.TestCase):
    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self._command = CliRunner()

    def test_watcher_state_conflict(self):
        with patch('mailman.commands.cli_control.master_state',
                   return_value=(WatcherState.conflict, object())):
            results = self._command.invoke(start)
            self.assertEqual(results.exit_code, 2)
            self.assertEqual(
                results.output,
                'Usage: start [OPTIONS]\n'
                'Try \'start --help\' for help.\n\n'
                'Error: GNU Mailman is already running\n')

    def test_reopen(self):
        with patch('mailman.commands.cli_control.kill_watcher') as mock:
            result = self._command.invoke(reopen)
        mock.assert_called_once_with(signal.SIGHUP)
        self.assertEqual(result.output, 'Reopening the Mailman runners\n')

    def test_reopen_quiet(self):
        with patch('mailman.commands.cli_control.kill_watcher') as mock:
            result = self._command.invoke(reopen, ('--quiet',))
        mock.assert_called_once_with(signal.SIGHUP)
        self.assertEqual(result.output, '')

    def test_restart(self):
        with patch('mailman.commands.cli_control.kill_watcher') as mock:
            result = self._command.invoke(restart)
        mock.assert_called_once_with(signal.SIGUSR1)
        self.assertEqual(result.output, 'Restarting the Mailman runners\n')

    def test_restart_quiet(self):
        with patch('mailman.commands.cli_control.kill_watcher') as mock:
            result = self._command.invoke(restart, ('--quiet',))
        mock.assert_called_once_with(signal.SIGUSR1)
        self.assertEqual(result.output, '')
