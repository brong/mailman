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

"""The terminal 'discard' chain."""

import logging

from mailman.chains.base import TerminalChainBase
from mailman.core.i18n import _, format_reasons
from mailman.interfaces.chain import DiscardEvent
from public import public
from zope.event import notify


SEMISPACE = '; '

log = logging.getLogger('mailman.vette')


@public
class DiscardChain(TerminalChainBase):
    """Discard a message."""

    name = 'discard'
    description = _('Discard a message and stop processing.')

    def _process(self, mlist, msg, msgdata):
        """See `TerminalChainBase`.

        This writes a log message, fires a Zope event and then throws the
        message away.
        """
        with _.using('en'):
            reasons = format_reasons(
                msgdata.get('moderation_reasons', ['N/A'])
            )
            log.info(
                '%s: DISCARD: %s: post from %s: %s',
                mlist.fqdn_listname,
                msg.get('message-id', 'n/a').strip(),
                msg.sender,
                SEMISPACE.join(reasons),
            )
        notify(DiscardEvent(mlist, msg, msgdata, self))
        # Nothing more needs to happen.
