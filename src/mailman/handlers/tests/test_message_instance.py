# Copyright (C) 2025-2026 by the Free Software Foundation, Inc.
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

"""Tests for Message-Instance header support (DKIM2)."""

import base64
import copy
import email
import hashlib
import json
import os
import pickle
import re
import time
import unittest

from email.generator import BytesGenerator
from io import BytesIO
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.core.pipelines import process as process_pipeline
from mailman.email.message import Message
from mailman.handlers import decorate
from mailman.handlers.message_instance import (
    BODY_IDENTICAL,
    BODY_TOO_BIG,
    build_mi_header_value,
    compute_body_recipe,
    compute_header_hash,
    compute_header_recipe,
    get_max_mi_version,
    MalformedInstance,
    NULL_BODY_RECIPE,
    verify_mi_raw,
    _b64,
    _fold_mi_value,
    _normalize_crlf,
    _split_raw,
    _parse_mi,
    _get_mi_version,
    _should_exclude_header,
    _collect_headers,
    _body_lines_raw,
    _body_diff,
    _hash_header_pairs,
    _serialize_msg,
    _wire_bytes,
    compute_body_hash_raw,
    compute_header_hash_raw,
)
from mailman.handlers.tests.mi_support import (
    compute_body_hash,
    undo_message_instance,
    verify_message_instance,
    _get_body_lines,
    _get_raw_body,
    _plan_undo,
)
from mailman.interfaces.archiver import ArchivePolicy
from mailman.interfaces.template import ITemplateManager
from mailman.testing.helpers import get_queue_messages
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer
from mailman.utilities.email import add_message_hash
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zope.component import getUtility


def _msg_from_bytes(raw):
    """Create a Mailman Message from raw bytes."""
    return email.message_from_bytes(raw, Message)


def _decode_mi_hashes(mi_value):
    """Parse a Message-Instance value and return (header_hash, body_hash)."""
    _, hashes, _ = _parse_mi(mi_value)
    if hashes is None:
        return None, None
    return hashes['h'][1], hashes['b'][1]


def _decode_mi_recipe(mi_value):
    """Parse a Message-Instance value and return the recipe dict, or None."""
    _, _, recipe = _parse_mi(mi_value)
    return recipe


def _recipe_at(msg, version):
    """Return the decoded recipe of the Message-Instance with this m= value.

    Returns None when no instance carries that version.
    """
    for val in msg.get_all('message-instance', []):
        if re.search(r'm\s*=\s*{}\b'.format(version), str(val)):
            return _decode_mi_recipe(val)
    return None


def _copy_steps(steps):
    """The {"c": [start, end]} copy steps of a recipe (spec-06 §5)."""
    return [s for s in steps if isinstance(s, dict) and 'c' in s]


def _data_steps(steps):
    """The {"d": [...]} literal-data steps of a recipe (spec-06 §5)."""
    return [s for s in steps if isinstance(s, dict) and 'd' in s]


def _octets(raw):
    """Raw octets as the str form message_instance works in: decoded as
    UTF-8 with surrogateescape, so bytes >= 0x80 that are not UTF-8 become
    lone surrogates and valid UTF-8 becomes the characters it encodes."""
    return raw.decode('utf-8', errors='surrogateescape')


def _assert_no_surrogates_in_json(test, recipe):
    """A recipe must JSON-encode without the \\udcXX lone-surrogate escapes
    that no other implementation can decode."""
    encoded = json.dumps(recipe, separators=(',', ':'))
    test.assertNotIn('\\udc', encoded, encoded)


# A subset of the body diff vectors the DKIM2 implementations share:
# (name, current lines, previous lines, max_literals, expected), where
# expected is "identical", "too_big" or the flat recipe: [from, to]
# copy ranges of the current lines and literal previous lines.
BODY_DIFF_CASES = [
    ('identical', ['a', 'b', 'c'], ['a', 'b', 'c'], 1000, 'identical'),
    ('both empty', [], [], 1000, 'identical'),
    ('line added at the top', ['x', 'a', 'b', 'c'], ['a', 'b', 'c'], 1000,
     [[2, 4]]),
    ('line removed at the end', ['a', 'b', 'c'], ['a', 'b', 'c', 'd'], 1000,
     [[1, 3], 'd']),
    ('changed middle line', ['a', 'B', 'c'], ['a', 'b', 'c'], 1000,
     [[1, 1], 'b', [3, 3]]),
    ('empty current body', [], ['a', 'b'], 1000, ['a', 'b']),
    ('empty previous body', ['a', 'b'], [], 1000, []),
    ('swap', ['a', 'b'], ['b', 'a'], 1000, [[2, 2], 'a']),
    ('blank lines', ['', 'a', '', '', 'b', ''], ['', '', 'a', 'b', '', ''],
     1000, [[1, 1], [3, 3], 'a', [5, 5], '', [6, 6]]),
    ('list wrap', ['header', '--b', '', 'x', 'y', '--b--', 'footer'],
     ['x', 'y'], 1000, [[4, 5]]),
    ('cap 2 exceeded', ['a', 'b', 'c'], ['a', 'x', 'y', 'z', 'c'], 2,
     'too_big'),
    ('cap 3 met', ['a', 'b', 'c'], ['a', 'x', 'y', 'z', 'c'], 3,
     [[1, 1], 'x', 'y', 'z', [3, 3]]),
    ('line-count floor',
     ['x', 'y', 'x', 'y', 'x', 'y', 'x', 'y', 'x', 'y', 'x', 'y', 'x', 'y',
      'x', 'y', 'x', 'y', 'x', 'y'],
     ['y', 'y', 'x', 'y', 'y', 'x', 'y', 'y', 'x', 'y', 'y', 'x', 'y', 'y',
      'x', 'y', 'y', 'x', 'y', 'y', 'x', 'y', 'y', 'x', 'y', 'y', 'x', 'y',
      'y', 'x'],
     5, 'too_big'),
    ('repeated block', ['a', 'b', 'c', 'a', 'b', 'c', 'a', 'b', 'c'],
     ['c', 'b', 'a', 'c', 'b', 'a'], 1000,
     [[3, 3], [5, 5], [7, 7], [9, 9], 'b', 'a']),
    ('random 1', ['a', 'b', 'c', 'd', 'a', 'b', 'c'],
     ['a', 'b', 'c', 'd', 'a', 'b', 'c', 'd', 'a', 'b', 'c', 'd', 'a', 'b',
      'c'],
     1000, [[1, 7], 'd', 'a', 'b', 'c', 'd', 'a', 'b', 'c']),
    ('random 2', ['c', 'a', 'c', 'c', 'a', 'a', 'b', 'c', 'c', 'b', 'b', 'a'],
     ['c', 'c', 'a', 'c', 'c', 'c', 'a', 'b', 'c'], 1000,
     [[1, 1], 'c', [2, 4], 'c', [5, 5], [7, 8]]),
    ('random 3', ['b', 'a', 'a', 'a'],
     ['c', 'c', 'a', 'c', 'c', 'c', 'c', 'b', 'c'], 1000,
     ['c', 'c', [2, 2], 'c', 'c', 'c', 'c', 'b', 'c']),
    ('random 4', ['a', 'a', 'a', 'a'],
     ['a', 'a', 'a', 'a', 'a', 'a', 'a', 'a', 'a'], 1000,
     [[1, 4], 'a', 'a', 'a', 'a', 'a']),
    ('random 5', ['b', 'a', 'c', 'a'],
     ['c', 'b', 'a', 'a', 'a', 'b', 'c', 'b', 'c'], 1000,
     ['c', [1, 2], [4, 4], 'a', 'b', 'c', 'b', 'c']),
    ('random 6', ['a', 'b', 'c', 'b'],
     ['a', 'a', 'b', 'a', 'a', 'c', 'c', 'a', 'a'], 1000,
     [[1, 1], 'a', [2, 2], 'a', 'a', [3, 3], 'c', 'a', 'a']),
    ('random 7', ['c', 'd', 'b', 'b'],
     ['b', 'e', 'a', 'c', 'e', 'c', 'b', 'e', 'a'], 1000,
     ['b', 'e', 'a', [1, 1], 'e', 'c', [3, 3], 'e', 'a']),
    ('random 8', ['b', 'a', 'b', 'a'],
     ['a', 'b', 'a', 'b', 'a', 'b', 'a', 'b', 'a'], 1000,
     ['a', 'b', 'a', 'b', 'a', [1, 4]]),
    ('random 9', ['c', 'b', 'd', 'b'],
     ['d', 'b', 'b', 'c', 'b', 'a', 'd', 'b', 'a'], 1000,
     ['d', 'b', 'b', [1, 2], 'a', [3, 4], 'a']),
    ('random 10', ['b', 'a', 'b', 'a'],
     ['a', 'b', 'a', 'b', 'a', 'b', 'a', 'b', 'a'], 2, 'too_big'),
    ('random 11', ['b', 'a', 'b', 'a'],
     ['a', 'b', 'a', 'b', 'a', 'b', 'a', 'b', 'a'], 1000,
     ['a', 'b', 'a', 'b', 'a', [1, 4]]),
    ('random 12', ['c', 'a', 'a', 'c'],
     ['a', 'a', 'b', 'b', 'c', 'b', 'c', 'c', 'a'], 1000,
     [[2, 3], 'b', 'b', [4, 4], 'b', 'c', 'c', 'a']),
    ('random 13', ['e', 'c', 'c', 'd'],
     ['e', 'c', 'a', 'c', 'd', 'c', 'a', 'a', 'b'], 1000,
     [[1, 2], 'a', [3, 4], 'c', 'a', 'a', 'b']),
    ('random 14', ['b', 'c', 'd', 'a'],
     ['c', 'd', 'a', 'b', 'c', 'd', 'a', 'b', 'c'], 1000,
     ['c', 'd', 'a', [1, 4], 'b', 'c']),
    ('random 20', ['b', 'a', 'a', 'a'],
     ['b', 'b', 'a', 'b', 'b', 'b', 'b', 'c', 'c'], 2, 'too_big'),
    ('random 30', ['b', 'c', 'd', 'a'],
     ['c', 'd', 'a', 'b', 'c', 'd', 'a', 'b', 'c'], 2, 'too_big'),
]


# =====================================================================
# Unit tests for hash computation (no ConfigLayer needed)
# =====================================================================

class TestHeaderExclusion(unittest.TestCase):
    """Test that the correct headers are excluded from hashing."""

    def test_excluded_headers(self):
        for name in ('Received', 'Return-Path', 'Delivered-To',
                     'Message-Instance', 'DKIM2-Signature', 'DKIM-Signature',
                     'Authentication-Results'):
            self.assertTrue(_should_exclude_header(name), name)

    def test_excluded_prefixes(self):
        for name in ('X-Mailer', 'X-Spam-Status', 'ARC-Seal',
                     'ARC-Message-Signature'):
            self.assertTrue(_should_exclude_header(name), name)

    def test_included_headers(self):
        for name in ('From', 'To', 'Subject', 'Date', 'Content-Type',
                     'List-Id', 'Reply-To'):
            self.assertFalse(_should_exclude_header(name), name)

    def test_spec05_excluded_names(self):
        # spec-06 §4: names added by the HDRMAINT survey
        for name in ('Apparently-To', 'Auto-Submitted', 'DL-Expansion-History',
                     'Original-Recipient', 'SIO-Label-History', 'VBR-Info',
                     'X400-Received', 'X400-Trace'):
            self.assertTrue(_should_exclude_header(name), name)

    def test_spec05_received_prefix(self):
        # spec-06 §4: any Received-* field is a trace field
        self.assertTrue(_should_exclude_header('Received-SPF'))
        self.assertTrue(_should_exclude_header('Received-Anything'))

    def test_spec05_arc_narrowed(self):
        # spec-05 §4: the ARC- prefix narrowed to the three RFC 8617 names
        self.assertTrue(_should_exclude_header('ARC-Seal'))
        self.assertTrue(_should_exclude_header('ARC-Message-Signature'))
        self.assertTrue(_should_exclude_header('ARC-Authentication-Results'))
        self.assertFalse(_should_exclude_header('ARC-Something-Else'))


