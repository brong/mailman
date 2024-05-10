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

"""The runner process."""

import os
import sys
import click
import signal
import logging
import traceback

from mailman.config import config
from mailman.core.i18n import _
from mailman.core.initialize import initialize
from mailman.utilities.modules import find_name
from mailman.utilities.options import I18nCommand, validate_runner_spec
from mailman.version import MAILMAN_VERSION_FULL
from public import public


log = None


# Enable coverage if run under the appropriate test suite.
if os.environ.get('COVERAGE_PROCESS_START') is not None:
    import coverage
    coverage.process_startup()


def make_runner(name, slice, range, once=False):
    # The runner name must be defined in the configuration.  Only runner short
    # names are supported.
    runner_config = getattr(config, 'runner.' + name, None)
    if runner_config is None:
        print(_('Undefined runner name: ${name}'), file=sys.stderr)
        # Exit with SIGTERM exit code so the master won't try to restart us.
        sys.exit(signal.SIGTERM)
    class_path = runner_config['class']
    try:
        runner_class = find_name(class_path)
    except ImportError:
        if os.environ.get('MAILMAN_UNDER_MASTER_CONTROL') is not None:
            print(_('Cannot import runner module: ${class_path}'),
                  file=sys.stderr)
            traceback.print_exc()
            sys.exit(signal.SIGTERM)
        else:
            raise
    if once:
        # Subclass to hack in the setting of the stop flag in _do_periodic()
        class Once(runner_class):
            def _do_periodic(self):
                self.stop()
        return Once(name, slice)
    return runner_class(name, slice)


@click.command(
    cls=I18nCommand,
    context_settings=dict(help_option_names=['-h', '--help']),
    help=_("""\
    Start a runner.

    The runner named on the command line is started, and it can either run
    through its main loop once (for those runners that support this) or
    continuously.  The latter is how the master runner starts all its
    subprocesses.

    -r is required unless -l or -h is given, and its argument must be one of
    the names displayed by the -l switch.

    Normally, this script should be started from `mailman start`.  Running it
    separately or with -o is generally useful only for debugging.  When run
    this way, the environment variable $MAILMAN_UNDER_MASTER_CONTROL will be
    set which subtly changes some error handling behavior.
    """))
@click.option(
    '-C', '--config', 'config_file',
    envvar='MAILMAN_CONFIG_FILE',
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help=_("""\
    Configuration file to use.  If not given, the environment variable
    MAILMAN_CONFIG_FILE is consulted and used if set.  If neither are given, a
    default configuration file is loaded."""))
@click.option(
    '-l', '--list', 'list_runners',
    is_flag=True, is_eager=True, default=False,
    help=_('List the available runner names and exit.'))
@click.option(
    '-o', '--once',
    is_flag=True, default=False,
    help=_("""\
    Run the named runner exactly once through its main loop.  Otherwise, the
    runner runs indefinitely until the process receives a signal.  This is not
    compatible with runners that cannot be run once."""))
@click.option(
    '-r', '--runner', 'runner_spec',
    metavar='runner[:slice:range]',
    callback=validate_runner_spec, default=None,
    help=_("""\

    Start the named runner, which must be one of the strings returned by the -l
    option.

    For runners that manage a queue directory, optional `slice:range` if given
    is used to assign multiple runner processes to that queue.  range is the
    total number of runners for the queue while slice is the number of this
    runner from [0..range).  For runners that do not manage a queue, slice and
    range are ignored.

    When using the `slice:range` form, you must ensure that each runner for the
    queue is given the same range value.  If `slice:runner` is not given, then
    1:1 is used.
    """))
@click.option(
    '-v', '--verbose',
    is_flag=True, default=False,
    help=_('Display more debugging information to the log file.'))
@click.version_option(MAILMAN_VERSION_FULL)
@click.pass_context
@public
def main(ctx, config_file, verbose, list_runners, once, runner_spec):
    # XXX https://github.com/pallets/click/issues/303
    """Start a runner.

    The runner named on the command line is started, and it can either run
    through its main loop once (for those runners that support this) or
    continuously.  The latter is how the master runner starts all its
    subprocesses.

    -r is required unless -l or -h is given, and its argument must be one
    of the names displayed by the -l switch.

    Normally, this script should be started from 'mailman start'.  Running
    it separately or with -o is generally useful only for debugging.  When
    run this way, the environment variable $MAILMAN_UNDER_MASTER_CONTROL
    will be set which subtly changes some error handling behavior.
    """
    global log

    if runner_spec is None and not list_runners:
        ctx.fail(_('No runner name given.'))

    try:
        import setproctitle
        setproctitle.setproctitle(
            f'mailman: runner [{runner_spec[0]}]'
        )  # pragma: nocover
    except ImportError:  # pragma: nocover
        pass

    # Initialize the system.  Honor the -C flag if given.
    initialize(config_file, verbose)
    log = logging.getLogger('mailman.runner')
    if verbose:
        console = logging.StreamHandler(sys.stderr)
        formatter = logging.Formatter(config.logging.root.format,
                                      config.logging.root.datefmt)
        console.setFormatter(formatter)
        logging.getLogger().addHandler(console)
        logging.getLogger().setLevel(logging.DEBUG)

    if list_runners:
        descriptions = {}
        for section in config.runner_configs:
            ignore, dot, shortname = section.name.rpartition('.')
            ignore, dot, classname = getattr(section, 'class').rpartition('.')
            descriptions[shortname] = classname
        longest = max(len(name) for name in descriptions)
        for shortname in sorted(descriptions):
            classname = descriptions[shortname]
            spaces = longest - len(shortname)
            name = (' ' * spaces) + shortname       # noqa: F841
            print(_('${name} runs ${classname}'))
        sys.exit(0)

    runner = make_runner(*runner_spec, once=once)
    runner.set_signals()
    # Now start up the main loop
    log.info('{} runner started.'.format(runner.name))
    runner.run()
    log.info('{} runner exiting.'.format(runner.name))
    sys.exit(runner.status)
