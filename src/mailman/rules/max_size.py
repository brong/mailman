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

"""The maximum message size rule."""

from copy import deepcopy
from lazr.config import as_boolean
from mailman.config import config
from mailman.core.i18n import _
from mailman.handlers.mime_delete import process
from mailman.interfaces.pipeline import DiscardMessage, RejectMessage
from mailman.interfaces.rules import IRule
from public import public
from zope.interface import implementer


@public
@implementer(IRule)
class MaximumSize:
    """The implicit destination rule."""

    name = 'max-size'
    description = _('Catch messages that are bigger than a specified maximum.')
    record = True

    def check(self, mlist, msg, msgdata):
        """See `IRule`."""
        if mlist.max_message_size == 0:
            return False
        assert hasattr(msg, 'original_size'), (
            'Message was not sized on initial parsing.')
        if msg.original_size / 1024.0 <= mlist.max_message_size:
            # If it's already not too big, no need to filter it
            return False
        test_size = msg.original_size
        if (mlist.filter_content and
                as_boolean(config.mailman.check_max_size_on_filtered_message)):
            test_msg = deepcopy(msg)
            test_data = msgdata.copy()
            test_data['fwd_preserve'] = False
            try:
                process(mlist, test_msg, test_data)
            except (DiscardMessage, RejectMessage):
                # Nothing left after content filtering.  Let the pipeline
                # handle it.
                return False
            test_size = len(test_msg.as_string())
        # The maximum size is specified in 1024 bytes.
        if test_size / 1024.0 > mlist.max_message_size:
            msgdata['moderation_sender'] = msg.sender
            with _.defer_translation():
                # This will be translated at the point of use.
                msgdata.setdefault('moderation_reasons', []).append(
                    (_('The message is larger than the {} KB maximum size'),
                     mlist.max_message_size))
            return True
        return False
