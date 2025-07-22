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

"""The 'unshunt' command."""

import sys
import click

from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.options import I18nCommand
from public import public
from zope.interface import implementer


@click.command(
    cls=I18nCommand,
    help=_('Unshunt messages.'))
@click.option(
    '--discard', '-d',
    is_flag=True, default=False,
    help=_("""\
    Discard all shunted messages instead of moving them back to their original
    queue."""))
def unshunt(discard):
    shunt_queue = config.switchboards['shunt']
    shunt_queue.recover_backup_files()
    for filebase in shunt_queue.files:
        try:
            msg, msgdata = shunt_queue.dequeue(filebase)
            which_queue = msgdata.get('whichq', 'in')
            if not discard:
                config.switchboards[which_queue].enqueue(msg, msgdata)
        except Exception as error:                             # noqa: F841
            print(_('Cannot unshunt message ${filebase}, skipping:\n${error}'),
                  file=sys.stderr)
        else:
            # Unlink the .bak file left by dequeue()
            shunt_queue.finish(filebase)


@public
@implementer(ICLISubCommand)
class Unshunt:
    name = 'unshunt'
    command = unshunt
