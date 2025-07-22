# Copyright (C) 2020-2025 by the Free Software Foundation, Inc.
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

"""Test the scrubber utility."""

import unittest

from importlib.resources import open_text
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.utilities import scrubber


class TestScrubber(unittest.TestCase):
    def test_simple_message(self):
        msg = mfs("""\
From: user@example.com
To: list@example.com
Subject: simple test message

This is the text
""")
        self.assertEqual(scrubber.scrub(msg), 'This is the text\n')

    def test_bogus_charset(self):
        msg = mfs("""\
From: user@example.com
To: list@example.com
Subject: simple test message
MIME-Version: 1.0
Content-Type: text/plain; charset="bogus"

This is the text
""")
        self.assertEqual(scrubber.scrub(msg), 'This is the text\n')

    def test_complex_message(self):
        with open_text('mailman.utilities.tests.data', 'scrub_test.eml') as fp:
            msg = mfs(fp.read())
        self.assertEqual(scrubber.scrub(msg), """\
This is the first text/plain part
-------------- next part --------------
A message part incompatible with plain text digests has been removed ...
Name: not available
Type: text/html
Size: 27 bytes
Desc: not available
-------------- next part --------------
Plain text with \\u201cfancy quotes\\u201d from embedded message.
-------------- next part --------------
A message part incompatible with plain text digests has been removed ...
Name: not available
Type: text/html
Size: 58 bytes
Desc: not available
-------------- next part --------------
A message part incompatible with plain text digests has been removed ...
Name: Image
Type: image/jpeg
Size: 16 bytes
Desc: A JPEG image
""")
