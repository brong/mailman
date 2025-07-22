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

"""The 'mailman' command dispatcher."""
import os
import click

from mailman.commands.cli_help import help as help_command
from mailman.config import config
from mailman.core.i18n import _
from mailman.core.initialize import initialize
from mailman.database.transaction import transaction
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.modules import add_components
from mailman.version import MAILMAN_VERSION_FULL
from public import public


class Subcommands(click.MultiCommand):
    # Handle dynamic listing and loading of `mailman` subcommands.
    def __init__(self, *args, **kws):
        super().__init__(*args, **kws)
        self._commands = {}
        self._loaded = False

    def _load(self):
        # Load commands lazily as commands in plugins can only be found after
        # the configuration file is loaded.
        if not self._loaded:
            add_components('commands', ICLISubCommand, self._commands)
            self._loaded = True

    def list_commands(self, ctx):                    # pragma: nocover
        self._load()
        return sorted(self._commands)

    def get_command(self, ctx, name):
        self._load()
        try:
            return self._commands[name].command
        except KeyError:
            # Returning None here signals click to report usage information
            # and a "No such command" error message.
            return None

    # This is here to hook command parsing into the Mailman database
    # transaction system.  If the subcommand succeeds, the transaction is
    # committed, otherwise it's aborted.
    # See https://github.com/pallets/click/issues/1134
    def invoke(self, ctx):
        # If given a bogus subcommand, the database won't have been
        # initialized so there's no transaction to commit.
        if config.db is not None:
            with transaction():
                return super().invoke(ctx)
        return super().invoke(ctx)             # pragma: missed

    # https://github.com/pallets/click/issues/834
    #
    # Note that this only handles the case for the `mailman --help` output.
    # To handle `mailman <subcommand> --help` we create a custom click.Command
    # subclass and override this method there too.  See
    # src/mailman/utilities/options.py
    def format_options(self, ctx, formatter):
        """Writes all the options into the formatter if they exist."""
        opts = []
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                part_a, part_b = rv
                opts.append((part_a, part_b.replace('\n', ' ')))
        if opts:
            with formatter.section('Options'):
                formatter.write_dl(opts)
        # Print the list of available commands.
        super().format_commands(ctx, formatter)


def initialize_config(ctx, param, value):
    if not ctx.resilient_parsing:
        initialize(value)


@click.option(
    '-C', '--config', 'config_file',
    envvar='MAILMAN_CONFIG_FILE',
    type=click.Path(exists=True, dir_okay=False, resolve_path=True),
    help=_("""\
    Configuration file to use.  If not given, the environment variable
    MAILMAN_CONFIG_FILE is consulted and used if set.  If neither are given, a
    default configuration file is loaded."""),
    is_eager=True, callback=initialize_config)
@click.option(
    '--run-as-root',
    is_flag=True, default=False,
    help=_("""\
    Running mailman commands as root is not recommended and mailman will
    refuse to run as root unless this option is specified."""))
@click.group(
    cls=Subcommands,
    context_settings=dict(help_option_names=['-h', '--help']),
    invoke_without_command=True)
@click.pass_context
@click.version_option(MAILMAN_VERSION_FULL, message='%(version)s')
@public
def main(ctx, config_file, run_as_root):
    # XXX https://github.com/pallets/click/issues/303
    """\
    The GNU Mailman mailing list management system
    https://www.list.org
    """
    # Only run as root if allowed.
    if os.geteuid() == 0 and not run_as_root:
        raise click.UsageError(_("""\
    If you are sure you want to run as root, specify --run-as-root."""))
    # click handles dispatching to the subcommand via the Subcommands class.
    if ctx.invoked_subcommand is None:
        ctx.invoke(help_command)
