# Copyright (C) 2002-2023 by the Free Software Foundation, Inc.
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

"""MIME-stripping filter for Mailman.

This module scans a message for MIME content, removing those sections whose
MIME types match one of a list of matches.  multipart/alternative sections are
replaced by the first non-empty component, and multipart/mixed sections
wrapping only single sections after other processing are replaced by their
contents.
"""

import os
import copy
import shutil
import logging
import tempfile

from contextlib import ExitStack, suppress
from email.iterators import typed_subpart_iterator
from email.mime.message import MIMEMessage
from email.mime.text import MIMEText
from itertools import count
from lazr.config import as_boolean
from mailman.config import config
from mailman.core.i18n import _
from mailman.email.message import OwnerNotification
from mailman.interfaces.action import FilterAction
from mailman.interfaces.handler import IHandler
from mailman.interfaces.pipeline import DiscardMessage, RejectMessage
from mailman.utilities.string import oneline
from mailman.version import VERSION
from public import public
from string import Template
from subprocess import CalledProcessError, check_output
from zope.interface import implementer


log = logging.getLogger('mailman.error')


def dispose(mlist, msg, msgdata, why):
    if mlist.filter_action is FilterAction.reject:
        # Bounce the message to the original author.
        raise RejectMessage(why)
    elif (mlist.filter_action is FilterAction.forward and
            msgdata.get('fwd_preserve', True)):
        # Forward it on to the list moderators.
        text = _("""\
The attached message matched the ${mlist.display_name} mailing list's content
filtering rules and was prevented from being forwarded on to the list
membership.  You are receiving the only remaining copy of the discarded
message.

""")
        subject = _('Content filter message notification')
        notice = OwnerNotification(mlist, subject, roster=mlist.administrators)
        notice.set_type('multipart/mixed')
        notice.attach(MIMEText(text))
        notice.attach(MIMEMessage(msg))
        notice.send(mlist)
        # Let this fall through so the original message gets discarded.
    elif (mlist.filter_action is FilterAction.preserve and
            msgdata.get('fwd_preserve', True)):
        if as_boolean(config.mailman.filtered_messages_are_preservable):
            # This is just like discarding the message except that a copy is
            # placed in the 'bad' queue should the site administrator want to
            # inspect the message.
            filebase = config.switchboards['bad'].enqueue(msg, msgdata)
            log.info('{} preserved in file base {}'.format(
                msg.get('message-id', 'n/a').strip(), filebase))
    elif mlist.filter_action is FilterAction.discard:
        pass
    elif msgdata.get('fwd_preserve', True):
        log.error(
            '{} invalid FilterAction: {}.  Treating as discard'.format(
                mlist.fqdn_listname, mlist.filter_action.name))
    # Most cases also discard the message
    raise DiscardMessage(why)


