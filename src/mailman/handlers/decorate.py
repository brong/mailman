# Copyright (C) 1998-2025 by the Free Software Foundation, Inc.
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

"""Decorate a message by sticking the header and footer around it."""

import base64
import re
import copy
import logging
import quopri

from io import BytesIO
from email.mime.text import MIMEText
from email.utils import formataddr
from mailman.archiving.mailarchive import MailArchive
from mailman.core.i18n import _
from mailman.interfaces.handler import IHandler
from mailman.interfaces.mailinglist import IListArchiverSet
from mailman.interfaces.template import ITemplateLoader
from mailman.utilities.string import expand
from public import public
from zope.component import getUtility
from zope.interface import implementer


log = logging.getLogger('mailman.error')
alog = logging.getLogger('mailman.archiver')


def process(mlist, msg, msgdata):
    """Decorate the message with headers and footers."""
    # Digests and Mailman-craft messages should not get additional headers.
    if msgdata.get('isdigest') or msgdata.get('nodecorate'):
        return
    # Kludge to not decorate mail for Mail-Archive.com.
    if ('recipients' in msgdata and len(msgdata['recipients']) == 1 and
            list(msgdata['recipients'])[0] == MailArchive().recipient):
        return
    d = {}
    member = msgdata.get('member')
    if member is not None:
        # Calculate the extra personalization dictionary.
        # member.subscriber can be a User instance or an Address instance, and
        # member.address can be None and so can member._user.preferred_address.
        if member._address is not None:
            _address = member._address
        else:
            _address = (member._user.preferred_address or
                        list(member._user.addresses)[0])
        recipient = msgdata.get('recipient', _address.original_email)
        d['member'] = formataddr(
            (_address.display_name, _address.email))
        d['user_email'] = recipient
        d['user_delivered_to'] = _address.original_email
        d['user_language'] = member.preferred_language.description
        d['user_name'] = member.display_name
        d['user_name_or_email'] = member.display_name or recipient
        # For backward compatibility.
        d['user_name_or_address'] = member.display_name or recipient
        d['user_address'] = recipient
    # Calculate the archiver permalink substitution variables.  This provides
    # the $<archive-name>_url placeholder for every enabled archiver.
    for archiver in IListArchiverSet(mlist).archivers:
        if archiver.is_enabled:
            # Get the permalink of the message from the archiver.  Watch out
            # for exceptions in the archiver plugin.
            try:
                archive_url = archiver.system_archiver.permalink(mlist, msg)
            except Exception:
                alog.exception('Exception in "{}" archiver'.format(
                    archiver.system_archiver.name))
                archive_url = None
            if archive_url is not None:
                placeholder = '{}_url'.format(archiver.system_archiver.name)
                d[placeholder] = archive_url
    # These strings are descriptive for the log file and shouldn't be i18n'd
    d.update(msgdata.get('decoration-data', {}))
    header = decorate('list:member:regular:header', mlist, d)
    footer = decorate('list:member:regular:footer', mlist, d)
    # Escape hatch if both the footer and header are empty or None.
    if len(header) == 0 and len(footer) == 0:
        return
    # Be MIME smart here.  We only attach the header and footer by
    # concatenation when the message is a non-multipart of type text/plain.
    # Otherwise, if it is not a multipart, we make it a multipart, and then we
    # add the header and footer as text/plain parts.
    #
    # BJG: In addition, only add the footer if the message's character set
    # matches the charset of the list's preferred language.  This is a
    # suboptimal solution, and should be solved by allowing a list to have
    # multiple headers/footers, for each language the list supports.
    #
    # Also, if the list's preferred charset is us-ascii, we can always
    # safely add the header/footer to a plain text message since all
    # charsets Mailman supports are strict supersets of us-ascii --
    # no, UTF-16 emails are not supported yet.
    #
    # TK: Message with 'charset=' cause trouble. So, instead of
    #     mgs.get_content_charset('us-ascii') ...
    mcset = msg.get_content_charset() or 'us-ascii'
    lcset = mlist.preferred_language.charset
    msgtype = msg.get_content_type()
    # BAW: If the charsets don't match, should we add the header and footer by
    # MIME multipart chroming the message?
    wrap = True
    if not msg.is_multipart() and msgtype == 'text/plain':
        # Save the RFC-3676 format parameters.
        format_param = msg.get_param('format')
        delsp = msg.get_param('delsp')
        # Save 'Content-Transfer-Encoding' header in case decoration fails.
        cte_header = msg.get('content-transfer-encoding')
        cte = (cte_header or '7bit').strip().lower()
        if cte in ('7bit', '8bit'):
            # Direct concatenation preserving CTE.  The old code called
            # msg.set_payload(bytes, charset) which lets Python's email
            # library choose the CTE — it picks base64 for utf-8 and
            # quoted-printable for iso-8859-1.  This silently re-encodes
            # the entire body, changing every line.  Instead, we encode
            # the concatenated text ourselves and set the payload as a
            # string, preserving the original CTE.
            try:
                oldpayload = msg.get_payload(decode=True).decode(mcset)
                frontsep = endsep = ''
                if len(header) > 0 and not header.endswith('\n'):
                    frontsep = '\n'
                if len(footer) > 0 and not oldpayload.endswith('\n'):
                    endsep = '\n'
                payload = header + frontsep + oldpayload + endsep + footer
                for cset in (lcset, mcset, 'utf-8'):
                    try:
                        payload_bytes = payload.encode(cset)
                    except (UnicodeError, LookupError):
                        continue
                    # For 7bit, verify all bytes are ASCII; if not, use 8bit.
                    has_high = any(b > 127 for b in payload_bytes)
                    actual_cte = '8bit' if (cte == '7bit' and has_high) else cte
                    # Store as string using surrogateescape so
                    # BytesGenerator reproduces the original bytes.
                    del msg['content-transfer-encoding']
                    msg.set_payload(
                        payload_bytes.decode('ascii', 'surrogateescape'))
                    msg['Content-Transfer-Encoding'] = actual_cte
                    msg.set_param('charset', cset)
                    if format_param:
                        msg.set_param('format', format_param)
                    if delsp:
                        msg.set_param('delsp', delsp)
                    wrap = False
                    break
            except (LookupError, UnicodeError):
                pass
        elif cte == 'quoted-printable':
            # Preserve original QP encoding of the body by concatenating
            # at the raw (QP-encoded) level.  Only the header/footer text
            # is freshly QP-encoded; the original body lines remain
            # byte-identical.  This preserves the original sender's QP
            # choices, including unnecessarily-quoted characters (e.g.
            # =48 for 'H') and non-standard soft line break positions.
            try:
                # Normalize to \n — the generator will convert to
                # \r\n on output.  This ensures consistent line endings
                # when concatenating with quopri.encode() output.
                raw_qp = msg.get_payload().replace('\r\n', '\n')
                oldpayload = msg.get_payload(decode=True).decode(mcset)
                frontsep = endsep = ''
                if len(header) > 0 and not header.endswith('\n'):
                    frontsep = '\n'
                if len(footer) > 0 and not oldpayload.endswith('\n'):
                    endsep = '\n'
                for cset in (lcset, mcset, 'utf-8'):
                    try:
                        parts = []
                        header_text = header + frontsep
                        if header_text:
                            hdr_bytes = header_text.encode(cset)
                            inp, out = BytesIO(hdr_bytes), BytesIO()
                            quopri.encode(inp, out, quotetabs=False)
                            parts.append(out.getvalue().decode('ascii'))
                        parts.append(raw_qp)
                        footer_text = endsep + footer
                        if footer_text:
                            # Ensure a newline before the footer
                            if parts[-1] and not parts[-1].endswith('\n'):
                                parts.append('\n')
                            ftr_bytes = footer_text.encode(cset)
                            inp, out = BytesIO(ftr_bytes), BytesIO()
                            quopri.encode(inp, out, quotetabs=False)
                            parts.append(out.getvalue().decode('ascii'))
                        new_payload = ''.join(parts)
                        del msg['content-transfer-encoding']
                        msg.set_payload(new_payload)
                        msg['Content-Transfer-Encoding'] = 'quoted-printable'
                        msg.set_param('charset', cset)
                        if format_param:
                            msg.set_param('format', format_param)
                        if delsp:
                            msg.set_param('delsp', delsp)
                        wrap = False
                        break
                    except (UnicodeError, LookupError):
                        continue
            except (LookupError, UnicodeError):
                pass
        elif cte == 'base64':
            # Re-encode the concatenated text as base64 using the same
            # line width as the original.  Base64 is deterministic, so
            # all complete lines before the original's last line will be
            # byte-identical.  Only the last original line changes
            # (its padding disappears) and new lines appear for the
            # footer.  This produces a compact MI recipe: one range
            # for the matching lines plus one literal for the original
            # last line.
            #
            # Falls through to MIME wrapping if re-encoding fails.
            try:
                raw_b64 = msg.get_payload().replace('\r\n', '\n')
                # Detect original line width from first complete line.
                first_nl = raw_b64.find('\n')
                if first_nl > 0:
                    line_width = first_nl
                else:
                    line_width = 76
                oldpayload = msg.get_payload(decode=True).decode(mcset)
                frontsep = endsep = ''
                if len(header) > 0 and not header.endswith('\n'):
                    frontsep = '\n'
                if len(footer) > 0 and not oldpayload.endswith('\n'):
                    endsep = '\n'
                payload = header + frontsep + oldpayload + endsep + footer
                for cset in (lcset, mcset, 'utf-8'):
                    try:
                        payload_bytes = payload.encode(cset)
                    except (UnicodeError, LookupError):
                        continue
                    b64_flat = base64.b64encode(payload_bytes).decode('ascii')
                    b64_wrapped = '\n'.join(
                        b64_flat[i:i+line_width]
                        for i in range(0, len(b64_flat), line_width))
                    b64_wrapped += '\n'
                    del msg['content-transfer-encoding']
                    msg.set_payload(b64_wrapped)
                    msg['Content-Transfer-Encoding'] = 'base64'
                    msg.set_param('charset', cset)
                    if format_param:
                        msg.set_param('format', format_param)
                    if delsp:
                        msg.set_param('delsp', delsp)
                    wrap = False
                    break
            except (LookupError, UnicodeError):
                pass
        # For any other CTE, or if the above paths failed,
        # fall through to MIME wrapping below (wrap remains True).
    elif msg.get_content_type() == 'multipart/mixed':
        # The next easiest thing to do is just prepend the header and append
        # the footer as additional subparts
        payload = msg.get_payload()
        if not isinstance(payload, list):
            payload = [payload]
        if len(footer) > 0:
            mimeftr = MIMEText(
                footer.encode(lcset, errors='replace'), 'plain', lcset)
            mimeftr['Content-Disposition'] = 'inline'
            payload.append(mimeftr)
        if len(header) > 0:
            mimehdr = MIMEText(
                header.encode(lcset, errors='replace'), 'plain', lcset)
            mimehdr['Content-Disposition'] = 'inline'
            payload.insert(0, mimehdr)
        msg.set_payload(payload)
        wrap = False
    # If we couldn't add the header or footer in a less intrusive way, we can
    # at least do it by MIME encapsulation.  We want to keep as much of the
    # outer chrome as possible.
    if not wrap:
        return
    # Because of the way Message objects are passed around to process(), we
    # need to play tricks with the outer message -- i.e. the outer one must
    # remain the same instance.  So we're going to create a clone of the outer
    # message, with all the header chrome intact, then delete unwanted headers.
    inner = copy.deepcopy(msg)
    # Which headers to keep?  Let's just do the Content-* headers
    for h, v in inner.items():
        if not h.lower().startswith('content-'):
            del inner[h]
    # Now, play games with the outer message to make it contain three
    # subparts: the header (if any), the wrapped message, and the footer (if
    # any).
    payload = [inner]
    if len(header) > 0:
        mimehdr = MIMEText(
            header.encode(lcset, errors='replace'), 'plain', lcset)
        mimehdr['Content-Disposition'] = 'inline'
        payload.insert(0, mimehdr)
    if len(footer) > 0:
        mimeftr = MIMEText(
            footer.encode(lcset, errors='replace'), 'plain', lcset)
        mimeftr['Content-Disposition'] = 'inline'
        payload.append(mimeftr)
    msg.set_payload(payload)
    del msg['content-type']
    del msg['content-transfer-encoding']
    del msg['content-disposition']
    msg['Content-Type'] = 'multipart/mixed'


@public
def decorate(name, mlist, extradict=None):
    """Expand the named decoration template uri."""
    if extradict is None:
        extradict = {}
    # Get the decorator template.
    template = getUtility(ITemplateLoader).get(name, mlist, **extradict)
    return decorate_template(mlist, template, extradict)


@public
def decorate_template(mlist, template, extradict=None):
    """Expand the decoration template."""
    # Create a dictionary which includes the default set of interpolation
    # variables allowed in headers and footers.  These will be augmented by
    # any key/value pairs in the extradict.
    substitutions = {
        key: getattr(mlist, key)
        for key in ('fqdn_listname',
                    'list_name',
                    'mail_host',
                    'display_name',
                    'request_address',
                    'description',
                    'info',
                    )
        }
    if extradict is not None:
        substitutions.update(extradict)
    text = expand(template, mlist, substitutions)
    # Don't return non-empty, whitespace only templates.
    if not text.strip():
        return ''
    # Turn any \r\n line endings into just \n
    return re.sub(r'\r\n', r'\n', text)


@public
@implementer(IHandler)
class Decorate:
    """Decorate a message with headers and footers."""

    name = 'decorate'
    description = _('Decorate a message with headers and footers.')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        process(mlist, msg, msgdata)
