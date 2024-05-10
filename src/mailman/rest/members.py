# Copyright (C) 2010-2023 by the Free Software Foundation, Inc.
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

"""REST for members."""

import logging

from lazr.config import as_boolean
from mailman.app.membership import add_member
from mailman.interfaces.action import Action
from mailman.interfaces.address import IAddress, InvalidEmailAddressError
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.member import (
    AlreadySubscribedError,
    DeliveryMode,
    DeliveryStatus,
    MemberRole,
    MembershipError,
    MembershipIsBannedError,
    MissingPreferredAddressError,
)
from mailman.interfaces.subscriptions import (
    ISubscriptionManager,
    ISubscriptionService,
    RequestRecord,
    SubscriptionPendingError,
    TokenOwner,
)
from mailman.interfaces.user import IUser, UnverifiedAddressError
from mailman.interfaces.usermanager import IUserManager
from mailman.rest.helpers import (
    accepted,
    bad_request,
    child,
    CollectionMixin,
    conflict,
    created,
    etag,
    no_content,
    not_found,
    NotFound,
    okay,
)
from mailman.rest.preferences import Preferences, ReadOnlyPreferences
from mailman.rest.validator import (
    enum_validator,
    list_of_strings_validator,
    subscriber_validator,
    Validator,
)
from operator import attrgetter
from public import public
from uuid import UUID
from zope.component import getUtility


log = logging.getLogger('mailman.http')


class _MemberBase(CollectionMixin):
    """Shared base class for member representations."""

    @property
    def all_fields(self):
        """Get a mapping of all the supported fields for a Member resource.

        Keys are the name of the fields and values are callables that return
        values of those fields.
        """
        # The member will always have a member id and an address id.  It will
        # only have a user id if the address is linked to a user.
        # E.g. nonmembers we've only seen via postings to lists they are not
        # subscribed to will not have a user id.  The user_id and the
        # member_id are UUIDs.  In API 3.0 we use the integer equivalent of
        # the UID in the URL, but in API 3.1 we use the hex equivalent.  See
        # issue #121 for details.
        return {
            'address': self._get_address,
            'bounce_score': attrgetter('bounce_score'),
            'last_bounce_received': attrgetter('last_bounce_received'),
            'last_warning_sent': attrgetter('last_warning_sent'),
            'total_warnings_sent': attrgetter('total_warnings_sent'),
            'delivery_mode': attrgetter('delivery_mode'),
            'email': attrgetter('address.email'),
            'list_id': attrgetter('list_id'),
            'subscription_mode': attrgetter('subscription_mode'),
            'role': attrgetter('role.name'),
            'user': self._get_user,
            'moderation_action': attrgetter('moderation_action'),
            'display_name': attrgetter('display_name'),
            'self_link': self._get_self_link,
            'member_id': self._get_member_id,
            }

    def _get_member_id(self, member):
        """Get member_id."""
        return self.api.from_uuid(member.member_id)

    def _get_address(self, member):
        """Get url to member's addresses."""
        return self.api.path_to(
                'addresses/{}'.format(member.address.email))

    def _get_user(self, member):
        """Get url to member's user if one exists."""
        user = member.user
        if user:
            user_id = self.api.from_uuid(user.user_id)
            return self.api.path_to('users/{}'.format(user_id))
        return None

    def _get_self_link(self, member):
        """Get self_link to member resource."""
        member_id = self.api.from_uuid(member.member_id)
        return self.api.path_to('members/{}'.format(member_id))

    def _resource_as_dict(self, member, fields=None):
        """See `CollectionMixin`."""
        if fields is None:
            fields = self.all_fields.keys()

        response = {}
        for field in fields:
            value_getter = self.all_fields.get(field, None)
            if value_getter is None:
                raise ValueError(
                    'Unknown field "{}" for Member resource.'
                    ' Allowed fields are: {}'.format(
                        field, ', '.join(self.all_fields.keys())))
            value = value_getter(member)
            if value is not None:
                response[field] = value
        return response

    def _get_collection(self, request):
        """See `CollectionMixin`."""
        return list(getUtility(ISubscriptionService))

    def on_get(self, request, response):
        """/members"""
        validator = Validator(
            fields=list_of_strings_validator,
            count=int,
            page=int,
            _optional=['fields', 'count', 'page'],
            )
        try:
            data = validator(request)
        except ValueError as ex:
            bad_request(response, str(ex))
            return
        fields = data.get('fields', None)

        try:
            resource = self._make_collection(request, fields)
        except ValueError as ex:
            bad_request(response, str(ex))
            return
        okay(response, etag(resource))


