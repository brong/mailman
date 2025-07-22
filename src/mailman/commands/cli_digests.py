# Copyright (C) 2015-2025 by the Free Software Foundation, Inc.
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

"""The `send_digests` subcommand."""

import sys
import click

from mailman.app.digests import (
    bump_digest_number_and_volume,
    maybe_send_digest_now,
)
from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


@click.command(
    cls=I18nCommand,
    help=_('Operate on digests.'))
@click.option(
    '--list', '-l', 'list_ids', metavar='list',
    multiple=True, help=_("""\
    Operate on this mailing list.  Multiple --list options can be given.  The
    argument can either be a List-ID or a fully qualified list name.  Without
    this option, operate on the digests for all mailing lists."""))
@click.option(
    '--send', '-s',
    is_flag=True, default=False,
    help=_("""\
    Send any collected digests right now, even if the size threshold has not
    yet been met."""))
@click.option(
    '--bump', '-b',
    is_flag=True, default=False,
    help=_("""\
    Increment the digest volume number and reset the digest number to one.  If
    given with --send, the volume number is incremented before any current
    digests are sent."""))
@click.option(
    '--dry-run', '-n',
    is_flag=True, default=False,
    help=_("""\
    Don't actually do anything, but in conjunction with --verbose, show what
    would happen."""))
@click.option(
    '--verbose', '-v',
    is_flag=True, default=False,
    help=_('Print some additional status.'))
@click.option(
    '--periodic', '-p',
    is_flag=True, default=False,
    help=_("""\
    Send any collected digests for the List only if their digest_send_periodic
    is set to True."""))
@click.pass_context
def digests(ctx, list_ids, send, bump, dry_run, verbose, periodic):
    # send and periodic options are mutually exclusive, if they both are
    # specified, exit.
    if send and periodic:
        print(_('--send and --periodic flags cannot be used together'),
              file=sys.stderr)
        exit(1)
    list_manager = getUtility(IListManager)
    if list_ids:
        lists = []
        for spec in list_ids:
            # We'll accept list-ids or fqdn list names.
            if '@' in spec:
                mlist = list_manager.get(spec)
            else:
                mlist = list_manager.get_by_list_id(spec)
            if mlist is None:
                print(_('No such list found: ${spec}'), file=sys.stderr)
            else:
                lists.append(mlist)
    else:
        lists = list(list_manager.mailing_lists)
    if bump:
        for mlist in lists:
            if verbose:
                print(_('\
${mlist.list_id} is at volume ${mlist.volume}, number \
${mlist.next_digest_number}'))
            if not dry_run:
                bump_digest_number_and_volume(mlist)
                if verbose:
                    print(_('\
${mlist.list_id} bumped to volume ${mlist.volume}, number \
${mlist.next_digest_number}'))
    if send:
        for mlist in lists:
            if verbose:
                print(_('\
${mlist.list_id} sent volume ${mlist.volume}, number \
${mlist.next_digest_number}'))
            if not dry_run:
                maybe_send_digest_now(mlist, force=True)

    if periodic:
        for mlist in lists:
            if mlist.digest_send_periodic:
                if verbose:
                    print(_('\
${mlist.list_id} sent volume ${mlist.volume}, number \
${mlist.next_digest_number}'))
                if not dry_run:
                    maybe_send_digest_now(mlist, force=True)


@public
@implementer(ICLISubCommand)
class Digests:
    name = 'digests'
    command = digests
