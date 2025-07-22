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

"""Test some additional plugin stuff."""

import sys
import unittest

from contextlib import ExitStack
from mailman.core.initialize import run_plugin_pre_hook
from mailman.interfaces.plugin import IPlugin
from mailman.plugins.initialize import initialize
from mailman.plugins.testing.layer import PluginRESTLayer
from mailman.testing.helpers import call_api
from mailman.testing.layers import ConfigLayer
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch
from urllib.error import HTTPError
from zope.interface import implementer


class TestRESTPlugin(unittest.TestCase):
    layer = PluginRESTLayer

    def test_plugin_raises_exception(self):
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.1/plugins/example/no')
        self.assertEqual(cm.exception.code, 400)


@implementer(IPlugin)
class TestablePlugin:
    def pre_hook(self):
        pass

    def post_hook(self):
        pass

    resource = None


@implementer(IPlugin)
class ExceptionRaisingPlugin:
    def pre_hook(self):
        raise Exception('Bad plugin')

    def post_hook(self):
        pass
    resource = None


class TestInitializePlugins(unittest.TestCase):
    layer = ConfigLayer

    def test_duplicate_plugin_name(self):
        with ExitStack() as resources:
            system_path = resources.enter_context(TemporaryDirectory())
            fake_plugin_config = {
                'path': system_path,
                'enabled': 'yes',
                'class': 'ExamplePlugin',
                }
            log_mock = resources.enter_context(
                patch('mailman.plugins.initialize.log'))
            fake_mailman_config = SimpleNamespace(
                plugin_configs=[('example', fake_plugin_config)],
                plugins=['example'],
                )
            resources.enter_context(patch(
                'mailman.plugins.initialize.config', fake_mailman_config))
            initialize()
            log_mock.error.assert_called_once_with(
                'Duplicate plugin name: example')

    def test_does_not_implement(self):
        with ExitStack() as resources:
            system_path = resources.enter_context(TemporaryDirectory())
            fake_plugin_config = {
                'path': system_path,
                'enabled': 'yes',
                'class': 'ExamplePlugin',
                }
            log_mock = resources.enter_context(
                patch('mailman.plugins.initialize.log'))
            fake_mailman_config = SimpleNamespace(
                plugin_configs=[('example', fake_plugin_config)],
                plugins=[],
                )
            resources.enter_context(patch(
                'mailman.plugins.initialize.config', fake_mailman_config))
            resources.enter_context(patch(
                'mailman.plugins.initialize.call_name',
                # object() does not implement IPlugin.
                return_value=object()))
            initialize()
            log_mock.error.assert_called_once_with(
                'Plugin class does not implement IPlugin: ExamplePlugin')
            self.assertNotIn(system_path, sys.path)

    def test_adds_plugins_to_config(self):
        with ExitStack() as resources:
            system_path = resources.enter_context(TemporaryDirectory())
            fake_plugin_config = {
                'path': system_path,
                'enabled': 'yes',
                'class': 'ExamplePlugin',
                }
            fake_mailman_config = SimpleNamespace(
                plugin_configs=[('example', fake_plugin_config)],
                plugins={},
                )
            resources.enter_context(patch(
                'mailman.plugins.initialize.config', fake_mailman_config))
            testable_plugin = TestablePlugin()
            resources.enter_context(patch(
                'mailman.plugins.initialize.call_name',
                # object() does not implement IPlugin.
                return_value=testable_plugin))
            initialize()
            self.assertIn('example', fake_mailman_config.plugins)
            self.assertEqual(
                fake_mailman_config.plugins['example'],
                testable_plugin)

    def test_not_enabled(self):
        with ExitStack() as resources:
            fake_plugin_config = {
                'path': '/does/not/exist',
                'enabled': 'no',
                'class': 'ExamplePlugin',
                }
            log_mock = resources.enter_context(
                patch('mailman.plugins.initialize.log'))
            fake_mailman_config = SimpleNamespace(
                plugin_configs=[('example', fake_plugin_config)],
                plugins={},
                )
            resources.enter_context(patch(
                'mailman.plugins.initialize.config', fake_mailman_config))
            initialize()
            log_mock.info.assert_called_once_with(
                'Plugin not enabled, or empty class path: example')

    def test_plugin_disabled_with_exception(self):
        with ExitStack() as resources:
            system_path = resources.enter_context(TemporaryDirectory())
            fake_plugin_config = {
                'path': system_path,
                'enabled': 'yes',
                'class': 'ExamplePlugin',
                }
            testable_plugin = ExceptionRaisingPlugin()
            fake_mailman_config = SimpleNamespace(
                plugin_configs=[('example', fake_plugin_config)],
                plugins={'example': testable_plugin},
                )

            resources.enter_context(patch(
                'mailman.plugins.initialize.call_name',
                return_value=testable_plugin))
            run_plugin_pre_hook(fake_mailman_config)
            self.assertNotIn('example', fake_mailman_config.plugins)
