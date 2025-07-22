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

"""The 'help' subcommand."""

import sys
import click

from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.options import I18nCommand
from public import public
from zope.interface import implementer


@click.command(
    cls=I18nCommand,
    help=_('Show this help message and exit.'))
@click.pass_context
# https://github.com/pallets/click/issues/832
def help(ctx):                                      # pragma: nocover
    click.echo(ctx.parent.get_help(), color=ctx.color)
    sys.exit()


@public
@implementer(ICLISubCommand)
class Help:
    name = 'help'
    command = help
