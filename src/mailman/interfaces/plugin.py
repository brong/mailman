# Copyright (C) 2017-2025 by the Free Software Foundation, Inc.
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

"""Interfaces for plugins."""

from public import public
from zope.interface import Attribute, Interface


@public
class IPlugin(Interface):
    """A plugin providing components and hooks."""

    def pre_hook():
        """A plugin hook called in the first initialization step.

        This is called before the database is initialized.
        """

    def post_hook():
        """A plugin hook called in the second initialization step.

        This is called after the database is initialized.
        """

    resource = Attribute("""\
        The object for use as the root of this plugin's REST resources.

        This is the resource which will be hooked up to the REST API, and
        served at the /<api>/plugins/<plugin.name>/ location.  All parsing
        below that location is up to the plugin.

        This attribute should be None if the plugin doesn't provide a REST
        resource.

        The resource must support getattr() and dir().
        """)
