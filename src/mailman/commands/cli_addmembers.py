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

"""The 'addmembers' subcommand."""

import sys
import click

from email.utils import formataddr, parseaddr
from mailman.core.i18n import _
from mailman.database.transaction import transactional
from mailman.interfaces.address import IEmailValidator
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.member import (
    AlreadySubscribedError,
    DeliveryMode,
    DeliveryStatus,
    MembershipIsBannedError,
)
from mailman.interfaces.subscriptions import (
    ISubscriptionManager,
    SubscriptionPendingError,
)
from mailman.interfaces.usermanager import IUserManager
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


def get_addr(display_name, email, user_manager):
    """Return an existing address record if available, otherwise make one."""
    addr = user_manager.get_address(email)
    if addr is not None:
        # We have an address with this email.  Return that.
        return addr
    # Unknown email.  Create an address for this.
    # XXX Should we be making a user instead?
    return user_manager.create_address(email, display_name)


@transactional
def add_members(mlist, in_fp, delivery, invite, welcome_msg, admin_notify):
    """Add members to a mailing list."""
    user_manager = getUtility(IUserManager)
    registrar = ISubscriptionManager(mlist)
    email_validator = getUtility(IEmailValidator)
    for line in in_fp:
        # Ignore blank lines and lines that start with a '#'.
        if line.startswith('#') or len(line.strip()) == 0:
            continue
        # Parse the line and ensure that the values are unicodes.
        display_name, email = parseaddr(line)
        # parseaddr can return invalid emails.  E.g. parseaddr('foobar@')
        # returns ('', 'foobar@') in python 3.6.7 and 3.7.1 so check validity.
        if not email_validator.is_valid(email):
            line = line.strip()
            print(_('Cannot parse as valid email address (skipping): ${line}'),
                  file=sys.stderr)
            continue
        subscriber = get_addr(display_name, email, user_manager)
        # For error messages.
        email = formataddr((display_name, email))
        delivery_status = DeliveryStatus.enabled
        if delivery is None or delivery == 'regular' or delivery == 'disabled':
            delivery_mode = DeliveryMode.regular
            if delivery == 'disabled':
                delivery_status = DeliveryStatus.by_moderator
        elif delivery == 'mime':
            delivery_mode = DeliveryMode.mime_digests
        elif delivery == 'plain':
            delivery_mode = DeliveryMode.plaintext_digests
        elif delivery == 'summary':
            delivery_mode = DeliveryMode.summary_digests
        try:
            member = registrar.register(
                subscriber,
                pre_verified=True,
                pre_approved=True,
                pre_confirmed=True,
                invitation=invite,
                send_welcome_message=welcome_msg,
                admin_notify_mchanges=admin_notify,
            )[2]
            if member is not None:
                member.preferences.delivery_status = delivery_status
                member.preferences.delivery_mode = delivery_mode
        except AlreadySubscribedError:
            # It's okay if the address is already subscribed, just print a
            # warning and continue.
            print(_('Already subscribed (skipping): ${email}'),
                  file=sys.stderr)
        except MembershipIsBannedError:
            print(_('Membership is banned (skipping): ${email}'),
                  file=sys.stderr)
        except SubscriptionPendingError:
            print(_('Subscription already pending (skipping): ${email}'),
                  file=sys.stderr)


@click.command(
    cls=I18nCommand,
    help=_("""\
    Add all member addresses in FILENAME with delivery mode as specified
    with -d/--delivery.  FILENAME can be '-' to indicate standard input.
    Blank lines and lines that start with a '#' are ignored.
    """))
@click.option(
    '--delivery', '-d',
    type=click.Choice(('regular', 'mime', 'plain', 'summary', 'disabled')),
    help=_("""\
    Set the added members delivery mode to 'regular', 'mime', 'plain',
    'summary' or 'disabled'.  I.e., one of regular, three modes of digest
    or no delivery.  If not given, the default is regular.  Ignored for invited
    members."""))
@click.option(
    '--invite', '-i',
    is_flag=True, default=False,
    help=_("""\
    Send the added members an invitation rather than immediately adding them.
    """))
@click.option(
    '--welcome-msg/--no-welcome-msg', '-w/-W', 'welcome_msg', default=None,
    help=_("""\
    Override the list's setting for send_welcome_message."""))
@click.option(
    '--admin-notify/--no-admin-notify', '-n/-N', 'admin_notify', default=None,
    help=_("""\
    Override the list's setting for admin_notify_mchanges."""))
@click.argument('in_fp', metavar='FILENAME', type=click.File(encoding='utf-8'))
@click.argument('listspec')
@click.pass_context
def addmembers(ctx, in_fp, delivery, invite, welcome_msg, admin_notify,
               listspec):
    """Add members to a mailing list."""
    mlist = getUtility(IListManager).get(listspec)
    if mlist is None:
        ctx.fail(_('No such list: ${listspec}'))
    add_members(mlist, in_fp, delivery, invite, welcome_msg, admin_notify)


@public
@implementer(ICLISubCommand)
class AddMembers:
    name = 'addmembers'
    command = addmembers
