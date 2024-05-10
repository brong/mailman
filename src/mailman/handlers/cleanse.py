# Copyright (C) 1998-2023 by the Free Software Foundation, Inc.
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

"""Cleanse certain headers from all messages."""

import re
import logging

from email.utils import formataddr, make_msgid
from mailman.config import config
from mailman.core.i18n import _
from mailman.handlers.cook_headers import uheader
from mailman.interfaces.handler import IHandler
from public import public
from zope.interface import implementer


log = logging.getLogger('mailman.smtp')
elog = logging.getLogger('mailman.error')


@public
@implementer(IHandler)
class Cleanse:
    """Cleanse certain headers from all messages."""

    name = 'cleanse'
    description = _('Cleanse certain headers from all messages.')

    def remove_nonkeepers(self, msg):
        cres = []
        for regexp in config.mailman.anonymous_list_keep_headers.split():
            try:
                if regexp.endswith(':'):
                    regexp = regexp[:-1] + '$'
                cres.append(re.compile(regexp, re.IGNORECASE))
            except re.error as e:
                elog.error(
                    'Ignored bad anonymous_list_keep_headers regexp %s: %s',
                    regexp, e)
        for hdr in msg.keys():
            # Only remove X- headers here.
            if not hdr.lower().startswith('x-'):
                continue
            keep = False
            for cre in cres:
                if cre.search(hdr):
                    keep = True
                    break
            if not keep:
                del msg[hdr]

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        # Remove headers that could contain passwords.
        del msg['approved']
        del msg['approve']
        del msg['x-approved']
        del msg['x-approve']
        del msg['urgent']
        # We remove other headers from anonymous lists.
        if mlist.anonymous_list:
            log.info('post to %s from %s anonymized',
                     mlist.fqdn_listname, msg.get('from').strip())
            del msg['from']
            del msg['reply-to']
            del msg['sender']
            del msg['organization']
            del msg['return-path']
            # Hotmail sets this one
            del msg['x-originating-email']
            # And these can reveal the sender too
            del msg['received']
            # And so can the message-id so replace it.
            del msg['message-id']
            msg['Message-ID'] = make_msgid()
            # And something sets these
            del msg['x-mailfrom']
            del msg['x-envelope-from']
            # And now remove all but the keepers.
            self.remove_nonkeepers(msg)
            i18ndesc = str(uheader(mlist, mlist.description, 'From'))
            msg['From'] = formataddr((i18ndesc, mlist.posting_address))
            msg['Reply-To'] = mlist.posting_address
        # Some headers can be used to fish for membership.
        del msg['return-receipt-to']
        del msg['disposition-notification-to']
        del msg['x-confirm-reading-to']
        # Pegasus mail uses this one... sigh.
        del msg['x-pmrqc']
        # Don't let this header be spoofed.  See RFC 5064.
        del msg['archived-at']
