# Copyright (C) 2007-2023 by the Free Software Foundation, Inc.
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

"""Model for members."""

from datetime import datetime
from mailman.core.constants import system_preferences
from mailman.database.model import Model
from mailman.database.transaction import dbconnection
from mailman.database.types import Enum, SAUnicode, UUID
from mailman.interfaces.action import Action
from mailman.interfaces.address import IAddress
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.member import (
    DeliveryStatus,
    IMember,
    IMembershipManager,
    MemberRole,
    MembershipError,
    SubscriptionMode,
    UnsubscriptionEvent,
)
from mailman.interfaces.user import IUser, UnverifiedAddressError
from mailman.interfaces.usermanager import IUserManager
from mailman.utilities.datetime import now
from mailman.utilities.uid import UIDFactory
from public import public
from sqlalchemy import and_, Column, DateTime, ForeignKey, Integer
from sqlalchemy.orm import relationship
from zope.component import getUtility
from zope.event import notify
from zope.interface import implementer


uid_factory = UIDFactory(context='members')


@public
@implementer(IMember)
class Member(Model):
    """See `IMember`."""

    __tablename__ = 'member'

    id = Column(Integer, primary_key=True)
    _member_id = Column(UUID, index=True)
    role = Column(Enum(MemberRole), index=True)
    list_id = Column(SAUnicode, index=True)

    moderation_action = Column(Enum(Action))

    address_id = Column(Integer, ForeignKey('address.id'), index=True)
    _address = relationship('Address')
    preferences_id = Column(Integer, ForeignKey('preferences.id'), index=True)
    preferences = relationship('Preferences')
    user_id = Column(Integer, ForeignKey('user.id'), index=True)
    _user = relationship('User')

    bounce_score = Column(Integer, default=0, index=True)
    last_bounce_received = Column(DateTime)
    last_warning_sent = Column(DateTime, default=datetime.min, index=True)
    total_warnings_sent = Column(Integer, default=0, index=True)

    _mailing_list = None

    def __init__(self, role, list_id, subscriber):
        self._member_id = uid_factory.new()
        self.role = role
        self.list_id = list_id
        if IAddress.providedBy(subscriber):
            self._address = subscriber
            # Look this up dynamically.
            self._user = None
        elif IUser.providedBy(subscriber):
            self._user = subscriber
            # Look this up dynamically.
            self._address = None
        else:
            raise ValueError('subscriber must be a user or address')
        if role in (MemberRole.owner, MemberRole.moderator):
            self.moderation_action = Action.accept
        else:
            assert role in (MemberRole.member, MemberRole.nonmember), (
                'Invalid MemberRole: {}'.format(role))
            self.moderation_action = None

    def __repr__(self):
        return '<Member: {} on {} as {}>'.format(
            self.address, self.mailing_list.fqdn_listname, self.role)

    @property
    def mailing_list(self):
        """See `IMember`."""
        if self._mailing_list is None:
            self._mailing_list = getUtility(
                IListManager).get_by_list_id(self.list_id)
        return self._mailing_list

    @property
    def member_id(self):
        """See `IMember`."""
        return self._member_id

    @property
    def address(self):
        """See `IMember`."""
        return (self._user.preferred_address
                if self._address is None
                else self._address)

    @property
    def subscription_mode(self):
        """See `IMember`"""
        return (SubscriptionMode.as_address
                if self._address
                else SubscriptionMode.as_user)

    @address.setter
    def address(self, new_address):
        """See `IMember`."""
        if self._address is None:
            # XXX Either we need a better exception here, or we should allow
            # changing a subscription from preferred address to explicit
            # address (and vice versa via del'ing the .address attribute.
            raise MembershipError('Membership is via preferred address')
        if new_address.verified_on is None:
            # A member cannot change their subscription address to an
            # unverified address.
            raise UnverifiedAddressError('Unverified address')
        user = getUtility(IUserManager).get_user(new_address.email)
        if user is None or user != self.user:
            raise MembershipError('Address is not controlled by user')
        self._address = new_address

    @property
    def user(self):
        """See `IMember`."""
        return (self._user
                if self._address is None
                else self._address.user)

    @property
    def subscriber(self):
        return (self._user if self._address is None else self._address)

    @property
    def display_name(self):
        # Try to find a non-empty display name.  We first look at the directly
        # subscribed record, which will either be the address or the user.
        # That's handled automatically by going through member.subscriber.  If
        # that doesn't give us something useful, try whatever user is linked
        # to the subscriber.
        if self.subscriber.display_name:
            return self.subscriber.display_name
        # If an unlinked address is subscribed there will be no .user.
        elif self.user is not None and self.user.display_name:
            return self.user.display_name
        else:
            return ''

    def _lookup(self, preference, default=None):
        pref = getattr(self.preferences, preference)
        if pref is not None:
            return pref
        pref = getattr(self.address.preferences, preference)
        if pref is not None:
            return pref
        if self.address.user:
            pref = getattr(self.address.user.preferences, preference)
            if pref is not None:
                return pref
        if default is None:
            return getattr(system_preferences, preference)
        return default

    @property
    def acknowledge_posts(self):
        """See `IMember`."""
        return self._lookup('acknowledge_posts')

    @property
    def preferred_language(self):
        """See `IMember`."""
        missing = object()
        language = self._lookup('preferred_language', missing)
        return (self.mailing_list.preferred_language
                if language is missing
                else language)

    @property
    def receive_list_copy(self):
        """See `IMember`."""
        return self._lookup('receive_list_copy')

    @property
    def receive_own_postings(self):
        """See `IMember`."""
        return self._lookup('receive_own_postings')

    @property
    def delivery_mode(self):
        """See `IMember`."""
        return self._lookup('delivery_mode')

    @property
    def delivery_status(self):
        """See `IMember`."""
        return self._lookup('delivery_status')

    @dbconnection
    def unsubscribe(self, store):
        """See `IMember`."""
        # Yes, this must get triggered before self is deleted.
        notify(UnsubscriptionEvent(self.mailing_list, self))
        store.delete(self.preferences)
        store.delete(self)


