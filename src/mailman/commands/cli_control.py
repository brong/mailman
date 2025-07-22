# Copyright (C) 2009-2025 by the Free Software Foundation, Inc.
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

"""Start/stop/reopen/restart commands."""

import os
import sys
import click
import errno
import signal
import logging

from mailman.bin.master import master_state, WatcherState
from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.modules import call_name
from mailman.utilities.options import I18nCommand
from public import public
from zope.interface import implementer


qlog = logging.getLogger('mailman.runner')


@click.command(
    cls=I18nCommand,
    help=_('Start the Mailman master and runner processes.'))
@click.option(
    '--force', '-f',
    is_flag=True, default=False,
    help=_("""\
    If the master watcher finds an existing master lock, it will normally exit
    with an error message.  With this option, the master will perform an extra
    level of checking.  If a process matching the host/pid described in the
    lock file is running, the master will still exit, requiring you to manually
    clean up the lock.  But if no matching process is found, the master will
    remove the apparently stale lock and make another attempt to claim the
    master lock."""))
@click.option(
    '--generate-alias-file', '-g',
    is_flag=True, default=True,
    help=_("""\
    Generate the MTA alias files upon startup. Some MTA, like postfix, can't
    deliver email if alias files mentioned in its configuration are not
    present. In some situations, this could lead to a deadlock at the first
    start of mailman3 server. Setting this option to true will make this
    script create the files and thus allow the MTA to operate smoothly."""))
@click.option(
    '--run-as-user', '-u',
    is_flag=True, default=True,
    help=_("""\
    Normally, this script will refuse to run if the user id and group id are
    not set to the 'mailman' user and group (as defined when you configured
    Mailman).  If run as root, this script will change to this user and group
    before the check is made.

    This can be inconvenient for testing and debugging purposes, so the -u flag
    means that the step that sets and checks the uid/gid is skipped, and the
    program is run as the current user and group.  This flag is not recommended
    for normal production environments.

    Note though, that if you run with -u and are not in the mailman group, you
    may have permission problems, such as being unable to delete a list's
    archives through the web.  Tough luck!"""))
@click.option(
    '--quiet', '-q',
    is_flag=True, default=False,
    help=_("""\
    Don't print status messages.  Error messages are still printed to standard
    error."""))
@click.pass_context
def start(ctx, force, generate_alias_file, run_as_user, quiet):
    # Although there's a potential race condition here, it's a better user
    # experience for the parent process to refuse to start twice, rather than
    # having it try to start the master, which will error exit.
    status, lock = master_state()
    if status is WatcherState.conflict:
        ctx.fail(_('GNU Mailman is already running'))
    elif status in (WatcherState.stale_lock, WatcherState.host_mismatch):
        if not force:
            ctx.fail(
                _('A previous run of GNU Mailman did not exit '
                  'cleanly ({}).  Try using --force'.format(status.name)))
    # Daemon process startup according to Stevens, Advanced Programming in the
    # UNIX Environment, Chapter 13.
    pid = os.fork()
    if pid:
        # parent
        if not quiet:
            print(_("Starting Mailman's master runner"))
        if generate_alias_file:
            if not quiet:
                print(_("Generating MTA alias maps"))
            call_name(config.mta.incoming).regenerate()
        return
    # child: Create a new session and become the session leader, but since we
    # won't be opening any terminal devices, don't do the ultra-paranoid
    # suggestion of doing a second fork after the setsid() call.
    os.setsid()
    # Instead of cd'ing to root, cd to the Mailman runtime directory.  However,
    # before we do that, set an environment variable used by the subprocesses
    # to calculate their path to the $VAR_DIR.
    os.environ['MAILMAN_VAR_DIR'] = config.VAR_DIR
    os.chdir(config.VAR_DIR)
    # Exec the master watcher.
    execl_args = [
        sys.executable, sys.executable,
        os.path.join(config.BIN_DIR, 'master'),
        ]
    if force:
        execl_args.append('--force')
    # Always pass the configuration file path to the master process, so there's
    # no confusion about which one is being used.
    execl_args.extend(['-C', config.filename])
    qlog.debug('starting: %s', execl_args)
    os.execl(*execl_args)
    # We should never get here.
    raise RuntimeError('os.execl() failed')


@public
@implementer(ICLISubCommand)
class Start:
    name = 'start'
    command = start


def kill_watcher(sig):
    try:
        with open(config.PID_FILE) as fp:
            pid = int(fp.read().strip())
    except (IOError, ValueError) as error:        # pragma: nocover
        # For i18n convenience
        print(_('PID unreadable in: ${config.PID_FILE}'), file=sys.stderr)
        print(error, file=sys.stderr)
        print(_('Is the master even running?'), file=sys.stderr)
        return
    try:
        os.kill(pid, sig)
    except OSError as error:                      # pragma: nocover
        if error.errno != errno.ESRCH:
            raise
        print(_('No child with pid: ${pid}'), file=sys.stderr)
        print(error, file=sys.stderr)
        print(_('Stale pid file removed.'), file=sys.stderr)
        os.unlink(config.PID_FILE)


@click.command(
    cls=I18nCommand,
    help=_('Stop the Mailman master and runner processes.'))
@click.option(
    '--quiet', '-q',
    is_flag=True, default=False,
    help=_("""\
    Don't print status messages.  Error messages are still printed to standard
    error."""))
def stop(quiet):
    if not quiet:
        print(_("Shutting down Mailman's master runner"))
    kill_watcher(signal.SIGTERM)


@public
@implementer(ICLISubCommand)
class Stop:
    name = 'stop'
    command = stop


@click.command(
    cls=I18nCommand,
    help=_('Signal the Mailman processes to re-open their log files.'))
@click.option(
    '--quiet', '-q',
    is_flag=True, default=False,
    help=_("""\
    Don't print status messages.  Error messages are still printed to standard
    error."""))
def reopen(quiet):
    if not quiet:
        print(_('Reopening the Mailman runners'))
    kill_watcher(signal.SIGHUP)


@public
@implementer(ICLISubCommand)
class Reopen:
    name = 'reopen'
    command = reopen


@click.command(
    cls=I18nCommand,
    help=_('Stop and restart the Mailman runner subprocesses.'))
@click.option(
    '--quiet', '-q',
    is_flag=True, default=False,
    help=_("""\
    Don't print status messages.  Error messages are still printed to standard
    error."""))
def restart(quiet):
    if not quiet:
        print(_('Restarting the Mailman runners'))
    kill_watcher(signal.SIGUSR1)


@public
@implementer(ICLISubCommand)
class Restart:
    name = 'restart'
    command = restart
