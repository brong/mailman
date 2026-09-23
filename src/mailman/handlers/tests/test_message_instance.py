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
import email
import hashlib
import json
import os
import re
import unittest

from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.email.message import Message
from mailman.handlers import decorate
from mailman.handlers.message_instance import (
    build_mi_header_value,
    compute_body_hash,
    compute_body_recipe,
    compute_header_hash,
    compute_header_recipe,
    get_max_mi_version,
    undo_message_instance,
    verify_message_instance,
    _b64,
    _get_body_lines,
    _parse_mi,
    _get_mi_version,
    _should_exclude_header,
    _collect_headers,
    _body_lines_raw,
    _hash_header_pairs,
    _hashed_pairs,
    _plan_undo,
    _raw_header_pairs,
    compute_body_hash_raw,
    compute_header_hash_raw,
)
from mailman.interfaces.template import ITemplateManager
from mailman.testing.helpers import specialized_message_from_string as mfs
from mailman.testing.layers import ConfigLayer
from tempfile import TemporaryDirectory
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
        # Fast path: appended lines only — Recipe is a single copy of the
        # original N lines, no diff required.
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
        # spec-06 §5.2: "b": null says the body cannot be recreated.
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

    def test_folded_recipe_parses(self):
        value = build_mi_header_value(
            2, b'\x00' * 32, b'\x01' * 32, header_recipe={'subject': []})
        r_start = value.index('r=') + 2
        folded = value[:r_start + 8] + '\r\n  ' + value[r_start + 8:]
        _, _, recipe = _parse_mi(folded)
        self.assertEqual(recipe['h'], {'subject': []})


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


class TestDraftVersion(unittest.TestCase):

    def test_draft_version_is_06(self):
        from mailman.handlers.message_instance import DKIM2_DRAFT, DKIM2_DATE
        self.assertEqual(DKIM2_DRAFT, 'ietf-dkim-dkim2-spec-06')
        self.assertEqual(DKIM2_DATE, '2026-10-04')


# =====================================================================
# Integration tests for CTE preservation + MI (need ConfigLayer)
# =====================================================================