@public
class MemberCollection(_MemberBase):
    """Abstract class for supporting submemberships.

    This is used for example to return a resource representing all the
    memberships of a mailing list, or all memberships for a specific email
    address.
    """
    def _get_collection(self, request):
        """See `CollectionMixin`."""
        raise NotImplementedError


@public
class AMember(_MemberBase):
    """A member."""

    def __init__(self, member_id):
        # The member_id is either the member's UUID or the string
        # representation of the member's UUID.
        service = getUtility(ISubscriptionService)
        self._member_id = member_id
        self._member = (None if member_id is None
                        else service.get_member(member_id))

    def on_get(self, request, response):
        """Return a single member end-point."""
        if self._member is None:
            not_found(response)
        else:
            okay(response, self._resource_as_json(self._member))

    @child()
    def preferences(self, context, segments):
        """/members/<id>/preferences"""
        if len(segments) != 0:
            return NotFound(), []
        if self._member is None:
            return NotFound(), []
        member_id = self.api.from_uuid(self._member_id)
        child = Preferences(
            self._member.preferences, 'members/{}'.format(member_id))
        return child, []

    @child()
    def all(self, context, segments):
        """/members/<id>/all/preferences"""
        if len(segments) == 0:
            return NotFound(), []
        if self._member is None:
            return NotFound(), []
        member_id = self.api.from_uuid(self._member_id)
        child = ReadOnlyPreferences(
            self._member, 'members/{}/all'.format(member_id))
        return child, []

    def on_delete(self, request, response):
        """Delete the member (i.e. unsubscribe)."""
        # Leaving a list is a bit different than deleting a moderator or
        # owner.  Handle the former case first.  For now too, we will not send
        # an admin or user notification.
        if self._member is None:
            not_found(response)
            return
        mlist = getUtility(IListManager).get_by_list_id(self._member.list_id)
        if self._member.role is MemberRole.member:
            try:
                values = Validator(
                    pre_confirmed=as_boolean,
                    pre_approved=as_boolean,
                    _optional=('pre_confirmed', 'pre_approved'),
                    )(request)
            except ValueError as error:
                bad_request(response, str(error))
                return
            manager = ISubscriptionManager(mlist)
            # XXX(maxking): For backwards compat, we are going to keep
            # pre-confirmed to be "True" by defualt instead of "False", that it
            # should be. Any, un-authenticated requests should manually specify
            # that it is *not* confirmed by the user.
            if 'pre_confirmed' in values:
                pre_confirmed = values.get('pre_confirmed')
            else:
                pre_confirmed = True
            token, token_owner, member = manager.unregister(
                self._member.address,
                pre_approved=values.get('pre_approved'),
                pre_confirmed=pre_confirmed)
            if member is None:
                assert token is None
                assert token_owner is TokenOwner.no_one
                no_content(response)
            else:
                assert token is not None
                content = dict(token=token, token_owner=token_owner.name)
                accepted(response, etag(content))
        else:
            self._member.unsubscribe()
            no_content(response)

    def on_patch(self, request, response):
        """Patch the membership.

        This is how subscription changes are done.
        """
        if self._member is None:
            not_found(response)
            return
        try:
            values = Validator(
                address=str,
                delivery_mode=enum_validator(DeliveryMode),
                moderation_action=enum_validator(Action, allow_blank=True),
                _optional=('address', 'delivery_mode', 'moderation_action'),
                )(request)
        except ValueError as error:
            bad_request(response, str(error))
            return
        if 'address' in values:
            email = values['address']
            address = getUtility(IUserManager).get_address(email)
            if address is None:
                bad_request(response, b'Address not registered')
                return
            try:
                self._member.address = address
            except (MembershipError, UnverifiedAddressError) as error:
                bad_request(response, str(error))
                return
        if 'delivery_mode' in values:
            self._member.preferences.delivery_mode = values['delivery_mode']
        if 'moderation_action' in values:
            self._member.moderation_action = values['moderation_action']
        no_content(response)


