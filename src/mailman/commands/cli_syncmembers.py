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

"""The 'syncmembers' subcommand."""

import sys
import click

from email.utils import formataddr, parseaddr
from mailman.app.membership import delete_member
from mailman.core.i18n import _
from mailman.database.transaction import transactional
from mailman.interfaces.address import IEmailValidator
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.member import (
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


def get_addr(display_name, email):
    """Return an existing address record if available, otherwise make one."""
    addr = user_manager.get_address(email)
    if addr is not None:
        # We have an address with this email.  Return that.
        return addr
    # Unknown email.  Create an address for this.
    # XXX Should we be making a user instead?`
    return user_manager.create_address(email, display_name)


@transactional
def add_members(mlist, member, delivery, welcome_msg, admin_notify):
    """Add members to a mailing list."""
    display_name, email = parseaddr(member)
    subscriber = get_addr(display_name, email)
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
            send_welcome_message=welcome_msg,
            admin_notify_mchanges=admin_notify,
        )[2]
        member.preferences.delivery_status = delivery_status
        member.preferences.delivery_mode = delivery_mode
    except MembershipIsBannedError:
        print(_('Membership is banned (skipping): ${email}'),
              file=sys.stderr)
    except SubscriptionPendingError:
        print(_('Subscription already pending (skipping): ${email}'),
              file=sys.stderr)


@transactional
def sync_members(mlist, in_fp, delivery, welcome_msg, goodbye_msg,
                 admin_notify, no_change):
    """Add and delete mailing list members to match an input file."""
    subscribers = mlist.members
    addresses = list(subscribers.addresses)
    # Variable that shows if something was done to the original mailing list
    ml_changed = False
    # A list (set) of the members currently subscribed.
    members_of_list = set([address.email.lower()
                          for address in addresses])
    # A list (set) of all valid email addresses in a file.
    file_emails = set()
    # A list (dict) of (display name + address) for a members address.
    formatted_addresses = {}
    for line in in_fp:
        # Don't include newlines or whitespaces at the start or end
        line = line.strip()
        # Ignore blank lines and lines that start with a '#'.
        if line.startswith('#') or len(line) == 0:
            continue
        # Parse the line to a tuple.
        parsed_addr = parseaddr(line)
        # parseaddr can return invalid emails.  E.g. parseaddr('foobar@')
        # returns ('', 'foobar@') in python 3.6.7 and 3.7.1 so check validity.
        if not email_validator.is_valid(parsed_addr[1]):
            print(_('Cannot parse as valid email address (skipping): ${line}'),
                  file=sys.stderr)
            continue
        new_display_name, new_email = parsed_addr
        # Address to lowercase
        lc_email = new_email.lower()
        # Format output with display name if available
        formatted_addr = formataddr((new_display_name, new_email))
        # Add the 'outputable' version to a dict
        formatted_addresses[lc_email] = formatted_addr
        file_emails.add(lc_email)
    addresses_to_add = file_emails - members_of_list
    addresses_to_delete = members_of_list - file_emails
    for email in sorted(addresses_to_add):
        # Add to mailing list if not dryrun.
        print(_("[ADD] %s") % formatted_addresses[email])
        if not no_change:
            add_members(mlist, formatted_addresses[email], delivery,
                        welcome_msg, admin_notify)
        # Indicate that we done something to the mailing list.
        ml_changed = True
        continue
    for email in sorted(addresses_to_delete):
        # Delete from mailing list if not dryrun.
        member = str(subscribers.get_member(email).address)
        print(_("[DEL] %s") % member)
        if not no_change:
            delete_member(mlist, email, admin_notif=admin_notify,
                          userack=goodbye_msg)
        # Indicate that we done something to the mailing list.
        ml_changed = True
        continue
    # We did nothing to the mailing list -> We had nothing to do.
    if not ml_changed:
        print(_("Nothing to do"))


@click.command(
    cls=I18nCommand,
    help=_("""\
    Add and delete members as necessary to syncronize a list's membership
    with an input file.  FILENAME is the file containing the new membership,
    one member per line.  Blank lines and lines that start with a '#' are
    ignored.  Addresses in FILENAME which are not current list members
    will be added to the list with delivery mode as specified with
    -d/--delivery.  List members whose addresses are not in FILENAME will
    be removed from the list.  FILENAME can be '-' to indicate standard input.
    """))
@click.option(
    '--delivery', '-d',
    type=click.Choice(('regular', 'mime', 'plain', 'summary', 'disabled')),
    help=_("""\
    Set the added members delivery mode to 'regular', 'mime', 'plain',
    'summary' or 'disabled'.  I.e., one of regular, three modes of digest
    or no delivery.  If not given, the default is regular."""))
@click.option(
    '--welcome-msg/--no-welcome-msg', '-w/-W', 'welcome_msg', default=None,
    help=_("""\
    Override the list's setting for send_welcome_message to added members."""))
@click.option(
    '--goodbye-msg/--no-goodbye-msg', '-g/-G', 'goodbye_msg', default=None,
    help=_("""\
    Override the list's setting for send_goodbye_message to
    deleted members."""))
@click.option(
    '--admin-notify/--no-admin-notify', '-a/-A', 'admin_notify', default=None,
    help=_("""\
    Override the list's setting for admin_notify_mchanges."""))
@click.option(
    '--no-change', '-n', 'no_change',
    is_flag=True, default=False,
    help=_("""\
    Don't actually make the changes.  Instead, print out what would be
    done to the list."""))
@click.argument('in_fp', metavar='FILENAME', type=click.File(encoding='utf-8'))
@click.argument('listspec')
@click.pass_context
def syncmembers(ctx, in_fp, delivery, welcome_msg, goodbye_msg,
                admin_notify, no_change, listspec):
    """Add and delete mailing list members to match an input file."""
    global email_validator, registrar, user_manager
    mlist = getUtility(IListManager).get(listspec)
    if mlist is None:
        ctx.fail(_('No such list: ${listspec}'))
    email_validator = getUtility(IEmailValidator)
    registrar = ISubscriptionManager(mlist)
    user_manager = getUtility(IUserManager)
    sync_members(mlist, in_fp, delivery, welcome_msg, goodbye_msg,
                 admin_notify, no_change)


@public
@implementer(ICLISubCommand)
class SyncMembers:
    name = 'syncmembers'
    command = syncmembers
