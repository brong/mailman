# Copyright (C) 2000-2023 by the Free Software Foundation, Inc.
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

"""NNTP runner."""

import os
import re
import sys
import email
import socket
import logging
import nntplib
import subprocess

from datetime import datetime
from io import BytesIO
from lazr.config import as_timedelta
from mailman.config import config
from mailman.core.runner import Runner
from mailman.interfaces.nntp import NewsgroupModeration
from public import public


COMMA = ','
COMMASPACE = ', '
log = logging.getLogger('mailman.error')


# Matches our crafted Message-ID.
mcre = re.compile(r"""
    <                                    # match the prefix
    \d+\.                                # time in centi-seconds since epoch
    \d+\.                                # pid
    \d+\.                                # random number
    (?P<listname>[^@]+)                  # list's list_name
    @                                    # localpart@dom.ain
    (?P<hostname>[^>]+)                  # list's mail_host
    >                                    # trailer
    """, re.VERBOSE)


@public
class NNTPRunner(Runner):
    def __init__(self, name, slice=None):
        super().__init__(name, slice)
        self.lastrun = datetime.min
        self.delay = as_timedelta(config.nntp.gatenews_every)
        self.slice = slice
        python = sys.executable
        mailman = os.path.join(config.BIN_DIR, 'mailman')
        conf = config.filename
        self.cmd = [python, mailman, '-C', conf, 'gatenews']
        log.debug(self.cmd)

    def _dispose(self, mlist, msg, msgdata):
        # Get NNTP server connection information.
        host = config.nntp.host.strip()
        port = config.nntp.port.strip()
        if len(port) == 0:
            port = 119
        else:
            try:
                port = int(port)
            except (TypeError, ValueError):
                log.exception('Bad [nntp]port value: {}'.format(port))
                port = 119
        # Make sure we have the most up-to-date state
        if not msgdata.get('prepped'):
            prepare_message(mlist, msg, msgdata)
        # Flatten the message object, sticking it in a BytesIO object
        fp = BytesIO()
        email.generator.BytesGenerator(fp, maxheaderlen=0).flatten(msg)
        fp.seek(0)
        conn = None
        try:
            conn = nntplib.NNTP(host, port,
                                readermode=True,
                                user=config.nntp.user,
                                password=config.nntp.password)
            conn.post(fp)
        except nntplib.NNTPTemporaryError:
            # This could be a duplicate Message-ID for a message cross-posted
            # to another group.  See if we already munged the Message-ID.
            mo = mcre.search(msg.get('message-id', 'n/a'))
            if (mo and mo.group('listname') == mlist.list_name and
                    mo.group('hostname') == mlist.mail_host):
                # This is our munged Message-ID.  This must be a failure of the
                # requeued message or a Message-ID we added in prepare_message.
                # Get the original Message-ID or the one we added and log it.
                log_message_id = msgdata.get('original_message-id',
                                             msg.get('message-id', 'n/a'))
                log.exception('{} NNTP error for {}'.format(
                    log_message_id.strip(), mlist.fqdn_listname))
            else:
                # This might be a duplicate Message-ID.  Munge it and requeue
                # the message, but save the original Message-ID for logging.
                msgdata['original_message-id'] = msg.get('message-id', 'n/a')
                del msg['message-id']
                msg['Message-ID'] = email.utils.make_msgid(mlist.list_name,
                                                           mlist.mail_host)
                return True
        except socket.error:
            log.exception('{} NNTP socket error for {}'.format(
                msg.get('message-id', 'n/a').strip(), mlist.fqdn_listname))
        except Exception:
            # Some other exception occurred, which we definitely did not
            # expect, so set this message up for requeuing.
            log.exception('{} NNTP unexpected exception for {}'.format(
                msg.get('message-id', 'n/a').strip(), mlist.fqdn_listname))
            return True
        finally:
            if conn:
                conn.quit()
        return False

    def _do_periodic(self):
        """Invoked periodically by the run() method in the super class."""
        if self.lastrun + self.delay > datetime.now():
            return                                    # pragma: nocover
        if not (self.slice in (None, 0)):
            # If queue is sliced, only run for slice = 0.
            return                                    # pragma: nocover
        self.lastrun = datetime.now()
        log.debug('Running nntp runner periodic task gatenews')
        os.environ['_MAILMAN_GATENEWS_NNTP'] = 'yes'
        result = subprocess.run(self.cmd, stderr=subprocess.PIPE)
        if result.returncode != 0:
            log.error(f"""\
gatenews failed. status: {result.returncode}
message: {result.stderr}""")


