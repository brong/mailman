# Copyright (C) 2010-2025 by the Free Software Foundation, Inc.
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

"""Web service helpers."""

import json
import types
import falcon
import hashlib

from contextlib import suppress
from datetime import datetime, timedelta
from email.header import Header
from email.message import Message
from enum import Enum
from functools import partial
from lazr.config import as_boolean
from mailman.config import config
from pprint import pformat
from public import public


CONTENT_TYPE_JSON = 'application/json; charset=UTF-8'
CONTENT_TYPE_TEXT_PLAIN = 'text/plain'


class ExtendedEncoder(json.JSONEncoder):
    """An extended JSON encoder which knows about other data types."""

    def default(self, obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        elif isinstance(obj, timedelta):
            # as_timedelta() does not recognize microseconds, so convert these
            # to floating seconds, but only if there are any seconds.
            if obj.seconds > 0 or obj.microseconds > 0:
                seconds = obj.seconds + obj.microseconds / 1000000.0
                return '{}d{}s'.format(obj.days, seconds)
            return '{}d'.format(obj.days)
        elif isinstance(obj, Enum):
            # It's up to the decoding validator to associate this name with
            # the right Enum class.
            return obj.name
        elif isinstance(obj, bytes):
            return bytes_to_str(obj)
        elif isinstance(obj, Message):
            return obj.as_string()
        elif isinstance(obj, Header):
            return str(obj)
        return super().default(obj)


def bytes_to_str(value):
    # Convert a string to unicode when the encoding is not declared.
    if not isinstance(value, bytes):
        return value
    for encoding in ('ascii', 'utf-8', 'raw_unicode_escape'):
        with suppress(UnicodeDecodeError):
            return value.decode(encoding)


@public
def etag(resource):
    """Calculate the etag and return a JSON representation.

    The input is a dictionary representing the resource.  This
    dictionary must not contain an `http_etag` key.  This function
    calculates the etag by using the sha1 hexdigest of the
    pretty-printed (and thus key-sorted and predictable) representation
    of the dictionary.  It then inserts this value under the `http_etag`
    key, and returns the JSON representation of the modified dictionary.

    :param resource: The original resource representation.
    :type resource: dictionary
    :return: JSON representation of the modified dictionary.
    :rtype string
    """
    assert 'http_etag' not in resource, 'Resource already etagged'
    # Calculate the tag from a predictable (i.e. sorted) representation of the
    # dictionary.  The actual details aren't so important.  pformat() is
    # guaranteed to sort the keys, however it returns a str and the hash
    # library requires a bytes.  Use the safest possible encoding.
    hashfood = pformat(resource).encode('raw-unicode-escape')
    etag = hashlib.sha1(hashfood).hexdigest()
    resource['http_etag'] = '"{}"'.format(etag)
    return json.dumps(resource, cls=ExtendedEncoder,
                      sort_keys=as_boolean(config.devmode.enabled))


@public
class CollectionMixin:
    """Mixin class for common collection-ish things."""

    def _resource_as_dict(self, resource, fields=None):
        """Return the dictionary representation of a resource.

        This must be implemented by subclasses.

        :param resource: The resource object.
        :type resource: object
        :param fields: The resource fields which should be included.
        :type fields: List[str]
        :return: The representation of the resource.
        :rtype: dict
        """
        raise NotImplementedError

    def _resource_as_json(self, resource):
        """Return the JSON formatted representation of the resource."""
        resource = self._resource_as_dict(resource)
        assert resource is not None, resource
        return etag(resource)

    def _get_collection(self, request):
        """Return the collection as a sequence.

        The returned value must support the collections.abc.Sequence
        API.  This method must be implemented by subclasses.

        :param request: An http request.
        :return: The collection
        :rtype: collections.abc.Sequence
        """
        raise NotImplementedError

    def _paginate(self, request, collection):
        """Method to paginate through collection result lists.

        Use this to return only a slice of a collection, specified in
        the request itself.  The request should use query parameters
        `count` and `page` to specify the slice they want.  The slice
        will start at index ``(page - 1) * count`` and end (exclusive)
        at ``(page * count)``.
        """
        # Allow falcon's HTTPBadRequest exceptions to percolate up.  They'll
        # get turned into HTTP 400 errors.
        count = request.get_param_as_int('count')
        page = request.get_param_as_int('page')
        total_size = len(collection)
        if count is None and page is None:
            return 0, total_size, collection
        # TODO(maxking): Count and page should be positive integers. Once
        # falcon 2.0.0 is out and we can jump to it, we can remove this logic
        # and use `min_value` parameter in request.get_param_as_int.
        if count < 0:
            raise falcon.HTTPInvalidParam(
                count, 'count should be a positive integer.')
        if page < 1:
            raise falcon.HTTPInvalidParam(
                page, 'page should be greater than 0.')
        list_start = (page - 1) * count
        list_end = page * count
        return list_start, total_size, collection[list_start:list_end]

    def _make_collection(self, request, fields=None):
        """Provide the collection to the REST layer."""
        start, total_size, collection = self._paginate(
            request, self._get_collection(request))
        result = dict(start=start, total_size=total_size)
        if len(collection) != 0:
            # XXX(maxking): This is not the nicest way to use the mixin class,
            # but this is just meant to minimize the code changes since all the
            # resource endpoints do not support fields API. We pass on the
            # fields to underlying serialization method conditionally.
            as_dict = self._resource_as_dict
            if fields is not None:
                as_dict = partial(self._resource_as_dict, fields=fields)  # pragma: nocover  # noqa: E501
            entries = [as_dict(resource) for resource in collection]
            assert None not in entries, entries
            # Create the collection resource
            [etag(resource) for resource in entries]
            result['entries'] = entries
        return result


@public
class GetterSetter:
    """Get and set attributes on an object.

    Most attributes are fairly simple - a getattr() or setattr() on the object
    does the trick, with the appropriate encoding or decoding on the way in
    and out.  Encoding doesn't happen here though; the standard JSON library
    handles most types, but see ExtendedEncoder for additional support.

    Others are more complicated since they aren't kept in the model as direct
    columns in the database.  These will use subclasses of this base class.
    Read-only attributes will have a decoder which always raises ValueError.
    """

    def __init__(self, decoder=None):
        """Create a getter/setter for a specific attribute.

        :param decoder: The callable for decoding a web request value string
            into the specific data type needed by the object's attribute.  Use
            None to indicate a read-only attribute.  The callable should raise
            ValueError when the web request value cannot be converted.
        :type decoder: callable
        """
        self.decoder = decoder

    def get(self, obj, attribute):
        """Return the named object attribute value.

        :param obj: The object to access.
        :type obj: object
        :param attribute: The attribute name.
        :type attribute: string
        :return: The attribute value, ready for JSON encoding.
        :rtype: object
        """
        value = getattr(obj, attribute)
        # If the attribute is a generator type, return a list instead.
        if isinstance(value, types.GeneratorType):
            value = list(value)
        return value

    def put(self, obj, attribute, value):
        """Set the named object attribute value.

        :param obj: The object to change.
        :type obj: object
        :param attribute: The attribute name.
        :type attribute: string
        :param value: The new value for the attribute.
        """
        setattr(obj, attribute, value)

    def __call__(self, value):
        """Convert the value to its internal format.

        :param value: The web request value to convert.
        :type value: string
        :return: The converted value.
        :rtype: object
        """
        if self.decoder is None:
            return value
        if self.decoder is as_boolean and isinstance(value, bool):
            return value
        return self.decoder(value)


# Falcon REST framework add-ons.

@public
def child(matcher=None):
    def decorator(func):
        if matcher is None:
            func.__matcher__ = func.__name__
        else:
            func.__matcher__ = matcher
        return func
    return decorator


@public
class ChildError:
    def __init__(self, status):
        self._status = status

    def _oops(self, request, response):
        raise falcon.HTTPError(self._status, title=None)  # pragma: nocover

    on_get = _oops
    on_post = _oops
    on_put = _oops
    on_patch = _oops
    on_delete = _oops


@public
class BadRequest(ChildError):
    def __init__(self):
        super().__init__(falcon.HTTP_400)


@public
class NotFound(ChildError):
    def __init__(self):
        super().__init__(falcon.HTTP_404)


@public
def okay(response, body=None):
    response.status = falcon.HTTP_200
    if body is not None:
        response.text = body


@public
def no_content(response):
    response.status = falcon.HTTP_204


@public
def not_found(response, body='404 Not Found'):
    response.status = falcon.HTTP_404
    response.content_type = CONTENT_TYPE_JSON
    if isinstance(body, bytes):
        body = body.decode()
    if body is not None:
        response.text = falcon.HTTPNotFound(description=body).to_json()


@public
def accepted(response, body=None):
    response.status = falcon.HTTP_202
    if body is not None:
        response.text = body


@public
def bad_request(response, body='400 Bad Request'):
    response.status = falcon.HTTP_400
    response.content_type = CONTENT_TYPE_JSON
    if isinstance(body, bytes):
        body = body.decode()
    if body is not None:
        response.text = falcon.HTTPBadRequest(description=body).to_json()


@public
def created(response, location):
    response.status = falcon.HTTP_201
    response.location = location


@public
def conflict(response, body='409 Conflict'):
    response.status = falcon.HTTP_409
    response.content_type = CONTENT_TYPE_JSON
    if isinstance(body, bytes):
        body = body.decode()
    if body is not None:
        response.text = falcon.HTTPConflict(description=body).to_json()


@public
def forbidden(response, body='403 Forbidden'):
    response.status = falcon.HTTP_403
    response.content_type = CONTENT_TYPE_JSON
    if isinstance(body, bytes):
        body = body.decode()
    if body is not None:
        response.text = falcon.HTTPForbidden(description=body).to_json()


@public
def get_request_params(request):
    """Return the request items based on the content-type header"""
    # maxking: If there is a requirement for a new media type in future, The
    # way to handle that would be add a new media type handler in Falcon's API
    # class and then use the items returned by that handler here to return the
    # values to validators in the form of a dictionary.

    # We parse the request based on the content type. Falcon has a default
    # JSONHandler handler to parse json media type, so we can just do
    # `request.media` to return the request params passed as json body.
    if (request.content_type and
            request.content_type.startswith('application/json')):
        return request.media or dict()
    # request.params returns the parameters passed as URL form encoded.
    return request.params or dict()
