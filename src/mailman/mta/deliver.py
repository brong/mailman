# Copyright (C) 2009-2025 by the Free Software Foundation, Inc.
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

"""Generic delivery."""

import time
import logging

from mailman.config import config
from mailman.interfaces.mailinglist import Personalization
from mailman.interfaces.mta import SomeRecipientsFailed
from mailman.mta.arc_signing import ARCSigningMixin
from mailman.mta.base import IndividualDelivery
from mailman.mta.bulk import BulkDelivery
from mailman.mta.decorating import DecoratingMixin
from mailman.mta.personalized import PersonalizedMixin
from mailman.mta.verp import VERPMixin
from mailman.utilities.string import expand
from public import public


COMMA = ','
log = logging.getLogger('mailman.smtp')


@public
class Deliver(VERPMixin, DecoratingMixin, ARCSigningMixin, PersonalizedMixin,
              IndividualDelivery):
    """Deliver one message to one recipient.

    All current individualized features are avaialble to this
    `IMailTransportAgentDelivery` instance:

    * VERP
    * Full Personalization
    * Header/Footer decoration
    * ARC signing
    """

    def __init__(self):
        super().__init__()
        self.callbacks.extend([
            self.avoid_duplicates,
            self.decorate,
            self.personalize_to,
            self.arc_sign,
            ])


@public
def deliver(mlist, msg, msgdata):
    """Deliver a message to the outgoing mail server."""
    # If there are no recipients, there's nothing to do.
    recipients = msgdata.get('recipients')
    if not recipients:
        # Could be None, could be an empty sequence.
        return
    # Which delivery agent should we use?  Several situations can cause us to
    # use individual delivery.  If not specified, use bulk delivery.  See the
    # to-outgoing handler for when the 'verp' key is set in the metadata.
    if msgdata.get('verp', False):
        agent = Deliver()
    elif mlist.personalize != Personalization.none:
        agent = Deliver()
    else:
        agent = BulkDelivery(int(config.mta.max_recipients))
    log.debug('Using agent: %s', agent)
    # Keep track of the original recipients and the original sender for
    # logging purposes.
    original_recipients = msgdata['recipients']
    original_sender = msgdata.get('original-sender', msg.sender)
    # Let the agent attempt to deliver to the recipients.  Record all failures
    # for re-delivery later.
    t0 = time.time()
    refused = agent.deliver(mlist, msg, msgdata)
    # At this point we have completed the initial SMTP for this message.
    # We should close the SMTP connection regardless of the
    # sessions_per_connection setting because otherwise if there are no more
    # messages in the queue, the connection is left open until it times out
    # which can cause problems in the MTA.
    # XXX It would arguably be better to close only if the queue is empty, but
    # this means examining the queue here or closing from the outgoing runner,
    # either of which requires more information than should be available.
    agent._connection.quit()
    t1 = time.time()
    # Log this posting.
    size = getattr(msg, 'original_size', msgdata.get('original_size'))
    if size is None:
        size = len(msg.as_string())
    substitutions = dict(
        msgid       = msg.get('message-id',           # noqa: E221,E251
                              'n/a').strip(),
        listname    = mlist.fqdn_listname,            # noqa: E221,E251
        sender      = original_sender,                # noqa: E221,E251
        recip       = len(original_recipients),       # noqa: E221,E251
        size        = size,                           # noqa: E221,E251
        time        = t1 - t0,                        # noqa: E221,E251
        refused     = len(refused),                   # noqa: E221,E251
        smtpcode    = 'n/a',                          # noqa: E221,E251
        smtpmsg     = 'n/a',                          # noqa: E221,E251
        )
    template = config.logging.smtp.every
    if template.lower() != 'no':
        log.info('%s', expand(template, mlist, substitutions))
    if refused:
        template = config.logging.smtp.refused
        if template.lower() != 'no':
            log.info('%s', expand(template, mlist, substitutions))
    else:
        # Log the successful post, but if it was not destined to the mailing
        # list (e.g. to the owner or admin), print the actual recipients
        # instead of just the number.
        if not msgdata.get('to_list', False):
            # XXX This is meaningless as the config.logging.smtp.success
            # template doesn't contain a recip substitution, but do it anyway
            # in case the template is changed.
            recips = msg.get_all('to', [])
            recips.extend(msg.get_all('cc', []))
            # recips can contain a Header() instance.  Stringify it.
            substitutions['recip'] = COMMA.join(map(str, recips))
        template = config.logging.smtp.success
        if template.lower() != 'no':
            log.info('%s', expand(template, mlist, substitutions))
    # Process any failed deliveries.
    temporary_failures = []
    permanent_failures = []
    for recipient, (code, smtp_message) in refused.items():
        # RFC 5321, $4.5.3.1.10 says:
        #
        #   RFC 821 [1] incorrectly listed the error where an SMTP server
        #   exhausts its implementation limit on the number of RCPT commands
        #   ("too many recipients") as having reply code 552.  The correct
        #   reply code for this condition is 452.  Clients SHOULD treat a 552
        #   code in this case as a temporary, rather than permanent, failure
        #   so the logic below works.
        #
        if code >= 500 and code != 552:
            # A permanent failure.  Keep the code and message for a fake DSN.
            permanent_failures.append(
                (recipient, code, smtp_message))    # pragma: nocover
        else:
            # Deal with persistent transient failures by queuing them up for
            # future delivery.  TBD: this could generate lots of log entries!
            temporary_failures.append(recipient)
        template = config.logging.smtp.failure
        if template.lower() != 'no':
            substitutions.update(
                recip       = recipient,            # noqa: E221,E251
                smtpcode    = code,                 # noqa: E221,E251
                smtpmsg     = smtp_message,         # noqa: E221,E251
                )
            log.info('%s', expand(template, mlist, substitutions))
    # Return the results
    if temporary_failures or permanent_failures:
        raise SomeRecipientsFailed(temporary_failures, permanent_failures)
