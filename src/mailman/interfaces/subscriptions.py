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

"""Subscription management."""

from collections import namedtuple
from enum import Enum
from mailman.interfaces.errors import MailmanError
from mailman.interfaces.member import DeliveryMode, MembershipError
from public import public
from zope.interface import Interface


@public
class MissingUserError(MailmanError):
    """An invalid user id was given."""

    def __init__(self, user_id):
        super().__init__()
        self.user_id = user_id

    def __str__(self):
        return self.user_id


@public
class SubscriptionPendingError(MailmanError):
    def __init__(self, mlist, email):
        super().__init__()
        self.mlist = mlist
        self.email = email


@public
class TooManyMembersError(MembershipError):
    def __init__(self, subscriber, list_id, role):
        super().__init__()
        self.subscriber = subscriber
        self.list_id = list_id
        self.role = role


_RequestRecord = namedtuple(
    'RequestRecord',
    'email display_name delivery_mode language delivery_status')


@public
def RequestRecord(email, display_name='',
                  delivery_mode=DeliveryMode.regular,
                  language=None, delivery_status=None):
    if language is None:
        from mailman.core.constants import system_preferences
        language = system_preferences.preferred_language
    return _RequestRecord(
        email, display_name, delivery_mode, language, delivery_status)


@public
class TokenOwner(Enum):
    """Who 'owns' the token returned from the registrar?"""
    no_one = 0
    subscriber = 1
    moderator = 2


@public
class SubscriptionConfirmationNeededEvent:
    """Triggered when a subscription needs confirmation.

    Addresses must be verified before they can receive messages or post
    to mailing list.  The confirmation message is sent to the user when
    this event is triggered.
    """
    def __init__(self, mlist, token, email):
        self.mlist = mlist
        self.token = token
        self.email = email


@public
class SubscriptionInvitationNeededEvent:
    """Triggered when a subscription invitation needs confirmation.

    Invitations must be accepted before the subscriber is subscribed.  The
    invitation message is sent to the user when this event is triggered.
    """
    def __init__(self, mlist, token, email):
        self.mlist = mlist
        self.token = token
        self.email = email


@public
class UnsubscriptionConfirmationNeededEvent:
    """Triggered when an unsubscription request needs confirmation.

    The confirmation message is sent to the user when this event is
    triggered.
    """
    def __init__(self, mlist, token, email):
        self.mlist = mlist
        self.token = token
        self.email = email


@public
class ISubscriptionService(Interface):
    """General subscription services."""

    def get_members():
        """Return a sequence of all members of all mailing lists.

        The members are sorted first by fully-qualified mailing list name,
        then by subscribed email address, then by role.  Because the user may
        be a member of the list under multiple roles (e.g. as an owner and as
        a digest member), the member can appear multiple times in this list.
        Roles are sorted by: owner, moderator, member.

        :return: The list of all members.
        :rtype: list of `IMember`
        """

    def get_member(member_id):
        """Return a member record matching the member id.

        :param member_id: A member id.
        :type member_id: int
        :return: The matching member, or None if no matching member is found.
        :rtype: `IMember`
        """

    def find_members(subscriber=None, list_id=None, role=None):
        """Search for members matching some criteria.

        The members are sorted first by list-id, then by subscribed
        email address, then by role.  Because the user may be a member
        of the list under multiple roles (e.g. as an owner and as a
        digest member), the member can appear multiple times in this
        list.

        :param subscriber: The email address or user id of the user getting
            subscribed.  This argument may contain asterisks, which will be
            interpreted as wildcards in the search pattern.
        :type subscriber: string or int
        :param list_id: The list id of the mailing list to search for the
            subscriber's memberships on.
        :type list_id: string
        :param role: The member role.
        :type role: `MemberRole`
        :return: A sequence of all memberships, which may be empty.
        :rtype: A `QuerySequence` of `IMember`
        """

    def find_member(subscriber=None, list_id=None, role=None):
        """Search for a member matching some criteria.

        This is like find_members() but is guaranteed to return exactly
        one member.

        :param subscriber: The email address or user id of the user getting
            subscribed.
        :type subscriber: string or int
        :param list_id: The list id of the mailing list to search for the
            subscriber's memberships on.
        :type list_id: string
        :param role: The member role.
        :type role: `MemberRole`
        :return: The member matching the given criteria or None if no
            members match the criteria.
        :rtype: `IMember` or None
        :raises TooManyMembersError: when the given criteria matches
            more than one membership.
        """

    def __iter__():
        """See `get_members()`."""

    def leave(list_id, email):
        """Unsubscribe from a mailing list.

        :param list_id: The list id of the mailing list the user is
            unsubscribing from.
        :type list_id: string
        :param email: The email address of the user getting unsubscribed.
        :type email: string
        :raises InvalidEmailAddressError: if the email address is not valid.
        :raises NoSuchListError: if the named mailing list does not exist.
        :raises NotAMemberError: if the given address is not a member of the
            mailing list.
        """

    def unsubscribe_members(list_id, emails):
        """Unsubscribe a batch of members from a mailing list.

        :param list_id: The list id to operate on.
        :type list_id: string
        :param emails: A list of email addresses of the members getting
            unsubscribed.  Only list members with a role of `member` can be
            unsubscribed via this interface.
        :type emails: list of strings
        :return: A two item tuple whose first item is a set of all the
            successfully unsubscribed email addresses and second item is
            a set of all unsuccessful email addresses.
        :rtype: 2-tuple of (set-of-strings, set-of-strings)
        :raises NoSuchListError: if the named mailing list does not exist.
        """


