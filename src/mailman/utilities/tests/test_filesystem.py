# Copyright (C) 2011-2025 by the Free Software Foundation, Inc.
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

"""Testing functions in the filesystem utilities."""

import os
import shutil
import tempfile
import unittest

from mailman.utilities.filesystem import first_inexistent_directory, makedirs


def fake_makedirs(path, mode, exist_ok=False):
    """A fake makedirs function"""

    with open(path, 'a'):
        pass

    raise FileExistsError("%s exists.", path)


class TestMakedirs(unittest.TestCase):
    """Tests the makedirs utility function"""

    def setUp(self):
        """Makes a temp directory and defines some paths to create. Then, check
        what's the return of the functions."""

        self.test_directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.test_directory)

        self.foo = os.path.join(self.test_directory, "foo")
        self.bar = os.path.join(self.foo, "bar")
        self.baz = os.path.join(self.bar, "baz")

    def test_first_inexistent_directory(self):
        """Tests the output of first_inexistent_directory"""

        upwards_dir = first_inexistent_directory(self.baz)

        self.assertEqual(upwards_dir, self.foo)

    def test_first_inexistent_directory_traversing_nondir(self):
        """Tests the output of first_inexistent_directory when one of the
        intermediate directories is actually a file."""

        os.makedirs(self.foo, 0o0755)
        with open(self.bar, 'a'):
            pass

        with self.assertRaises(FileExistsError):
            first_inexistent_directory(self.baz)

    def test_makedirs_race_condition(self):
        """Mocks os.makedirs behaviour to create willingly a race condition in
        filesystem.makedirs and test it."""

        first_inexistent_directory(self.baz)

        makedirs(self.bar)

        with unittest.mock.patch('os.makedirs', new=fake_makedirs):
            with self.assertRaises(FileExistsError):
                makedirs(self.baz)
