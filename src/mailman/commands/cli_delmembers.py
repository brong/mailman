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

"""The 'delmembers' subcommand."""

import sys
import click

from email.utils import formataddr, parseaddr
from mailman.app.membership import delete_member
from mailman.core.i18n import _
from mailman.database.transaction import transactional
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.member import NotAMemberError
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


@transactional
def delete_members(mlists, memb_list, goodbye_msg, admin_notify):
    """Delete one or more members from one or more mailing lists."""
    mlists = list(mlists)
    for mlist in mlists:
        for display_name, email in memb_list:
            try:
                delete_member(mlist, email, admin_notif=admin_notify,
                              userack=goodbye_msg)
            except NotAMemberError:
                email = formataddr((display_name, email))
                if len(mlists) == 1:
                    print(_('Member not subscribed (skipping): ${email}'),
                          file=sys.stderr)


@click.command(
    cls=I18nCommand,
    help=_("""\
    Delete members from a mailing list."""))
@click.option(
    '--list', '-l', '_list', metavar='LISTSPEC',
    help=_("""\
    The list to operate on.  Required unless --fromall is specified.
    """))
@click.option(
    '--file', '-f', 'in_fp', metavar='FILENAME',
    type=click.File(encoding='utf-8'),
    help=_("""\
    Delete list members whose addresses are in FILENAME in addition to those
    specified with -m/--member if any.  FILENAME can be '-' to indicate
    standard input.  Blank lines and lines that start with a '#' are ignored.
    """))
@click.option(
    '--member', '-m', metavar='ADDRESS',  multiple=True,
    help=_("""\
    Delete the list member whose address is ADDRESS in addition to those
    specified with -f/--file if any.  This option may be repeated for
    multiple addresses.
    """))
@click.option(
    '--all', '-a', '_all',
    is_flag=True, default=False,
    help=_("""\
    Delete all the members of the list.  If specified, none of -f/--file,
    -m/--member or --fromall may be specified.
    """))
@click.option(
    '--fromall',
    is_flag=True, default=False,
    help=_("""\
    Delete the member(s) specified by -m/--member and/or -f/--file from all
    lists in the installation.  This may not be specified together with
    -a/--all or -l/--list.
    """))
@click.option(
    '--goodbye-msg/--no-goodbye-msg', '-g/-G', 'goodbye_msg', default=None,
    help=_("""\
    Override the list's setting for send_goodbye_message to
    deleted members."""))
@click.option(
    '--admin-notify/--no-admin-notify', '-n/-N', 'admin_notify', default=None,
    help=_("""\
    Override the list's setting for admin_notify_mchanges."""))
@click.pass_context
def delmembers(ctx, _list, in_fp, member, _all, fromall, goodbye_msg,
               admin_notify):
    """Delete members from mailing lists."""
    if fromall:
        if _list is not None or _all:
            ctx.fail('--fromall may not be specified with -l/--list, '
                     'or -a/--all')
    elif _all:
        if in_fp is not None or len(member) != 0:
            ctx.fail('-a/--all must not be specified with '
                     '-f/--file or -m/--member.')
    if _list is None and not fromall:
        ctx.fail('Without --fromall, -l/--list is required.')
    if not _all and in_fp is None and len(member) == 0:
        ctx.fail('At least one of -a/--all, -f/--file or -m/--member '
                 'is required.')
    list_manager = getUtility(IListManager)
    if fromall:
        mlists = list_manager.mailing_lists
    else:
        mlist = list_manager.get(_list)
        if mlist is None:
            ctx.fail(_('No such list: ${_list}'))
        mlists = [mlist]
    if _all:
        memb_list = [(address.display_name, address.email) for address in
                     mlist.members.addresses]
    else:
        memb_list = []
        memb_list.extend([parseaddr(x) for x in member])
        if in_fp:
            for line in in_fp:
                # Ignore blank lines and lines that start with a '#'.
                if line.startswith('#') or len(line.strip()) == 0:
                    continue
                memb_list.append(parseaddr(line))
    delete_members(mlists, memb_list, goodbye_msg, admin_notify)


@public
@implementer(ICLISubCommand)
class DelMembers:
    name = 'delmembers'
    command = delmembers
