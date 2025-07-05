# Copyright (C) 2016-2023 by the Free Software Foundation, Inc.
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

"""Check dmarc in a handler.

This handler is somewhat of a kludge. It is only for the owner pipeline,
and it's purpose is only to check the dmarc rule for messages to the -owner
address so dmarc mitigations can be applied to them."""

from mailman.core.i18n import _
from mailman.interfaces.handler import IHandler
from mailman.rules.dmarc import DMARCMitigation
from public import public
from zope.interface import implementer


def process(mlist, msg, msgdata):
    # All that's needed is running the check to set msgdata['dmarc'] = True
    # if required.
    DMARCMitigation().check(mlist, msg, msgdata)


@public
@implementer(IHandler)
class checkDMARC:
    """Check whether to Apply DMARC mitigations."""

    name = 'check_dmarc'
    description = _('Check whether to Apply DMARC mitigations.')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        process(mlist, msg, msgdata)
