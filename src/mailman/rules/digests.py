# Copyright (C) 2007-2025 by the Free Software Foundation, Inc.
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

"""The digest reply rule."""

import re

from lazr.config import as_boolean
from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.rules import IRule
from mailman.interfaces.template import ITemplateLoader
from mailman.utilities.string import expand, wrap
from public import public
from zope.component import getUtility
from zope.interface import implementer


# Re to recognize a digest subject:
DIGRE = re.compile(r' Digest, Vol \d+, Issue \d+$', re.IGNORECASE)


@public
@implementer(IRule)
class Digests:
    """The digest reply rule."""

    name = 'digests'
    description = _('Catch messages with digest Subject or boilerplate quote.')
    record = True

    def check(self, mlist, msg, msgdata):
        """See `IRule`."""
        if not as_boolean(config.mailman.hold_digest):
            return False
        # Convert the header value to a str because it may be an
        # email.header.Header instance.
        subject = str(msg.get('subject', '')).strip()
        if DIGRE.search(subject):
            msgdata['moderation_sender'] = msg.sender
            with _.defer_translation():
                # This will be translated at the point of use.
                msgdata.setdefault('moderation_reasons', []).append(
                    _('Message has a digest subject'))
            return True
            # Get the masthead, but without emails.
        mastheadtxt = getUtility(ITemplateLoader).get(
            'list:member:digest:masthead', mlist)
        mastheadtxt = wrap(expand(mastheadtxt, mlist, dict(
            display_name=mlist.display_name,
            listname='',
            list_id=mlist.list_id,
            request_email='',
            owner_email='',
            )))
        msgtext = ''
        for part in msg.walk():
            if part.get_content_maintype() == 'text':
                cset = part.get_content_charset('utf-8')
                msgtext += part.get_payload(decode=True).decode(
                    cset, errors='replace')
        matches = 0
        lines = mastheadtxt.splitlines()
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if msgtext.find(line) >= 0:
                matches += 1
        if matches >= int(config.mailman.masthead_threshold):
            msgdata['moderation_sender'] = msg.sender
            with _.defer_translation():
                # This will be translated at the point of use.
                msgdata.setdefault('moderation_reasons', []).append(
                    _('Message quotes digest boilerplate'))
            return True
        return False
