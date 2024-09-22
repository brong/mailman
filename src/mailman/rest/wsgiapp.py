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

"""Basic WSGI Application object for REST server."""

import re
import hmac
import logging

from base64 import b64decode
from falcon import App, HTTPUnauthorized
from falcon.routing import map_http_methods, set_default_responders
from mailman.config import config
from mailman.database.transaction import api_transaction
from mailman.rest.helpers import bad_request
from mailman.rest.root import Root
from public import public


log = logging.getLogger('mailman.http')

MISSING = object()
SLASH = '/'
EMPTYSTRING = ''
REALM = 'mailman3-rest'
UTF8 = 'utf-8'
WILDCARD_ACCEPT_HEADER = '*/*'


class Middleware:
    """Falcon middleware object for Mailman's REST API.

    This does two things.  It sets the API version on the resource
    object, and it verifies that the proper authentication has been
    performed.
    """
    def process_resource(self, request, response, resource, params):
        # Check the authorization credentials.
        authorized = False
        if request.auth is not None and request.auth.startswith('Basic '):
            # b64decode() returns bytes, but we require a str.
            credentials = b64decode(request.auth[6:]).decode('utf-8')
            username, password = credentials.split(':', 1)
            if (username == config.webservice.admin_user and
                    hmac.compare_digest(
                        password, config.webservice.admin_pass)):
                authorized = True
        if not authorized:
            # Not authorized.
            realm = 'Basic realm="{}",charset="{}"'.format(REALM, UTF8)
            raise HTTPUnauthorized(
                title='401 Unauthorized',
                description='REST API authorization failed',
                challenges=[realm])


def handle_ValueError(request, response, exc, params):
    """Handle ValueErrors in API code to return HTTPBadRequest.

    ValueErrors are raised often by Validator and should not return a 500 error
    response to the client.  This is a stop-gap for 500 errors due to
    ValueErrors, it is recommended that they be handled at the call-site,
    instead of here.
    """
    # Only handle ValueError, raise anything else right back.
    if not isinstance(exc, ValueError):
        raise exc
    bad_request(response, str(exc))


class ObjectRouter:
    def __init__(self, root):
        self._root = root

    def add_route(self, uri_template, method_map, resource):
        # We don't need this method for object-based routing.
        raise NotImplementedError

    def find(self, uri, req=None):
        segments = uri.split(SLASH)
        # Since the path is always rooted at /, skip the first segment, which
        # will always be the empty string.
        segments.pop(0)
        this_segment = segments.pop(0)
        resource = self._root
        context = {}
        while True:
            # Plumb the API through to all child resources.
            api = getattr(resource, 'api', None)
            # See if any of the resource's child links match the next segment.
            for name in dir(resource):
                if name.startswith('__') and name.endswith('__'):
                    continue
                attribute = getattr(resource, name, MISSING)
                assert attribute is not MISSING, name
                matcher = getattr(attribute, '__matcher__', MISSING)
                if matcher is MISSING:
                    continue
                result = None
                if isinstance(matcher, str):
                    # Is the matcher string a regular expression or plain
                    # string?  If it starts with a caret, it's a regexp.
                    if matcher.startswith('^'):
                        cre = re.compile(matcher)
                        # Search against the entire remaining path.
                        tmp_segments = segments[:]
                        tmp_segments.insert(0, this_segment)
                        remaining_path = SLASH.join(tmp_segments)
                        mo = cre.match(remaining_path)
                        if mo:
                            result = attribute(
                                context, segments, **mo.groupdict())
                    elif matcher == this_segment:
                        result = attribute(context, segments)
                else:
                    # The matcher is a callable.  It returns None if it
                    # doesn't match, and if it does, it returns a 3-tuple
                    # containing the positional arguments, the keyword
                    # arguments, and the remaining segments.  The attribute is
                    # then called with these arguments.  Note that the matcher
                    # wants to see the full remaining path components, which
                    # includes the current hop.
                    tmp_segments = segments[:]
                    tmp_segments.insert(0, this_segment)
                    matcher_result = matcher(tmp_segments)
                    if matcher_result is not None:
                        positional, keyword, segments = matcher_result
                        result = attribute(
                            context, segments, *positional, **keyword)
                # The attribute could return a 2-tuple giving the resource and
                # remaining path segments, or it could just return the result.
                # Of course, if the result is None, then the matcher did not
                # match.
                if result is None:
                    continue
                elif isinstance(result, tuple):
                    resource, segments = result
                else:
                    resource = result
                # See if the context set an API and set it on the next
                # resource in the chain, falling back to the parent resource's
                # API if there is one.
                resource.api = context.pop('api', api)
                # The method could have truncated the remaining segments,
                # meaning, it's consumed all the path segments, or this is the
                # last path segment.  In that case the resource we're left at
                # is the responder.
                if len(segments) == 0:
                    # We're at the end of the path, so the root must be the
                    # responder.
                    method_map = map_http_methods(resource)
                    set_default_responders(method_map)
                    return resource, method_map, context
                this_segment = segments.pop(0)
                break
            else:
                # None of the attributes matched this path component, so the
                # response is a 404.
                return None, None, None


class RootedAPI(App):
    def __init__(self, root, *args, **kws):
        super().__init__(
            *args,
            middleware=Middleware(),
            router=ObjectRouter(root),
            **kws)
        # Let Falcon parse the form data into the request object's
        # .params attribute.
        self.req_options.auto_parse_form_urlencoded = True
        # Don't ignore empty query parameters, e.g. preserve empty string
        # values, which some resources will interpret as a DELETE.
        self.req_options.keep_blank_qs_values = True

    # Override the base class implementation to wrap a transactional
    # handler around the call, so that the current transaction is
    # committed if no errors occur, and aborted otherwise.
    @api_transaction
    def __call__(self, environ, start_response):
        return super().__call__(environ, start_response)


@public
def make_application():
    """Return a callable WSGI application object."""
    app = RootedAPI(Root())
    app.add_error_handler(ValueError, handle_ValueError)
    return app
