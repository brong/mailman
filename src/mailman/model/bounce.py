# Copyright (C) 2011-2023 by the Free Software Foundation, Inc.
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

"""Bounce support."""

import logging
import datetime

from email.utils import make_msgid
from lazr.config import as_boolean, as_timedelta
from mailman.app.bounces import _ProbePendable, send_probe
from mailman.app.membership import delete_member
from mailman.app.notifications import (
    send_admin_disable_notice,
    send_admin_increment_notice,
    send_admin_removal_notice,
    send_user_disable_warning,
)
from mailman.config import config
from mailman.database.model import Model
from mailman.database.transaction import (
    dbconnection,
    transaction,
    transactional,
)
from mailman.database.types import Enum, SAUnicode
from mailman.interfaces.bounce import (
    BounceContext,
    IBounceEvent,
    IBounceProcessor,
    InvalidBounceEvent,
)
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.member import DeliveryStatus, IMembershipManager
from mailman.interfaces.messages import IMessageStore
from mailman.interfaces.pending import IPendings
from mailman.utilities.datetime import now
from public import public
from sqlalchemy import Boolean, Column, DateTime, Integer
from zope.component import getUtility
from zope.interface import implementer


log = logging.getLogger('mailman.bounce')


@public
@implementer(IBounceEvent)
class BounceEvent(Model):
    """See `IBounceEvent`."""

    __tablename__ = 'bounceevent'

    id = Column(Integer, primary_key=True)
    list_id = Column(SAUnicode)
    email = Column(SAUnicode)
    timestamp = Column(DateTime)
    message_id = Column(SAUnicode)
    context = Column(Enum(BounceContext))
    processed = Column(Boolean)

    def __init__(self, list_id, email, msg, context=None):
        self.list_id = list_id
        self.email = email
        self.timestamp = now()
        msgid = msg['message-id']
        self.message_id = msgid
        self.context = (BounceContext.normal if context is None else context)
        self.processed = False


