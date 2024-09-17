# Copyright (C) 2001-2023 by the Free Software Foundation, Inc.
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

"""Master subprocess watcher."""

import os
import sys
import click
import signal
import socket
import logging

from datetime import timedelta
from enum import Enum
from flufl.lock import Lock, NotLockedError, TimeOutError
from lazr.config import as_boolean
from mailman.config import config
from mailman.core.i18n import _
from mailman.core.initialize import initialize
from mailman.core.logging import reopen
from mailman.utilities.options import I18nCommand, validate_runner_spec
from mailman.version import MAILMAN_VERSION_FULL
from public import public


DOT = '.'
LOCK_LIFETIME = timedelta(days=1, hours=6)
SECONDS_IN_A_DAY = 86400
SUBPROC_START_WAIT = timedelta(seconds=20)

# Environment variables to forward into subprocesses.
#
# This is used by the test framework, but also to make sure spawned
# Python interpreters behave the same way as ours.
PRESERVE_ENVS = {
    'COVERAGE_PROCESS_START',
    'LANG',
    'LANGUAGE',
    'LC_ADDRESS',
    'LC_ALL',
    'LC_COLLATE',
    'LC_CTYPE',
    'LC_IDENTIFICATION',
    'LC_MEASUREMENT',
    'LC_MESSAGES',
    'LC_MONETARY',
    'LC_NAME',
    'LC_NUMERIC',
    'LC_PAPER',
    'LC_TELEPHONE',
    'LC_TIME',
    'LOCALE_ARCHIVE',
    'MAILMAN_EXTRA_TESTING_CFG',
    'MAILMAN_VAR_DIR',

    # These variables tweak the behavior of the Python interpreter.
    # If the user specifies them for the master process, they should
    # also be applied to the started runners.
    'PYTHONASYNCIODEBUG',
    'PYTHONBREAKPOINT',
    'PYTHONDEBUG',
    'PYTHONDONTWRITEBYTECODE',
    'PYTHONHOME',
    'PYTHONIOENCODING',
    'PYTHONMALLOC',
    'PYTHONMALLOCSTATS',
    'PYTHONNOUSERSITE',
    'PYTHONOPTIMIZE',
    'PYTHONPATH',
    'PYTHONPLATLIBDIR',
    'PYTHONPROFILEIMPORTTIME',
    'PYTHONSAFEPATH',
    'PYTHONTRACEMALLOC',
    'PYTHONUNBUFFERED',
    'PYTHONUSERBASE',
    'PYTHONVERBOSE',
    'PYTHONWARNINGS',
    }


@public
class WatcherState(Enum):
    """Enum for the state of the master process watcher."""
    # No lock has been acquired by any process.
    none = 0
    # Another master watcher is running.
    conflict = 1
    # No conflicting process exists.
    stale_lock = 2
    # Hostname from lock file doesn't match.
    host_mismatch = 3


@public
def master_state(lock_file=None):
    """Get the state of the master watcher.

    :param lock_file: Path to the lock file, otherwise `config.LOCK_FILE`.
    :type lock_file: str
    :return: 2-tuple of the WatcherState describing the state of the lock
        file, and the lock object.
    """
    if lock_file is None:
        lock_file = config.LOCK_FILE
    # We'll never acquire the lock, so the lifetime doesn't matter.
    lock = Lock(lock_file)
    try:
        hostname, pid, tempfile = lock.details
    except NotLockedError:
        return WatcherState.none, lock
    if hostname != socket.getfqdn():
        return WatcherState.host_mismatch, lock
    # Find out if the process exists by calling kill with a signal 0.
    try:
        os.kill(pid, 0)
        return WatcherState.conflict, lock
    except (ProcessLookupError, PermissionError):
        # No matching process id or pid not Mailman's.
        return WatcherState.stale_lock, lock


def acquire_lock_1(force, lock_file=None):
    """Try to acquire the master lock.

    :param force: Flag that controls whether to force acquisition of the lock.
    :type force: bool
    :param lock_file: Path to the lock file, otherwise `config.LOCK_FILE`.
    :type lock_file: str
    :return: The master lock.
    :raises: `TimeOutError` if the lock could not be acquired.
    """
    if lock_file is None:
        lock_file = config.LOCK_FILE
    lock = Lock(lock_file, LOCK_LIFETIME)
    try:
        lock.lock(timedelta(seconds=0.1))
        return lock
    except TimeOutError:
        if not force:
            raise
        # Force removal of lock first.
        hostname, pid, tempfile = lock.details
        os.unlink(lock_file)
        # Also remove any stale claim files.
        dname = os.path.dirname(lock_file)
        for fname in os.listdir(dname):
            fpath = os.path.join(dname, fname)
            if fpath.startswith(lock_file):
                os.unlink(fpath)
        return acquire_lock_1(force=False)


