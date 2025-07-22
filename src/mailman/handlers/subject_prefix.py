# Copyright (C) 2014-2025 by the Free Software Foundation, Inc.
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

"""Subject header prefix munging."""

import re

from contextlib import suppress
from email.header import decode_header, Header, make_header
from mailman.core.i18n import _
from mailman.interfaces.handler import IHandler
from public import public
from zope.interface import implementer


RE_PATTERN = r'\s*((RE|AW|SV|VS)(\[\d+\])?\s*:\s*)+'
ASCII_CHARSETS = (None, 'ascii', 'us-ascii')
EMPTYSTRING = ''


def ascii_header(mlist, msgdata, subject, prefix, prefix_pattern, ws):
    if mlist.preferred_language.charset not in ASCII_CHARSETS:
        return None
    for chunk, charset in decode_header(subject.encode()):
        if charset not in ASCII_CHARSETS:
            return None
    subject_text = EMPTYSTRING.join(str(subject).splitlines())
    subject_text = re.sub(prefix_pattern, '', subject_text)
    # Replace empty subject.
    if subject_text.strip() == '':
        with _.using(mlist.preferred_language.code):
            subject_text = _('(no subject)')
    msgdata['stripped_subject'] = subject_text
    rematch = re.match(RE_PATTERN, subject_text, re.I)
    if rematch:
        subject_text = subject_text[rematch.end():]
        recolon = 'Re: '
    else:
        recolon = ''
    lines = subject_text.splitlines()
    # If the subject was only the prefix or Re:, the text could be null.
    first_line = []
    if lines:
        first_line = [lines[0]]
    if recolon:
        first_line.insert(0, recolon)
    if prefix:
        first_line.insert(0, prefix)
    subject_text = EMPTYSTRING.join(first_line)
    return Header(subject_text, continuation_ws=ws)


def all_same_charset(mlist, msgdata, subject, prefix, prefix_pattern, ws):
    list_charset = mlist.preferred_language.charset
    chunks = []
    for chunk, charset in decode_header(subject.encode()):
        if charset is None:
            charset = 'us-ascii'
        if isinstance(chunk, str):
            chunks.append(chunk)
        else:
            try:
                chunks.append(chunk.decode(charset))
            except LookupError:
                # The charset value is unknown.
                return None
        if charset != list_charset:
            try:
                chunks[-1].encode(list_charset)
            except UnicodeEncodeError:
                return None
    subject_text = EMPTYSTRING.join(chunks)
    subject_text = re.sub(prefix_pattern, '', subject_text)
    # Replace empty subject.
    if subject_text.strip() == '':
        with _.using(mlist.preferred_language.code):
            subject_text = _('(no subject)')
    msgdata['stripped_subject'] = subject_text
    rematch = re.match(RE_PATTERN, subject_text, re.I)
    if rematch:
        subject_text = subject_text[rematch.end():]
        recolon = 'Re: '
    else:
        recolon = ''
    lines = subject_text.splitlines()
    # If the subject was only the prefix or Re:, the text could be null.
    first_line = []
    if lines:
        first_line = [lines[0]]
    if recolon:
        first_line.insert(0, recolon)
    if prefix:
        first_line.insert(0, prefix)
    if len(lines) > 1:
        first_line.extend(lines[1:])
    subject_text = EMPTYSTRING.join(first_line)
    return Header(subject_text, charset=list_charset, continuation_ws=ws)


def mixed_charsets(mlist, msgdata, subject, prefix, prefix_pattern, ws):
    list_charset = mlist.preferred_language.charset
    chunks = decode_header(subject.encode())
    # This code was:
    # if len(chunks) == 0:
    #     with _.using(mlist.preferred_language.code):
    #         subject_text = _('(no subject)')
    #     chunks = [(prefix, list_charset),
    #               (subject_text, list_charset),
    #               ]
    #     return make_header(chunks, continuation_ws=ws)
    # but len(chunks) == 0 is always False and an empty Subject: will always
    # be processed in all_same_charset() anyway.

    # Only search the first chunk for Re and existing prefix.
    chunk_text, chunk_charset = chunks[0]
    if chunk_charset is None:
        chunk_charset = 'us-ascii'
    if isinstance(chunk_text, str):
        first_text = chunk_text
    else:
        try:
            first_text = chunk_text.decode(chunk_charset)
        except LookupError:
            # The chunk_charset is unknown. Add a dummy first_text.
            chunks.insert(0, ('', 'us-ascii'))
            first_text = ''
    first_text = re.sub(prefix_pattern, '', first_text).lstrip()
    rematch = re.match(RE_PATTERN, first_text, re.I)
    if rematch:
        first_text = 'Re: ' + first_text[rematch.end():]
    chunks[0] = (first_text, chunk_charset)
    # The subject text stripped of the prefix, for use in the NNTP gateway.
    msgdata['stripped_subject'] = str(make_header(chunks, continuation_ws=ws))
    chunks.insert(0, (prefix, list_charset))
    return make_header(chunks, continuation_ws=ws)


@public
@implementer(IHandler)
class SubjectPrefix:
    """Add a list-specific prefix to the Subject header value."""

    name = 'subject-prefix'
    description = _('Add a list-specific prefix to the Subject header value.')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        if msgdata.get('isdigest') or msgdata.get('_fasttrack'):
            return
        prefix = mlist.subject_prefix
        if not prefix.strip():
            return
        subject = msg.get('subject', '')
        # Turn the value into a Header instance and try to figure out what
        # continuation whitespace is being used.
        # Save the original Subject.
        msgdata['original_subject'] = subject
        if isinstance(subject, Header):
            subject_text = str(subject)
        else:
            subject = make_header(decode_header(subject))
            subject_text = str(subject)
        lines = subject_text.splitlines()
        ws = '\t'
        if len(lines) > 1 and lines[1] and lines[1][0] in ' \t':
            ws = lines[1][0]
        # If the subject_prefix contains '%d', it is replaced with the mailing
        # list's sequence number.  The sequential number format allows '%d' or
        # '%05d' like pattern.
        prefix_pattern = re.escape(prefix)
        # Unescape '%'.
        prefix_pattern = '%'.join(prefix_pattern.split(r'\%'))
        p = re.compile(r'%\d*d')
        if p.search(prefix, 1):
            # The prefix has number, so we should search prefix w/number in
            # subject.  Also, force new style.
            prefix_pattern = p.sub(r'\\s*\\d+\\s*', prefix_pattern)
        # Substitute %d in prefix with post_id
        with suppress(TypeError):
            prefix = prefix % mlist.post_id
        for handler in (ascii_header,
                        all_same_charset,
                        mixed_charsets,
                        ):
            new_subject = handler(
                mlist, msgdata, subject, prefix, prefix_pattern, ws)
            if new_subject is not None:
                del msg['subject']
                msg['Subject'] = new_subject
                return
