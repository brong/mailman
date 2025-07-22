# Copyright (C) 2012-2025 by the Free Software Foundation, Inc.
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

"""Test the system-wide global configuration."""

import os
import tempfile
import unittest

from contextlib import ExitStack
from importlib.resources import path
from mailman.config.config import (
    Configuration,
    external_configuration,
    load_external,
)
from mailman.interfaces.configuration import (
    ConfigurationUpdatedEvent,
    MissingConfigurationFileError,
)
from mailman.testing.helpers import configuration, event_subscribers
from mailman.testing.layers import ConfigLayer
from tempfile import NamedTemporaryFile, TemporaryDirectory
from unittest import mock


class TestConfiguration(unittest.TestCase):
    layer = ConfigLayer

    def test_push_and_pop_trigger_events(self):
        # Pushing a new configuration onto the stack triggers a
        # post-processing event.
        events = []
        def on_event(event):                                     # noqa: E306
            if isinstance(event, ConfigurationUpdatedEvent):
                # Record both the event and the top overlay.
                events.append(event.config.overlays[0].name)
        # Do two pushes, and then pop one of them.
        with event_subscribers(on_event):
            with configuration('test', _configname='first'):
                with configuration('test', _configname='second'):
                    pass
                self.assertEqual(events, ['first', 'second', 'first'])

    def test_config_template_dir_is_source(self):
        # This test will leave a 'var' directory in the top-level source
        # directory.  Be sure to clean it up.
        config = Configuration()
        with ExitStack() as resources:
            fp = resources.enter_context(
                NamedTemporaryFile('w', encoding='utf-8'))
            var_dir = resources.enter_context(TemporaryDirectory())
            # Don't let the post-processing after the config.load() to put a
            # 'var' directory in the source tree's top level directory.
            print("""\
[paths.here]
template_dir: :source:
var_dir: {}
""".format(var_dir), file=fp)
            fp.flush()
            config.load(fp.name)
        import mailman.templates
        self.assertEqual(config.TEMPLATE_DIR,
                         os.path.dirname(mailman.templates.__file__))


class TestExternal(unittest.TestCase):
    """Test external configuration file loading APIs."""

    def test_load_external_by_filename(self):
        with path('mailman.config', 'postfix.cfg') as filename:
            contents = load_external(str(filename))
        self.assertEqual(contents[:9], '[postfix]')

    def test_load_external_by_path(self):
        contents = load_external('python:mailman.config.postfix')
        self.assertEqual(contents[:9], '[postfix]')

    def test_external_configuration_by_filename(self):
        with path('mailman.config', 'postfix.cfg') as filename:
            parser = external_configuration(str(filename))
        self.assertEqual(parser.get('postfix', 'postmap_command'),
                         '/usr/sbin/postmap')

    def test_external_configuration_by_path(self):
        parser = external_configuration('python:mailman.config.postfix')
        self.assertEqual(parser.get('postfix', 'postmap_command'),
                         '/usr/sbin/postmap')

    def test_missing_configuration_file(self):
        with self.assertRaises(MissingConfigurationFileError) as cm:
            external_configuration('path:mailman.config.missing')
        self.assertEqual(cm.exception.path, 'path:mailman.config.missing')


class TestConfigurationErrors(unittest.TestCase):
    layer = ConfigLayer

    def test_bad_path_layout_specifier(self):
        # Using a [mailman]layout name that doesn't exist is a fatal error.
        config = Configuration()
        with ExitStack() as resources:
            fp = resources.enter_context(
                NamedTemporaryFile('w', encoding='utf-8'))
            print("""\
[mailman]
layout: nonesuch
""", file=fp)
            fp.flush()
            # Suppress warning messages in the test output.  Also, make sure
            # that the config.load() call doesn't break global state.
            resources.enter_context(mock.patch('sys.stderr'))
            resources.enter_context(mock.patch.object(config, '_clear'))
            cm = resources.enter_context(self.assertRaises(SystemExit))
            config.load(fp.name)
        self.assertEqual(cm.exception.args, (1,))

    def test_path_expansion_infloop(self):
        # A path expansion never completes because it references a non-existent
        # substitution variable.
        config = Configuration()
        with ExitStack() as resources:
            fp = resources.enter_context(
                NamedTemporaryFile('w', encoding='utf-8'))
            print("""\
[paths.here]
log_dir: $nopath/log_dir
""", file=fp)
            fp.flush()
            # Suppress warning messages in the test output.  Also, make sure
            # that the config.load() call doesn't break global state.
            resources.enter_context(mock.patch('sys.stderr'))
            resources.enter_context(mock.patch.object(config, '_clear'))
            cm = resources.enter_context(self.assertRaises(SystemExit))
            config.load(fp.name)
        self.assertEqual(cm.exception.args, (1,))


