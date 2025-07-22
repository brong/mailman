# Copyright (C) 2014-2025 by the Free Software Foundation, Inc.
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

"""Test Postfix config."""

import os
import unittest

from mailman.config import config
from mailman.mta.postfix import LMTP
from mailman.testing.layers import ConfigLayer


class TestPostfixConfig(unittest.TestCase):
    layer = ConfigLayer

    def test_type_default_config(self):
        config.push('database_type_config', """\
[mta]
configuration: python:mailman.mta.tests.data.postfix_type_default
""")
        lmtp = LMTP()
        lmtp.regenerate()
        for db_basename in ('postfix_lmtp', 'postfix_domains'):
            src_path = f'{config.DATA_DIR}/{db_basename}'
            db_path = f'{src_path}.created-db'
            self.assertTrue(
                os.path.isfile(db_path),
                f'Created database file: {db_path}'
            )
            with open(db_path, 'r') as f:
                self.assertEqual(
                    src_path, f.read().rstrip('\n'),
                    'Command-line argument(s) for postmap command'
                )
        config.pop('database_type_config')

    def test_type_hash_config(self):
        config.push('database_type_config', """\
[mta]
configuration: python:mailman.mta.tests.data.postfix_type_hash
""")
        lmtp = LMTP()
        lmtp.regenerate()
        for db_basename in ('postfix_lmtp', 'postfix_domains'):
            src_path = f'{config.DATA_DIR}/{db_basename}'
            db_path = f'{src_path}.created-db'
            self.assertTrue(
                os.path.isfile(db_path),
                f'Created database file: {db_path}'
            )
            with open(db_path, 'r') as f:
                self.assertEqual(
                    f'hash:{src_path}', f.read().rstrip("\n"),
                    'Command-line argument(s) for postmap command'
                )
        config.pop('database_type_config')

    def test_type_regexp_config(self):
        config.push('regexp_config', """\
[mta]
configuration: python:mailman.mta.tests.data.postfix_type_regexp
""")
        lmtp = LMTP()
        self.assertEqual('regexp', lmtp.transport_file_type)
        config.pop('regexp_config')