@public
class ISubscriptionManager(Interface):
    """Handling subscription and unsubscription of addresses and users.

    This is a higher level interface to user registration and
    unregistration, email address confirmation, etc. than the
    `IUserManager`.  The latter does no validation, syntax checking, or
    confirmation, while this interface does.

    To use this, adapt an ``IMailingList`` to this interface.
    """
    def register(subscriber=None, *,
                 pre_verified=False, pre_confirmed=False, pre_approved=False,
                 invitation=False, send_welcome_message=None,
                 delivery_mode=None, delivery_status=None):
        """Subscribe an address or user according to subscription policies.

        The mailing list's subscription policy is used to subscribe
        `subscriber` to the given mailing list.  The subscriber can be
        an ``IUser``, in which case the user must have a preferred
        address, and that preferred address will be subscribed.  The
        subscriber can also be an ``IAddress``, in which case the
        address will be subscribed.

        The workflow may pause (i.e. be serialized, saved, and
        suspended) when some out-of-band confirmation step is required.
        For example, if the user must confirm, or the moderator must
        approve the subscription.  Use the ``confirm(token)`` method to
        resume the workflow.

        :param subscriber: The user or address to subscribe.
        :type subscriber: ``IUser`` or ``IAddress``
        :param pre_verified: A flag indicating whether the subscriber's email
            address should be considered pre-verified.  Normally a never
            before seen email address must be verified by mail-back
            confirmation.  Setting this flag to True automatically verifies
            such addresses without the mail-back.  (A confirmation message may
            still be sent under other conditions.)
        :type pre_verified: bool
        :param pre_confirmed: A flag indicating whether, when required by the
            subscription policy, a subscription request should be considered
            pre-confirmed.  Normally in such cases, a mail-back confirmation
            message is sent to the subscriber, which must be positively
            acknowledged by some manner.  Setting this flag to True
            automatically confirms the subscription request.  (A confirmation
            message may still be sent under other conditions.)
        :type pre_confirmed: bool
        :param pre_approved: A flag indicating whether, when required by the
            subscription policy, a subscription request should be considered
            pre-approved.  Normally in such cases, the list administrator is
            notified that an approval is necessary, which must be positively
            acknowledged in some manner.  Setting this flag to True
            automatically approves the subscription request.
        :type pre_approved: bool
        :param invitation: A flag indicating whether or not this should result
            in an invitation to join the list rather than a normal subscription
            request.  Setting this flag to True overides pre_verified,
            pre_confirmed and pre_approved and sends an invitation message to
            the subscriber and holds the subscription.  The subscription is
            completed if and when the invitation is confirmed.
        :type invitation: bool
        :param send_welcome_message: A flag indicating whether the new member
            should receive a welcome message. This overrides the list's
            configuration of send_welcome_message if it is specified.
        :type send_welcome_message: bool
        :param delivery_mode: A ``DeliveryMode`` enum which if specified sets
            delivery_mode for the subscription.
        :type delivery_mode: ``DeliveryMode`` enum or None:
        :param delivery_status: A ``DeliveryStatus`` enum which if specified
            sets delivery_status for the subscription.
        :type delivery_status: ``DeliveryStatus`` enum or None:
        :return: A 3-tuple is returned where the first element is the token
            hash, the second element is a ``TokenOwner`, and the third element
            is the subscribed member.  If the subscriber got subscribed
            immediately, the token will be None and the member will be
            an ``IMember``.  If the subscription got held, the token
            will be a hash and the member will be None.
        :rtype: (str-or-None, ``TokenOwner``, ``IMember``-or-None)
        :raises MembershipIsBannedError: when the address being subscribed
            appears in the global or list-centric bans.
        :raises InvalidEmailAddressError: If the address being subscribed is
            the list's posting address.

        """

    def unregister(subscriber=None, *,
                   pre_confirmed=False, pre_approved=False):
        """Unsubscribe an address or user according to subscription policies.

        The mailing list's unsubscription policy is used to unsubscribe
        `subscriber` from the given mailing list.  The subscriber can be
        an ``IUser`` or an ``IAddress``, and must already be subscribed to the
        mailing list.

        The workflow may pause (i.e. be serialized, saved, and
        suspended) when some out-of-band confirmation step is required.
        For example, if the user must confirm, or the moderator must
        approve the unsubscription.  Use the ``confirm(token)`` method to
        resume the workflow.

        :param subscriber: The user or address to unsubscribe.
        :type subscriber: ``IUser`` or ``IAddress``
        :param pre_confirmed: A flag indicating whether, when required by the
            unsubscription policy, an unsubscription request should be
            considered pre-confirmed.  Normally in such cases, a mail-back
            confirmation message is sent to the subscriber, which must be
            positively acknowledged by some manner.  Setting this flag to True
            automatically confirms the unsubscription request.  (A confirmation
            message may still be sent under other conditions.)
        :type pre_confirmed: bool
        :param pre_approved: A flag indicating whether, when required by the
            unsubscription policy, an unsubscription request should be
            considered pre-approved.  Normally in such cases, the list
            administrator is notified that an approval is necessary, which
            must be positively acknowledged in some manner.  Setting this flag
            to True automatically approves the unsubscription request.
        :type pre_approved: bool
        :return: A 3-tuple is returned where the first element is the token
            hash, the second element is a ``TokenOwner`, and the third element
            is the unsubscribing member.  If the subscriber got unsubscribed
            immediately, the token will be None and the member will be
            an ``IMember``.  If the unsubscription got held, the token
            will be a hash and the member will be None.
        :rtype: (str-or-None, ``TokenOwner``, ``IMember``-or-None)
        """

    def confirm(token):
        """Continue any paused workflow.

        Confirmation may occur after the user confirms their
        subscription request, or their email address must be verified,
        or the moderator must approve the subscription request.

        :param token: A token matching a workflow.
        :type token: string
        :return: A 3-tuple is returned where the first element is the token
            hash, the second element is a ``TokenOwner`, and the third element
            is the subscribed member.  If the subscriber got subscribed
            immediately, the token will be None and the member will be
            an ``IMember``.  If the subscription is still being held, the token
            will be a hash and the member will be None.
        :rtype: (str-or-None, ``TokenOwner``, ``IMember``-or-None)
        :raises LookupError: when no workflow is associated with the token.
        """

    def discard(token):
        """Discard the workflow matched to the given `token`.

        :param token: A token matching a pending event with a type of
            'registration'.
        :raises LookupError: when no workflow is associated with the token.
        """

    def evict():
        """Evict all saved workflows which have expired."""
