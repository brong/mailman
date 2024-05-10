# Copyright (C) 2001-2023 by the Free Software Foundation, Inc.
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

"""Bounce runner."""

import logging

from flufl.bounce import all_failures
from mailman.app.bounces import maybe_forward, ProbeVERP, StandardVERP
from mailman.core.runner import Runner
from mailman.interfaces.bounce import (
    BounceContext,
    IBounceProcessor,
    InvalidBounceEvent,
)
from public import public
from zope.component import getUtility


COMMASPACE = ', '

log = logging.getLogger('mailman.bounce')
elog = logging.getLogger('mailman.error')


@public
class BounceRunner(Runner):
    """The bounce runner."""

    def __init__(self, name, slice=None):
        super().__init__(name, slice)
        self._processor = getUtility(IBounceProcessor)

    def _dispose(self, mlist, msg, msgdata):
        # List isn't doing bounce processing?
        if not mlist.process_bounces:
            return False
        # Try VERP detection first, since it's quick and easy
        context = BounceContext.normal
        addresses = StandardVERP().get_verp(mlist, msg)
        if len(addresses) > 0:
            # Scan the message to see if it contained permanent or temporary
            # failures.  We'll ignore temporary failures, and if there
            # are no permanent failures, we'll assume this is a vacation
            # response or similar.
            temporary, permanent = all_failures(msg)
            if len(temporary) > 0:
                # This was a temporary failure, so just ignore it.
                return False
            if len(permanent) == 0:
                log.info('VERPed bounce message but not a recognized DSN: %s',
                         msg.get('message-id', 'n/a').strip())
                maybe_forward(mlist, msg)
                return False
        else:
            # See if this was a probe message.
            addresses = ProbeVERP().get_verp(mlist, msg)
            if len(addresses) > 0:
                context = BounceContext.probe
            else:
                # That didn't give us anything useful, so try the old fashion
                # bounce matching modules.  Since Mailman currently doesn't
                # score temporary failures, if we get no permanent failures,
                # we're done, but we do need to check for temporary failures
                # to know if the bounce was recognized.
                temporary, addresses = all_failures(msg)
                if len(addresses) == 0 and len(temporary) > 0:
                    # This is a recognized temp fail so ignore it.
                    return False
        # If that still didn't return us any useful addresses, then send it on
        # or discard it.  The addresses will come back from flufl.bounce as
        # bytes/8-bit strings, but we must store them as unicodes in the
        # database.  Assume utf-8 encoding, but be cautious.
        if len(addresses) > 0:
            for address in addresses:
                if isinstance(address, bytes):
                    try:
                        address = address.decode('utf-8')
                    except UnicodeError:
                        log.exception('Ignoring non-UTF-8 encoded '
                                      'address: {}'.format(address))
                        continue
                self._processor.register(mlist, address, msg, context)
        else:
            log.info('Bounce message w/no discernable addresses: %s',
                     msg.get('message-id', 'n/a').strip())
            maybe_forward(mlist, msg)
        # Dequeue this message.
        return False

    def _do_periodic(self):
        """Invoked periodically by the run() method in the super class."""
        self._process_events()
        self._send_warnings()

    def _process_events(self):
        """Process all the pending bounce events."""
        log.debug('Processing bounce events.')
        for bounce_event in self._processor.unprocessed:
            try:
                self._processor.process_event(bounce_event)
            except InvalidBounceEvent as e:
                # This member is either unsubscribed or this is a very stale
                # event.
                log.info('Bounce message for a non subscriber: {}'.format(e))

    def _send_warnings(self):
        """Send warnings to disabled users and remove them if needed."""
        log.debug('Sending warnings to members with disabled delivery.')
        self._processor.send_warnings_and_remove()