class TestARCParameterValidation(unittest.TestCase):
    """Test ARCSigning Exceptions."""
    layer = ConfigLayer

    def setUp(self):
        privkey = b"""-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDkHlOQoBTzWRiGs5V6NpP3idY6Wk08a5qhdR6wy5bdOKb2jLQi
Y/J16JYi0Qvx/byYzCNb3W91y3FutACDfzwQ/BC/e/8uBsCR+yz1Lxj+PL6lHvqM
KrM3rG4hstT5QjvHO9PzoxZyVYLzBfO2EeC3Ip3G+2kryOTIKT+l/K4w3QIDAQAB
AoGAH0cxOhFZDgzXWhDhnAJDw5s4roOXN4OhjiXa8W7Y3rhX3FJqmJSPuC8N9vQm
6SVbaLAE4SG5mLMueHlh4KXffEpuLEiNp9Ss3O4YfLiQpbRqE7Tm5SxKjvvQoZZe
zHorimOaChRL2it47iuWxzxSiRMv4c+j70GiWdxXnxe4UoECQQDzJB/0U58W7RZy
6enGVj2kWF732CoWFZWzi1FicudrBFoy63QwcowpoCazKtvZGMNlPWnC7x/6o8Gc
uSe0ga2xAkEA8C7PipPm1/1fTRQvj1o/dDmZp243044ZNyxjg+/OPN0oWCbXIGxy
WvmZbXriOWoSALJTjExEgraHEgnXssuk7QJBALl5ICsYMu6hMxO73gnfNayNgPxd
WFV6Z7ULnKyV7HSVYF0hgYOHjeYe9gaMtiJYoo0zGN+L3AAtNP9huqkWlzECQE1a
licIeVlo1e+qJ6Mgqr0Q7Aa7falZ448ccbSFYEPD6oFxiOl9Y9se9iYHZKKfIcst
o7DUw1/hz2Ck4N5JrgUCQQCyKveNvjzkkd8HjYs0SwM0fPjK16//5qDZ2UiDGnOe
uEzxBDAr518Z8VFbR41in3W4Y3yCDgQlLlcETrS+zYcL
-----END RSA PRIVATE KEY-----
"""
        self.keyfile = tempfile.NamedTemporaryFile(delete=True)
        self.keyfile.write(privkey)
        self.keyfile.flush()

    def tearDown(self):
        self.keyfile.close()

    def test_arc_enabled_but_missing_privkey(self):
        # Missing private key when ARC is enabled.
        config = Configuration()
        with ExitStack() as resources:
            fp = resources.enter_context(
                NamedTemporaryFile('w', encoding='utf-8'))
            print("""\
[ARC]
enabled: yes
authserv_id: lists.example.org
selector: dummy
domain: example.org
sig_headers: mime-version, date, from, to, subject
privkey:
""", file=fp)
            fp.flush()
            # Suppress warning messages in the test output.  Also, make sure
            # that the config.load() call doesn't break global state.
            resources.enter_context(mock.patch('sys.stderr'))
            resources.enter_context(mock.patch.object(config, '_clear'))
            resources.enter_context(self.assertRaises(SystemExit))
            config.load(fp.name)

    def test_arc_enabled_but_wrong_file(self):
        # Unreadable private key when ARC is enabled.
        config = Configuration()
        with ExitStack() as resources:
            fp = resources.enter_context(
                NamedTemporaryFile('w', encoding='utf-8'))
            print("""\
[ARC]
enabled: yes
authserv_id: lists.example.org
selector: dummy
domain: example.org
sig_headers: mime-version, date, from, to, subject
privkey: /missing/location.pem
""", file=fp)
            fp.flush()
            # Suppress warning messages in the test output.  Also, make sure
            # that the config.load() call doesn't break global state.
            resources.enter_context(mock.patch('sys.stderr'))
            resources.enter_context(mock.patch.object(config, '_clear'))
            resources.enter_context(self.assertRaises(SystemExit))
            config.load(fp.name)

    def test_arc_sign_non_ascii_privkey(self):
        # Private Key contains non-ascii characters.
        config = Configuration()

        uni_keyfile = tempfile.NamedTemporaryFile(delete=True)
        uni_keyfile.write("¢¢¢¢¢¢¢".encode('utf-8'))
        uni_keyfile.flush()
        with ExitStack() as resources:

            fp = resources.enter_context(
                NamedTemporaryFile('w', encoding='utf-8'))
            print("""\
[ARC]
enabled: yes
authserv_id: lists.example.org
selector: dummy
domain: example.org
sig_headers: mime-version, date, from, to, subject
privkey: {}
""".format(uni_keyfile.name), file=fp)
            fp.flush()
            # Suppress warning messages in the test output.  Also, make sure
            # that the config.load() call doesn't break global state.
            resources.enter_context(mock.patch('sys.stderr'))
            resources.enter_context(mock.patch.object(config, '_clear'))
            resources.enter_context(self.assertRaises(SystemExit))
            config.load(fp.name)

    def test_arc_missing_from_in_headers(self):
        # List of sig_headers should always include "From" header, otherwise,
        # exception is raised when signing a message.
        config = Configuration()

        with ExitStack() as resources:
            fp = resources.enter_context(
                NamedTemporaryFile('w', encoding='utf-8'))
            print("""\
[ARC]
enabled: yes
authserv_id: lists.example.org
selector: dummy
domain: example.org
sig_headers: to, subject, date
privkey: {}
""".format(self.keyfile.name), file=fp)
            fp.flush()
            # Suppress warning messages in the test output.  Also, make sure
            # that the config.load() call doesn't break global state.
            resources.enter_context(mock.patch('sys.stderr'))
            resources.enter_context(mock.patch.object(config, '_clear'))
            resources.enter_context(self.assertRaises(SystemExit))
            config.load(fp.name)
