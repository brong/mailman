# Copyright (C) 2017-2025 by the Free Software Foundation, Inc.
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

"""Perform authentication checks and adds headers to outgoing message"""

import logging

from authheaders import sign_message
from dkim import DKIMException
from mailman.config import config
from mailman.core.i18n import _
from mailman.handlers.validate_authenticity import prepend_headers
from mailman.interfaces.handler import IHandler
from public import public
from zope.interface import implementer


# A manual override used by the test suite.
timestamp = None

log = logging.getLogger('mailman.error')


def sign(msg, msgdata):
    """ARC sign a message, and prepend the signature headers to the message."""
    try:
        # Since the underlying `sign_message` expects bytes for all the fields,
        # we will encode all the parameters.
        sig = sign_message(msg.as_bytes(),
                           config.arc.selector,
                           config.arc.domain,
                           config.arc.private_key,
                           config.arc.headers,
                           'ARC',
                           config.arc.authserv_id.encode(),
                           timestamp=timestamp,
                           standardize=('ARC-Standardize' in msgdata))
    except DKIMException:
        log.exception('Failed to sign message: %s', msg['Message-ID'])
        return

    headers = [x.decode('utf-8').split(': ', 1) for x in sig]
    prepend_headers(msg, headers)


@public
@implementer(IHandler)
class ARCSign:
    """Sign message and attach result headers."""

    name = 'arc-sign'
    description = _('Perform ARC auth checks and attach resulting headers')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""

        if config.arc_enabled:
            sign(msg, msgdata)
