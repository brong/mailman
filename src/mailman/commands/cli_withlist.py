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

"""The `mailman shell` subcommand."""

import re
import sys
import click

from contextlib import ExitStack, suppress
from lazr.config import as_boolean
from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.utilities.interact import DEFAULT_BANNER, interact
from mailman.utilities.modules import call_name
from mailman.utilities.options import I18nCommand
from public import public
from string import Template
from traceback import print_exc
from zope.component import getUtility
from zope.interface import implementer


# Global holding onto the open mailing list.
m = None
# Global holding the results of --run.
r = None


def start_ipython1(overrides, banner, *, debug=False):
    try:
        from IPython.frontend.terminal.embed import InteractiveShellEmbed
    except ImportError:
        if debug:
            print_exc()
        return None
    return InteractiveShellEmbed.instance(banner1=banner, user_ns=overrides)


def start_ipython4(overrides, banner, *, debug=False):
    try:
        from IPython.terminal.embed import InteractiveShellEmbed
    except ImportError:
        if debug:
            print_exc()
        return None
    return InteractiveShellEmbed.instance(banner1=banner, user_ns=overrides)


def start_ipython(overrides, banner, debug):
    shell = None
    for starter in (start_ipython4, start_ipython1):
        shell = starter(overrides, banner, debug=debug)
        if shell is not None:
            shell()
            break
    else:
        print(_('ipython is not available, set use_ipython to no'))


def start_python(overrides, banner):
    # Set the tab completion.
    with ExitStack() as resources:
        try:                                    # pragma: nocover
            import readline  # noqa: F401,E401
            import rlcompleter  # noqa: F401
        except ImportError:                     # pragma: nocover
            print(_('readline not available'), file=sys.stderr)
            pass
        else:
            readline.parse_and_bind('tab: complete')
            history_file_template = config.shell.history_file.strip()
            if len(history_file_template) > 0:
                # Expand substitutions.
                substitutions = {
                    key.lower(): value
                    for key, value in config.paths.items()
                    }
                history_file = Template(
                    history_file_template).safe_substitute(substitutions)
                with suppress(FileNotFoundError):
                    readline.read_history_file(history_file)
                resources.callback(
                    readline.write_history_file,
                    history_file)
        sys.ps1 = config.shell.prompt + ' '
        interact(upframe=False, banner=banner, overrides=overrides)


def do_interactive(ctx, banner):
    overrides = dict(
        m=m,
        commit=config.db.commit,
        abort=config.db.abort,
        config=config,
        getUtility=getUtility
        )
    # Bootstrap some useful names into the namespace, mostly to make
    # the component architecture and interfaces easily available.
    for module_name in sys.modules:
        if not module_name.startswith('mailman.interfaces.'):
            continue
        module = sys.modules[module_name]
        for name in module.__all__:
            overrides[name] = getattr(module, name)
    banner = config.shell.banner + '\n' + (
        banner if isinstance(banner, str) else '')
    try:
        use_ipython = as_boolean(config.shell.use_ipython)
    except ValueError:
        if config.shell.use_ipython == 'debug':
            use_ipython = True
            debug = True
        else:
            print(_('Invalid value for [shell]use_python: {}').format(
                config.shell.use_ipython), file=sys.stderr)
            return
    else:
        debug = False
    if use_ipython:
        start_ipython(overrides, banner, debug)
    else:
        start_python(overrides, banner)