class TestHashComputation(unittest.TestCase):
    """Test MI header and body hash computation against known values."""

    def test_simple_message_hashes_deterministic(self):
        raw = (
            b'From: sender@test1.dkim2.com\r\n'
            b'To: recipient@example.com\r\n'
            b'Subject: Simple test message\r\n'
            b'Date: Sat, 01 Mar 2026 12:00:00 +0000\r\n'
            b'Message-ID: <test-simple@test1.dkim2.com>\r\n'
            b'\r\n'
            b'This is a simple test message.\r\n'
        )
        msg = _msg_from_bytes(raw)
        h1 = compute_header_hash(msg)
        h2 = compute_header_hash(msg)
        self.assertEqual(h1, h2)
        b1 = compute_body_hash(msg)
        b2 = compute_body_hash(msg)
        self.assertEqual(b1, b2)

    def test_interop_hashes(self):
        # Test vector from the DKIM2 interop suite (simple.eml signed as
        # simple-ed25519.eml).  The m=1 hashes below are the reference
        # signer's, hard-coded so this test locks Mailman's hash computation
        # to the other implementations rather than to itself.
        raw = (
            b'DKIM2-Signature: i=1; m=1; t=1740000000; d=test1.dkim2.com; '
            b'mf=PHNlbmRlckB0ZXN0MS5ka2ltMi5jb20+; '
            b'rt=PHJlY2lwaWVudEBleGFtcGxlLmNvbT4=; '
            b's=ed25519:ed25519-sha256:'
            b'RgE/0zAwiTp1c+QYjOCgmMT9ybXqyqgBMSUhsU//WWDESskkw3Penu0DX4At'
            b'+lrGKrU2hPB8/axUhYhE+c0VBg==;\r\n'
            b'Message-Instance: m=1; '
            b'h=sha256:SLtzk6LO68CCaX4edrJ6yfpWbp3hwgvI8IdMBRLDk+Y=:'
            b'SgG5fNGEg1x24MwItCUYGDHQkWKng06W1/IvTGBdwzU=;\r\n'
            b'From: sender@test1.dkim2.com\r\n'
            b'To: recipient@example.com\r\n'
            b'Subject: Simple test message\r\n'
            b'Date: Sat, 01 Mar 2026 12:00:00 +0000\r\n'
            b'Message-ID: <test-simple@test1.dkim2.com>\r\n'
            b'\r\n'
            b'Hello, this is a simple test message.\r\n'
        )
        msg = _msg_from_bytes(raw)
        mi_values = msg.get_all('message-instance', [])
        self.assertTrue(len(mi_values) > 0, 'No MI header in test message')
        stored_h, stored_b = _decode_mi_hashes(mi_values[0])
        self.assertEqual(
            stored_h, 'SLtzk6LO68CCaX4edrJ6yfpWbp3hwgvI8IdMBRLDk+Y=')
        self.assertEqual(
            stored_b, 'SgG5fNGEg1x24MwItCUYGDHQkWKng06W1/IvTGBdwzU=')
        computed_h = _b64(compute_header_hash(msg))
        computed_b = _b64(compute_body_hash(msg))
        self.assertEqual(stored_h, computed_h)
        self.assertEqual(stored_b, computed_b)

    def test_excluded_headers_dont_affect_hash(self):
        raw = (
            b'From: a@b.com\r\nTo: c@d.com\r\nSubject: test\r\n'
            b'\r\nbody\r\n'
        )
        msg1 = _msg_from_bytes(raw)
        h1 = compute_header_hash(msg1)
        msg2 = _msg_from_bytes(raw)
        msg2['X-Spam-Score'] = '5'
        msg2['Received'] = 'from mx.example.com'
        msg2['Authentication-Results'] = 'none'
        msg2['ARC-Seal'] = 'i=1; ...'
        msg2['Message-Instance'] = 'm=1; h=xxx'
        msg2['DKIM-Signature'] = 'v=1; ...'
        self.assertEqual(h1, compute_header_hash(msg2))

    def test_raw_8bit_header_hashes_as_the_octets_on_the_wire(self):
        # A Latin-1 From display name Mailman never rewrites goes out as
        # the raw octets (BytesGenerator folds raw_items()).  The hash must
        # be over those octets, not the =?unknown-8bit?b?...?= encoded word
        # compat32's items() wraps them in, and the Recipe side must carry
        # the same octets.
        raw = (
            b'From: Jos\xe9 <jose@example.com>\r\n'
            b'To: list@example.com\r\n'
            b'Subject: hi\r\n'
            b'Message-ID: <x@example.com>\r\n'
            b'\r\n'
            b'body\r\n'
        )
        msg = _msg_from_bytes(raw)
        wire_bytes = msg.as_bytes()
        self.assertIn(b'From: Jos\xe9 <jose@example.com>', wire_bytes)
        expected = hashlib.sha256(
            b'from:Jos\xe9 <jose@example.com>\r\n'
            b'message-id:<x@example.com>\r\n'
            b'subject:hi\r\n'
            b'to:list@example.com\r\n').digest()
        self.assertEqual(compute_header_hash(msg), expected)
        self.assertEqual(compute_header_hash(_msg_from_bytes(wire_bytes)),
                         expected)
        self.assertEqual(dict(_collect_headers(msg))['From'],
                         _octets(b'Jos\xe9 <jose@example.com>'))
        self.assertNotIn('unknown-8bit', dict(_collect_headers(msg))['From'])

    def test_body_hash_trailing_blank_lines(self):
        raw1 = b'From: a@b.com\r\n\r\nbody\r\n'
        raw2 = b'From: a@b.com\r\n\r\nbody\r\n\r\n\r\n'
        msg1 = _msg_from_bytes(raw1)
        msg2 = _msg_from_bytes(raw2)
        self.assertEqual(compute_body_hash(msg1), compute_body_hash(msg2))


class TestBodyLines(unittest.TestCase):

    def test_trailing_empty_lines_are_not_numbered(self):
        # §6.1 canonicalization drops trailing empty lines, so a Recipe has
        # nothing to say about them and must not count them.
        msg = _msg_from_bytes(b'From: a@b.com\r\n\r\nline1\r\nline2\r\n\r\n\r\n')
        self.assertEqual(_get_body_lines(msg), ['line1', 'line2'])
        # ... while an empty line INSIDE the body is a line like any other.
        msg = _msg_from_bytes(b'From: a@b.com\r\n\r\nline1\r\n\r\nline3\r\n')
        self.assertEqual(_get_body_lines(msg), ['line1', '', 'line3'])

    def test_simple_body(self):
        msg = _msg_from_bytes(
            b'From: a@b.com\r\n\r\nline1\r\nline2\r\n')
        self.assertEqual(_get_body_lines(msg), ['line1', 'line2'])

    def test_empty_body(self):
        msg = _msg_from_bytes(b'From: a@b.com\r\n\r\n')
        self.assertEqual(_get_body_lines(msg), [])


# =====================================================================
# Unit tests for Recipe computation
# =====================================================================

class TestBodyRecipe(unittest.TestCase):

    def test_identical_bodies(self):
        lines = ['Hello', 'World']
        self.assertIsNone(compute_body_recipe(lines, lines))

    def test_pure_append(self):
        # Appended lines only: the Recipe is a single copy of the original
        # N lines, the common prefix the diff trims.
        recipe = compute_body_recipe(
            ['Hello', 'World', 'Footer appended by list'],
            ['Hello', 'World'])
        self.assertEqual(recipe, [{'c': [1, 2]}])

    def test_append_to_empty_original(self):
        # Previous body was empty; Recipe is empty (discard all appended
        # lines).
        recipe = compute_body_recipe(['Footer added by list'], [])
        self.assertEqual(recipe, [])

    def test_prepend_and_append(self):
        recipe = compute_body_recipe(
            ['Header', 'Hello', 'World', 'Footer'],
            ['Hello', 'World'])
        self.assertEqual(recipe, [{'c': [2, 3]}])

    def test_body_completely_different(self):
        recipe = compute_body_recipe(
            ['New line 1', 'New line 2'],
            ['Old line 1', 'Old line 2'])
        self.assertEqual(recipe, [{'d': ['Old line 1', 'Old line 2']}])

    def test_interleaved_changes(self):
        recipe = compute_body_recipe(
            ['A', 'X', 'B', 'Y', 'C'],
            ['A', 'B', 'C'])
        self.assertEqual(
            recipe, [{'c': [1, 1]}, {'c': [3, 3]}, {'c': [5, 5]}])

    def test_wrapped_repeated_lines_are_one_copy_and_fast(self):
        # The DKIM2 wrap puts the original body, unchanged, after a
        # preamble and part headers.  A body of identical lines (a
        # zero-filled base64 attachment) must not send the diff quadratic.
        previous = ['A' * 76] * 20000
        current = (['preamble', '', '--b', 'Content-Type: text/plain', '']
                   + previous + ['--b', 'footer', '--b--'])
        start = time.monotonic()
        recipe = compute_body_recipe(current, previous)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual(recipe, [{'c': [6, 20005]}])

    def test_non_contiguous_previous_uses_the_diff(self):
        # The previous lines are all there but not as one run: a copy
        # range for each run.
        recipe = compute_body_recipe(
            ['x', 'a', 'y', 'b', 'c', 'z'], ['a', 'b', 'c'])
        self.assertEqual(recipe, [{'c': [2, 2]}, {'c': [4, 5]}])

    def test_8bit_literal_is_a_b_step(self):
        # A restored line with any byte >= 0x80 -- Latin-1 here, which is
        # not UTF-8 and so not representable as JSON text -- is emitted as
        # a {"b": [...]} step holding the base64 of its raw octets, never
        # as a "d" string of lone surrogates.
        latin1 = b'caf\xe9 au lait'
        recipe = compute_body_recipe(['New'], ['Old ascii', _octets(latin1)])
        self.assertEqual(recipe, [{'d': ['Old ascii']},
                                  {'b': [_b64(latin1)]}])
        _assert_no_surrogates_in_json(self, recipe)
        self.assertEqual(base64.b64decode(recipe[1]['b'][0]), latin1)

    def test_utf8_literal_is_a_b_step_too(self):
        # Valid UTF-8 above 0x7f is representable as JSON text, but the rule
        # is by octet: anything >= 0x80 goes in a "b" step.
        utf8 = 'Hello w\u00f6rld'.encode('utf-8')
        recipe = compute_body_recipe(['New'], [_octets(utf8)])
        self.assertEqual(recipe, [{'b': [_b64(utf8)]}])

    def test_mixed_literals_alternate_d_and_b_steps(self):
        # Consecutive literals of one kind share a step; a mixed run
        # alternates, in order.
        latin1 = b'caf\xe9'
        utf8 = 'w\u00f6rld'.encode('utf-8')
        recipe = compute_body_recipe(
            ['New'],
            ['one', 'two', _octets(latin1), _octets(utf8), 'three'])
        self.assertEqual(recipe, [
            {'d': ['one', 'two']},
            {'b': [_b64(latin1), _b64(utf8)]},
            {'d': ['three']},
        ])
        _assert_no_surrogates_in_json(self, recipe)


class TestBodyDiff(unittest.TestCase):
    """The capped Myers line diff behind compute_body_recipe."""

    def test_shared_vectors(self):
        for name, cur, prev, max_literals, expected in BODY_DIFF_CASES:
            with self.subTest(name):
                got = _body_diff(cur, prev, max_literals)
                if got is BODY_IDENTICAL:
                    got = 'identical'
                elif got is BODY_TOO_BIG:
                    got = 'too_big'
                self.assertEqual(got, expected)

    def test_alternating_lines_are_fast_and_small(self):
        # a,b,a,b... against b,a,b,a...: one line moved from the front to
        # the end, which an unbounded LCS takes quadratic time over.
        cur = ['a', 'b'] * 2000
        prev = ['b', 'a'] * 2000
        start = time.monotonic()
        flat = _body_diff(cur, prev)
        self.assertLess(time.monotonic() - start, 0.5)
        self.assertEqual(flat, [[2, 4000], 'a'])
        self.assertEqual(compute_body_recipe(cur, prev),
                         [{'c': [2, 4000]}, {'d': ['a']}])

    def test_literal_bound_stops_the_search(self):
        # 30000 x then 30000 y against the reverse needs 30000 literals; no
        # line is unique to one side, so the search itself finds that out,
        # stopping once it passes the edits 1000 literals allow.
        cur = ['x'] * 30000 + ['y'] * 30000
        prev = ['y'] * 30000 + ['x'] * 30000
        start = time.monotonic()
        self.assertIs(_body_diff(cur, prev), BODY_TOO_BIG)
        self.assertLess(time.monotonic() - start, 10.0)
        self.assertIs(compute_body_recipe(cur, prev), NULL_BODY_RECIPE)

    def test_work_budget(self):
        # The shared vectors' pair either side of where MAX_DIFF_WORK runs
        # out: one literal line is enough for both, so only the work count
        # separates them.
        prev = ['z', 'x', 'y']
        self.assertEqual(_body_diff(['x', 'y'] * 1413 + ['z'], prev),
                         ['z', [1, 2]])
        self.assertIs(_body_diff(['x', 'y'] * 1414 + ['z'], prev),
                      BODY_TOO_BIG)

    def test_1000_literals_is_a_recipe(self):
        literals = ['old {}'.format(i) for i in range(1000)]
        recipe = compute_body_recipe(['a', 'z'], ['a'] + literals + ['z'])
        self.assertEqual(
            recipe, [{'c': [1, 1]}, {'d': literals}, {'c': [2, 2]}])

    def test_1001_literals_is_a_null_recipe(self):
        literals = ['old {}'.format(i) for i in range(1001)]
        recipe = compute_body_recipe(['a', 'z'], ['a'] + literals + ['z'])
        self.assertIs(recipe, NULL_BODY_RECIPE)

    def test_max_literals_lowers_the_cap(self):
        prev = ['a', 'x', 'y', 'z', 'c']
        self.assertIs(compute_body_recipe(['a', 'b', 'c'], prev, 2),
                      NULL_BODY_RECIPE)
        self.assertEqual(compute_body_recipe(['a', 'b', 'c'], prev, 3),
                         [{'c': [1, 1]}, {'d': ['x', 'y', 'z']},
                          {'c': [3, 3]}])

    def test_max_literals_raises_the_cap(self):
        literals = ['old {}'.format(i) for i in range(1001)]
        recipe = compute_body_recipe(
            ['a', 'z'], ['a'] + literals + ['z'], 5000)
        self.assertEqual(
            recipe, [{'c': [1, 1]}, {'d': literals}, {'c': [2, 2]}])