def process(mlist, msg, msgdata):
    global attach_report, report
    report = _("""
___________________________________________
Mailman's content filtering has removed the
following MIME parts from this message.
""")
    attach_report = False
    ctype = msg.get_content_type()
    mtype = msg.get_content_maintype()
    # Check to see if the outer type matches one of the filter types
    filtertypes = set(mlist.filter_types)
    passtypes = set(mlist.pass_types)
    if ctype in filtertypes or mtype in filtertypes:
        dispose(mlist, msg, msgdata,
                _("The message's content type was explicitly disallowed"))
    # Check to see if there is a pass types and the outer type doesn't match
    # one of these types
    if passtypes and not (ctype in passtypes or mtype in passtypes):
        dispose(mlist, msg, msgdata,
                _("The message's content type was not explicitly allowed"))
    # Filter by file extensions
    filterexts = set(mlist.filter_extensions)
    passexts = set(mlist.pass_extensions)
    fext = get_file_ext(msg)
    if fext:
        if fext in filterexts:
            dispose(
                mlist, msg, msgdata,
                _("The message's file extension was explicitly disallowed"))
        if passexts and not (fext in passexts):
            dispose(
                mlist, msg, msgdata,
                _("The message's file extension was not explicitly allowed"))
    numparts = len([subpart for subpart in msg.walk()])
    # If the message is a multipart, filter out matching subparts
    if msg.is_multipart():
        # Recursively filter out any subparts that match the filter list
        prelen = len(msg.get_payload())
        premsg = copy.deepcopy(msg)
        filter_parts(msg, filtertypes, passtypes, filterexts, passexts)
        # If the outer message is now an empty multipart (and it wasn't
        # before!) then, again it gets discarded.
        postlen = len(msg.get_payload())
        if postlen == 0 and prelen > 0:
            dispose(mlist, premsg, msgdata,
                    _("After content filtering, the message was empty"))
    # Now replace all multipart/alternatives with just the first non-empty
    # alternative.  BAW: We have to special case when the outer part is a
    # multipart/alternative because we need to retain most of the outer part's
    # headers.  For now we'll move the subpart's payload into the outer part,
    # and then copy over its Content-Type: and Content-Transfer-Encoding:
    # headers (any others?).
    if mlist.collapse_alternatives:
        collapse_multipart_alternatives(msg)
        if ctype == 'multipart/alternative':
            firstalt = msg.get_payload(0)
            reset_payload(msg, firstalt)
            report += _("""
Replaced multipart/alternative part with first alternative.
""")
            # MAS Not setting attach_report True here will not report if the
            # only change is collapsing an outer MPA message. On lists where
            # most people post from MUAs that compose HTML and send MPA,
            # setting this here will add this report to most messages which
            # can be annoying.
            # attach_report = True
    # Now that we've collapsed the MPA parts, go through the message
    # and recast any multipart parts with only one sub-part as just
    # the sub-part.
    if msg.is_multipart():
        recast_multipart(msg)
    # If we removed some parts, make note of this
    changedp = 0
    if numparts != len([subpart for subpart in msg.walk()]):
        changedp = 1
    # Now perhaps convert all text/html to text/plain.
    if mlist.convert_html_to_plaintext:
        changedp += to_plaintext(msg)
    # If we're left with only two parts, an empty body and one attachment,
    # recast the message to one of just that part
    if msg.is_multipart() and len(msg.get_payload()) == 2:
        if msg.get_payload(0).get_payload() == '':
            useful = msg.get_payload(1)
            reset_payload(msg, useful)
            changedp = 1
    if changedp:
        msg['X-Content-Filtered-By'] = 'Mailman/MimeDel {}'.format(VERSION)
    if attach_report and as_boolean(config.mailman.filter_report):
        if msg.is_multipart():
            if msg.get_content_type() == 'multipart/mixed':
                msg.attach(MIMEText(report))
            else:
                # Some non-mixed multipart, we need to wrap it.
                # This is based on code in handlers/decorate.py
                # Because of the way Message objects are passed around to
                # process(), we need to play tricks with the outer message
                # -- i.e. the outer one must remain the same instance.
                #  So we're going to create a clone of the outer message,
                # with all the header chrome intact, then delete unwanted
                # headers.
                inner = copy.deepcopy(msg)
                # Which headers to keep?  Let's just do the Content-* headers
                for h, v in inner.items():
                    if not h.lower().startswith('content-'):
                        del inner[h]
                # Now, play games with the outer message to make it contain two
                # subparts: the wrapped message, and the report.
                payload = [inner]
                payload.append(MIMEText(report))
                msg.set_payload(payload)
                del msg['content-type']
                del msg['content-transfer-encoding']
                del msg['content-disposition']
                msg['Content-Type'] = 'multipart/mixed'
        else:
            pl = msg.get_payload(decode=True)
            cset = msg.get_content_charset(None) or 'us-ascii'
            del msg['content-transfer-encoding']
            new_pl = pl.decode(cset)
            if not pl.endswith(b'\n'):
                new_pl += '\n'
            new_pl += report
            msg.set_payload(new_pl, cset)


def reset_payload(msg, subpart):
    # Reset payload of msg to contents of subpart, and fix up content headers
    if subpart.is_multipart():
        msg.set_payload(subpart.get_payload())
    else:
        cset = subpart.get_content_charset() or 'us-ascii'
        msg.set_payload(subpart.get_payload(decode=True).decode(
                        cset, errors='replace'),
                        charset=cset)
    # Don't restore Content-Transfer-Encoding; set_payload sets it based
    # on the charset.
    del msg['content-type']
    del msg['content-disposition']
    del msg['content-description']
    msg['Content-Type'] = subpart.get('content-type', 'text/plain')
    cdisp = subpart.get('content-disposition')
    if cdisp:
        msg['Content-Disposition'] = cdisp
    cdesc = subpart.get('content-description')
    if cdesc:
        msg['Content-Description'] = cdesc