class TestDecorateCTEPreservation(unittest.TestCase):
    """Test that decoration preserves Content-Transfer-Encoding."""

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
            fp.write('-- \nList Footer\nhttps://example.com/unsub\n')
        getUtility(ITemplateManager).set(
            'list:member:regular:footer', None, 'mailman:///myfooter.txt')

    def test_7bit_preserved(self):
        msg = mfs("""\
To: ant@example.com
From: aperson@example.com
Message-ID: <alpha>
Content-Type: text/plain; charset=us-ascii
Content-Transfer-Encoding: 7bit

Hello world.
This is a test.
""")
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg['content-transfer-encoding'], '7bit')
        body = msg.get_payload()
        self.assertIn('Hello world.', body)
        self.assertIn('List Footer', body)

    def test_8bit_preserved(self):
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            b'Content-Transfer-Encoding: 8bit\r\n'
            b'\r\n'
            b'Hello w\xc3\xb6rld.\r\n'
        )
        msg = _msg_from_bytes(raw)
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg['content-transfer-encoding'], '8bit')
        as_bytes = msg.as_bytes()
        self.assertNotIn(b'base64', as_bytes.lower().split(b'\n\n')[1])
        self.assertIn(b'List Footer', as_bytes)

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

    def test_qp_preserves_original_encoding(self):
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=us-ascii\r\n'
            b'Content-Transfer-Encoding: quoted-printable\r\n'
            b'\r\n'
            b'Hello=20world.\r\n'
            b'This=20is=20a=3Dtest.\r\n'
        )
        msg = _msg_from_bytes(raw)
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg['content-transfer-encoding'], 'quoted-printable')
        payload = msg.get_payload()
        self.assertIn('Hello=20world.', payload)
        self.assertIn('This=20is=20a=3Dtest.', payload)
        self.assertIn('List Footer', payload)

    def test_qp_with_soft_line_breaks_preserved(self):
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            b'Content-Transfer-Encoding: quoted-printable\r\n'
            b'\r\n'
            b'This is a very long line that the original sender chose to '
            b'brea=\r\n'
            b'k at this specific point for some reason.\r\n'
        )
        msg = _msg_from_bytes(raw)
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg['content-transfer-encoding'], 'quoted-printable')
        payload = msg.get_payload()
        self.assertIn(
            'This is a very long line that the original sender chose to '
            'brea=\n'
            'k at this specific point for some reason.',
            payload)

    def test_qp_unnecessarily_quoted_chars_preserved(self):
        # =48=65=6C=6C=6F decodes to 'Hello' — these don't need quoting.
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=us-ascii\r\n'
            b'Content-Transfer-Encoding: quoted-printable\r\n'
            b'\r\n'
            b'=48=65=6C=6C=6F =57=6F=72=6C=64=2E\r\n'
        )
        msg = _msg_from_bytes(raw)
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg['content-transfer-encoding'], 'quoted-printable')
        payload = msg.get_payload()
        self.assertIn('=48=65=6C=6C=6F =57=6F=72=6C=64=2E', payload)
        self.assertIn('List Footer', payload)

    def test_base64_stays_single_part(self):
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            b'Content-Transfer-Encoding: base64\r\n'
            b'\r\n'
            b'SGVsbG8gd29ybGQu\r\n'
        )
        msg = _msg_from_bytes(raw)
        decorate.process(self._mlist, msg, {})
        # Should stay single-part base64, not MIME-wrapped.
        self.assertEqual(msg.get_content_type(), 'text/plain')
        self.assertEqual(msg['content-transfer-encoding'], 'base64')
        # Footer should be in the decoded content.
        decoded = msg.get_payload(decode=True).decode('utf-8')
        self.assertIn('List Footer', decoded)

    def test_base64_weird_line_length_preserved(self):
        content = b'Hello world. This is a longer test message for base64.'
        b64 = base64.b64encode(content).decode('ascii')
        b64_40 = '\r\n'.join(
            b64[i:i+40] for i in range(0, len(b64), 40))
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            b'Content-Transfer-Encoding: base64\r\n'
            b'\r\n'
            + b64_40.encode('ascii') + b'\r\n'
        )
        msg = _msg_from_bytes(raw)
        decorate.process(self._mlist, msg, {})
        # Should stay base64, re-encoded at original 40-char width.
        self.assertEqual(msg['content-transfer-encoding'], 'base64')
        payload = msg.get_payload()
        lines = [l for l in payload.split('\n') if l.strip()]
        # All complete lines (except possibly the last) should be 40 chars.
        for line in lines[:-1]:
            self.assertEqual(len(line.strip()), 40,
                             f'Line length changed: {line!r}')

    def test_base64_very_short_lines_preserved(self):
        content = b'Short lines test content here.'
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
        original_b64_lines = [l for l in b64_20.split('\r\n') if l]
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg['content-transfer-encoding'], 'base64')
        payload = msg.get_payload()
        # All original complete lines should appear in the output.
        for orig_line in original_b64_lines[:-1]:
            self.assertIn(orig_line, payload,
                          f'Original b64 line lost: {orig_line!r}')


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
        with open(msgdata['mi_snapshot']['mi_file'], 'rb') as fp:
            self.assertEqual(fp.read(), self.RECEIVED)

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

    def test_ingress_without_received_octets_still_works(self):
        # A message Mailman made itself, or one from an older queue entry,
        # has no original_bytes: the parsed form is the baseline, as before.
        msg = self._make_7bit_msg()
        self.assertFalse(hasattr(msg, 'original_bytes'))
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        version, error = verify_message_instance(msg)
        self.assertEqual((version, error), (1, None))

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
                               r'sw=mailman; action=mi-m=1; hc=\d+; hn=\S+; '
                               r'snaps=\S+;$')

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
    # Recipe compactness checks
    # -----------------------------------------------------------------

    def test_7bit_body_recipe_is_compact(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        recipe = _recipe_at(msg, 2)
        self.assertIsNotNone(recipe, 'MI m=2 not found')
        body_recipe = recipe.get('b', [])
        # A footer append reverses with a single copy step over the original
        # lines -- no literal data needed.
        self.assertEqual(len(body_recipe), 1)
        self.assertEqual(len(_copy_steps(body_recipe)), 1)

    def test_qp_recipe_has_no_literals(self):
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
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        recipe = _recipe_at(msg, 2)
        self.assertIsNotNone(recipe, 'MI m=2 not found')
        body_recipe = recipe.get('b', [])
        literals = _data_steps(body_recipe)
        self.assertEqual(len(literals), 0,
                         f'Recipe should have no literals: {literals}')

    def test_base64_recipe_has_one_literal_for_last_line(self):
        # Base64 re-encoding at the original line width produces
        # matching lines for all complete blocks, but the last line
        # changes (original had padding, new doesn't).  The Recipe
        # should be a range + one literal for the original last line.
        content = b'Base64 content for recipe test.'
        b64 = base64.b64encode(content).decode('ascii')
        b64_30 = '\r\n'.join(
            b64[i:i+30] for i in range(0, len(b64), 30))
        raw = (
            b'To: ant@example.com\r\n'
            b'From: aperson@example.com\r\n'
            b'Message-ID: <alpha>\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            b'Content-Transfer-Encoding: base64\r\n'
            b'\r\n'
            + b64_30.encode('ascii') + b'\r\n'
        )
        msg = _msg_from_bytes(raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        recipe = _recipe_at(msg, 2)
        self.assertIsNotNone(recipe, 'MI m=2 not found')
        body_recipe = recipe.get('b', [])
        ranges = _copy_steps(body_recipe)
        literals = _data_steps(body_recipe)
        self.assertGreater(len(ranges), 0,
                           'Recipe should have range references')
        self.assertLessEqual(len(literals), 1,
                             f'Recipe should have at most 1 literal '
                             f'(original last line): {literals}')

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
