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

"""Task runner."""

import os
import logging

from datetime import datetime
from lazr.config import as_timedelta
from mailman.app.bounces import PENDABLE_LIFETIME
from mailman.config import config
from mailman.core.runner import Runner
from mailman.database.transaction import dbconnection, transactional
from mailman.interfaces.cache import ICacheManager
from mailman.interfaces.messages import IMessageStore
from mailman.interfaces.pending import IPendings
from mailman.interfaces.workflow import IWorkflowStateManager
from mailman.model.bounce import BounceEvent
from mailman.model.requests import _Request
from mailman.utilities.datetime import now
from public import public
from zope.component import getUtility


dlog = logging.getLogger('mailman.debug')
tlog = logging.getLogger('mailman.task')


@public
class TaskRunner(Runner):
    """The task runner."""

    is_queue_runner = False

    def __init__(self, name, slice=None):
        super().__init__(name, slice)
        self.lastrun = datetime.min
        self.delay = as_timedelta(config.mailman.run_tasks_every)

    @transactional
    def _do_periodic(self):
        """Invoked periodically by the run() method in the super class."""
        if self.lastrun + self.delay > datetime.now():
            return                                    # pragma: nocover
        self.lastrun = datetime.now()
        dlog.debug('Running task runner periodic tasks')
        self._evict_pendings()
        self._evict_expired_bounce_events()
        self._evict_cache()

    @dbconnection
    def _get_requests(self, store):
        # Get (id, token) for all requests.
        results = store.query(_Request).all()
        yield from [(result.id, result.data_hash) for result in results]

    @dbconnection
    def _delete_request(self, store, id):
        # Delete the request with id = id.
        request = (store.query(_Request).get(id))
        if request is not None:
            store.delete(request)

    def _evict_pendings(self):
        pendings = getUtility(IPendings)
        wfmanager = getUtility(IWorkflowStateManager)
        opendings = pendings.count()
        owflows = wfmanager.count
        pendings.evict()
        count = opendings - pendings.count()
        tlog.info('Task runner evicted %d expired pendings', count)
        # Now delete any orphaned workflow states.
        for token in wfmanager.get_all_tokens():
            if pendings.confirm(token, expunge=False) is None:
                wfmanager.discard(token)
        count = owflows - wfmanager.count
        tlog.info('Task runner deleted %d orphaned workflows', count)
        count = 0
        for id, token in self._get_requests():
            if pendings.confirm(token, expunge=False) is None:
                self._delete_request(id)
                count += 1
        tlog.info('Task runner deleted %d orphaned requests', count)
        # Also, delete any orphaned messages from the message store.
        mids = dict()
        for token, pendable in pendings:
            if not pendable:
                continue                                # pragma: nocover
            mid = pendable.get('_mod_message_id')
            if mid:
                mids[mid] = True
        count = 0
        messages = getUtility(IMessageStore)
        # Need this below.
        hashes = dict()
        for msg in messages.messages:
            # msg can be None if file is already removed.
            if msg is not None:
                hashes[msg['message-id-hash']] = True
                mid = msg.get('message-id')
                if mid not in mids:
                    messages.delete_message(mid)
                    count += 1
        # We also need to delete files which aren't in the message store.
        # MAS This is clunky, but I don't know a better way.
        # Find all the saved message files and remove orphans.
        base_dir = config.MESSAGES_DIR
        for root, dirs, files in os.walk(base_dir):
            if files:
                for f in files:
                    if f not in hashes:
                        os.remove(os.path.join(root, f))
                        count += 1
        tlog.info('Task runner deleted %d orphaned messages', count)

    @dbconnection
    def _evict_expired_bounce_events(self, store):
        count = 0
        for entry in store.query(BounceEvent).all():
            if not entry.processed:
                continue
            if entry.timestamp > now() - as_timedelta(PENDABLE_LIFETIME):
                continue
            store.delete(entry)
            count += 1
        tlog.info('Task runner evicted %d expired bounce events', count)

    def _evict_cache(self):
        getUtility(ICacheManager).evict_expired()
        tlog.info('Task runner evicted expired cache entries')