def filter_parts(msg, filtertypes, passtypes, filterexts, passexts):
    global attach_report, report
    # Look at all the message's subparts, and recursively filter
    if not msg.is_multipart():
        return True
    payload = msg.get_payload()
    prelen = len(payload)
    newpayload = []
    for subpart in payload:
        keep = filter_parts(subpart, filtertypes, passtypes,
                            filterexts, passexts)
        if not keep:
            continue
        ctype = subpart.get_content_type()
        mtype = subpart.get_content_maintype()
        fname = subpart.get_filename('') or subpart.get_param('name', '')
        if ctype in filtertypes or mtype in filtertypes:
            # Throw this subpart away
            report += '\nContent-Type: %s\n' % ctype
            if fname:
                report += '    ' + _('Name: ${fname}\n')
            attach_report = True
            continue
        if passtypes and not (ctype in passtypes or mtype in passtypes):
            # Throw this subpart away
            report += '\nContent-Type: %s\n' % ctype
            if fname:
                report += '    ' + _('Name: ${fname}\n')
            attach_report = True
            continue
        # check file extension
        fext = get_file_ext(subpart)
        if fext:
            if fext in filterexts:
                report += '\nContent-Type: %s\n' % ctype
                if fname:
                    report += '    ' + _('Name: ${fname}\n')
                attach_report = True
                continue
            if passexts and not (fext in passexts):
                report += '\nContent-Type: %s\n' % ctype
                if fname:
                    report += '    ' + _('Name: ${fname}\n')
                attach_report = True
                continue
        newpayload.append(subpart)
    # Check to see if we discarded all the subparts
    postlen = len(newpayload)
    msg.set_payload(newpayload)
    if postlen == 0 and prelen > 0:
        # We threw away everything
        return False
    return True


def collapse_multipart_alternatives(msg):
    global attach_report, report
    if not msg.is_multipart():
        return
    newpayload = []
    for subpart in msg.get_payload():
        if subpart.get_content_type() == 'multipart/alternative':
            with suppress(IndexError):
                firstalt = subpart.get_payload(0)
                if msg.get_content_type() == 'message/rfc822':
                    # This is a multipart/alternative message in a
                    # message/rfc822 part. We treat it specially so as not to
                    # lose the headers.
                    reset_payload(subpart, firstalt)
                    newpayload.append(subpart)
                else:
                    newpayload.append(firstalt)
                report += _("""
Replaced multipart/alternative part with first alternative.
""")
                attach_report = True
        elif subpart.is_multipart():
            collapse_multipart_alternatives(subpart)
            newpayload.append(subpart)
        else:
            newpayload.append(subpart)
    msg.set_payload(newpayload)


def recast_multipart(msg):
    # If we're left with a multipart message with only one sub-part, recast
    # the message to just the sub-part, but not if the part is message/rfc822
    # because we don't want to lose the headers.
    # Also, if this is a multipart/signed part, stop now as the original part
    # may have had a multipart sub-part with only one sub-sub-part, the sig
    # may still be valid and going further may break it.  (LP: #1551075)
    if msg.get_content_type() == 'multipart/signed':
        return
    if msg.is_multipart():
        if (len(msg.get_payload()) == 1 and
                msg.get_content_type() != 'message/rfc822'):
            reset_payload(msg, msg.get_payload(0))
            # now that we've recast this part, check the subordinate parts
            recast_multipart(msg)
        else:
            # This part's OK but check deeper.
            for part in msg.get_payload():
                recast_multipart(part)


def to_plaintext(msg):
    changedp = 0
    counter = count()
    with ExitStack() as resources:
        tempdir = tempfile.mkdtemp()
        resources.callback(shutil.rmtree, tempdir)
        for subpart in typed_subpart_iterator(msg, 'text', 'html'):
            filename = os.path.join(tempdir, '{}.html'.format(next(counter)))
            cset = subpart.get_content_charset('us-ascii')
            with open(filename, 'w', encoding='utf-8') as fp:
                fp.write(subpart.get_payload(decode=True).decode(cset,
                         errors='replace'))
            template = Template(config.mailman.html_to_plain_text_command)
            command = template.safe_substitute(filename=filename).split()
            try:
                stdout = check_output(command, universal_newlines=True)
            except (CalledProcessError, FileNotFoundError, PermissionError):
                log.exception('HTML -> text/plain command error')
            else:
                # Replace the payload of the subpart with the converted text
                # and tweak the content type.
                del subpart['content-transfer-encoding']
                subpart.set_payload(stdout, charset=cset)
                subpart.set_type('text/plain')
                changedp += 1
    return changedp


def get_file_ext(m):
    """
    Get filename extension. Caution: some virus don't put filename
    in 'Content-Disposition' header.
"""
    fext = ''
    filename = m.get_filename('') or m.get_param('name', '')
    if filename:
        fext = os.path.splitext(oneline(filename, 'utf-8', in_unicode=True))[1]
        if len(fext) > 1:
            fext = fext[1:]
        else:
            fext = ''
    return fext.lower()


@public
@implementer(IHandler)
class MIMEDelete:
    """Filter the MIME content of messages."""

    name = 'mime-delete'
    description = _('Filter the MIME content of messages.')

    def process(self, mlist, msg, msgdata):
        # Short-circuits
        if not mlist.filter_content:
            return
        if msgdata.get('isdigest'):
            return
        process(mlist, msg, msgdata)
