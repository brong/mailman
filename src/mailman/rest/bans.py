# Copyright (C) 2016-2025 by the Free Software Foundation, Inc.
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

"""REST for banned emails."""

from mailman.interfaces.bans import IBanManager
from mailman.rest.helpers import (
    bad_request,
    child,
    CollectionMixin,
    created,
    etag,
    no_content,
    not_found,
    okay,
)
from mailman.rest.validator import email_or_regexp_validator, Validator
from public import public
from urllib.parse import quote


class _BannedBase:
    """Common base class."""

    def __init__(self, mlist):
        self._mlist = mlist
        self.ban_manager = IBanManager(self._mlist)

    def _location(self, email):
        if self._mlist is None:
            base_location = ''
        else:
            base_location = 'lists/{}/'.format(self._mlist.list_id)
        return self.api.path_to(
            f"{base_location}bans/{quote(email, '@')}",
        )


@public
class BannedEmail(_BannedBase):
    """A banned email."""

    def __init__(self, mlist, email):
        super().__init__(mlist)
        self._email = email

    def on_get(self, request, response):
        """Get a banned email."""
        if self.ban_manager.is_banned(self._email):
            resource = dict(
                email=self._email,
                self_link=self._location(self._email),
                )
            if self._mlist is not None:
                resource['list_id'] = self._mlist.list_id
            okay(response, etag(resource))
        else:
            not_found(response, 'Email is not banned: {}'.format(self._email))

    def on_delete(self, request, response):
        """Remove an email from the ban list."""
        if self.ban_manager.is_banned(self._email):
            self.ban_manager.unban(self._email)
            no_content(response)
        else:
            not_found(response, 'Email is not banned: {}'.format(self._email))


@public
class BannedEmails(_BannedBase, CollectionMixin):
    """The list of all banned emails."""

    def _resource_as_dict(self, ban):
        """See `CollectionMixin`."""
        resource = dict(
            email=ban.email,
            self_link=self._location(ban.email),
            )
        if ban.list_id is not None:
            resource['list_id'] = ban.list_id
        return resource

    def _get_collection(self, request):
        """See `CollectionMixin`."""
        return self.ban_manager.bans

    def on_get(self, request, response):
        """/bans"""
        resource = self._make_collection(request)
        okay(response, etag(resource))

    def on_post(self, request, response):
        """Ban some email from subscribing."""
        validator = Validator(email=email_or_regexp_validator)
        try:
            email = validator(request)['email']
        except ValueError as error:
            bad_request(response, str(error))
            return

        if self.ban_manager.is_banned(email):
            bad_request(response, b'Address is already banned')
        else:
            self.ban_manager.ban(email)
            created(response, self._location(email))

    # HACK: Support email addresses with slash (`/`) in its local part.
    # WSGI does NOT allow `%2F` (URI-encoded `/`) in path components.
    # https://falcon.readthedocs.io/en/stable/user/faq.html#why-is-my-url-with-percent-encoded-forward-slashes-2f-routed-incorrectly
    @child(r'^(?P<email>.+)')
    def email(self, context, segments, **kw):
        return BannedEmail(self._mlist, kw['email']), []
