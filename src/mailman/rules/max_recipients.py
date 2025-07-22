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

"""The maximum number of recipients rule."""

from mailman.core.i18n import _
from mailman.interfaces.rules import IRule
from public import public
from zope.interface import implementer


@public
@implementer(IRule)
class MaximumRecipients:
    """The maximum number of recipients rule."""

    name = 'max-recipients'
    description = _('Catch messages with too many explicit recipients.')
    record = True

    def check(self, mlist, msg, msgdata):
        """See `IRule`."""
        # Zero means any number of recipients are allowed.
        if mlist.max_num_recipients == 0:
            return False
        # Figure out how many recipients there are
        num_recipients = len(msg.get_addresses('to', []))
        num_recipients += len(msg.get_addresses('cc', []))
        if num_recipients >= mlist.max_num_recipients:
            msgdata['moderation_sender'] = msg.sender
            with _.defer_translation():
                # This will be translated at the point of use.
                msgdata.setdefault('moderation_reasons', []).append(
                    (_('Message has more than {} recipients'),
                     mlist.max_num_recipients))
            return True
        return False
