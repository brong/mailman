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

"""REST for plugins, dynamically proxies requests to plugin's rest_object."""

from lazr.config import as_boolean
from mailman.config import config
from mailman.rest.helpers import CollectionMixin, etag, NotFound, okay
from operator import itemgetter
from public import public


@public
class AllPlugins(CollectionMixin):
    """Read-only list of all plugin configs."""

    def _resource_as_dict(self, plugin_config):
        """See `CollectionMixin`."""
        name, plugin_section = plugin_config
        resource = {
            'name': name,
            'class': plugin_section['class'],
            'enabled': as_boolean(plugin_section['enabled']),
            }
        # Add the path to the plugin's own configuration file, if one was
        # given.
        plugin_config = plugin_section['configuration'].strip()
        if len(plugin_config) > 0:
            resource['configuration'] = plugin_config
        return resource

    def _get_collection(self, request):
        """See `CollectionMixin`."""
        # plugin_configs returns a 2-tuple of (name, section), so sort
        # alphabetically on the plugin name.
        return sorted(config.plugin_configs, key=itemgetter(0))

    def on_get(self, request, response):
        """/plugins"""
        resource = self._make_collection(request)
        okay(response, etag(resource))


@public
class APlugin:
    """REST proxy to the plugin's rest_object."""

    def __init__(self, plugin_name):
        self._resource = None
        if plugin_name in config.plugins:
            plugin = config.plugins[plugin_name]
            self._resource = plugin.resource
        # If the plugin doesn't exist or doesn't provide a resource, just proxy
        # to NotFound.
        if self._resource is None:
            self._resource = NotFound()

    def __getattr__(self, attrib):
        return getattr(self._resource, attrib)

    def __dir__(self):
        return dir(self._resource)
