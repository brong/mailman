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

"""REST layer for plugins."""

import os

from contextlib import ExitStack
from importlib.resources import path
from mailman.testing.helpers import (
    hackenv,
    TestableMaster,
    wait_for_webservice,
)
from mailman.testing.layers import SMTPLayer
from public import public


# Don't inherit from RESTLayer since layers get run in bottom up order,
# meaning RESTLayer will get setUp() before this layer does, and that won't
# use the configuration file we need it to use.
@public
class PluginRESTLayer(SMTPLayer):
    @classmethod
    def setUp(cls):
        cls.resources = ExitStack()
        plugin_dir = str(cls.resources.enter_context(
            path('mailman.plugins', '__init__.py')))
        plugin_path = os.path.join(os.path.dirname(plugin_dir), 'testing')
        config_file = str(cls.resources.enter_context(
            path('mailman.plugins.testing', 'rest.cfg')))
        cls.resources.enter_context(
            hackenv('MAILMAN_CONFIG_FILE', config_file))
        cls.resources.enter_context(
            hackenv('PYTHONPATH', plugin_path))
        cls.server = TestableMaster(wait_for_webservice)
        cls.server.start('rest')
        cls.resources.callback(cls.server.stop)

    @classmethod
    def tearDown(cls):
        cls.resources.close()