class TestHeaderRecipe(unittest.TestCase):

    def test_no_changes(self):
        headers = [('From', 'a@b.com'), ('To', 'c@d.com')]
        self.assertIsNone(compute_header_recipe(headers, headers))

    def test_added_header(self):
        recipe = compute_header_recipe(
            [('From', 'a@b.com'), ('List-Id', '<list.example.com>')],
            [('From', 'a@b.com')])
        self.assertEqual(recipe['list-id'], [])

    def test_removed_header(self):
        recipe = compute_header_recipe(
            [('From', 'a@b.com')],
            [('From', 'a@b.com'), ('Reply-To', 'list@example.com')])
        self.assertEqual(recipe['reply-to'], [{'d': ['list@example.com']}])

    def test_modified_header(self):
        recipe = compute_header_recipe(
            [('Subject', '[List] Hello')],
            [('Subject', 'Hello')])
        self.assertEqual(recipe['subject'], [{'d': ['Hello']}])

    def test_excluded_headers_ignored(self):
        self.assertIsNone(compute_header_recipe(
            [('From', 'a@b.com'), ('Received', 'from mx')],
            [('From', 'a@b.com'), ('Received', 'from mx2')]))

    def test_8bit_literal_is_a_b_step(self):
        # A raw EUC-KR Subject (not UTF-8) restored literally goes in a
        # "b" step as base64 of its octets.
        euckr = b'(\xb1\xa4---\xb0\xed) \xc0\xcc\xb8\xe1 500'
        recipe = compute_header_recipe(
            [('Subject', '[Ant] ' + _octets(euckr))],
            [('Subject', _octets(euckr))])
        self.assertEqual(recipe['subject'], [{'b': [_b64(euckr)]}])
        _assert_no_surrogates_in_json(self, recipe)
        self.assertEqual(base64.b64decode(recipe['subject'][0]['b'][0]),
                         euckr)

    def test_mixed_literals_alternate_d_and_b_steps(self):
        latin1 = b'Caf\xe9'
        recipe = compute_header_recipe(
            [('Comments', 'x')],
            [('Comments', 'plain'), ('Comments', _octets(latin1)),
             ('Comments', 'also plain')])
        # Bottom-up: 'also plain', latin1, 'plain'.
        self.assertEqual(recipe['comments'], [
            {'d': ['also plain']}, {'b': [_b64(latin1)]}, {'d': ['plain']},
        ])

    def test_reordered_duplicates_never_descend(self):
        # Two instances swapped.  Bottom-up the current message is
        # 1='two', 2='one'; the previous was 1='one', 2='two'.  'one' is
        # copied from instance 2, after which 'two' at instance 1 cannot
        # be copied -- spec-06 §5.1 requires "c" starts to ascend past the
        # previous end -- so it is restored literally.
        recipe = compute_header_recipe(
            [('Comments', 'one'), ('Comments', 'two')],
            [('Comments', 'two'), ('Comments', 'one')])
        self.assertEqual(recipe['comments'],
                         [{'c': [2, 2]}, {'d': ['two']}])

    def test_identical_duplicates_copy_distinct_instances(self):
        # Two identical previous values must come from two different
        # current instances: [2, 3], not [2, 2] twice.
        recipe = compute_header_recipe(
            [('Comments', 'same'), ('Comments', 'same'), ('Comments', 'x')],
            [('Comments', 'same'), ('Comments', 'same')])
        self.assertEqual(recipe['comments'], [{'c': [2, 3]}])

    def test_copy_ranges_always_ascend(self):
        # Whatever the permutation, every emitted "c" starts above the end
        # of the one before it.
        import itertools
        values = ['a', 'b', 'c', 'd']
        for perm in itertools.permutations(values):
            recipe = compute_header_recipe(
                [('Comments', v) for v in perm],
                [('Comments', v) for v in values])
            if recipe is None:
                continue
            last_end = 0
            for step in _copy_steps(recipe['comments']):
                start, end = step['c']
                self.assertGreater(start, last_end, (perm, recipe))
                self.assertGreaterEqual(end, start, (perm, recipe))
                last_end = end


class TestMIHeaderValue(unittest.TestCase):

    def test_v1_no_recipe(self):
        value = build_mi_header_value(1, b'\x00' * 32, b'\x01' * 32)
        self.assertTrue(value.startswith('m=1;'))
        self.assertIn('h=', value)
        self.assertNotIn('r=', value)

    def test_v2_with_recipe_roundtrips(self):
        value = build_mi_header_value(
            2, b'\x00' * 32, b'\x01' * 32,
            header_recipe={'list-id': []},
            body_recipe=[[2, 5]])
        self.assertIn('r=', value)
        recipe = _decode_mi_recipe(value)
        self.assertEqual(recipe['h'], {'list-id': []})
        self.assertEqual(recipe['b'], [[2, 5]])


class TestRecipeUndo(unittest.TestCase):
    """Recipe validation and application by undo / verify (spec-06 §5).

    Pure-function tests: the instance under test is built by hand on a
    fixed message, with hashes that match it, so only the Recipe decides
    whether it verifies and undoes.
    """

    RAW = (
        b'From: a@example.com\r\n'
        b'To: list@example.com\r\n'
        b'Subject: hello\r\n'
        b'Comments: one\r\n'
        b'Comments: two\r\n'
        b'Message-ID: <x@example.com>\r\n'
        b'\r\n'
        b'line one\r\n'
        b'line two\r\n'
        b'line three\r\n'
    )

    def _with_recipe(self, header_recipe=None, body_recipe=None):
        """The fixed message carrying an m=2 whose hashes match it and whose
        r= holds exactly these (possibly invalid) recipe objects."""
        msg = _msg_from_bytes(self.RAW)
        mi2 = build_mi_header_value(
            2, compute_header_hash(msg), compute_body_hash(msg),
            header_recipe, body_recipe)
        msg._headers.insert(0, ('Message-Instance', mi2))
        return msg

    def _assert_rejected(self, msg, what):
        before = msg.as_bytes()
        self.assertEqual(undo_message_instance(msg), 0, what)
        self.assertEqual(msg.as_bytes(), before,
                         'a rejected Recipe must leave the message alone')
        self.assertEqual(get_max_mi_version(msg), 2)
        version, error = verify_message_instance(msg)
        self.assertEqual(version, 0, what)
        self.assertIn('malformed Recipe', error, what)

    def test_bad_body_copy_ranges_are_rejected(self):
        cases = {
            'descending': [{'c': [2, 3]}, {'c': [1, 1]}],
            'overlapping': [{'c': [1, 2]}, {'c': [2, 3]}],
            'touching is still overlapping': [{'c': [1, 1]}, {'c': [1, 2]}],
            'zero start': [{'c': [0, 1]}],
            'end before start': [{'c': [2, 1]}],
            'past the last line': [{'c': [1, 9]}],
            'one integer': [{'c': [1]}],
            'three integers': [{'c': [1, 2, 3]}],
            'string': [{'c': [1, '2']}],
            'float': [{'c': [1, 2.0]}],
            'boolean': [{'c': [True, 1]}],
            'null': [{'c': [None, 1]}],
        }
        for what, steps in cases.items():
            with self.subTest(what):
                self._assert_rejected(
                    self._with_recipe(body_recipe=steps), what)

    def test_bad_header_copy_ranges_are_rejected(self):
        cases = {
            'descending': [{'c': [2, 2]}, {'c': [1, 1]}],
            'overlapping': [{'c': [1, 2]}, {'c': [2, 2]}],
            'zero start': [{'c': [0, 2]}],
            'past the top instance': [{'c': [1, 3]}],
            'string': [{'c': ['1', 2]}],
        }
        for what, steps in cases.items():
            with self.subTest(what):
                self._assert_rejected(
                    self._with_recipe(header_recipe={'comments': steps}),
                    what)

    def test_bad_literals_are_rejected(self):
        cases = {
            'b not base64': [{'b': ['not base64!']}],
            'b bad padding': [{'b': ['YQ']}],
            'b non-ascii': [{'b': ['caf\u00e9']}],
            'b decodes to CRLF': [{'b': [_b64(b'a\r\nb')]}],
            'b decodes to LF': [{'b': [_b64(b'a\nb')]}],
            'b decodes to CR': [{'b': [_b64(b'a\rb')]}],
            'd with LF': [{'d': ['a\nb']}],
            'd with CR': [{'d': ['a\rb']}],
            'd non-string item': [{'d': [1]}],
            'b non-string item': [{'b': [1]}],
            'd empty': [{'d': []}],
            'b empty': [{'b': []}],
        }
        for what, steps in cases.items():
            with self.subTest('body ' + what):
                self._assert_rejected(
                    self._with_recipe(body_recipe=steps), what)
            with self.subTest('header ' + what):
                self._assert_rejected(
                    self._with_recipe(header_recipe={'comments': steps}),
                    what)

    def test_bad_step_shapes_are_rejected(self):
        cases = {
            'unknown step type': [{'x': [1]}],
            'two keys': [{'c': [1, 1], 'd': ['x']}],
            'empty object': [{}],
            'number': [7],
            'c not an array': [{'c': 1}],
            'd not an array': [{'d': 'x'}],
        }
        for what, steps in cases.items():
            with self.subTest(what):
                self._assert_rejected(
                    self._with_recipe(body_recipe=steps), what)
        with self.subTest('body steps not an array'):
            self._assert_rejected(
                self._with_recipe(body_recipe={'c': [1, 1]}), 'object')
        with self.subTest('h not an object'):
            self._assert_rejected(
                self._with_recipe(header_recipe=['x']), 'array')

    def test_unparseable_r_is_rejected(self):
        msg = _msg_from_bytes(self.RAW)
        not_json = _b64(b'not json')
        msg._headers.insert(0, ('Message-Instance', 'm=2; h=sha256:{}:{}; '
                                'r={};'.format(_b64(compute_header_hash(msg)),
                                               _b64(compute_body_hash(msg)),
                                               not_json)))
        self._assert_rejected(msg, 'r= is not JSON')

    def test_b_steps_undo_to_the_original_octets(self):
        latin1 = b'caf\xe9'
        utf8 = 'w\u00f6rld'.encode('utf-8')
        msg = self._with_recipe(
            header_recipe={'subject': [{'b': [_b64(latin1)]}]},
            body_recipe=[{'c': [1, 1]}, {'b': [_b64(latin1), _b64(utf8)]}])
        self.assertEqual(verify_message_instance(msg)[0], 2)
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(_get_body_lines(msg),
                         ['line one', _octets(latin1), _octets(utf8)])
        # And the octets are what reaches the wire.  (undo re-adds a header
        # under the Recipe's lower-case key; the hash ignores case.)
        wire = msg.as_bytes().replace(b'\r\n', b'\n')
        self.assertIn(b'\nline one\ncaf\xe9\nw\xc3\xb6rld\n', wire)
        self.assertRegex(wire, rb'(?i)\nsubject: caf\xe9\n')

    def test_reordered_duplicates_undo_correctly(self):
        # The recipe compute_header_recipe emits for Comments swapped from
        # (two, one) to (one, two): copy instance 2 ('one'), then 'two'
        # literally.  Undo must give back (two, one) top-down.
        msg = self._with_recipe(
            header_recipe={'comments': [{'c': [2, 2]}, {'d': ['two']}]})
        self.assertEqual(verify_message_instance(msg)[0], 2)
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(msg.get_all('Comments'), ['two', 'one'])

    def test_null_body_recipe_leaves_the_body_alone(self):
        # spec-06 §4.2: "b": null says the body cannot be recreated.
        msg = self._with_recipe(header_recipe={'comments': []},
                                body_recipe=None)
        # build_mi_header_value omits a None body recipe; put the null in
        # by hand.
        msg = _msg_from_bytes(self.RAW)
        r = _b64(json.dumps({'h': {'comments': []}, 'b': None}).encode())
        msg._headers.insert(0, ('Message-Instance', 'm=2; h=sha256:{}:{}; '
                                'r={};'.format(_b64(compute_header_hash(msg)),
                                               _b64(compute_body_hash(msg)),
                                               r)))
        self.assertEqual(verify_message_instance(msg)[0], 2)
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(_get_body_lines(msg),
                         ['line one', 'line two', 'line three'])
        self.assertIsNone(msg.get_all('Comments'))

    def test_8bit_message_round_trips_through_b_steps(self):
        # End to end on the producer side: a message with a raw EUC-KR body
        # line and swapped Comments is changed the way a list would change
        # it; the recipe is "b"/"d"/"c" only, encodes without surrogates,
        # and undoing it restores the m=1 hashes exactly.
        original = (
            b'From: a@example.com\r\n'
            b'Subject: hello\r\n'
            b'Comments: two\r\n'
            b'Comments: one\r\n'
            b'Message-ID: <x@example.com>\r\n'
            b'Content-Type: text/plain; charset=euc-kr\r\n'
            b'Content-Transfer-Encoding: 8bit\r\n'
            b'\r\n'
            b'\xc0\xcc\xb8\xe1 line\r\n'
            b'ascii line\r\n'
        )
        orig = _msg_from_bytes(original)
        h1, b1 = compute_header_hash(orig), compute_body_hash(orig)
        msg = _msg_from_bytes(original)
        msg.replace_header('Subject', '[Ant] hello')
        del msg['Comments']
        msg['Comments'] = 'one'
        msg['Comments'] = 'two'
        msg.set_payload(msg.get_payload() + 'footer\r\n')
        header_recipe = compute_header_recipe(
            _collect_headers(msg), _collect_headers(orig))
        body_recipe = compute_body_recipe(
            _get_body_lines(msg), _get_body_lines(orig))
        self.assertEqual(body_recipe,
                         [{'b': [_b64(b'\xc0\xcc\xb8\xe1 line')]},
                          {'c': [2, 2]}])
        self.assertEqual(header_recipe['comments'],
                         [{'c': [2, 2]}, {'d': ['two']}])
        _assert_no_surrogates_in_json(
            self, {'h': header_recipe, 'b': body_recipe})
        mi2 = build_mi_header_value(
            2, compute_header_hash(msg), compute_body_hash(msg),
            header_recipe, body_recipe)
        msg._headers.insert(0, ('Message-Instance',
                                build_mi_header_value(1, h1, b1)))
        msg._headers.insert(0, ('Message-Instance', mi2))
        self.assertEqual(verify_message_instance(msg), (2, None))
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(_b64(compute_header_hash(msg)), _b64(h1))
        self.assertEqual(_b64(compute_body_hash(msg)), _b64(b1))
        self.assertEqual(verify_message_instance(msg), (1, None))

    def test_copied_raw_8bit_header_keeps_its_octets(self):
        # A raw Latin-1 From that the Recipe copies must come back out as
        # the same octets, not re-encoded, so the header hash still matches
        # the wire after undo.
        raw = (
            b'From: Jos\xe9 <jose@example.com>\r\n'
            b'To: list@example.com\r\n'
            b'Comments: one\r\n'
            b'Message-ID: <x@example.com>\r\n'
            b'\r\n'
            b'body\r\n'
        )
        msg = _msg_from_bytes(raw)
        before = compute_header_hash(msg)
        mi2 = build_mi_header_value(
            2, before, compute_body_hash(msg),
            {'from': [{'c': [1, 1]}], 'comments': [{'c': [1, 1]}]})
        msg._headers.insert(0, ('Message-Instance', mi2))
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertRegex(msg.as_bytes(),
                         rb'(?i)\nfrom: Jos\xe9 <jose@example.com>\n')
        self.assertEqual(compute_header_hash(msg), before)
        self.assertEqual(compute_header_hash(_msg_from_bytes(msg.as_bytes())),
                         before)

    def test_legacy_bare_steps_still_apply(self):
        # Pre-spec-06 bare [start, end] and bare string steps are accepted,
        # under the same range rules.
        msg = self._with_recipe(body_recipe=[[1, 1], 'legacy'])
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(_get_body_lines(msg), ['line one', 'legacy'])
        self._assert_rejected(
            self._with_recipe(body_recipe=[[2, 3], [1, 1]]), 'descending')