def acquire_lock(force):
    """Acquire the master lock.

    :param force: Flag that controls whether to force acquisition of the lock.
    :type force: bool
    :return: The master runner lock or None if the lock couldn't be acquired.
        In that case, an error messages is also printed to standard error.
    """
    try:
        lock = acquire_lock_1(force)
        return lock
    except TimeOutError:
        status, lock = master_state()
        if status is WatcherState.conflict:
            # Hostname matches and process exists.
            message = _("""\
The master lock could not be acquired because it appears as though another
master is already running.""")
        elif status is WatcherState.stale_lock:
            # Hostname matches but the process does not exist.
            program = sys.argv[0]                   # noqa: F841
            message = _("""\
The master lock could not be acquired.  It appears as though there is a stale
master lock.  Try re-running ${program} with the --force flag.""")
        elif status is WatcherState.host_mismatch:
            # Hostname doesn't even match.
            hostname, pid, tempfile = lock.details
            message = _("""\
The master lock could not be acquired, because it appears as if some process
on some other host may have acquired it.  We can't test for stale locks across
host boundaries, so you'll have to clean this up manually.

Lock file: ${config.LOCK_FILE}
Lock host: ${hostname}

Exiting.""")
        else:
            assert status is WatcherState.none, (
                'Invalid enum value: {}'.format(status))
            hostname, pid, tempfile = lock.details
            message = _("""\
For unknown reasons, the master lock could not be acquired.

Lock file: ${config.LOCK_FILE}
Lock host: ${hostname}

Exiting.""")
        print(message, file=sys.stderr)
        sys.exit(1)


class PIDWatcher:
    """A class which safely manages child process ids."""

    def __init__(self):
        self._pids = {}

    def __contains__(self, pid):
        return pid in self._pids.keys()

    def __iter__(self):
        # Safely iterate over all the keys in the dictionary.  Because
        # asynchronous signals are involved, the dictionary's size could
        # change during iteration.  Iterate over a copy of the keys to avoid
        # that.
        for pid in list(self._pids):
            yield pid

    def add(self, pid, info):
        """Add process information.

        :param pid: The process id.  The watcher must not already be tracking
            this process id.
        :type pid: int
        :param info: The process information.
        :type info: 4-tuple consisting of
            (runner-name, slice-number, slice-count, restart-count)
        """
        old_info = self._pids.get(pid)
        assert old_info is None, (
            'Duplicate process id {0} with existing info: {1}'.format(
                pid, old_info))
        self._pids[pid] = info

    def pop(self, pid):
        """Remove and return existing process information.

        :param pid: The process id.  The watcher must already be tracking this
            process id.
        :type pid: int
        :return: The process information.
        :rtype: 4-tuple consisting of
            (runner-name, slice-number, slice-count, restart-count)
        :raise KeyError: if the process id is not being tracked.
        """
        return self._pids.pop(pid)

    def drop(self, pid):
        """Remove and return existing process information.

        This is like `pop()` except that no `KeyError` is raised if the
        process id is not being tracked.

        :param pid: The process id.
        :type pid: int
        :return: The process information, or None if the process id is not
            being tracked.
        :rtype: 4-tuple consisting of
            (runner-name, slice-number, slice-count, restart-count)
        """
        return self._pids.pop(pid, None)


