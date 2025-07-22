# Copyright (C) 2010-2025 by the Free Software Foundation, Inc.
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

"""Importing list data into Mailman 3."""

import sys
import click
import pickle

from contextlib import ExitStack
from mailman.core.i18n import _
from mailman.database.transaction import transaction
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.utilities.importer import Import21Error, import_config_pck
from mailman.utilities.modules import hacked_sys_modules
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


# A fake module to go with `Bouncer`.
class _Mailman:
    __path__ = 'src/mailman/commands/cli_import.py'


# A fake Mailman object with Bouncer class from Mailman 2.1, we don't use it
# but there are instances in the .pck files.
class _Bouncer:
    class _BounceInfo:
        pass


@click.command(
    cls=I18nCommand,
    help=_("""\
    Import Mailman 2.1 list data.  Requires the fully-qualified name of the
    list to import and the path to the Mailman 2.1 pickle file."""))
@click.option(
    '--charset', '-c', default='utf-8',
    help=_("""\
    Specify the encoding of strings in PICKLE_FILE if not utf-8 or a subset
    thereof. This will normally be the Mailman 2.1 charset of the list's
    preferred_language."""))
@click.argument('listspec')
@click.argument(
    'pickle_file', metavar='PICKLE_FILE',
    type=click.File(mode='rb'))
@click.pass_context
def import21(ctx, charset, listspec, pickle_file):
    mlist = getUtility(IListManager).get(listspec)
    if mlist is None:
        ctx.fail(_('No such list: ${listspec}'))
    with ExitStack() as resources:
        resources.enter_context(hacked_sys_modules('Mailman', _Mailman))
        resources.enter_context(
            hacked_sys_modules('Mailman.Bouncer', _Bouncer))
        resources.enter_context(transaction())
        while True:
            try:
                config_dict = pickle.load(
                    pickle_file, encoding=charset, errors='ignore')
            except EOFError:
                break
            except pickle.UnpicklingError:
                ctx.fail(
                    _('Not a Mailman 2.1 configuration file: ${pickle_file}'))
            else:
                if not isinstance(config_dict, dict):
                    print(_('Ignoring non-dictionary: {0!r}').format(
                        config_dict), file=sys.stderr)
                    continue
                try:
                    import_config_pck(mlist, config_dict)
                except Import21Error as error:
                    print(error, file=sys.stderr)
                    sys.exit(1)


@public
@implementer(ICLISubCommand)
class Import21:
    name = 'import21'
    command = import21