@public
class AllMembers(_MemberBase):
    """The members."""

    def on_post(self, request, response):
        """Create a new member."""
        try:
            validator = Validator(
                list_id=str,
                subscriber=subscriber_validator(self.api),
                display_name=str,
                delivery_mode=enum_validator(DeliveryMode),
                delivery_status=enum_validator(DeliveryStatus),
                role=enum_validator(MemberRole),
                pre_verified=as_boolean,
                pre_confirmed=as_boolean,
                pre_approved=as_boolean,
                invitation=as_boolean,
                send_welcome_message=as_boolean,
                _optional=('delivery_mode', 'display_name', 'role',
                           'pre_verified', 'pre_confirmed', 'pre_approved',
                           'invitation', 'send_welcome_message',
                           'delivery_status'))
            arguments = validator(request)
        except ValueError as error:
            bad_request(response, str(error))
            return
        # Dig the mailing list out of the arguments.
        list_id = arguments.pop('list_id')
        mlist = getUtility(IListManager).get_by_list_id(list_id)
        if mlist is None:
            bad_request(response, b'No such list')
            return
        # Figure out what kind of subscriber is being registered.  Either it's
        # a user via their preferred email address or it's an explicit address.
        # If it's a UUID, then it must be associated with an existing user.
        subscriber = arguments.pop('subscriber')
        user_manager = getUtility(IUserManager)
        # We use the display name if there is one.
        display_name = arguments.pop('display_name', '')
        if isinstance(subscriber, UUID):
            user = user_manager.get_user_by_id(subscriber)
            if user is None:
                bad_request(response, b'No such user')
                return
            subscriber = user
        else:
            # This must be an email address.  See if there's an existing
            # address object associated with this email.
            address = user_manager.get_address(subscriber)
            if address is None:
                # Create a new address, which of course will not be validated.
                address = user_manager.create_address(
                    subscriber, display_name)
            subscriber = address
        # What role are we subscribing?  Regular members go through the
        # subscription policy workflow while owners, moderators, and
        # nonmembers go through the legacy API for now.
        role = arguments.pop('role', MemberRole.member)
        if role is MemberRole.member:
            # Get the pre_ flags for the subscription workflow.
            pre_verified = arguments.pop('pre_verified', False)
            pre_confirmed = arguments.pop('pre_confirmed', False)
            pre_approved = arguments.pop('pre_approved', False)
            invitation = arguments.pop('invitation', False)
            send_welcome_message = arguments.pop('send_welcome_message', None)
            delivery_status = arguments.pop('delivery_status', None)
            delivery_mode = arguments.pop('delivery_mode', None)
            # Now we can run the registration process until either the
            # subscriber is subscribed, or the workflow is paused for
            # verification, confirmation, or approval.
            registrar = ISubscriptionManager(mlist)
            try:
                token, token_owner, member = registrar.register(
                    subscriber,
                    pre_verified=pre_verified,
                    pre_confirmed=pre_confirmed,
                    pre_approved=pre_approved,
                    invitation=invitation,
                    send_welcome_message=send_welcome_message,
                    delivery_mode=delivery_mode,
                    delivery_status=delivery_status)
            except AlreadySubscribedError:
                conflict(response, b'Member already subscribed')
                return
            except MissingPreferredAddressError:
                bad_request(response, b'User has no preferred address')
                return
            except MembershipIsBannedError:
                bad_request(response, b'Membership is banned')
                return
            except InvalidEmailAddressError:
                bad_request(response, b'List posting address cannot subscribe')
                return
            except SubscriptionPendingError:
                conflict(response, b'Subscription request already pending')
                return
            except Exception as e:
                log.exception(e)
                bad_request(response, str(e))
                return
            if token is None:
                assert token_owner is TokenOwner.no_one, token_owner
                # The subscription completed.  Let's get the resulting member
                # and return the location to the new member.  Member ids are
                # UUIDs and need to be converted to URLs because JSON doesn't
                # directly support UUIDs.
                member_id = self.api.from_uuid(member.member_id)
                location = self.api.path_to('members/{}'.format(member_id))
                created(response, location)
                return
            # The member could not be directly subscribed because there are
            # some out-of-band steps that need to be completed.  E.g. the user
            # must confirm their subscription or the moderator must approve
            # it.  In this case, an HTTP 202 Accepted is exactly the code that
            # we should use, and we'll return both the confirmation token and
            # the "token owner" so the client knows who should confirm it.
            assert token is not None, token
            assert token_owner is not TokenOwner.no_one, token_owner
            assert member is None, member
            content = dict(token=token, token_owner=token_owner.name)
            accepted(response, etag(content))
            return
        # 2015-04-15 BAW: We're subscribing some role other than a regular
        # member.  Use the legacy API for this for now.
        assert role in (MemberRole.owner,
                        MemberRole.moderator,
                        MemberRole.nonmember)
        # 2015-04-15 BAW: We're limited to using an email address with this
        # legacy API, so if the subscriber is a user, the user must have a
        # preferred address, which we'll use, even though it will subscribe
        # the explicit address.  It is an error if the user does not have a
        # preferred address.
        #
        # If the subscriber is an address object, just use that.
        if IUser.providedBy(subscriber):
            if subscriber.preferred_address is None:
                bad_request(response, b'User without preferred address')
                return
            email = subscriber.preferred_address.email
        else:
            assert IAddress.providedBy(subscriber)
            email = subscriber.email
        delivery_mode = arguments.pop('delivery_mode', DeliveryMode.regular)
        delivery_status = arguments.pop('delivery_status', None)
        record = RequestRecord(
            email, display_name, delivery_mode,
            delivery_status=delivery_status)
        try:
            member = add_member(mlist, record, role)
        except MembershipIsBannedError:
            bad_request(response, b'Membership is banned')
            return
        except AlreadySubscribedError:
            bad_request(response,
                        '{} is already an {} of {}'.format(
                            email, role.name, mlist.fqdn_listname))
            return
        except InvalidEmailAddressError:
            bad_request(response, b'List posting address cannot be added')
            return
        # The subscription completed.  Let's get the resulting member
        # and return the location to the new member.  Member ids are
        # UUIDs and need to be converted to URLs because JSON doesn't
        # directly support UUIDs.
        member_id = self.api.from_uuid(member.member_id)
        location = self.api.path_to('members/{}'.format(member_id))
        created(response, location)