@public
class Loop:
    """Main control loop class."""

    def __init__(self, lock=None, restartable=None, config_file=None):
        self._log = logging.getLogger('mailman.runner')
        self._lock = lock
        self._restartable = restartable
        self._config_file = config_file
        self._kids = PIDWatcher()

    def install_signal_handlers(self):
        """Install various signals handlers for control from the master."""
        # Set up our signal handlers.  Also set up a SIGALRM handler to
        # refresh the lock once per day.  The lock lifetime is 1 day + 6 hours
        # so this should be plenty.
        def sigalrm_handler(signum, frame):                      # noqa: E306
            self._lock.refresh()
            signal.alarm(SECONDS_IN_A_DAY)
        signal.signal(signal.SIGALRM, sigalrm_handler)
        signal.alarm(SECONDS_IN_A_DAY)
        signal.signal(signal.SIGHUP, self._sighup_handler)
        signal.signal(signal.SIGUSR1, self._sigusr1_handler)
        signal.signal(signal.SIGTERM, self._sigterm_handler)
        signal.signal(signal.SIGINT, self._sigint_handler)

    def _sighup_handler(self, signum, frame):
        """Handles SIGHUP.

        SIGHUP tells the runners to close and reopen their log files.
        """
        reopen()
        for pid in self._kids:
            try:
                os.kill(pid, signal.SIGHUP)
            except ProcessLookupError:                       # pragma: nocover
                pass
        self._log.info('Master watcher caught SIGHUP.  Re-opening log files.')

    def _sigusr1_handler(self, signum, frame):
        """Handles SIGUSR1.

        SIGUSR1 is used by 'mailman restart'.
        """
        for pid in self._kids:
            try:
                os.kill(pid, signal.SIGUSR1)
            except ProcessLookupError:                       # pragma: nocover
                pass
        self._log.info('Master watcher caught SIGUSR1.  Restarting.')

    def _sigterm_handler(self, signum, frame):
        """Handles SIGTERM.

        SIGTERM is what init will kill this process with when changing
        run levels.  It's also the signal 'mailman stop' uses.
        """
        # Clear the flag so that we won't try to restart the runners.
        self._restartable = False
        for pid in self._kids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:                       # pragma: nocover
                pass
        self._log.info('Master watcher caught SIGTERM.  Exiting.')

    def _sigint_handler(self, signum, frame):
        """Handles SIGINT.

        SIGINT is what control-C gives.
        """
        # Clear the flag so that we won't try to restart the runners.
        self._restartable = False
        for pid in self._kids:
            try:
                os.kill(pid, signal.SIGINT)
            except ProcessLookupError:                       # pragma: nocover
                pass
        self._log.info('Master watcher caught SIGINT.  Exiting.')

    def _start_runner(self, spec):
        """Start a runner.

        All arguments are passed to the process.

        :param spec: A runner spec, in a format acceptable to
            bin/runner's --runner argument, e.g. name:slice:count
        :type spec: string
        :return: The process id of the child runner.
        :rtype: int
        """
        pid = os.fork()
        if pid:
            # Parent.
            return pid
        # Child.
        #
        # Preserve some environment variables.
        env = {k: v for k, v in os.environ.items()     # pragma: nocover
               if k in PRESERVE_ENVS}

        # Set the environment variable which tells the runner that it's
        # running under bin/master control.  This subtly changes the error
        # behavior of bin/runner.
        env['MAILMAN_UNDER_MASTER_CONTROL'] = '1'      # pragma: nocover

        # Craft the command line arguments for the exec() call.
        rswitch = '--runner=' + spec
        # Always pass the explicit path to the configuration file to the
        # sub-runners.  This avoids any debate about which cfg file is used.
        config_file = (config.filename if self._config_file is None
                       else self._config_file)
        # Wherever master lives, so too must live the runner script.
        exe = os.path.join(config.BIN_DIR, 'runner')   # pragma: nocover
        # config.PYTHON, which is the absolute path to the Python interpreter,
        # must be given as argv[0] due to Python's library search algorithm.
        args = [sys.executable, sys.executable, exe,   # pragma: nocover
                '-C', config_file, rswitch]
        log = logging.getLogger('mailman.runner')
        log.debug('starting: %s', args)
        args.append(env)
        os.execle(*args)
        # We should never get here.
        raise RuntimeError('os.execle() failed')

    def start_runners(self, runner_names=None):
        """Start all the configured runners.

        :param runners: If given, a sequence of runner names to start.  If not
            given, this sequence is taken from the configuration file.
        :type runners: a sequence of strings
        """
        if not runner_names:
            runner_names = []
            for runner_config in config.runner_configs:
                # Strip off the 'runner.' prefix.
                assert runner_config.name.startswith('runner.'), (
                    'Unexpected runner configuration section name: {}'.format(
                        runner_config.name))
                runner_names.append(runner_config.name[7:])
        # For each runner we want to start, find their config section, which
        # will tell us the name of the class to instantiate, along with the
        # number of hash space slices to manage.
        for name in runner_names:
            section_name = 'runner.' + name
            # Let AttributeError propagate.
            runner_config = getattr(config, section_name)
            if not as_boolean(runner_config.start):
                continue
            # Find out how many runners to instantiate.  This must be a power
            # of 2.
            count = int(runner_config.instances)
            assert (count & (count - 1)) == 0, (
                'Runner "{0}", not a power of 2: {1}'.format(name, count))
            for slice_number in range(count):
                # runner name, slice #, # of slices, restart count
                info = (name, slice_number, count, 0)
                spec = '{0}:{1:d}:{2:d}'.format(name, slice_number, count)
                pid = self._start_runner(spec)
                log = logging.getLogger('mailman.runner')
                log.debug('[{0:d}] {1}'.format(pid, spec))
                self._kids.add(pid, info)

    def _pause(self):
        """Sleep until a signal is received."""
        # Sleep until a signal is received.  This prevents the master from
        # exiting immediately even if there are no runners (as happens in the
        # test suite).  We install a handler for SIGCHLD so that pause(2)
        # will return control to us.
        signal.signal(signal.SIGCHLD,
                      lambda sig, stack: None)               # pragma: nocover
        signal.pause()
        # Note: We cannot set the signal handler to SIG_IGN again,
        # because since POSIX.1-2001 this changes the semantics of
        # wait(2) to not return until all children have exited.

    def loop(self):
        """Main loop.

        Wait until all the runner subprocesses have exited, restarting them if
        necessary and configured to do so.
        """
        log = logging.getLogger('mailman.runner')
        log.info('Master started')
        self._pause()
        while True:
            try:
                pid, status = os.wait()
            except ChildProcessError:
                # No children?  We're done.
                break
            except InterruptedError:                         # pragma: nocover
                # If the system call got interrupted, just restart it.
                continue
            if pid not in self._kids:                        # pragma: nocover
                # This is not a runner subprocess that we own.  E.g. maybe a
                # plugin started it.
                continue
            # Find out why the subprocess exited by getting the signal
            # received or exit status.
            if os.WIFSIGNALED(status):
                why = os.WTERMSIG(status)                    # pragma: nocover
                sig_or_exit = 'SIGNAL '                      # pragma: nocover
            elif os.WIFEXITED(status):
                why = os.WEXITSTATUS(status)
                sig_or_exit = 'EXIT '
            else:                                            # pragma: nocover
                why = None
                sig_or_exit = 'UNKNOWN'
            # We'll restart the subprocess if it exited with a SIGUSR1 or
            # because of a failure (i.e. no exit signal), and the no-restart
            # command line switch was not given.  This lets us better handle
            # runaway restarts (e.g.  if the subprocess had a syntax error!)
            rname, slice_number, count, restarts = self._kids.pop(pid)
            config_name = 'runner.' + rname
            restart = self._restartable
            # Don't restart if the runner explicitly asks not to be
            # restarted.
            if why == signal.SIGTERM:
                # Note: See bin/runner.py and core/runner.py where
                # this signaling is used.
                restart = False
            # Have we hit the maximum number of restarts?
            if not why == signal.SIGUSR1:
                # Note: Explicit restarts using SIGUSR1 should not be
                # counted here, because max_restarts only limits the
                # number of automated restarts (see
                # mailman/config/schema.cfg).
                restarts += 1
            max_restarts = int(getattr(config, config_name).max_restarts)
            if restarts > max_restarts:
                restart = False
            # Are we permanently non-restartable?
            log.debug("""\
Master detected subprocess exit
(pid: {0:d}, why: {1}{2}, class: {3}, slice: {4:d}/{5:d}) {6}""".format(
                     pid, sig_or_exit, why, rname, slice_number + 1, count,
                     ('[restarting]' if restart else '')))
            # See if we've reached the maximum number of allowable restarts.
            if restarts > max_restarts:
                log.info("""\
Runner {0} reached maximum restart limit of {1:d}, not restarting.""".format(
                         rname, max_restarts))
            # Now perhaps restart the process unless it exited with a
            # SIGTERM or we aren't restarting.
            if restart:
                spec = '{0}:{1:d}:{2:d}'.format(rname, slice_number, count)
                new_pid = self._start_runner(spec)
                new_info = (rname, slice_number, count, restarts)
                self._kids.add(new_pid, new_info)
        log.info('Master stopped')

    def cleanup(self):
        """Ensure that all children have exited."""
        log = logging.getLogger('mailman.runner')
        # Send SIGTERMs to all the child processes and wait for them all to
        # exit.
        for pid in self._kids:
            try:
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:              # pragma: nocover
                # The child has already exited.
                log.info('ESRCH on pid: %d', pid)
            except OSError:                         # pragma: nocover
                # XXX I'm not so sure about this.  It preserves the semantics
                # before conversion to PEP 3151 exceptions.  But is it right?
                pass
        # Wait for all the children to go away.
        while self._kids:
            try:
                pid, status = os.wait()
                self._kids.drop(pid)
            except ChildProcessError:
                break
            except InterruptedError:                # pragma: nocover
                continue