class TestParseMIFolding(unittest.TestCase):
    """Folded Message-Instance values must parse (spec-06 §2.12, §2.14).

    RFC 5322 FWS is CRLF followed by one *or more* WSP.  Unfolding only a
    single WSP used to leave one behind, which truncated the h= match at the
    fold and reported "could not parse h= tag".
    """

    UNFOLDED = ('m=1; h=sha256:' + 'A' * 43 + '=:' + 'B' * 43 + '=;')

    def _fold_h(self, fws):
        # Insert the fold six characters into the header hash.
        prefix, rest = self.UNFOLDED.split('h=sha256:', 1)
        return prefix + 'h=sha256:' + rest[:6] + fws + rest[6:]

    def test_unfolded_baseline(self):
        version, hashes, recipe = _parse_mi(self.UNFOLDED)
        self.assertEqual(version, 1)
        self.assertEqual(hashes['h'][1], 'A' * 43 + '=')
        self.assertEqual(hashes['b'][1], 'B' * 43 + '=')
        self.assertIsNone(recipe)

    def test_every_legal_fws_run_parses(self):
        for fws in ('\r\n\t', '\r\n ', '\r\n\t\t', '\r\n  ', '\r\n \t ',
                    '\n\t', '\n  '):
            with self.subTest(fws=repr(fws)):
                version, hashes, _ = _parse_mi(self._fold_h(fws))
                self.assertEqual(version, 1)
                self.assertIsNotNone(
                    hashes, 'h= tag did not parse across the fold')
                self.assertEqual(hashes['h'][1], 'A' * 43 + '=')
                self.assertEqual(hashes['b'][1], 'B' * 43 + '=')

    def test_value_unfolded_to_a_space_parses(self):
        # verify_mi_raw takes the value from the raw octets, unfolded the
        # RFC 5322 way: the fold becomes WSP inside the base64 hash.
        for wsp in (' ', '\t', '  '):
            with self.subTest(wsp=repr(wsp)):
                _, hashes, _ = _parse_mi(self._fold_h(wsp))
                self.assertIsNotNone(hashes, 'h= tag did not parse')
                self.assertEqual(hashes['h'][1], 'A' * 43 + '=')
                self.assertEqual(hashes['b'][1], 'B' * 43 + '=')

    def test_folded_recipe_parses(self):
        value = build_mi_header_value(
            2, b'\x00' * 32, b'\x01' * 32, header_recipe={'subject': []})
        r_start = value.index('r=') + 2
        folded = value[:r_start + 8] + '\r\n  ' + value[r_start + 8:]
        _, _, recipe = _parse_mi(folded)
        self.assertEqual(recipe['h'], {'subject': []})


class TestParseMISyntax(unittest.TestCase):
    """The whole Message-Instance value is a tag-list (spec-06 §7, §7.3).

    A fragment that is not a tag, a byte outside x-tag-char, a repeated
    tag or a malformed hash-set makes the field a syntax error; it is
    never skipped with the rest verified.
    """

    H = 'sha256:' + 'A' * 43 + '=:' + 'B' * 43 + '='

    def _ok(self, value):
        return _parse_mi(value)

    def _bad(self, value):
        with self.assertRaises(MalformedInstance) as cm:
            _parse_mi(value)
        return str(cm.exception)

    def test_baseline_and_tolerated_forms(self):
        for value in ('m=1; h={};'.format(self.H),
                      'm=1;; h={};;'.format(self.H),
                      ' m = 1 ; h = {} ; x_ext9=a b'.format(self.H),
                      'M=1; H={}'.format(self.H),
                      'm=1; h=sha256 :{}'.format(self.H[7:]),
                      'm=1; h= sha256\r\n\t:{}'.format(self.H[7:]),
                      'm=1; h=sha512:AAAA:AAAA, {}'.format(self.H)):
            with self.subTest(value=value):
                version, hashes, _ = self._ok(value)
                self.assertEqual(version, 1)
                self.assertEqual(hashes['h'], ['sha256', 'A' * 43 + '='])
                self.assertEqual(hashes['b'], ['sha256', 'B' * 43 + '='])

    def test_bad_fragments(self):
        for frag in ('junk', '9bad=foo', '=v', 'x=a\x00b', 'x=a\x7fb',
                     'x=caf\xe9', 'x=caf\udce9', 'x=a\nb', 'x-y=1'):
            with self.subTest(frag=frag):
                self.assertEqual(
                    self._bad('m=1; h={}; {};'.format(self.H, frag)),
                    'Message-Instance m=1 syntax error')

    def test_repeated_tag(self):
        for value in ('m=1; h={0}; h={0}', 'm=1; h={0}; H={0}',
                      'm=1; M=1; h={0}'):
            with self.subTest(value=value):
                self._bad(value.format(self.H))

    def test_bad_hash_sets(self):
        for hset in ('sha 256:{}', 'sha\r\n 256:{}', 'sha256:{}:'):
            with self.subTest(hset=hset):
                self._bad('m=1; h=' + hset.format(self.H[7:]))
        self._bad('m=1; h=sha256:' + 'A' * 43 + '=')
        self._bad('m=1; h={0},{0}'.format(self.H))

    def test_verify_mi_raw_reports_syntax_error(self):
        # A correctly hashed instance with a junk fragment does not verify.
        plain = b'From: a@example.com\r\nSubject: hi\r\n\r\nHello.\r\n'
        mi1 = build_mi_header_value(1, compute_header_hash_raw(plain),
                                    compute_body_hash_raw(plain))
        good = b'Message-Instance: ' + mi1.encode('ascii') + b'\r\n' + plain
        self.assertEqual(verify_mi_raw(good), (1, None))
        bad = good.replace(b'm=1;', b'm=1; junk;', 1)
        self.assertEqual(verify_mi_raw(bad),
                         (0, 'Message-Instance m=1 syntax error'))


class TestGetMaxMIVersion(unittest.TestCase):

    def test_no_mi(self):
        msg = _msg_from_bytes(b'From: a@b.com\r\n\r\nbody\r\n')
        self.assertEqual(get_max_mi_version(msg), 0)

    def test_single_mi(self):
        msg = _msg_from_bytes(b'From: a@b.com\r\n\r\nbody\r\n')
        msg['Message-Instance'] = 'm=1; h=abc'
        self.assertEqual(get_max_mi_version(msg), 1)

    def test_multiple_mi(self):
        msg = _msg_from_bytes(b'From: a@b.com\r\n\r\nbody\r\n')
        msg['Message-Instance'] = 'm=1; h=abc'
        msg['Message-Instance'] = 'm=3; h=def'
        msg['Message-Instance'] = 'm=2; h=ghi'
        self.assertEqual(get_max_mi_version(msg), 3)


def _old_fold_mi_value(value, target=72, hard_max=77):
    """The original (quadratic) _fold_mi_value, as the reference output."""
    name_len = len('Message-Instance: ')
    first_target, first_max = target - name_len, hard_max - name_len
    cont_target, cont_max = target - 8, hard_max - 8
    lines = []
    current = value
    tgt, mx = first_target, first_max
    while len(current) > tgt + 2:
        brk_min = 10 if not lines else tgt - 5
        break_at = current.rfind('; ', brk_min, mx)
        if break_at > 0:
            lines.append(current[:break_at + 1])
            current = current[break_at + 2:]
        else:
            lines.append(current[:tgt])
            current = current[tgt:]
        tgt, mx = cont_target, cont_max
    if current:
        lines.append(current)
    return '\r\n\t'.join(lines)


class TestFoldMIValue(unittest.TestCase):
    """_fold_mi_value is linear and folds as it always did."""

    def _values(self):
        b64 = base64.b64encode(bytes(range(256)) * 40).decode('ascii')
        yield ''
        yield 'm=1;'
        yield 'm=1; h=sha256:' + 'A' * 43 + '=:' + 'B' * 43 + '=;'
        for n in (1, 2, 3, 50, 63, 64, 65, 66, 67, 200, len(b64)):
            yield ('m=2; h=sha256:' + 'A' * 43 + '=:' + 'B' * 43 +
                   '=; r=' + b64[:n] + ';')
        yield '; '.join('t{}=v{}'.format(i, 'x' * (i % 70))
                        for i in range(300))

    def test_same_output_as_before(self):
        for value in self._values():
            with self.subTest(length=len(value)):
                self.assertEqual(_fold_mi_value(value),
                                 _old_fold_mi_value(value))

    def test_large_value_is_fast_and_sane(self):
        b64 = base64.b64encode(os.urandom(1536 * 1024)).decode('ascii')
        value = ('m=2; h=sha256:' + 'A' * 43 + '=:' + 'B' * 43 +
                 '=; r=' + b64 + ';')
        self.assertGreater(len(value), 2 * 1024 * 1024)
        start = time.monotonic()
        folded = _fold_mi_value(value)
        self.assertLess(time.monotonic() - start, 1)
        lines = folded.split('\r\n\t')
        self.assertLessEqual(len('Message-Instance: ') + len(lines[0]), 77)
        for line in lines[1:]:
            self.assertLessEqual(8 + len(line), 77)
        # A break at a tag boundary drops the space after the ";".
        unfolded = re.sub(r';\r\n\t', '; ', folded).replace('\r\n\t', '')
        self.assertEqual(unfolded, value)


class TestDraftVersion(unittest.TestCase):

    def test_draft_version_is_06(self):
        from mailman.handlers.message_instance import DKIM2_DRAFT, DKIM2_DATE
        self.assertEqual(DKIM2_DRAFT, 'ietf-dkim-dkim2-spec-06')
        self.assertEqual(DKIM2_DATE, '2026-10-07')


# =====================================================================
# Header hashing over the wire form (need ConfigLayer)
# =====================================================================

