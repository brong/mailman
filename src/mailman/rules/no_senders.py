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

"""Membership related rules."""

from mailman.core.i18n import _
from mailman.interfaces.rules import IRule
from public import public
from zope.interface import implementer


@public
@implementer(IRule)
class NoSenders:
    """The message has no senders rule."""

    name = 'no-senders'
    description = _('Match messages with no valid senders.')
    record = True

    def check(self, mlist, msg, msgdata):
        """See `IRule`."""
        if msg.sender:
            return False
        else:
            msgdata['moderation_sender'] = 'N/A'
            with _.defer_translation():
                # This will be translated at the point of use.
                msgdata.setdefault('moderation_reasons', []).append(
                    _('The message has no valid senders'))
            return True