@click.command(
    cls=I18nCommand,
    context_settings=dict(help_option_names=['-h', '--help']),
    help=_("""\
    Master subprocess watcher.

    Start and watch the configured runners, ensuring that they stay alive and
    kicking.  Each runner is forked and exec'd in turn, with the master waiting
    on their process ids.  When it detects a child runner has exited, it may
    restart it.

    The runners respond to SIGINT, SIGTERM, SIGUSR1 and SIGHUP.  SIGINT,
    SIGTERM and SIGUSR1 all cause a runner to exit cleanly.  The master will
    restart runners that have exited due to a SIGUSR1 or some kind of other
    exit condition (say because of an uncaught exception).  SIGHUP causes the
    master and the runners to close their log files, and reopen then upon the
    next printed message.

    The master also responds to SIGINT, SIGTERM, SIGUSR1 and SIGHUP, which it
    simply passes on to the runners.  Note that the master will close and
    reopen its own log files on receipt of a SIGHUP.  The master also leaves
    its own process id in the file specified in the configuration file but you
    normally don't need to use this PID directly."""))
@click.option(
    '-C', '--config', 'config_file',
    envvar='MAILMAN_CONFIG_FILE',
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help=_("""\
    Configuration file to use.  If not given, the environment variable
    MAILMAN_CONFIG_FILE is consulted and used if set.  If neither are given, a
    default configuration file is loaded."""))
