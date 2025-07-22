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

"""The `admin_notify` subcommand."""

import sys
import click

from email.header import decode_header, make_header
from mailman.core.i18n import _
from mailman.email.message import OwnerNotification
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.pending import IPendings
from mailman.interfaces.requests import IListRequests, RequestType
from mailman.interfaces.template import ITemplateLoader
from mailman.utilities.options import I18nCommand
from mailman.utilities.string import expand, wrap
from public import public
from zope.component import getUtility
from zope.interface import implementer


@click.command(
    cls=I18nCommand,
    help=_('Notify list owners/moderators of pending requests.'))
@click.option(
    '--list', '-l', 'list_ids', metavar='list',
    multiple=True, help=_("""\
    Operate on this mailing list.  Multiple --list options can be given.  The
    argument can either be a List-ID or a fully qualified list name.  Without
    this option, operate on the requests for all mailing lists."""))
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
@click.pass_context
def notify(ctx, list_ids, dry_run, verbose):
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
        lists = list_manager.mailing_lists
    for mlist in lists:
        requestdb = IListRequests(mlist)
        count = requestdb.count
        subs, unsubs = _get_subs(mlist)
        count += len(subs) + len(unsubs)
        if verbose:
            print(_('The {} list has {} moderation requests waiting.').format(
                    mlist.fqdn_listname, count))
        if count > 0 and not dry_run:
            detail = _build_detail(requestdb, subs, unsubs)
            _send_notice(mlist, count, detail)


def _get_subs(mlist):
    """Gets the pending subscriptions and unsubscriptions waiting moderator
       approval for a list.
       Returns a 2-tuple of lists of email addresses pending subscription and
       unsubscription.
       """
    pendingsdb = getUtility(IPendings)
    subs = []
    unsubs = []
    for token, data in pendingsdb.find(mlist):
        if data.get('token_owner') == 'moderator':
            if data['type'] == 'subscription':
                subs.append(data['email'])
            elif data['type'] == 'unsubscription':
                unsubs.append(data['email'])
    return (subs, unsubs)


def _build_detail(requestdb, subs, unsubs):
    """Builds the detail of held messages and pending subscriptions and
       unsubscriptions for the body of the notification email.
       """
    detail = ''
    if len(subs) > 0:
        detail += _('\nHeld Subscriptions:\n')
        for sub in subs:
            detail += '    ' + _('User: {}\n').format(sub)
    if len(unsubs) > 0:
        detail += _('\nHeld Unsubscriptions:\n')
        for unsub in unsubs:
            detail += '    ' + _('User: {}\n').format(unsub)
    if requestdb.count_of(RequestType.held_message) > 0:
        detail += _('\nHeld Messages:\n')
        for rq in requestdb.of_type(RequestType.held_message):
            if requestdb.get_request(rq.id):
                key, data = requestdb.get_request(rq.id)
                sender = data['_mod_sender']
                subject = data['_mod_subject']
                reason = data['_mod_reason']
                detail += '    ' + _('Sender: {}\n').format(sender)
                try:
                    detail += '    ' + _('Subject: {}\n').format(
                        str(make_header(decode_header(subject))))
                except UnicodeDecodeError:
                    detail += '    ' + _('Subject: {}\n').format(subject)
                detail += '    ' + _('Reason: {}\n\n').format(reason)
            else:
                detail += '    ' + _(                    # pragma: nocover
                    'Missing data for request {}\n\n').format(rq.id)
    return detail


def _send_notice(mlist, count, detail):
    """Creates and sends the notice to the list administrators."""
    subject = _('The {} list has {} moderation requests waiting.').format(
                mlist.fqdn_listname, count)
    template = getUtility(ITemplateLoader).get(
        'list:admin:notice:pending', mlist)
    text = wrap(expand(template, mlist, dict(
        count=count,
        data=detail,
        )))
    msg = OwnerNotification(mlist, subject, text, mlist.administrators)
    msg.send(mlist)


@public
@implementer(ICLISubCommand)
class Notify:
    name = 'notify'
    command = notify
