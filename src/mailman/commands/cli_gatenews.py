# Copyright (C) 1998-2025 by the Free Software Foundation, Inc.
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

"""The `gatenews` subcommand."""

import os
import click
import socket
import logging
import nntplib
import datetime

from email import errors, message_from_bytes
from flufl.lock import Lock, TimeOutError
from mailman.config import config
from mailman.core.i18n import _
from mailman.email import message
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


NL = b'\n'
log = None
conn = None


def open_newsgroup(mlist):
    global conn
    nntp_host = config.nntp.host or 'localhost'
    nntp_port = int(config.nntp.port) if config.nntp.port else 119
    # Open up a "mode reader" connection to nntp server.  This will be shared
    # for all the gated lists having the same nntp_host.
    if conn is None:
        try:
            conn = nntplib.NNTP(nntp_host, nntp_port,
                                readermode=True,
                                user=config.nntp.user,
                                password=config.nntp.password)
        except (socket.error, nntplib.NNTPError, IOError) as e:
            log.error('error opening connection to nntp_host: %s\n%s',
                      nntp_host, e)
            raise
    # Get the GROUP information for the list, but we're only really interested
    # in the first article number and the last article number
    r, c, f, l, n = conn.group(mlist.linked_newsgroup)
    return conn, int(f), int(l)


def poll_newsgroup(mlist, conn, first, last, glock):
    listname = mlist.fqdn_listname
    # NEWNEWS is not portable and has synchronization issues.
    for num in range(first, last):
        glock.refresh()
        try:
            headers = conn.head(num)[1].lines
            found_to = False
            beenthere = False
            unfolded = [b'Dummy:']
            for header in headers:
                if header.startswith((b' ', b'\t')):
                    unfolded[-1] += header
                else:
                    unfolded.append(header)
            for header in unfolded:
                i = header.find(b':')
                value = header[:i].lower()
                if i > 0 and value == b'to':
                    found_to = True
                if value != b'list-id':
                    continue
                our_list_id = '<{}>'.format(mlist.list_id)
                if header.endswith(our_list_id.encode('us-ascii')):
                    beenthere = True
                    break
            if not beenthere:
                lines = conn.article(num)[1].lines
                try:
                    msg = message_from_bytes(NL.join(lines), message.Message)
                except errors.MessageError as e:
                    log.error('email package exception for %s:%d\n%s',
                              mlist.linked_newsgroup, num, e)
                    continue
                if found_to:
                    del msg['X-Originally-To']
                    msg['X-Originally-To'] = msg['To']
                    del msg['To']
                msg['To'] = mlist.posting_address
                # Post the message to the list
                inq = config.switchboards['in']
                # original_size is both a message attribute and a key in
                # msgdata.
                msg.original_size = len(msg.as_bytes())
                inq.enqueue(msg,
                            listid=mlist.list_id,
                            original_size=msg.original_size,
                            fromusenet=True)
                log.info('posted to list %s: %7d', listname, num)
        except nntplib.NNTPError as e:
            log.error('NNTP error for list %s: %7d\n%s', listname, num, e)
        # Even if we don't post the message because it was seen on the
        # list already, update the watermark
        mlist.usenet_watermark = num


def process_lists(glock):
    list_manager = getUtility(IListManager)
    for mlist in list_manager.mailing_lists:
        glock.refresh()
        listname = mlist.fqdn_listname
        if not mlist.gateway_to_mail:
            continue
        # Get the list's watermark, i.e. the last article number that we gated
        # from news to mail.  None means that this list has never polled its
        # newsgroup and that we should do a catch up.
        watermark = getattr(mlist, 'usenet_watermark', None)
        # Open the newsgroup, but let most exceptions percolate up.
        try:
            conn, first, last = open_newsgroup(mlist)
        except (socket.error, nntplib.NNTPError, IOError) as e:
            log.error('NNTP error for list %s:\n%s', listname, e)
            break
        log.info('%s: [%d..%d]', listname, first, last)
        if watermark is None:
            # This is the first time we've tried to gate this
            # newsgroup.  We essentially do a mass catch-up, otherwise
            # we'd flood the mailing list.
            mlist.usenet_watermark = last
            log.info('%s caught up to article %d', listname, last)
        else:
            # The list has been polled previously, so now we simply
            # grab all the messages on the newsgroup that have not
            # been seen by the mailing list.  The first such article
            # is the maximum of the lowest article available in the
            # newsgroup and the watermark.  It's possible that some
            # articles have been expired since the last time gatenews
            # has run.  Not much we can do about that.
            start = max(watermark + 1, first)
            if start > last:
                log.info('nothing new for list %s', listname)
            else:
                log.info('gating %s articles [%d..%d]',
                         listname, start, last)
                # Use last+1 because poll_newsgroup() employes a for
                # loop over range, and this will not include the last
                # element in the list.
                poll_newsgroup(mlist, conn, start, last + 1, glock)
        log.info('%s watermark: %d', listname, mlist.usenet_watermark)


@click.command(
    cls=I18nCommand,
    help=_("""\
Poll the NNTP server for messages to be gatewayed to mailing lists."""))
@click.pass_context
def gatenews(ctx):
    global conn, log
    missing = object()
    if os.getenv('_MAILMAN_GATENEWS_NNTP', missing) is missing:
        raise click.UsageError(_("""\
    The gatenews command is run periodically by the nntp runner.
    If you are running it via cron, you should remove it from the crontab.
    If you want to run it manually, set _MAILMAN_GATENEWS_NNTP in the
    environment."""))
    GATENEWS_LOCK_FILE = os.path.join(config.LOCK_DIR, 'gatenews.lock')
    LOCK_LIFETIME = datetime.timedelta(hours=2)
    log = logging.getLogger('mailman.fromusenet')
    try:
        with Lock(GATENEWS_LOCK_FILE,
                  # It's okay to hijack this
                  lifetime=LOCK_LIFETIME) as lock:
            process_lists(lock)
        if conn:
            conn.quit()
        conn = None
    except TimeOutError:                                 # pragma: nocover
        log.error('Could not acquire gatenews lock')


@public
@implementer(ICLISubCommand)
class GateNews:
    name = 'gatenews'
    command = gatenews