class TestHeaderWireForm(unittest.TestCase):
    """The header hash and Recipe see the headers as they go on the wire."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.preferred_language = 'en'
        config.push('test_mi', """\
        [mta]
        message_instance: yes
        """)
        self.addCleanup(config.pop, 'test_mi')

    def test_header_hash_covers_the_wire_form_of_an_encoded_subject(self):
        # subject_prefix stores a non-ASCII Subject as a Header object; the
        # hash must be over what the generator emits (RFC 2047 encoded
        # words), not str(Header), which is the decoded text.
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Subject: =?ISO-2022-JP?B?UmU6IBskQiUkJXMlLSVlJVkhPCU/QjQ2SD1gSHcbKEI=?=\r\n'
            b'Content-Type: text/plain; charset=iso-2022-jp\r\n'
            b'Content-Transfer-Encoding: 7bit\r\n'
            b'\r\n'
            b'\x1b$B%F%9%H\x1b(B\r\n'
        )
        msg = _msg_from_bytes(raw)
        self._mlist.subject_prefix = '[Ant] '
        config.handlers['subject-prefix'].process(self._mlist, msg, {})
        self.assertNotIsInstance(msg['subject'], str)
        wire = email.message_from_bytes(msg.as_bytes(), Message)
        self.assertIn(b'=?iso-2022-jp?', msg.as_bytes())
        self.assertEqual(compute_header_hash(msg), compute_header_hash(wire))
        # And the Recipe side sees the same value the verifier will.
        collected = dict(_collect_headers(msg))
        self.assertEqual(collected['Subject'], str(wire['subject']))

    def test_collected_header_values_are_unfolded(self):
        # A long encoded-word Subject arrives folded; the parser keeps the
        # fold in the value.  Recipe "d" strings MUST NOT contain CR or LF
        # (spec-06 §5.1): a literal with a fold inside it verified in two
        # implementations and failed in three.
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Subject: =?UTF-8?B?UmU6IEZsaW5rS2Fma2FQcm9kdWNlciDlvIDlkK9FeGNhdGx5IE9uY2U=?=\r\n'
            b' =?UTF-8?B?5LmL5ZCOIOWIneWni+WMluS6i+WKoeeKtuaAgei2heaXtueahOmXrumimA==?=\r\n'
            b'\r\n'
            b'body\r\n'
        )
        msg = _msg_from_bytes(raw)
        for name, value in _collect_headers(msg):
            self.assertNotIn('\n', value, name)
            self.assertNotIn('\r', value, name)
        subject = dict(_collect_headers(msg))['Subject']
        self.assertIn('?= =?UTF-8?B?', subject)

    def test_header_hash_covers_the_wire_form_of_a_raw_8bit_subject(self):
        # Raw non-UTF-8 bytes in a Subject (2003-era spam, some current
        # senders): the prefix handler re-encodes it as unknown-8bit, and
        # the hash must follow the bytes actually sent.
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Subject: (\xb1\xa4---\xb0\xed) \xc0\xcc\xb8\xe1 500\r\n'
            b'Content-Type: text/plain; charset=euc-kr\r\n'
            b'\r\n'
            b'body\r\n'
        )
        msg = _msg_from_bytes(raw)
        self._mlist.subject_prefix = '[Ant] '
        config.handlers['subject-prefix'].process(self._mlist, msg, {})
        wire = email.message_from_bytes(msg.as_bytes(), Message)
        self.assertEqual(compute_header_hash(msg), compute_header_hash(wire))


# =====================================================================
# Integration tests for the full MI flow with undo verification
# =====================================================================

class TestMessageInstanceFlow(unittest.TestCase):
    """Test MI ingress → modification → egress → undo → verify."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.preferred_language = 'en'
        temporary_dir = TemporaryDirectory()
        self.addCleanup(temporary_dir.cleanup)
        template_dir = temporary_dir.name
        config.push('test_mi', """\
        [paths.testing]
        template_dir: {}
        [mta]
        message_instance: yes
        """.format(template_dir))
        self.addCleanup(config.pop, 'test_mi')
        site_dir = os.path.join(config.TEMPLATE_DIR, 'site', 'en')
        os.makedirs(site_dir)
        footer_path = os.path.join(site_dir, 'myfooter.txt')
        with open(footer_path, 'w', encoding='utf-8') as fp:
            fp.write('-- \nList Footer\n')
        getUtility(ITemplateManager).set(
            'list:member:regular:footer', None, 'mailman:///myfooter.txt')
        self._ingress = config.handlers['message-instance-ingress']
        self._egress = config.handlers['message-instance-egress']

    def _make_7bit_msg(self):
        return mfs("""\
To: ant@example.com
From: aperson@example.com
Message-ID: <alpha>
Content-Type: text/plain; charset=us-ascii
Content-Transfer-Encoding: 7bit

Hello world.
This is a test.
""")

    # -----------------------------------------------------------------
    # Basic ingress tests
    # -----------------------------------------------------------------

    # A multipart message whose octets Python's generator will not
    # reproduce: a part header with a trailing space, and a final boundary
    # with no line ending.  What arrived is what the inbound signer hashed.
    RECEIVED = (
        b'To: ant@example.com\r\n'
        b'From: aperson@example.com\r\n'
        b'Message-ID: <alpha>\r\n'
        b'Subject: as received\r\n'
        b'MIME-Version: 1.0\r\n'
        b'Content-Type: multipart/alternative; boundary="b"\r\n'
        b'\r\n'
        b'--b\r\n'
        b'Content-Type: text/plain; charset="us-ascii" \r\n'
        b'\r\n'
        b'Hello world.\r\n'
        b'--b\r\n'
        b'Content-Type: text/html; charset="us-ascii"\r\n'
        b'\r\n'
        b'<p>Hello world.</p>\r\n'
        b'--b--')

    def _make_received_msg(self):
        msg = _msg_from_bytes(self.RECEIVED)
        msg.original_bytes = self.RECEIVED      # as the LMTP runner does
        return msg

    def test_ingress_hashes_the_received_octets(self):
        msg = self._make_received_msg()
        # The premise: re-serializing is not byte-faithful here.
        self.assertNotEqual(msg.as_bytes().replace(b'\n', b'\r\n')
                            .replace(b'\r\r\n', b'\r\n'), self.RECEIVED)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        _, hashes, _ = _parse_mi(msg['message-instance'])
        self.assertEqual(hashes['h'][1],
                         _b64(compute_header_hash_raw(self.RECEIVED)))
        self.assertEqual(hashes['b'][1],
                         _b64(compute_body_hash_raw(self.RECEIVED)))
        self.assertEqual(msg.original_bytes, self.RECEIVED)

    def test_ingress_sets_original_bytes_without_received_octets(self):
        msg = self._make_7bit_msg()
        self.assertIsNone(getattr(msg, 'original_bytes', None))
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertIsInstance(msg.original_bytes, bytes)
        self.assertIn(b'\r\nHello world.\r\n', msg.original_bytes)
        self.assertNotIn('mi_file', msgdata['mi_snapshot'])

    def test_no_mi_cache_directory(self):
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 2)
        self.assertFalse(
            os.path.exists(os.path.join(config.VAR_DIR, 'mi-cache')))

    def test_snapshot_survives_a_queue_pickle(self):
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msg, msgdata = pickle.loads(pickle.dumps((msg, msgdata)))
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)

    def test_recipe_rebuilds_the_received_octets(self):
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        _, hashes1, _ = _parse_mi(msg['message-instance'])
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 2)
        m2 = [v for v in msg.get_all('message-instance')
              if _get_mi_version(v) == 2][0]
        _, _, recipe = _parse_mi(m2)
        body_lines, headers = _plan_undo(msg, recipe)
        # Applied to what Mailman is about to send, the Recipe gives back
        # the octets that arrived, trailing space and bare boundary included.
        self.assertEqual(body_lines, _body_lines_raw(self.RECEIVED))
        pairs = []
        seen = set()
        for name, value in _collect_headers(msg):
            lname = name.lower()
            if lname in headers:
                if lname not in seen:
                    seen.add(lname)
                    pairs.extend((lname, v) for v in headers[lname])
            else:
                pairs.append((name, value))
        for lname, values in headers.items():
            if lname not in seen:
                pairs.extend((lname, v) for v in values)
        self.assertEqual(_b64(_hash_header_pairs(pairs)), hashes1['h'][1])
        rebuilt_body = ('\r\n'.join(body_lines) + '\r\n').encode(
            'utf-8', 'surrogateescape')
        self.assertEqual(
            _b64(hashlib.sha256(rebuilt_body).digest()), hashes1['b'][1])

    def test_egress_records_bcc_removal(self):
        # smtplib.send_message drops Bcc; egress removes it first so the
        # m=2 Recipe says so and undoing it gives back what arrived.
        raw = (b'To: ant@example.com\r\n'
               b'From: aperson@example.com\r\n'
               b'Bcc: \r\n'
               b'Resent-Bcc: hidden@example.com\r\n'
               b'Message-ID: <alpha>\r\n'
               b'Subject: has a Bcc\r\n'
               b'\r\n'
               b'Hello world.\r\n')
        msg = _msg_from_bytes(raw)
        msg.original_bytes = raw
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        _, hashes1, _ = _parse_mi(msg['message-instance'])
        self._egress.process(self._mlist, msg, msgdata)
        self.assertIsNone(msg['bcc'])
        self.assertIsNone(msg['resent-bcc'])
        m2 = [v for v in msg.get_all('message-instance')
              if _get_mi_version(v) == 2][0]
        _, hashes2, recipe = _parse_mi(m2)
        self.assertEqual(recipe['h']['bcc'], [{'d': ['']}])
        self.assertEqual(recipe['h']['resent-bcc'],
                         [{'d': ['hidden@example.com']}])
        # m=2 describes the message as it will be sent ...
        self.assertEqual(hashes2['h'][1], _b64(compute_header_hash(msg)))
        # ... and its Recipe rebuilds m=1.
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(_b64(compute_header_hash(msg)), hashes1['h'][1])

    def test_egress_after_a_retry_adds_no_second_instance(self):
        # BulkDelivery decorates and runs egress on the queued message
        # itself; a temporary failure re-enqueues that stamped message to
        # the retry queue (with nodecorate), and egress runs again on it.
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 2)
        msg, msgdata = pickle.loads(pickle.dumps((msg, msgdata)))
        msgdata['nodecorate'] = True
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(len(msg.get_all('message-instance')), 2)
        self.assertEqual(len(msg.get_all('x-dkim2-info')), 2)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        self.assertEqual(undo_message_instance(msg), 2)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_ingress_drops_received_octets_when_the_list_opts_out(self):
        # A list with Message-Instance off on a site with it on must not
        # carry the received octets through every queue pickle.
        self._mlist.dkim2_message_instance = False
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertFalse(hasattr(msg, 'original_bytes'))
        self.assertEqual(get_max_mi_version(msg), 0)
        self.assertNotIn('mi_snapshot', msgdata)

    def test_ingress_without_received_octets_still_works(self):
        # A message Mailman made itself, or one from an older queue entry,
        # has no original_bytes: the parsed form is the baseline, as before.
        msg = self._make_7bit_msg()
        self.assertFalse(hasattr(msg, 'original_bytes'))
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        version, error = verify_message_instance(msg)
        self.assertEqual((version, error), (1, None))

    def test_injected_message_stamp_goes_into_the_recipe(self):
        # `mailman inject` stamps Message-ID-Hash before the pipeline and
        # keeps no received octets.  The snapshot leaves Mailman's stamp out,
        # as the LMTP runner's octets do, so m=1 describes the post without
        # it and m=2 records it.
        msg = self._make_7bit_msg()
        add_message_hash(msg)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        self.assertIn('message-id-hash', _recipe_at(msg, 2)['h'])
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertNotIn('Message-ID-Hash', msg)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_injected_message_with_an_instance_records_the_stamp(self):
        # An injected message whose existing instance covers content with no
        # Message-ID-Hash: Mailman's stamp goes into the egress Recipe, and
        # undoing it gets back to the existing instance.
        msg = self._make_7bit_msg()
        msg['Message-Instance'] = build_mi_header_value(
            1, compute_header_hash(msg), compute_body_hash(msg))
        add_message_hash(msg)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        self.assertIn('message-id-hash', _recipe_at(msg, 2)['h'])
        self.assertEqual(undo_message_instance(msg), 2)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_injected_message_with_its_own_stamp_under_the_instance(self):
        # A sender put a Message-ID-Hash under its own instance and the
        # message came in without received octets.  The snapshot leaves
        # every Message-ID-Hash out, so the instance does not match it:
        # that is logged, and egress still adds an m=2 that verifies.
        msg = self._make_7bit_msg()
        add_message_hash(msg)
        msg['Message-Instance'] = build_mi_header_value(
            1, compute_header_hash(msg), compute_body_hash(msg))
        msgdata = {}
        with self.assertLogs('mailman.dkim2', 'WARNING') as logs:
            self._ingress.process(self._mlist, msg, msgdata)
        self.assertIn('Existing Message-Instance m=1 does not match',
                      logs.output[0])
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)

    def test_ingress_adds_v1(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 1)
        self.assertIn('mi_snapshot', msgdata)

    def test_ingress_info_is_debug_header_01_form(self):
        # draft-gondwana-dkim2-debug-header-01: a tag-list in the DKIM2
        # syntax, every tag followed by ';', and the action is mi-m=<N>.
        msg = self._make_7bit_msg()
        self._ingress.process(self._mlist, msg, {})
        # Unfold, then drop the whitespace a consumer ignores next to ';'
        # and ','.
        info = re.sub(r'([;,])\s+', r'\1', msg['x-dkim2-info']
                      .replace('\r\n\t', ' '))
        info = info.replace(';', '; ').rstrip()
        self.assertTrue(info.endswith(';'), info)
        self.assertRegex(info, r'^draft=\S+; repo=\S+; date=\d{4}-\d\d-\d\d; '
                               r'sw=mailman; action=mi-m=1; hc=\d+; hn=\S+;$')

    def test_ingress_v1_verifies(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        version, error = verify_message_instance(msg)
        self.assertEqual(version, 1)
        self.assertIsNone(error)

    def test_ingress_does_not_add_v1_if_mi_exists(self):
        """A message arriving with an existing MI must not get a second m=1."""
        msg = self._make_7bit_msg()
        # Simulate a message that already has MI m=1 from a prior hop.
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        existing_mi = build_mi_header_value(1, h_hash, b_hash)
        msg['Message-Instance'] = existing_mi
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # Should still be exactly one MI header.
        mi_values = msg.get_all('message-instance')
        self.assertEqual(len(mi_values), 1)
        # And it should be the original one.
        self.assertEqual(str(mi_values[0]), existing_mi)
        # Snapshot should still be created.
        self.assertIn('mi_snapshot', msgdata)

    def test_ingress_keeps_corrupted_mi_and_flags_it_stale(self):
        """A stale incoming MI is preserved, not replaced.

        An arriving Message-Instance may be covered by a DKIM2-Signature, so
        ingress must never rewrite or drop it -- doing so would destroy the
        evidence that the chain is broken and let Mailman silently pose as the
        originator.  The mismatch is logged, no second instance is invented,
        and no X-DKIM2-Info is added: that field records actions which add a
        header, and accepting an existing instance adds none.
        """
        msg = self._make_7bit_msg()
        # Build a well-formed MI m=1 but corrupt the hashes.
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        good_mi = build_mi_header_value(1, h_hash, b_hash)
        corrupt_mi = good_mi.replace(_b64(h_hash), 'AAAA', 1).replace(
            _b64(b_hash), 'BBBB', 1)
        msg['Message-Instance'] = corrupt_mi
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # The corrupt instance is still there, untouched, and alone.
        mi_values = msg.get_all('message-instance')
        self.assertEqual(len(mi_values), 1,
                         'ingress must not add a second instance')
        self.assertEqual(str(mi_values[0]), corrupt_mi,
                         'ingress must not rewrite an incoming instance')
        # It still does not verify -- that is the point.
        version, error = verify_message_instance(msg)
        self.assertEqual(version, 0)
        self.assertIsNotNone(error)
        # No X-DKIM2-Info is emitted for accepting an existing instance.
        self.assertEqual(msg.get_all('x-dkim2-info', []), [])
        # Snapshot must still be present.
        self.assertIn('mi_snapshot', msgdata)

    def test_ingress_skips_digest(self):
        msg = self._make_7bit_msg()
        msgdata = {'isdigest': True}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 0)

    def test_ingress_runs_with_nodecorate(self):
        # MI should still be added even when nodecorate is set (e.g.
        # owner pipeline messages).  nodecorate controls decoration,
        # not MI.
        msg = self._make_7bit_msg()
        msgdata = {'nodecorate': True}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 1)

    # -----------------------------------------------------------------
    # Basic egress tests
    # -----------------------------------------------------------------

    def test_egress_no_change_no_new_mi(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 1)

    def test_egress_without_snapshot_adds_originator_v1(self):
        """Egress adds MI m=1 for originated messages (no snapshot)."""
        msg = self._make_7bit_msg()
        self._egress.process(self._mlist, msg, {})
        self.assertEqual(get_max_mi_version(msg), 1)

    def test_egress_adds_v2_after_decoration(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 2)

    def test_egress_v2_verifies(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        version, error = verify_message_instance(msg)
        self.assertEqual(version, 2, error)

    # -----------------------------------------------------------------
    # Undo tests: undo MI m=2, verify MI m=1
    # -----------------------------------------------------------------

    def test_undo_7bit_then_v1_verifies(self):
        """After undoing MI m=2, MI m=1 must verify against the message."""
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        # Sanity: m=2 verifies before undo.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        # Undo MI m=2.
        undone = undo_message_instance(msg)
        self.assertEqual(undone, 2)
        self.assertEqual(get_max_mi_version(msg), 1)
        # MI m=1 must now verify.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_undo_7bit_with_added_headers_then_v1_verifies(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msg['List-Id'] = '<ant.example.com>'
        msg['List-Unsubscribe'] = '<mailto:ant-leave@example.com>'
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        undo_message_instance(msg)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_undo_qp_unnecessarily_quoted_then_v1_verifies(self):
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=us-ascii\r\n'
            b'Content-Transfer-Encoding: quoted-printable\r\n'
            b'\r\n'
            b'=48=65=6C=6C=6F =57=6F=72=6C=64=2E\r\n'
            b'=54=68=69=73 =69=73 =61 =74=65=73=74=2E\r\n'
        )
        msg = _msg_from_bytes(raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        # Undo and verify m=1.
        undo_message_instance(msg)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_undo_base64_weird_lines_then_v1_verifies(self):
        content = b'Base64 with 50-char lines for DKIM2 MI undo test.'
        b64 = base64.b64encode(content).decode('ascii')
        b64_50 = '\r\n'.join(
            b64[i:i+50] for i in range(0, len(b64), 50))
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            b'Content-Transfer-Encoding: base64\r\n'
            b'\r\n'
            + b64_50.encode('ascii') + b'\r\n'
        )
        msg = _msg_from_bytes(raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        # Undo and verify m=1.
        undo_message_instance(msg)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_undo_base64_very_short_lines_then_v1_verifies(self):
        content = b'Short 20-char lines.'
        b64 = base64.b64encode(content).decode('ascii')
        b64_20 = '\r\n'.join(
            b64[i:i+20] for i in range(0, len(b64), 20))
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            b'Content-Transfer-Encoding: base64\r\n'
            b'\r\n'
            + b64_20.encode('ascii') + b'\r\n'
        )
        msg = _msg_from_bytes(raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        undo_message_instance(msg)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    # -----------------------------------------------------------------
    # Pre-existing MI: message arrives with MI m=1 from prior hop
    # -----------------------------------------------------------------

    def test_existing_mi_preserved_and_v2_added(self):
        """A message with an existing MI m=1 should get MI m=2 at egress."""
        msg = self._make_7bit_msg()
        # Add MI m=1 externally (simulating a prior hop).
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        external_mi = build_mi_header_value(1, h_hash, b_hash)
        msg['Message-Instance'] = external_mi
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # Ingress should NOT add another MI.
        self.assertEqual(len(msg.get_all('message-instance')), 1)
        # Modify and egress.
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        # Should now have m=1 and m=2.
        self.assertEqual(get_max_mi_version(msg), 2)
        self.assertEqual(len(msg.get_all('message-instance')), 2)
        # m=2 should verify.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)

    def test_existing_mi_undo_v2_then_v1_verifies(self):
        """Undo MI m=2 on a message that arrived with MI m=1."""
        msg = self._make_7bit_msg()
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        external_mi = build_mi_header_value(1, h_hash, b_hash)
        msg['Message-Instance'] = external_mi
        # Verify the external MI m=1 is valid.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        msg['List-Id'] = '<ant.example.com>'
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        # Undo m=2 → message should revert to the state where m=1 verifies.
        undo_message_instance(msg)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_existing_mi_undo_v2_restores_original_body(self):
        """After undo, the body must match what MI m=1 was computed over."""
        msg = self._make_7bit_msg()
        original_body_hash = compute_body_hash(msg)
        h_hash = compute_header_hash(msg)
        external_mi = build_mi_header_value(1, h_hash, original_body_hash)
        msg['Message-Instance'] = external_mi
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        # Body has changed (footer added).
        self.assertNotEqual(compute_body_hash(msg), original_body_hash)
        # Undo.
        undo_message_instance(msg)
        # Body hash must match original.
        self.assertEqual(compute_body_hash(msg), original_body_hash)

    def test_existing_mi_undo_v2_restores_original_headers(self):
        """After undo, the header hash must match what MI m=1 covered."""
        msg = self._make_7bit_msg()
        original_header_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        external_mi = build_mi_header_value(1, original_header_hash, b_hash)
        msg['Message-Instance'] = external_mi
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msg['List-Id'] = '<ant.example.com>'
        msg['List-Unsubscribe'] = '<mailto:ant-leave@example.com>'
        self._egress.process(self._mlist, msg, msgdata)
        # Headers changed.
        self.assertNotEqual(compute_header_hash(msg), original_header_hash)
        # Undo.
        undo_message_instance(msg)
        self.assertEqual(compute_header_hash(msg), original_header_hash)

    # -----------------------------------------------------------------
    # Config and originator tests
    # -----------------------------------------------------------------

    def test_config_disabled_skips_ingress(self):
        config.push('mi_off', '[mta]\nmessage_instance: no')
        try:
            msg = self._make_7bit_msg()
            msgdata = {}
            self._ingress.process(self._mlist, msg, msgdata)
            self.assertEqual(get_max_mi_version(msg), 0)
            self.assertNotIn('mi_snapshot', msgdata)
        finally:
            config.pop('mi_off')

    def test_config_disabled_skips_egress(self):
        # First add MI with config enabled (setUp already enables it).
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        # Disable config before egress.
        config.push('mi_off', '[mta]\nmessage_instance: no')
        try:
            self._egress.process(self._mlist, msg, msgdata)
            # Should still be m=1 only — egress skipped.
            self.assertEqual(get_max_mi_version(msg), 1)
        finally:
            config.pop('mi_off')

    def test_egress_adds_originator_v1_without_snapshot(self):
        """Messages without a snapshot (e.g. VirginPipeline) get MI m=1."""
        msg = self._make_7bit_msg()
        msgdata = {}  # No snapshot — simulates VirginPipeline path.
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 1)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_egress_originator_skips_if_mi_exists(self):
        """Egress originator path does not duplicate existing MI."""
        msg = self._make_7bit_msg()
        msg['Message-Instance'] = 'm=1; h=existing'
        msgdata = {}
        self._egress.process(self._mlist, msg, msgdata)
        # Should not have added another MI header.
        mi_values = msg.get_all('message-instance')
        self.assertEqual(len(mi_values), 1)

    def test_mi_header_is_prepended(self):
        """MI headers should appear at the top, not the bottom.

        X-DKIM2-Info is prepended after the instance it describes, so it ends
        up above it.  That is fine -- it is our own diagnostic header and is
        excluded from the header hash.  What matters is that the newest
        Message-Instance precedes every header of the original message.
        """
        def first_mi_index(msg):
            keys = msg.keys()
            for i, name in enumerate(keys):
                if name.lower() == 'message-instance':
                    return i, keys
            self.fail('no Message-Instance header found')

        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        idx, keys = first_mi_index(msg)
        # Only X-DKIM2-Info may sit above it.
        self.assertTrue(
            all(k.lower() == 'x-dkim2-info' for k in keys[:idx]),
            f'unexpected headers above Message-Instance: {keys[:idx]}')
        # And it must precede the original message's own headers.
        self.assertNotIn('From', keys[:idx])

        # After egress with modifications, the new instance is also on top.
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        idx, keys = first_mi_index(msg)
        self.assertTrue(
            all(k.lower() == 'x-dkim2-info' for k in keys[:idx]),
            f'unexpected headers above Message-Instance: {keys[:idx]}')
        self.assertNotIn('From', keys[:idx])

    def test_mi_header_line_lengths(self):
        """All MI header lines should be under 78 characters."""
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msg['List-Id'] = '<ant.example.com>'
        msg['List-Unsubscribe'] = '<mailto:ant-leave@example.com>'
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        raw = msg.as_string()
        for line in raw.split('\n'):
            if 'Message-Instance' in line or line.startswith('\t'):
                self.assertLessEqual(
                    len(line), 78,
                    f'MI header line too long ({len(line)}): {line!r}')

    def test_mi_header_no_orphaned_tails(self):
        """Folded MI values should not have orphaned 1-2 char tails."""
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        for val in msg.get_all('message-instance', []):
            val_str = str(val)
            if '\t' in val_str:
                # Check each continuation line.
                parts = val_str.split('\t')
                for part in parts[1:]:
                    stripped = part.strip()
                    if stripped:
                        self.assertGreater(
                            len(stripped), 2,
                            f'Orphaned tail in MI header: {stripped!r}')

    def test_mi_header_no_leading_semicolon(self):
        """Continuation lines should not start with a semicolon."""
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msg['List-Id'] = '<ant.example.com>'
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        for val in msg.get_all('message-instance', []):
            val_str = str(val)
            if '\t' in val_str:
                parts = val_str.split('\t')
                for part in parts[1:]:
                    stripped = part.strip()
                    self.assertFalse(
                        stripped.startswith(';'),
                        f'Continuation starts with semicolon: {stripped!r}')


