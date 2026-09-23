# Copyright (C) 2026 by the Free Software Foundation, Inc.
#
# This file is part of GNU Mailman.
#
# GNU Mailman is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.

"""Regression: the egress Message-Instance recipe must reverse cleanly.

A list post is hashed at ingress (m=1), modified by Mailman (subject prefix,
list headers, footer), and an egress recipe (m=2) is recorded. A verifier
reconstructs the original by undoing m=2 and re-hashing — that reconstruction
MUST match the m=1 header/body hash, or downstream full-chain verification
fails. These are pure-function tests (no config/database layer needed).
"""

import email
import unittest

from mailman.handlers.message_instance import (
    _b64, _collect_headers, build_mi_header_value, compute_body_recipe,
    compute_header_hash, compute_header_recipe)
from mailman.handlers.tests.mi_support import (
    _get_body_lines, compute_body_hash, undo_message_instance)


ORIGINAL = (
    'From: Bron <brong@example.com>\r\n'
    'To: test@lists.example.com\r\n'
    'Subject: hello there\r\n'
    'Message-ID: <orig-1@example.com>\r\n'
    'Date: Wed, 24 Jun 2026 20:00:00 +0000\r\n'
    'MIME-Version: 1.0\r\n'
    'Content-Type: text/plain; charset="utf-8"\r\n'
    'Content-Transfer-Encoding: 7bit\r\n'
    '\r\n'
    'Original body line one.\r\n'
    'Original body line two.\r\n'
)


def _make_original():
    return email.message_from_string(ORIGINAL)


def _round_trip(modify):
    """Apply `modify` to a fresh original, build the egress recipe, undo it,
    and return (ok, detail) for whether the reconstruction matches m=1."""
    orig = _make_original()
    h1 = compute_header_hash(orig)
    b1 = compute_body_hash(orig)
    prev_headers = _collect_headers(orig)
    prev_body = _get_body_lines(orig)

    mod = email.message_from_bytes(orig.as_bytes())
    modify(mod)

    header_recipe = compute_header_recipe(_collect_headers(mod), prev_headers)
    body_recipe = compute_body_recipe(_get_body_lines(mod), prev_body)
    mi2 = build_mi_header_value(2, compute_header_hash(mod),
                                compute_body_hash(mod), header_recipe,
                                body_recipe)
    mi1 = build_mi_header_value(1, h1, b1)
    # Prepend m=1 then m=2 (highest first), as on the wire.
    mod._headers.insert(0, ('Message-Instance', mi1))
    mod._headers.insert(0, ('Message-Instance', mi2))

    undo_message_instance(mod)
    got_h = _b64(compute_header_hash(mod))
    got_b = _b64(compute_body_hash(mod))
    detail = []
    if got_h != _b64(h1):
        detail.append('header hash {} != {}'.format(got_h, _b64(h1)))
    if got_b != _b64(b1):
        detail.append('body hash {} != {}'.format(got_b, _b64(b1)))
    return (not detail), '; '.join(detail)


class TestMIRoundTrip(unittest.TestCase):
    def test_subject_prefix_only(self):
        def m(msg):
            msg.replace_header('Subject', '[Test] hello there')
        ok, detail = _round_trip(m)
        self.assertTrue(ok, detail)

    def test_added_list_headers(self):
        def m(msg):
            msg['List-Id'] = 'Test <test.lists.example.com>'
            msg['List-Post'] = '<mailto:test@lists.example.com>'
            msg['Archived-At'] = '<https://archive.example.com/x>'
            msg['Precedence'] = 'list'
        ok, detail = _round_trip(m)
        self.assertTrue(ok, detail)

    def test_footer_appended(self):
        def m(msg):
            body = msg.get_payload()
            msg.set_payload(body + '-- \r\nMailing list footer\r\n')
        ok, detail = _round_trip(m)
        self.assertTrue(ok, detail)

    def test_minimal_original_mailman_adds_mime_headers(self):
        # The real-world list case: the author sends a bare message with NO
        # Content-Type/MIME-Version/Content-Transfer-Encoding, and Mailman adds
        # them (plus list headers + subject prefix + footer). Undo must remove
        # the mailman-added headers so the reconstruction matches the bare m=1.
        global ORIGINAL
        saved = ORIGINAL
        try:
            ORIGINAL = (
                'From: Bron <brong@example.com>\r\n'
                'To: test@lists.example.com\r\n'
                'Subject: hello there\r\n'
                'Message-ID: <orig-1@example.com>\r\n'
                'Date: Wed, 24 Jun 2026 20:00:00 +0000\r\n'
                '\r\n'
                'Capture body.\r\n'
            )

            def m(msg):
                msg.replace_header('Subject', '[Test] hello there')
                msg['MIME-Version'] = '1.0'
                msg['Content-Type'] = 'text/plain; charset="utf-8"'
                msg['Content-Transfer-Encoding'] = '7bit'
                msg['List-Id'] = 'Test <test.lists.example.com>'
                msg['Archived-At'] = '<https://archive.example.com/x>'
                msg['Precedence'] = 'list'
                body = msg.get_payload()
                msg.set_payload(body + '-- \r\nfooter\r\n')
            ok, detail = _round_trip(m)
            self.assertTrue(ok, detail)
        finally:
            ORIGINAL = saved

    def test_full_list_transformation(self):
        # Everything a list does at once: the real-world case.
        def m(msg):
            msg.replace_header('Subject', '[Test] hello there')
            msg['List-Id'] = 'Test <test.lists.example.com>'
            msg['List-Post'] = '<mailto:test@lists.example.com>'
            msg['Archived-At'] = '<https://archive.example.com/x>'
            msg['Precedence'] = 'list'
            body = msg.get_payload()
            msg.set_payload(body + '-- \r\nMailing list footer\r\n')
        ok, detail = _round_trip(m)
        self.assertTrue(ok, detail)


if __name__ == '__main__':
    unittest.main(verbosity=2)
