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

"""Test some plugin behavior."""

import unittest

from contextlib import ExitStack
from mailman.testing.documentation import run_mailman
from mailman.testing.layers import ConfigLayer
from tempfile import NamedTemporaryFile


class TestExternalHooks(unittest.TestCase):
    layer = ConfigLayer
    maxDiff = None

    def setUp(self):
        self.resources = ExitStack()
        self.addCleanup(self.resources.close)
        self.config_file = self.resources.enter_context(NamedTemporaryFile())

    def test_pre_hook_deprecated(self):
        with open(self.config_file.name, 'w', encoding='utf-8') as fp:
            print("""\
[mailman]
pre_hook: sys.exit

[logging.plugins]
propagate: yes
""", file=fp)
        proc = run_mailman(['-C', self.config_file.name, 'info'])
        # We only care about the log warning printed to stdout.
        warning = proc.stdout.splitlines()[0]
        self.assertEqual(
            warning[-111:],
            'The [mailman]pre_hook configuration value has been replaced '
            "by the plugins infrastructure, and won't be called.")

    def test_post_hook_deprecated(self):
        with open(self.config_file.name, 'w', encoding='utf-8') as fp:
            print("""\
[mailman]
post_hook: sys.exit

[logging.plugins]
propagate: yes
""", file=fp)
        proc = run_mailman(['-C', self.config_file.name, 'info'])
        # We only care about the log warning printed to stdout.
        warning = proc.stdout.splitlines()[0]
        self.assertEqual(
            warning[-112:],
            'The [mailman]post_hook configuration value has been replaced '
            "by the plugins infrastructure, and won't be called.")