@click.option(
    '--no-restart', '-n', 'restartable',
    is_flag=True, default=True,
    help=_("""\
    Don't restart the runners when they exit because of an error or a SIGUSR1.
    Use this only for debugging."""))
@click.option(
    '--force', '-f',
    is_flag=True, default=False,
    help=_("""\
    If the master watcher finds an existing master lock, it will normally exit
    with an error message.  With this option,the master will perform an extra
    level of checking.  If a process matching the host/pid described in the
    lock file is running, the master will still exit, requiring you to manually
    clean up the lock.  But if no matching process is found, the master will
    remove the apparently stale lock and make another attempt to claim the
    master lock."""))
@click.option(
    '--runners', '-r',
    metavar='runner[:slice:range]',
    callback=validate_runner_spec, default=None,
    multiple=True,
    help=_("""\
    Override the default set of runners that the master will invoke, which is
    typically defined in the configuration file.  Multiple -r options may be
    given.  The values for -r are passed straight through to bin/runner."""))
@click.option(
    '-v', '--verbose',
    is_flag=True, default=False,
    help=_('Display more debugging information to the log file.'))
@click.version_option(MAILMAN_VERSION_FULL)
@public
def main(config_file, restartable, force, runners, verbose):
    # XXX https://github.com/pallets/click/issues/303
    """Master subprocess watcher.

    Start and watch the configured runners and ensure that they stay
    alive and kicking.  Each runner is forked and exec'd in turn, with
    the master waiting on their process ids.  When it detects a child
    runner has exited, it may restart it.

    The runners respond to SIGINT, SIGTERM, SIGUSR1 and SIGHUP.  SIGINT,
    SIGTERM and SIGUSR1 all cause a runner to exit cleanly.  The master
    will restart runners that have exited due to a SIGUSR1 or some kind
    of other exit condition (say because of an uncaught exception).
    SIGHUP causes the master and the runners to close their log files,
    and reopen then upon the next printed message.

    The master also responds to SIGINT, SIGTERM, SIGUSR1 and SIGHUP,
    which it simply passes on to the runners.  Note that the master will
    close and reopen its own log files on receipt of a SIGHUP.  The
    master also leaves its own process id in the file `data/master.pid`
    but you normally don't need to use this pid directly.
    """
    try:
        import setproctitle
        setproctitle.setproctitle('mailman: master')  # pragma: nocover
    except ImportError:  # pragma: nocover
        pass

    initialize(config_file, verbose)
    # Acquire the master lock, exiting if we can't.  We'll let the caller
    # handle any clean up or lock breaking.  No `with` statement here because
    # Lock's constructor doesn't support a timeout.
    lock = acquire_lock(force)
    try:
        with open(config.PID_FILE, 'w') as fp:
            print(os.getpid(), file=fp)
        loop = Loop(lock, restartable, config.filename)
        loop.install_signal_handlers()
        try:
            loop.start_runners(runners)
            loop.loop()
        finally:
            loop.cleanup()
            os.remove(config.PID_FILE)
    finally:
        lock.unlock()