@public
@implementer(IBounceProcessor)
class BounceProcessor:
    """See `IBounceProcessor`."""

    @dbconnection
    def register(self, store, mlist, email, msg, where=None):
        """See `IBounceProcessor`."""
        # Save the DSN in the message store for notices.  It doesn't
        # matter if we save it more than once.  Only one copy will be
        # saved, but ensure it has a Message-ID so we can retreive it.
        if msg.get('message-id') is None:
            msg['Message-ID'] = make_msgid          # pragma: nocover
        getUtility(IMessageStore).add(msg)
        # We also need to pend a token for this or the message will be
        # removed as an orphan by the task runner.  We don't need much
        # from this.  We pend the msgid as _mod_message_id for the
        # task runner.
        pendable = _ProbePendable(
            _mod_message_id=msg.get('message-id'))
        getUtility(IPendings).add(
            pendable, lifetime=as_timedelta(config.mailman.dsn_lifetime))
        event = BounceEvent(mlist.list_id, email, msg, where)
        store.add(event)
        return event

    @property
    @dbconnection
    def events(self, store):
        """See `IBounceProcessor`."""
        yield from store.query(BounceEvent).all()

    @property
    @dbconnection
    def unprocessed(self, store):
        """See `IBounceProcessor`."""
        yield from store.query(BounceEvent).filter_by(processed=False)

    def _disable_delivery(self, mlist, member, event):
        """Disable deliver for the member and maybe notify the admin.

        :param mlist: The mailing list which bounce event belongs to.
        :type mlist: IMailingList
        :param member: The member object the bouncing address belongs to.
        :type member: IMember
        :param event: The bounce event that causes this.
        :type event:" IBounceEvent
        """
        # If the membership is already disabled, do not sent another notice to
        # the admin.
        if member.preferences.delivery_status == DeliveryStatus.by_bounces:
            return
        member.preferences.delivery_status = DeliveryStatus.by_bounces
        # We also need to set these.  It doesn't matter if they are already
        # set from a prior disable.  They should be set no for this one.
        member.total_warnings_sent = 0
        member.last_warning_sent = datetime.datetime.min
        log.info('Disabling delivery for %s on list %s by bounce',
                 event.email, mlist.list_id)
        if mlist.bounce_notify_owner_on_disable:
            send_admin_disable_notice(
                mlist, event, display_name=member.display_name)

    @transactional
    @dbconnection
    def process_event(self, store, event):
        """See `IBounceProcessor`."""
        list_manager = getUtility(IListManager)
        mlist = list_manager.get(event.list_id)
        if mlist is None:
            # List was removed before the bounce is processed.
            event.processed = True
            # This needs an explicit commit because of the raise.
            config.db.commit()
            raise InvalidBounceEvent(
                'Bounce for non-existent list {}'.format(event.list_id))
        member = mlist.members.get_member(event.email)
        if member is None:
            event.processed = True
            # This needs an explicit commit because of the raise.
            config.db.commit()
            raise InvalidBounceEvent(
                'Email {} is not a subcriber of {}'.format(
                    event.email, mlist.list_id))

        # If this is a probe bounce, that we are sent before to check for this
        # Mailbox, we just disable the delivery for this member.
        if event.context == BounceContext.probe:
            log.info('Probe bounce received for member %s on list %s.',
                     event.email, mlist.list_id)
            event.processed = True
            self._disable_delivery(mlist, member, event)
            return

        # Looks like this is a regular bounce event, we need to process it
        # in the follow order:
        # 0. If the member is already disabled by bounce, we ignore this
        #    event.
        # 1. If the date of the bounce is same as the previous bounce, we
        #    ignore this event.
        # 2. Check if the bounce info is valid, bump the bounce_score and
        #    update the last bounce info.
        # 3. If the bounce info is stale, reset the bounce score with
        #    this new value.
        # 4. If the bounce_score is greater than threshold after the above,
        #    a) Send a VERP probe, if configured to do so
        #    b) Disable membership otherwise and notify the user and
        #       warnings.
        if member.preferences.delivery_status == DeliveryStatus.by_bounces:
            log.info('Residual bounce received for member %s on list %s.',
                     event.email, mlist.list_id)
            event.processed = True
            return
        if (member.last_bounce_received is not None and
                member.last_bounce_received.date() == event.timestamp.date()):
            # Update the timestamp of the last bounce received.
            member.last_bounce_received = event.timestamp
            event.processed = True
            log.info('Member %s already scored a bounce on list %s today.',
                     event.email, mlist.list_id)
            return

        if member.last_bounce_received is None or (
                member.last_bounce_received <
                event.timestamp - mlist.bounce_info_stale_after):
            # Reset the bounce score to 1, for the current bounce that we got.
            member.bounce_score = 1
        else:
            # Update the bounce score to reflect this bounce.
            member.bounce_score += 1
        # Update the last received time for the bounce.
        member.last_bounce_received = event.timestamp
        log.info('Member %s on list %s, bounce score = %d.', event.email,
                 mlist.list_id, member.bounce_score)
        # Now, we are done updating. Let's see if the threshold is reached and
        # disable based on that, but first if we're not going to disable we
        # need to send the bounce score incremented notice if requested.
        if mlist.bounce_notify_owner_on_bounce_increment and (
                member.bounce_score < mlist.bounce_score_threshold or
                not as_boolean(config.mta.verp_probes)):
            send_admin_increment_notice(
                mlist, event, display_name=member.display_name)
        if member.bounce_score >= mlist.bounce_score_threshold:
            # Save bounce_score because sending probe resets it.
            saved_bounce_score = member.bounce_score
            # Try to get the dsn from the message store.  It should be there.
            msg = getUtility(IMessageStore).get_message_by_id(event.message_id)
            if as_boolean(config.mta.verp_probes):
                send_probe(member, msg=msg, message_id=event.message_id)
                action = 'sending probe'
            else:
                # Bounce score is reset by sending a probe but needs to be
                # reset here too.  See #1061.
                member.bounce_score = 0
                self._disable_delivery(mlist, member, event)
                action = 'disabling delivery'
            log.info(
                'Member %s on list %s, bounce score %d >= threshold %d, %s.',
                event.email, mlist.list_id, saved_bounce_score,
                mlist.bounce_score_threshold, action)
        event.processed = True

    @transactional
    @dbconnection
    def send_warnings_and_remove(self, store):
        """Send a warning email to the users who are disabled, if needed.

        Also, if the max number of warnings have already been sent, remove the
        member.
        """
        # Query the database for all the Members, who have:

        # 1. Last warning was sent more than Mailinglist's disable notice
        #    warnings interval ago.
        # 2. Total warnings sent are less that Mailinglist's maximum number of
        #    warnings to be sent before the member is removed.
        self._send_warnings()
        self._remove_memberships()

    @dbconnection
    def _remove_memberships(self, store):
        """Remove all the memberships for whom max number of warnings have
        been sent.
        """
        manager = getUtility(IMembershipManager)
        for member in manager.memberships_pending_removal():
            # We do not want to send duplicate notifications to the
            # administrators, so we send them a membership change event only if
            # the list is configured not to notify them about removal due to
            # bounces.
            # Although, note that if admin_notif is None, they will receive
            # notification only if the mailinglist is configured to notify on
            # membership changes, which is a different setting..
            admin_notif = None
            send_admin_notif = False
            if member.mailing_list.bounce_notify_owner_on_removal:
                admin_notif = False
                send_admin_notif = True

            with transaction():
                delete_member(
                    mlist=member._mailing_list, email=member.address.email,
                    admin_notif=admin_notif, userack=True)

            if send_admin_notif:
                send_admin_removal_notice(
                    member.mailing_list, member.address.email,
                    member.display_name)

            log.info('Removed %s as a member of %s mailing list due to '
                     'excessive bounces', member.address.email,
                     member._mailing_list.display_name)

    @dbconnection
    def _send_warnings(self, store):
        """Send warnings to the user who have reached their bounce score
        threshold.

        We send warnings only to the members who have delivery disabled and
        haven't already got a warning in the last
        ``MalingLists.you_are_disabled_warnings_interval`` number of days.
        """
        manager = getUtility(IMembershipManager)
        for member in manager.memberships_pending_warning():
            log.debug('Sending membership disabled warning no. %s to %s due to'
                      ' excessive bounces on %s mailing list',
                      member.total_warnings_sent + 1,
                      member.address,
                      member.mailing_list.display_name)

            send_user_disable_warning(
                member.mailing_list, member.address.email,
                member.preferred_language)
            member.total_warnings_sent += 1
            member.last_warning_sent = now()