class TestNullBodyRecipe(unittest.TestCase):
    """A body Mailman rewrote before decoration gets "b": null."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        config.push('test_mi_null', """\
        [mta]
        message_instance: yes
        """)
        self.addCleanup(config.pop, 'test_mi_null')
        self._ingress = config.handlers['message-instance-ingress']
        self._egress = config.handlers['message-instance-egress']

    RAW = (b'To: ant@example.com\r\nFrom: aperson@example.com\r\n'
           b'Message-ID: <alpha>\r\nMIME-Version: 1.0\r\n'
           b'Content-Type: multipart/mixed; boundary="b"\r\n\r\n'
           b'--b\r\nContent-Type: text/plain\r\n\r\nhello\r\n'
           b'--b\r\nContent-Type: application/x-kitten\r\n'
           b'Content-Transfer-Encoding: base64\r\n\r\nAAAA\r\n--b--\r\n')

    def _msg(self):
        msg = _msg_from_bytes(self.RAW)
        msg.original_bytes = self.RAW
        return msg

    def _filtered_and_egressed(self):
        msg, msgdata = self._msg(), {}
        self._ingress.process(self._mlist, msg, msgdata)
        self._mlist.filter_content = True
        self._mlist.filter_types = ['application/x-kitten']
        config.handlers['mime-delete'].process(self._mlist, msg, msgdata)
        msg['Subject'] = '[ant] hi'
        self._egress.process(self._mlist, msg, msgdata)
        return msg

    def test_mime_delete_sets_body_modified(self):
        self._mlist.filter_content = True
        self._mlist.filter_types = ['application/x-kitten']
        msg, msgdata = self._msg(), {}
        config.handlers['mime-delete'].process(self._mlist, msg, msgdata)
        self.assertTrue(msgdata.get('body-modified'))

    def test_mime_delete_unchanged_leaves_flag_unset(self):
        self._mlist.filter_content = True
        self._mlist.filter_types = ['image/gif']
        msg, msgdata = self._msg(), {}
        config.handlers['mime-delete'].process(self._mlist, msg, msgdata)
        self.assertNotIn('body-modified', msgdata)

    def test_egress_emits_null_body_recipe(self):
        msg = self._filtered_and_egressed()
        recipe = _recipe_at(msg, 2)
        self.assertIn('b', recipe)
        self.assertIsNone(recipe['b'])
        # Spec-06 §4.2: only "b" may be null; the header Recipe is real.
        self.assertIn('h', recipe)
        self.assertIsNotNone(recipe['h'])
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        # The kitten is not in the Recipe.
        self.assertNotIn('AAAA', msg['message-instance'])

    def test_dmarc_wrap_sets_body_modified(self):
        from mailman.handlers.dmarc import wrap_message
        msg, msgdata = self._msg(), {}
        wrap_message(self._mlist, msg, msgdata)
        self.assertTrue(msgdata.get('body-modified'))

    def test_undo_null_body_restores_headers_only(self):
        msg = self._filtered_and_egressed()
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertIsNone(msg['subject'])


class TestBodyRecipeLimit(unittest.TestCase):
    """[mta]message_instance_max_recipe_lines caps the body Recipe."""

    layer = ConfigLayer

    RAW = (b'To: ant@example.com\r\nFrom: aperson@example.com\r\n'
           b'Message-ID: <alpha>\r\nContent-Type: text/plain\r\n\r\n'
           b'one\r\ntwo\r\nthree\r\n')

    def setUp(self):
        self._mlist = create_list('ant@example.com')

    def _rewritten_and_egressed(self, max_lines):
        config.push('test_mi_limit', """\
        [mta]
        message_instance: yes
        message_instance_max_recipe_lines: {}
        """.format(max_lines))
        self.addCleanup(config.pop, 'test_mi_limit')
        msg, msgdata = _msg_from_bytes(self.RAW), {}
        msg.original_bytes = self.RAW
        config.handlers['message-instance-ingress'].process(
            self._mlist, msg, msgdata)
        msg.set_payload('uno\r\ndos\r\ntres\r\n')
        config.handlers['message-instance-egress'].process(
            self._mlist, msg, msgdata)
        return _recipe_at(msg, 2)

    def test_within_the_limit_is_a_recipe(self):
        recipe = self._rewritten_and_egressed(3)
        self.assertEqual(recipe['b'], [{'d': ['one', 'two', 'three']}])

    def test_over_the_limit_is_a_null_recipe(self):
        recipe = self._rewritten_and_egressed(2)
        self.assertIn('b', recipe)
        self.assertIsNone(recipe['b'])


def _smtplib_wire(msg):
    """The octets smtplib.SMTP.send_message sends for this message (no
    SMTPUTF8): a copy without Bcc/Resent-Bcc, flattened by a BytesGenerator
    with the message's own policy -- which for compat32 mangles From_."""
    msg_copy = copy.copy(msg)
    del msg_copy['Bcc']
    del msg_copy['Resent-Bcc']
    buf = BytesIO()
    BytesGenerator(buf).flatten(msg_copy, linesep='\r\n')
    return buf.getvalue()


