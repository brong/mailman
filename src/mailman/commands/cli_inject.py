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

"""The `mailman inject` subcommand."""

import sys
import click

from mailman.app.inject import inject_text
from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


def show_queues(ctx, param, value):
    if value:
        print('Available queues:')
        for switchboard in sorted(config.switchboards):
            print('   ', switchboard)
        sys.exit(0)
    # Returning None tells click to process the rest of the command line.


@click.command(
    cls=I18nCommand,
    help=_("Inject a message from a file into a mailing list's queue."))
@click.option(
    '--queue', '-q',
    help=_("""\
    The name of the queue to inject the message to.  QUEUE must be one of the
    directories inside the queue directory.  If omitted, the incoming queue is
    used."""))
@click.option(
    '--show', '-s',
    is_flag=True, default=False, is_eager=True, expose_value=False,
    callback=show_queues,
    help=_('Show a list of all available queue names and exit.'))
@click.option(
    '--filename', '-f', 'message_file',
    default='-', type=click.File(encoding='utf-8'),
    help=_("""\
    Name of file containing the message to inject.  If not given, or
    '-' (without the quotes) standard input is used."""))
@click.option(
    '--metadata', '-m', 'keywords',
    multiple=True, metavar='KEY=VALUE',
    help=_("""\
    Additional metadata key/value pairs to add to the message metadata
    dictionary.  Use the format key=value.  Multiple -m options are
    allowed."""))
@click.argument('listspec')
@click.pass_context
def inject(ctx, queue, message_file, keywords, listspec):
    mlist = getUtility(IListManager).get(listspec)
    if mlist is None:
        ctx.fail(_('No such list: ${listspec}'))
    queue_name = ('in' if queue is None else queue)
    switchboard = config.switchboards.get(queue_name)
    if switchboard is None:
        ctx.fail(_('No such queue: ${queue}'))
    try:
        message_text = message_file.read()
    except KeyboardInterrupt:
        print('Interrupted')
        sys.exit(1)
    kws = {}
    for keyvalue in keywords:
        key, equals, value = keyvalue.partition('=')
        kws[key] = value
    inject_text(mlist, message_text, switchboard=queue, **kws)


@public
@implementer(ICLISubCommand)
class Inject:
    name = 'inject'
    command = inject
