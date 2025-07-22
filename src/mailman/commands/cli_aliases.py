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

"""Generate Mailman alias files for your MTA."""

import click

from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.modules import call_name
from mailman.utilities.options import I18nCommand
from public import public
from zope.interface import implementer


@click.command(
    cls=I18nCommand,
    help=_('Regenerate the aliases appropriate for your MTA.'))
@click.option(
    '--directory', '-d',
    type=click.Path(exists=True, file_okay=False, resolve_path=True,
                    writable=True),
    help=_('An alternative directory to output the various MTA files to.'))
def aliases(directory):
    call_name(config.mta.incoming).regenerate(directory)


@public
@implementer(ICLISubCommand)
class Aliases:
    name = 'aliases'
    command = aliases