class _FoundMembers(MemberCollection):
    """The found members collection."""

    def __init__(self, members, api):
        super().__init__()
        self._members = members
        self.api = api

    def _get_collection(self, request):
        """See `CollectionMixin`."""
        return self._members


@public
class FindMembers(_MemberBase):
    """/members/find"""

    def on_get(self, request, response):
        return self._find(request, response)

    def on_post(self, request, response):
        return self._find(request, response)

    def _find(self, request, response):
        """Find a member"""
        service = getUtility(ISubscriptionService)
        validator = Validator(
            list_id=str,
            subscriber=str,
            role=enum_validator(MemberRole),
            moderation_action=enum_validator(Action, allow_blank=True),
            # preferences.
            delivery_mode=enum_validator(DeliveryMode),
            delivery_status=enum_validator(DeliveryStatus),
            # Allow pagination.
            page=int,
            count=int,
            fields=list_of_strings_validator,
            _optional=(
                'list_id', 'subscriber', 'role', 'moderation_action',
                'delivery_mode', 'delivery_status',
                'page', 'count', 'fields'))
        try:
            data = validator(request)
        except ValueError as error:
            bad_request(response, str(error))
        else:
            # Remove any optional pagination query elements; they will be
            # handled later.
            data.pop('page', None)
            data.pop('count', None)
            fields = data.pop('fields', None)
            members = service.find_members(**data)
            resource = _FoundMembers(members, self.api)
            try:
                collection = resource._make_collection(request, fields)
            except ValueError as ex:
                bad_request(response, str(ex))
                return
            okay(response, etag(collection))
