# Copyright (C) 2025-2026 by the Free Software Foundation, Inc.
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

"""Message-Instance egress mixin for delivery."""

from mailman.config import config
from public import public


@public
class MessageInstanceMixin:
    """Add Message-Instance header at egress after all modifications."""

    def message_instance_egress(self, mlist, msg, msgdata):
        """Add MI v=N+1 if the message changed since ingress."""
        config.handlers['message-instance-egress'].process(
            mlist, msg, msgdata)
