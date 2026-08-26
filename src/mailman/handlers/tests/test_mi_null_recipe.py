# Copyright (C) 2026 by the Free Software Foundation, Inc.
#
# This file is part of GNU Mailman.
#
# GNU Mailman is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.

"""draft-05 §5.1: the DKIM2 handler must never emit a null header recipe.

These are pure-function tests over compute_header_recipe and therefore do not
need the Mailman config/database layer.
"""

import unittest

from mailman.handlers.message_instance import compute_header_recipe


class TestNoNullHeaderRecipe(unittest.TestCase):
    def test_body_only_change_yields_no_header_recipe(self):
        # Identical header sets → no header Recipe at all (None), never a
        # null "h" value.
        headers = [('From', 'a@example.com'), ('Subject', 'hi')]
        self.assertIsNone(compute_header_recipe(headers, headers))

    def test_header_change_yields_dict_not_null(self):
        cur = [('From', 'a@example.com'), ('Subject', '[list] hi')]
        prev = [('From', 'a@example.com'), ('Subject', 'hi')]
        recipe = compute_header_recipe(cur, prev)
        self.assertIsInstance(recipe, dict)
        self.assertIsNotNone(recipe)
        self.assertIn('subject', recipe)
