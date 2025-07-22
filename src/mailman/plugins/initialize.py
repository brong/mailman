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

"""Initialize the plugins."""

import logging

from lazr.config import as_boolean
from mailman.config import config
from mailman.interfaces.plugin import IPlugin
from mailman.utilities.modules import call_name
from public import public
from zope.interface import Invalid
from zope.interface.verify import verifyObject


log = logging.getLogger('mailman.plugins')


@public
def initialize():
    """Initialize all enabled plugins."""
    for name, plugin_config in config.plugin_configs:
        class_path = plugin_config['class'].strip()
        if not as_boolean(plugin_config['enabled']) or len(class_path) == 0:
            log.info('Plugin not enabled, or empty class path: {}'.format(
                name))
            continue
        if name in config.plugins:
            log.error('Duplicate plugin name: {}'.format(name))
            continue
        plugin = call_name(class_path)
        try:
            verifyObject(IPlugin, plugin)
        except Invalid:
            log.error('Plugin class does not implement IPlugin: {}'.format(
                class_path))
            continue
        plugin.name = name
        config.plugins[name] = plugin
