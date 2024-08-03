# Copyright (C) 2009-2023 by the Free Software Foundation, Inc.
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

"""The 'admins' subcommand."""

import sys
import click

from mailman.app.membership import add_member
from mailman.core.i18n import _
from mailman.interfaces.address import IEmailValidator
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.member import AlreadySubscribedError, MemberRole
from mailman.interfaces.subscriptions import RequestRecord
from mailman.interfaces.usermanager import IUserManager
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


def update_admins(ctx, add, delete, role, mlist):
    user_manager = getUtility(IUserManager)
    for email in add:
        # Ensure we have user and address records.
        user_manager.make_user(email)
        try:
            add_member(mlist, RequestRecord(email), role)
        except AlreadySubscribedError:
            print(_('{} is already an {} of {}').format(
                    email, role.name, mlist.fqdn_listname), file=sys.stderr)
    for email in delete:
        if role.name == 'owner':
            member = mlist.owners.get_member(email)
        else:
            member = mlist.moderators.get_member(email)
        if not member:
            print(_('{} is not an {} of {}').format(
                    email, role.name, mlist.fqdn_listname), file=sys.stderr)
            continue
        member.unsubscribe()


@click.command(
    cls=I18nCommand,
    help=_("""\
    Add and/or delete owners and moderators of a list.
    """))
@click.option(
    '--add', '-a', multiple=True,
    help=_("""\
    Email address of the user to add with the given role.
    May be repeated to add multiple users.
    """))
@click.option(
    '--delete', '-d', multiple=True,
    help=_("""\
    Email address of the user to be removed from the given role.
    May be repeated to delete multiple users.
    """))
@click.option(
    '--role', '-r', default='owner',
    type=click.Choice(('owner', 'moderator'), case_sensitive=False),
    help=_("""\
    The role to add/delete. This may be 'owner' or 'moderator'.
    If not given, then 'owner' role is assumed.
    """))
@click.argument('listspec')
@click.pass_context
def admins(ctx, add, delete, role, listspec):
    email_validator = getUtility(IEmailValidator)
    mlist = getUtility(IListManager).get(listspec)
    if mlist is None:
        ctx.fail(_('No such list: ${listspec}'))
    if not add and not delete:
        ctx.fail(_('Nothing to add or delete.'))
    for email in add:
        if not email_validator.is_valid(email):
            ctx.fail(_('Invalid email address: {}').format(email))
    for email in delete:
        if not email_validator.is_valid(email):
            ctx.fail(_('Invalid email address: {}').format(email))
    if role == 'owner':
        role = MemberRole.owner
    else:
        role = MemberRole.moderator
    update_admins(ctx, add, delete, role, mlist)


@public
@implementer(ICLISubCommand)
class Members:
    name = 'admins'
    command = admins
