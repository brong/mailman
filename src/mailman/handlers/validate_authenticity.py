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

"""Perform origination & content authentication checks and add
an Authentication-Results header to the outgoing message"""

import logging

from authheaders import authenticate_message
from authres import AuthenticationResultsHeader, SyntaxError
from dns.resolver import Timeout
from itertools import chain
from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.handler import IHandler
from mailman.utilities.retry import retry
from public import public
from zope.interface import implementer


log = logging.getLogger('mailman.debug')

# Number of times to retry authentication checks in case of DNS failure.
NUM_TIMEOUT_RETRIES = 2

# This value is used by the test suite to provide a faux DNS resolver.
dnsfunc = None

# Email header including the results of ARC validation from the sender.
AUTH_RESULT_HEADER = 'Authentication-Results'


def prepend_headers(msg, headers):
    """Appends a group of headers to the beginning of a message.

    :param msg: The message object to add headers to.
    :type msg: email.message.EmailMessage
    :param headers: The list of headers to added to message.
    :type msg: List(email.headers.Header)
    """
    old_headers = msg.items()

    for key in msg:
        del msg[key]

    for k, v in chain(headers, old_headers):
        msg[k] = v


def trusted_auth_res(msg):
    """Extract the most recent trusted Authentication-Results(AR) header.

    :param msg: The message to extract the AR header from.
    :type msg: email.message.EmailMessage.
    """

    if config.arc.trusted_authserv_ids and (AUTH_RESULT_HEADER in msg):
        header = '{}: {}'.format(AUTH_RESULT_HEADER, msg[AUTH_RESULT_HEADER])
        # prod.outlook.com sends malformed headers. If we find these,
        # ignore them, as they are the equivalent of not trusted.
        # See https://gitlab.com/mailman/mailman/-/issues/885
        try:
            authserv_id = AuthenticationResultsHeader.parse(header).authserv_id
            if authserv_id in config.arc.trusted_authserv_ids:
                return header
        except SyntaxError:
            # Treat the faulty header as though it does not exist
            return


@retry(Timeout, 1 + NUM_TIMEOUT_RETRIES)
def authenticate(msg, msgdata):
    """ARC verify a message and update the Authentication-Results header.

    If there is a previous Authentication-Results, remove that and add a
    new one.
    """
    prev = trusted_auth_res(msg)
    auth_result = authenticate_message(
        msg.as_bytes(), config.arc.authserv_id,
        prev=prev,
        spf=False,  # cant spf check in mailman
        dkim=config.arc.dkim_enabled,
        dmarc=config.arc.dmarc_enabled,
        arc=True,
        dnsfunc=dnsfunc)

    if AUTH_RESULT_HEADER in msg:
        del msg[AUTH_RESULT_HEADER]

    auth_result = auth_result.split(':', 1)[1].strip()
    prepend_headers(msg, [(AUTH_RESULT_HEADER, auth_result)])


@public
@implementer(IHandler)
class ValidateAuthenticity:
    """Perform authentication checks and attach resulting headers.

    Validate the ARC chain of a message and add results to a new
    Authentication-Results header
    """

    name = 'validate-authenticity'
    description = _(
        """Perform auth checks and attach Authentication-Results header.""")

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        if config.arc_enabled:
            authenticate(msg, msgdata)
