# Copyright (C) 2020-2025 by the Free Software Foundation, Inc.
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

"""Function to remove non plain text parts for plain text digests."""

from mailman.core.i18n import _
from public import public


@public
def scrub(msg):
    """Replace any non plain text parts in the message with notes and return
    a string containing all the text.
    """
    text = ''
    for part in msg.walk():
        if part.is_multipart():
            # Just handle the sub-parts.
            continue
        ctype = part.get_content_type()
        if ctype == 'text/plain':
            charset = part.get_content_charset('us-ascii')
            payload = part.get_payload(decode=True)
            try:
                # Do the decoding inside the try/except so that if the charset
                # conversion fails, we'll just drop back to ascii.
                payload = payload.decode(charset, 'replace')
            except (LookupError, TypeError):
                # Unknown or empty charset.
                payload = payload.decode('us-ascii', 'replace')
        else:
            size = len(part.get_payload(decode=True))           # noqa: F841
            desc = part.get('content-description',              # noqa: F841
                            _('not available'))
            filename = part.get_filename(_('not available'))    # noqa: F841
            payload = _("""\
A message part incompatible with plain text digests has been removed ...
Name: ${filename}
Type: ${ctype}
Size: ${size} bytes
Desc: ${desc}
""")
        if len(text) > 0:
            text += _('-------------- next part --------------\n')
        text += payload
    return text