@public
@implementer(IMembershipManager)
class MembershipManager:
    """See `IMembershipManager`."""

    @dbconnection
    def memberships_pending_warning(self, store):
        """See `IMembershipManager`."""
        from mailman.model.mailinglist import MailingList
        from mailman.model.preferences import Preferences

        # maxking: We don't care so much about the bounce score here since it
        # could have been reset due to bounce info getting stale. We will send
        # warnings to people who have been disabled already, regardless of
        # their bounce score. Same is true below for removal.
        query = store.query(
            Member,
            MailingList.bounce_you_are_disabled_warnings_interval).join(
            MailingList, Member.list_id == MailingList._list_id).join(
            Member.preferences).filter(and_(
                Member.role == MemberRole.member,
                MailingList.process_bounces == True,       # noqa: E712
                Member.total_warnings_sent < MailingList.bounce_you_are_disabled_warnings,  # noqa: E501
                Preferences.delivery_status == DeliveryStatus.by_bounces))

        # XXX(maxking): This is IMO a query that *should* work, but I haven't
        # been able to get it to work in my tests. It could be due to lack of
        # native datetime type in SQLite, which we use for tests. Hence, we are
        # going to do some filtering in Python, which is inefficient, but
        # works. We do filter as much as possible in SQL, so hopefully the
        # output of the above query won't be *too* many.
        # TODO: Port the Python loop below to SQLAlchemy.

        # (func.DATETIME(Member.last_warning_sent) +
        # func.DATETIME(MailingList.bounce_you_are_disabled_warnings_interval))
        # < func.DATETIME(now())))

        for member, interval in query.all():
            if (member.last_warning_sent + interval) <= now():
                yield member

    @dbconnection
    def memberships_pending_removal(self, store):
        """See `IMembershipManager`."""
        from mailman.model.mailinglist import MailingList
        from mailman.model.preferences import Preferences

        query = store.query(
            Member,
            MailingList.bounce_you_are_disabled_warnings_interval,
            MailingList.bounce_you_are_disabled_warnings).join(
            MailingList, Member.list_id == MailingList._list_id).join(
            Member.preferences).filter(and_(
                Member.role == MemberRole.member,
                MailingList.process_bounces == True,    # noqa: E712
                Member.total_warnings_sent >= MailingList.bounce_you_are_disabled_warnings,     # noqa: E501
                Preferences.delivery_status == DeliveryStatus.by_bounces))

        for member, interval, warnings in query.all():
            if (member.last_warning_sent + interval) <= now() or warnings == 0:
                yield member