def show_detailed_help(ctx, param, value):
    if not value:
        # Returning None tells click to process the rest of the command line.
        return
    # Split this up into paragraphs for easier translation.
    print(_("""\
This script provides you with a general framework for interacting with a
mailing list."""))
    print()
    print(_("""\
There are two ways to use this script: interactively or programmatically.
Using it interactively allows you to play with, examine and modify a mailing
list from Python's interactive interpreter.  When running interactively, the
variable 'm' will be available in the global namespace.  It will reference the
mailing list object."""))
    print()
    print(_("""\
Programmatically, you can write a function to operate on a mailing list, and
this script will take care of the housekeeping (see below for examples).  In
that case, the general usage syntax is:

% mailman withlist [options] -l listspec [args ...]

where `listspec` is either the posting address of the mailing list
(e.g. ant@example.com), or the List-ID (e.g. ant.example.com)."""))
    print()
    print(_("""\
Here's an example of how to use the --run option.  Say you have a file in the
Mailman installation directory called 'listaddr.py', with the following two
functions:

def listaddr(mlist):
    print(mlist.posting_address)

def requestaddr(mlist):
    print(mlist.request_address)

Run methods take at least one argument, the mailing list object to operate
on.  Any additional arguments given on the command line are passed as
positional arguments to the callable.

If -l is not given then you can run a function that takes no arguments.
"""))
    print()
    print(_("""\
You can print the list's posting address by running the following from the
command line:

% mailman withlist -r listaddr -l ant@example.com
Importing listaddr ...
Running listaddr.listaddr() ...
ant@example.com"""))
    print()
    print(_("""\
And you can print the list's request address by running:

% mailman withlist -r listaddr.requestaddr -l ant@example.com
Importing listaddr ...
Running listaddr.requestaddr() ...
ant-request@example.com"""))
    print()
    print(_("""\
As another example, say you wanted to change the display name for a particular
mailing list.  You could put the following function in a file called
`change.py`:

def change(mlist, display_name):
    mlist.display_name = display_name

and run this from the command line:

% mailman withlist -r change -l ant@example.com 'My List'

Note that you do not have to explicitly commit any database transactions, as
Mailman will do this for you (assuming no errors occured)."""))
    sys.exit(0)


@click.command(
    cls=I18nCommand,
    help=_("""\
    Operate on a mailing list.

    For detailed help, see --details
    """))
@click.option(
    '--interactive', '-i',
    is_flag=True, default=None,
    help=_("""\
    Leaves you at an interactive prompt after all other processing is complete.
    This is the default unless the --run option is given."""))
@click.option(
    '--run', '-r',
    help=_("""\

    Run a script.  The argument is the module path to a callable.  This
    callable will be imported and then, if --listspec/-l is also given, is
    called with the mailing list as the first argument.  If additional
    arguments are given at the end of the command line, they are passed as
    subsequent positional arguments to the callable.  For additional help, see
    --details.

    If no --listspec/-l argument is given, the script function being called is
    called with no arguments.
    """))
@click.option(
    '--details',
    is_flag=True, default=False, is_eager=True, expose_value=False,
    callback=show_detailed_help,
    help=_('Print detailed instructions and exit.'))
# Optional positional argument.
@click.option(
    '--listspec', '-l',
    help=_("""\
    A specification of the mailing list to operate on.  This may be the posting
    address of the list, or its List-ID.  The argument can also be a Python
    regular expression, in which case it is matched against both the posting
    address and List-ID of all mailing lists.  To use a regular expression,
    LISTSPEC must start with a ^ (and the matching is done with re.match().
    LISTSPEC cannot be a regular expression unless --run is given."""))
@click.argument('run_args', nargs=-1)
@click.pass_context
def shell(ctx, interactive, run, listspec, run_args):
    global m, r
    banner = DEFAULT_BANNER
    # Interactive is the default unless --run was given.
    interactive = (run is None) if interactive is None else interactive
    # List name cannot be a regular expression if --run is not given.
    if listspec and listspec.startswith('^') and not run:
        ctx.fail(_('Regular expression requires --run'))
    # Handle --run.
    list_manager = getUtility(IListManager)
    if run:
        # When the module and the callable have the same name, a shorthand
        # without the dot is allowed.
        dotted_name = (run if '.' in run else '{0}.{0}'.format(run))
        if listspec is None:
            r = call_name(dotted_name, *run_args)
        elif listspec.startswith('^'):
            r = {}
            cre = re.compile(listspec, re.IGNORECASE)
            for mlist in list_manager.mailing_lists:
                if cre.match(mlist.fqdn_listname) or cre.match(mlist.list_id):
                    results = call_name(dotted_name, mlist, *run_args)
                    r[mlist.list_id] = results
        else:
            m = list_manager.get(listspec)
            if m is None:
                ctx.fail(_('No such list: ${listspec}'))
            r = call_name(dotted_name, m, *run_args)
    else:
        # Not --run.
        if listspec is not None:
            m = list_manager.get(listspec)
            if m is None:
                ctx.fail(_('No such list: ${listspec}'))
            banner = _("The variable 'm' is the ${listspec} mailing list")
    # All other processing is finished; maybe go into interactive mode.
    if interactive:
        do_interactive(ctx, banner)


@public
@implementer(ICLISubCommand)
class Withlist:
    name = 'withlist'
    command = shell


@public
class Shell(Withlist):
    name = 'shell'
    command = shell