def prepare_message(mlist, msg, msgdata):
    # If the newsgroup is moderated, we need to add this header for the Usenet
    # software to accept the posting, and not forward it on to the n.g.'s
    # moderation address.  The posting would not have gotten here if it hadn't
    # already been approved.  1 == open list, mod n.g., 2 == moderated
    if mlist.newsgroup_moderation in (NewsgroupModeration.open_moderated,
                                      NewsgroupModeration.moderated):
        del msg['approved']
        msg['Approved'] = mlist.posting_address
    # Should we restore the original, non-prefixed subject for gatewayed
    # messages? TK: We use stripped_subject (prefix stripped) which was crafted
    # in the subject-prefix handler to ensure prefix was stripped from the
    # subject came from mailing list user.
    stripped_subject = msgdata.get('stripped_subject',
                                   msgdata.get('original_subject'))
    if not mlist.nntp_prefix_subject_too and stripped_subject is not None:
        del msg['subject']
        msg['Subject'] = stripped_subject
    # Add the appropriate Newsgroups header.  Multiple Newsgroups headers are
    # generally not allowed so we're not testing for them.
    header = msg.get('newsgroups')
    if header is None:
        msg['Newsgroups'] = mlist.linked_newsgroup
    else:
        # See if the Newsgroups: header already contains our linked_newsgroup.
        # If so, don't add it again.  If not, append our linked_newsgroup to
        # the end of the header list
        newsgroups = [value.strip() for value in header.split(COMMA)]
        if mlist.linked_newsgroup not in newsgroups:
            newsgroups.append(mlist.linked_newsgroup)
            # Subtitute our new header for the old one.
            del msg['newsgroups']
            msg['Newsgroups'] = COMMASPACE.join(newsgroups)
    # Ensure we have an unfolded Message-ID.
    if not msg.get('message-id'):
        msg['Message-ID'] = email.utils.make_msgid(mlist.list_name,
                                                   mlist.mail_host)
    mid = re.sub(r'[\s]', '', msg.get('message-id'))
    msg.replace_header('message-id', mid)
    # Lines: is useful.
    if msg['Lines'] is None:
        # BAW: is there a better way?
        count = len(list(email.iterators.body_line_iterator(msg)))
        msg['Lines'] = str(count)
    # Massage the message headers by remove some and rewriting others.  This
    # won't completely sanitize the message, but it will eliminate the bulk of
    # the rejections based on message headers.  The NNTP server may still
    # reject the message because of other problems.
    for header in config.nntp.remove_headers.split():
        del msg[header]
    dup_headers = config.nntp.rewrite_duplicate_headers.split()
    if len(dup_headers) % 2 != 0:
        # There are an odd number of headers; ignore the last one.
        bad_header = dup_headers.pop()
        log.error('Ignoring odd [nntp]rewrite_duplicate_headers: {}'.format(
            bad_header))
    dup_headers.reverse()
    while dup_headers:
        source = dup_headers.pop()
        target = dup_headers.pop()
        values = msg.get_all(source, [])
        if len(values) < 2:
            # We only care about duplicates.
            continue
        # Delete all the original headers.
        del msg[source]
        # Put the first value back on the original header.
        msg[source] = values[0]
        # And put all the subsequent values on the destination header.
        for value in values[1:]:
            msg[target] = value
    # Mark this message as prepared in case it has to be requeued.
    msgdata['prepped'] = True