def _wrap_round_trip(test, raw, layout=True):
    """Ingress, decorate, egress, then undo m=2: m=1 must verify and the
    rebuilt body must be the original octets.

    With layout (the usual case) egress must find the original body where
    the wrap put it, without diffing; either way the body Recipe must be
    exactly what the line diff makes of the same two bodies."""
    msg = _msg_from_bytes(raw)
    msg.original_bytes = raw
    msgdata = {}
    test._ingress.process(test._mlist, msg, msgdata)
    decorate.process(test._mlist, msg, msgdata)
    test.assertIn('dkim2-wrap', msgdata)
    if layout:
        with patch('mailman.handlers.message_instance.compute_body_recipe',
                   side_effect=AssertionError('body Recipe was diffed')):
            test._egress.process(test._mlist, msg, msgdata)
    else:
        test._egress.process(test._mlist, msg, msgdata)
    wire = _normalize_crlf(msg.as_bytes())
    test.assertEqual(msg.get_content_type(), 'multipart/mixed')
    v, err = verify_message_instance(msg)
    test.assertEqual(v, 2, err)
    recipe = _recipe_at(msg, 2)
    test.assertEqual(recipe['b'], compute_body_recipe(
        _body_lines_raw(_wire_bytes(msg)),
        _body_lines_raw(_normalize_crlf(raw))))
    # The original's lines are copied as one range, whatever they are.
    test.assertEqual(len(_copy_steps(recipe['b'])), 1, recipe['b'])
    test.assertEqual(undo_message_instance(msg), 2)
    v, err = verify_message_instance(msg)
    test.assertEqual(v, 1, err)
    _, body = _split_raw(_normalize_crlf(raw))
    test.assertEqual(_get_raw_body(msg).rstrip(b'\r\n'),
                     body.rstrip(b'\r\n'))
    return wire


