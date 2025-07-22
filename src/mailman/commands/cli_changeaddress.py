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

"""The 'changeaddress' subcommand."""

import click

from mailman.core.i18n import _
from mailman.interfaces.address import IEmailValidator
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.usermanager import IUserManager
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


@click.command(
    cls=I18nCommand,
    help=_("""\
    Change a user's email address from old_address to possibly case-preserved
    new_address.
    """))
@click.argument('old_address')
@click.argument('new_address')
@click.pass_context
def changeaddress(ctx, old_address, new_address):
    user_manager = getUtility(IUserManager)
    address = user_manager.get_address(old_address)
    if address is None:
        ctx.fail(_('Address {} not found.').format(old_address))
    if not getUtility(IEmailValidator).is_valid(new_address):
        ctx.fail(_('Address {} is not a valid email address.').format(
            new_address))
    if new_address == old_address:
        ctx.fail(_('Addresses are not different.  Nothing to change.'))
    if (user_manager.get_address(new_address) is not None and
            new_address.lower() != old_address.lower()):
        ctx.fail(_("Address {} already exists; can't change.").format(
            new_address))
    address.email = new_address.lower()
    address._original = (None if new_address.lower() == new_address
                         else new_address)
    print(_('Address changed from {} to {}.').format(old_address, new_address))


@public
@implementer(ICLISubCommand)
class ChangeAddress:
    name = 'changeaddress'
    command = changeaddress