class TestDKIM2Wrap(unittest.TestCase):
    """On a DKIM2 list decorate wraps the body octets as they arrived."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.preferred_language = 'en'
        temporary_dir = TemporaryDirectory()
        self.addCleanup(temporary_dir.cleanup)
        template_dir = temporary_dir.name
        config.push('test_mi_wrap', """\
        [paths.testing]
        template_dir: {}
        [mta]
        message_instance: yes
        """.format(template_dir))
        self.addCleanup(config.pop, 'test_mi_wrap')
        site_dir = os.path.join(config.TEMPLATE_DIR, 'site', 'en')
        os.makedirs(site_dir)
        for name, text in (('myheader.txt', 'List Header\n'),
                           ('myfooter.txt', '-- \nList Footer\n')):
            with open(os.path.join(site_dir, name), 'w',
                      encoding='utf-8') as fp:
                fp.write(text)
        manager = getUtility(ITemplateManager)
        manager.set('list:member:regular:header', None,
                    'mailman:///myheader.txt')
        manager.set('list:member:regular:footer', None,
                    'mailman:///myfooter.txt')
        self._ingress = config.handlers['message-instance-ingress']
        self._egress = config.handlers['message-instance-egress']

    HDRS = (b'To: ant@example.com\r\nFrom: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\nMIME-Version: 1.0\r\n')

    def test_7bit(self):
        raw = self.HDRS + (b'Content-Type: text/plain; charset=us-ascii\r\n'
                           b'Content-Transfer-Encoding: 7bit\r\n\r\n'
                           b'Hello.\r\n')
        wire = _wrap_round_trip(self, raw)
        self.assertIn(b'MIME-wrapped because the list adds DKIM2', wire)
        self.assertIn(b'List Header', wire)
        self.assertIn(b'List Footer', wire)

    def test_qp_bytes_untouched(self):
        body = b'=48=65=6C=6C=6F=\r\n world\r\n'
        raw = self.HDRS + (b'Content-Type: text/plain; charset=utf-8\r\n'
                           b'Content-Transfer-Encoding: quoted-printable'
                           b'\r\n\r\n') + body
        wire = _wrap_round_trip(self, raw)
        self.assertIn(body, wire)

    def test_base64_bytes_untouched(self):
        body = b'SGVs\r\nbG8u\r\n'
        raw = self.HDRS + (b'Content-Type: text/plain; charset=utf-8\r\n'
                           b'Content-Transfer-Encoding: base64\r\n\r\n') + body
        self.assertIn(body, _wrap_round_trip(self, raw))

    def test_8bit_latin1_bytes_untouched(self):
        body = b'Gr\xfc\xdfe aus K\xf6ln\r\n'
        raw = self.HDRS + (b'Content-Type: text/plain; charset=iso-8859-1\r\n'
                           b'Content-Transfer-Encoding: 8bit\r\n\r\n') + body
        self.assertIn(body, _wrap_round_trip(self, raw))

    def test_multipart_alternative_with_bare_final_boundary(self):
        raw = self.HDRS + (
            b'Content-Type: multipart/alternative;\r\n boundary="b"\r\n\r\n'
            b'--b\r\nContent-Type: text/plain; charset="us-ascii" \r\n\r\n'
            b'Hi\r\n'
            b'--b\r\nContent-Type: text/html\r\n\r\n<p>Hi</p>\r\n--b--')
        wire = _wrap_round_trip(self, raw)
        # Folded Content-Type carried as received.
        self.assertIn(
            b'Content-Type: multipart/alternative;\r\n boundary="b"\r\n',
            wire)
        # The part header's trailing space survives too.
        self.assertIn(b'charset="us-ascii" \r\n', wire)

    def test_no_content_type(self):
        raw = (b'To: ant@example.com\r\nFrom: aperson@example.com\r\n'
               b'Message-ID: <alpha>\r\n\r\nplain old mail\r\n')
        _wrap_round_trip(self, raw)

    def test_boundary_collision(self):
        with patch('mailman.handlers.decorate._new_boundary',
                   side_effect=['COLLIDE', 'COLLIDE', 'fresh-boundary']):
            raw = self.HDRS + (b'Content-Type: text/plain\r\n\r\n'
                               b'--COLLIDE\r\nnot a part\r\n')
            wire = _wrap_round_trip(self, raw)
        self.assertIn(b'boundary="fresh-boundary"', wire)

    def test_body_modified_uses_upstream_decoration(self):
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        msg = _msg_from_bytes(raw)
        msg.original_bytes = raw
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msgdata['body-modified'] = True
        decorate.process(self._mlist, msg, msgdata)
        # Upstream concatenation.
        self.assertEqual(msg.get_content_type(), 'text/plain')
        self.assertIn('List Footer', msg.get_payload())

    def test_list_flag_off_uses_upstream_decoration(self):
        self._mlist.dkim2_message_instance = False
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        msg = _msg_from_bytes(raw)
        msg.original_bytes = raw
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg.get_content_type(), 'text/plain')
        self.assertIn('List Footer', msg.get_payload())

    def test_no_original_bytes_uses_upstream_decoration(self):
        # Never went through ingress (e.g. the virgin pipeline).
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        msg = _msg_from_bytes(raw)
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg.get_content_type(), 'text/plain')
        self.assertIn('List Footer', msg.get_payload())

    def test_smtplib_wire_matches_hashed_body(self):
        # What smtplib.send_message puts on the wire must be what egress
        # hashed: check m=2's body hash against the CRLF wire body.  The
        # 8-bit octets survive, and a From_ line in the received part (which
        # _ReceivedPart writes itself) is not mangled.
        body = b'K\xf6ln\r\nFrom here\r\n'
        raw = self.HDRS + (b'Content-Type: text/plain; charset=iso-8859-1\r\n'
                           b'Content-Transfer-Encoding: 8bit\r\n\r\n') + body
        msg = _msg_from_bytes(raw)
        msg.original_bytes = raw
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        wire = _smtplib_wire(msg)
        self.assertIn(body, wire)
        self.assertIsNone(re.search(rb'(?<!\r)\n', wire))
        v, err = verify_mi_raw(wire)
        self.assertEqual(v, 2, err)

    def _set_footer(self, text):
        site_dir = os.path.join(config.TEMPLATE_DIR, 'site', 'en')
        with open(os.path.join(site_dir, 'myfooter.txt'), 'w',
                  encoding='utf-8') as fp:
            fp.write(text)

    def test_footer_from_line_matches_smtplib_wire(self):
        # smtplib's generator mangles a decoration line starting "From "
        # to ">From "; egress must hash what it sends.
        self._set_footer('From the list\n')
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        msg = _msg_from_bytes(raw)
        msg.original_bytes = raw
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        wire = _smtplib_wire(msg)
        self.assertIn(b'>From the list', wire)
        v, err = verify_mi_raw(wire)
        self.assertEqual(v, 2, err)
        # And the Recipe still undoes to m=1.  (m=2 itself is checked on
        # the wire bytes above: verify_message_instance judges the parsed,
        # unmangled view.)
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(verify_message_instance(msg)[0], 1)

    def test_upstream_decoration_from_line_matches_smtplib_wire(self):
        # The upstream path (body already rewritten: null body Recipe)
        # concatenates the footer into the text; same mangling.
        self._set_footer('From the list\n')
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        msg = _msg_from_bytes(raw)
        msg.original_bytes = raw
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msgdata['body-modified'] = True
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        wire = _smtplib_wire(msg)
        self.assertIn(b'>From the list', wire)
        v, err = verify_mi_raw(wire)
        self.assertEqual(v, 2, err)

    def test_inbound_from_line_without_received_octets(self):
        # An inbound m=1 made over the octets as sent, with a body line
        # starting "From ", on a message that came without original_bytes
        # (an older queue entry): ingress must judge it unmangled, the
        # wire must match m=2, and undoing m=2 must give back what m=1
        # describes.
        plain = self.HDRS + (b'Content-Type: text/plain\r\n\r\n'
                             b'Hello.\r\nFrom x\r\n')
        mi1 = build_mi_header_value(1, compute_header_hash_raw(plain),
                                    compute_body_hash_raw(plain))
        raw = b'Message-Instance: ' + mi1.encode('ascii') + b'\r\n' + plain
        msg = _msg_from_bytes(raw)
        self.assertEqual(verify_mi_raw(raw)[0], 1)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(msgdata['mi_snapshot']['version'], 1)
        decorate.process(self._mlist, msg, msgdata)
        self.assertEqual(msg.get_content_type(), 'multipart/mixed')
        self._egress.process(self._mlist, msg, msgdata)
        wire = _smtplib_wire(msg)
        v, err = verify_mi_raw(wire)
        self.assertEqual(v, 2, err)
        self.assertEqual(undo_message_instance(msg), 2)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)
        self.assertIn('From x', _get_body_lines(msg))

    def test_boundary_collision_in_content_fields(self):
        # The boundary must not occur anywhere in the received part, its
        # Content-* fields included.
        with patch('mailman.handlers.decorate._new_boundary',
                   side_effect=['COLLIDE', 'fresh-boundary']):
            raw = self.HDRS + (b'Content-Type: text/plain\r\n'
                               b'Content-Description: COLLIDE\r\n\r\n'
                               b'not a part\r\n')
            wire = _wrap_round_trip(self, raw)
        self.assertIn(b'boundary="fresh-boundary"', wire)

    def _set_header(self, text):
        site_dir = os.path.join(config.TEMPLATE_DIR, 'site', 'en')
        with open(os.path.join(site_dir, 'myheader.txt'), 'w',
                  encoding='utf-8') as fp:
            fp.write(text)

    def test_header_only_decoration(self):
        self._set_footer('')
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        wire = _wrap_round_trip(self, raw)
        self.assertIn(b'List Header', wire)
        self.assertNotIn(b'List Footer', wire)

    def test_footer_only_decoration(self):
        self._set_header('')
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        wire = _wrap_round_trip(self, raw)
        self.assertNotIn(b'List Header', wire)
        self.assertIn(b'List Footer', wire)

    def test_multipart_mixed_original(self):
        # A multipart/mixed original becomes a part of the wrap, its own
        # preamble, boundaries and epilogue intact.
        raw = self.HDRS + (
            b'Content-Type: multipart/mixed; boundary="inner"\r\n\r\n'
            b'This is a multi-part message in MIME format.\r\n'
            b'--inner\r\nContent-Type: text/plain\r\n\r\nHi\r\n'
            b'--inner\r\nContent-Type: application/octet-stream\r\n'
            b'Content-Transfer-Encoding: base64\r\n\r\nAAAA\r\n'
            b'--inner--\r\nepilogue\r\n')
        wire = _wrap_round_trip(self, raw)
        self.assertIn(b'--inner--\r\nepilogue\r\n', wire)
        self.assertIn(b'This is a multi-part message in MIME format.', wire)

    def test_broken_mime_missing_final_boundary(self):
        # No closing delimiter at all: the octets still go through as
        # they came, and the Recipe still rebuilds them.
        body = (b'--b\r\nContent-Type: text/plain\r\n\r\nHi\r\n'
                b'--b\r\nContent-Type: text/html\r\n\r\n<p>Hi</p>\r\n')
        raw = self.HDRS + (
            b'Content-Type: multipart/alternative; boundary="b"\r\n\r\n'
            + body)
        wire = _wrap_round_trip(self, raw)
        self.assertIn(body, wire)
        self.assertNotIn(b'--b--', wire)

    def test_body_that_is_also_the_list_header(self):
        # The original's one line is also the list header's: the Recipe
        # copies whichever run the line diff would, the first.  Either path
        # may make it (the layout path does, as the first occurrence is
        # followed by a delimiter too); the property is that the Recipe is
        # the diff's.
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nList Header\r\n'
        _wrap_round_trip(self, raw, layout=False)

    def test_body_with_trailing_empty_lines(self):
        # Trailing empty lines are not lines (spec-06 §6.1): the copy range
        # stops before them although the wrap carries them.
        raw = self.HDRS + (b'Content-Type: text/plain\r\n\r\n'
                           b'Hello.\r\n\r\n\r\n')
        _wrap_round_trip(self, raw)

    def test_body_without_final_line_break(self):
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.'
        _wrap_round_trip(self, raw)

    def test_boundaryless_multipart_ingress_wire_unchanged(self):
        # Ingress no longer serialises a message it has the received octets
        # of, which used to resolve a missing multipart boundary.  Parsed,
        # such a message is not multipart at all: nothing to resolve.
        raw = self.HDRS + (b'Content-Type: multipart/mixed\r\n\r\n'
                           b'--x\r\nContent-Type: text/plain\r\n\r\nHi\r\n'
                           b'--x--\r\n')
        wires = []
        for serialize_first in (False, True):
            msg = _msg_from_bytes(raw)
            msg.original_bytes = raw
            if serialize_first:
                _serialize_msg(msg)
            self._ingress.process(self._mlist, msg, {})
            self.assertFalse(msg.is_multipart())
            wires.append(_wire_bytes(msg))
        self.assertEqual(wires[0], wires[1])

    def test_unexpected_layout_falls_back_to_the_diff(self):
        raw = self.HDRS + (b'Content-Type: text/plain; charset=us-ascii\r\n'
                           b'\r\nHello.\r\n')
        msg = _msg_from_bytes(raw)
        msg.original_bytes = raw
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        msgdata['dkim2-wrap'] = 'not-the-boundary'
        with patch('mailman.handlers.message_instance.compute_body_recipe',
                   wraps=compute_body_recipe) as diff:
            self._egress.process(self._mlist, msg, msgdata)
        diff.assert_called_once()
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        self.assertEqual(undo_message_instance(msg), 2)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_upstream_decoration_records_no_wrap(self):
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        msg = _msg_from_bytes(raw)
        msg.original_bytes = raw
        msgdata = {'body-modified': True}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self.assertNotIn('dkim2-wrap', msgdata)


class TestReceivedPart(unittest.TestCase):
    """The wrap's middle part is one blob of octets, written verbatim."""

    PART = (b'Content-Type: text/plain; charset=iso-8859-1\r\n'
            b'Content-Transfer-Encoding: 8bit\r\n\r\n'
            b'From the start\r\ncaf\xe9\r\nlast line')

    def _container(self):
        outer = Message()
        outer['Content-Type'] = 'multipart/mixed; boundary="B"'
        outer.set_payload([decorate._ReceivedPart(self.PART)])
        return outer

    def test_holds_one_blob(self):
        part = decorate._ReceivedPart(self.PART)
        self.assertIs(part._received, self.PART)

    def test_as_bytes_uses_lf(self):
        out = self._container().as_bytes()
        self.assertIn(b'--B\n' + self.PART.replace(b'\r\n', b'\n') +
                      b'\n--B--', out)

    def test_smtplib_wire_uses_crlf_unmangled(self):
        out = _smtplib_wire(self._container())
        self.assertIn(b'--B\r\n' + self.PART + b'\r\n--B--', out)

    def test_as_string_is_the_same_text(self):
        out = self._container().as_string()
        self.assertIn('--B\nContent-Type: text/plain; charset=iso-8859-1\n'
                      'Content-Transfer-Encoding: 8bit\n\nFrom the start\n',
                      out)

    def test_part_queued_by_an_earlier_build(self):
        # Earlier builds held the part as a list of surrogate-escaped
        # lines; a wrapped message they left in the retry queue still goes.
        outer = self._container()
        expected = _smtplib_wire(outer)
        part = outer.get_payload()[0]
        del part._received
        part._received_lines = [
            line.decode('ascii', 'surrogateescape')
            for line in self.PART.split(b'\r\n')]
        outer = pickle.loads(pickle.dumps(outer))
        self.assertEqual(_smtplib_wire(outer), expected)

    def test_pickle_and_deepcopy_round_trip(self):
        outer = self._container()
        expected = outer.as_bytes()
        self.assertEqual(pickle.loads(pickle.dumps(outer)).as_bytes(),
                         expected)
        self.assertEqual(copy.deepcopy(outer).as_bytes(), expected)


class TestNormalizeCRLF(unittest.TestCase):
    @staticmethod
    def _three_pass(raw):
        raw = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        return raw.replace(b'\n', b'\r\n')

    def test_pure_crlf_is_returned_uncopied(self):
        raw = b'a: b\r\n\r\nline\r\n'
        self.assertIs(_normalize_crlf(raw), raw)

    def test_same_octets_as_three_passes(self):
        for raw in (b'', b'a', b'a\nb', b'a\rb', b'a\r\r\nb', b'a\n\rb',
                    b'\r\n\r', b'\n\n\r\r', b'x\r\n\n\ry\r', b'\r\n'):
            self.assertEqual(_normalize_crlf(raw), self._three_pass(raw),
                             raw)


class TestQueueCopiesWithoutReceivedOctets(unittest.TestCase):
    """The archive and NNTP copies do not carry the received octets; the
    copy that goes on to delivery does.  The archive copy is the post as
    archived, whichever archivers are on (the testing config enables
    Mail-Archive.com, which mails it on without a Message-Instance
    snapshot: a known limitation)."""

    layer = ConfigLayer

    RAW = (b'To: ant@example.com\r\nFrom: anne@example.com\r\n'
           b'Message-ID: <queues>\r\nSubject: queues\r\n'
           b'MIME-Version: 1.0\r\nContent-Type: text/plain\r\n\r\n'
           b'Hello.\r\n')

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.archive_policy = ArchivePolicy.public
        self._mlist.gateway_to_news = True
        self._mlist.linked_newsgroup = 'comp.lang.python'
        config.push('test_mi_queues', '[mta]\nmessage_instance: yes')
        self.addCleanup(config.pop, 'test_mi_queues')

    def test_posting_pipeline(self):
        msg = _msg_from_bytes(self.RAW)
        msg.original_bytes = self.RAW
        msgdata = {}
        process_pipeline(self._mlist, msg, msgdata,
                         'default-posting-pipeline')
        for queue in ('archive', 'nntp'):
            items = get_queue_messages(queue, expected_count=1)
            self.assertFalse(hasattr(items[0].msg, 'original_bytes'), queue)
        self.assertEqual(msg.original_bytes, self.RAW)
        items = get_queue_messages('out', expected_count=1)
        out, outdata = items[0].msg, items[0].msgdata
        self.assertEqual(out.original_bytes, self.RAW)
        # And delivery still records this hop.
        decorate.process(self._mlist, out, outdata)
        config.handlers['message-instance-egress'].process(
            self._mlist, out, outdata)
        v, err = verify_message_instance(out)
        self.assertEqual(v, 2, err)
        self.assertEqual(undo_message_instance(out), 2)
        v, err = verify_message_instance(out)
        self.assertEqual(v, 1, err)

    def test_received_octets_come_back_after_a_failed_enqueue(self):
        msg = _msg_from_bytes(self.RAW)
        msg.original_bytes = self.RAW
        with patch.object(config.switchboards['archive'], 'enqueue',
                          side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                config.handlers['to-archive'].process(self._mlist, msg, {})
        self.assertEqual(msg.original_bytes, self.RAW)
