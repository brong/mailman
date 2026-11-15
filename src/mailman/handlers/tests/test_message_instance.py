# Copyright (C) 2026 by the Free Software Foundation, Inc.
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

import os
import re
import copy
import json
import email
import base64
import pickle
import hashlib
import unittest
import itertools

from contextlib import ExitStack
from email.generator import BytesGenerator
from email.utils import parseaddr
from io import BytesIO
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.core.pipelines import process as process_pipeline
from mailman.email.message import Message
from mailman.handlers import decorate
from mailman.handlers.dmarc import wrap_message
from mailman.handlers.message_instance import (
    arrival_record,
    b64,
    build_instance_value,
    collect_headers,
    compute_body_hash_raw,
    compute_header_hash,
    compute_header_hash_raw,
    compute_header_recipe,
    DKIM2_DATE,
    DKIM2_DRAFT,
    fold_instance_value,
    get_max_instance_version,
    hash_header_pairs,
    instance_version,
    MalformedInstance,
    normalize_crlf,
    parse_instance,
    previous_header_pairs,
    serialize_message,
    should_exclude_header,
    wire_bytes,
)
from mailman.interfaces.mailinglist import (
    DMARCMitigateAction,
    Personalization,
    ReplyToMunging,
)
from mailman.interfaces.template import ITemplateManager
from mailman.mta.deliver import Deliver
from mailman.testing.helpers import (
    configuration,
    get_queue_messages,
    specialized_message_from_string as mfs,
    subscribe,
)
from mailman.testing.layers import ConfigLayer
from mailman.testing.message_instance import (
    body_lines_raw,
    compute_body_hash,
    get_body_lines,
    plan_undo,
    undo_message_instance,
    verify_instance_raw,
    verify_message_instance,
)
from mailman.utilities.email import add_message_hash
from tempfile import TemporaryDirectory
from unittest.mock import patch
from zope.component import getUtility


def _crlf(text):
    """Give text, written with LF line endings, CRLF line endings."""
    return text.replace(b'\n', b'\r\n')


def _msg_from_bytes(raw):
    """Create a Mailman Message from raw bytes."""
    return email.message_from_bytes(raw, Message)


def _arrive(msg, raw):
    """Leave msg as the LMTP runner does for the octets raw."""
    msg.dkim2_arrival = arrival_record(normalize_crlf(raw))
    msg.start_header_log()
    return msg


def _decode_hashes(instance_value):
    """Parse a Message-Instance value and return (header_hash, body_hash)."""
    _, hashes, _ = parse_instance(instance_value)
    if hashes is None:
        return None, None
    return hashes['h'][1], hashes['b'][1]


def _decode_recipe(instance_value):
    """Parse a Message-Instance value and return the recipe dict, or None."""
    _, _, recipe = parse_instance(instance_value)
    return recipe


def _recipe_at(msg, version):
    """Return the decoded recipe of the Message-Instance with this m= value.

    Returns None when no instance carries that version.
    """
    for val in msg.get_all('message-instance', []):
        if re.search(r'm\s*=\s*{}\b'.format(version), str(val)):
            return _decode_recipe(val)
    return None


def _copy_steps(steps):
    """The {"c": [start, end]} copy steps of a recipe."""
    return [s for s in steps if isinstance(s, dict) and 'c' in s]


def _data_steps(steps):
    """The {"d": [...]} literal-data steps of a recipe."""
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


# A DKIM2-Signature for posts that arrive signed.  Neither it nor any
# Message-Instance is hashed, and Mailman never checks it: its presence is
# what tells ingress the post's instances are covered and must be kept.
UPSTREAM_SIGNATURE = ('i=1; m=1; t=1; d=example.com; '
                      'mf=<aperson@example.com>; rt=<ant@example.com>; '
                      's1=sel; b1=AAAA;')


def _sign_upstream(msg):
    """Mark msg as arriving signed (see UPSTREAM_SIGNATURE)."""
    msg.prepend_header('DKIM2-Signature', UPSTREAM_SIGNATURE)
    return msg


def _assert_undoes_to_snapshot(test, msg, msgdata, version=1):
    """The top instance (m=version) verifies, and undoing it gives back
    the message as ingress took it: for an unsigned post, whose only
    instance is the m=1 egress adds, that is the post as received; for a
    signed post, the instance below then verifies too."""
    v, err = verify_message_instance(msg)
    test.assertEqual(v, version, err)
    test.assertEqual(undo_message_instance(msg), version)
    snapshot = msgdata['dkim2_snapshot']
    test.assertEqual(b64(compute_header_hash(msg)),
                     b64(snapshot['header_hash']))
    test.assertEqual(b64(compute_body_hash(msg)),
                     b64(snapshot['body_hash']))
    v, err = verify_message_instance(msg)
    test.assertEqual(v, version - 1, err)


class TestHeaderExclusion(unittest.TestCase):
    """Test that the correct headers are excluded from hashing."""

    layer = ConfigLayer

    def test_excluded_headers(self):
        for name in ('Received', 'Return-Path', 'Delivered-To',
                     'Message-Instance', 'DKIM2-Signature', 'DKIM-Signature',
                     'Authentication-Results'):
            self.assertTrue(should_exclude_header(name), name)

    def test_excluded_prefixes(self):
        for name in ('X-Mailer', 'X-Spam-Status', 'ARC-Seal',
                     'ARC-Message-Signature'):
            self.assertTrue(should_exclude_header(name), name)

    def test_included_headers(self):
        for name in ('From', 'To', 'Subject', 'Date', 'Content-Type',
                     'List-Id', 'Reply-To'):
            self.assertFalse(should_exclude_header(name), name)

    def test_more_excluded_names(self):
        for name in ('Apparently-To', 'DL-Expansion-History',
                     'Original-Recipient', 'SIO-Label-History',
                     'X400-Received', 'X400-Trace'):
            self.assertTrue(should_exclude_header(name), name)

    def test_auto_submitted_and_vbr_info_are_hashed(self):
        for name in ('Auto-Submitted', 'VBR-Info'):
            self.assertFalse(should_exclude_header(name), name)

    def test_received_prefix(self):
        # Any Received-* field is a trace field.
        self.assertTrue(should_exclude_header('Received-SPF'))
        self.assertTrue(should_exclude_header('Received-Anything'))

    def test_only_the_rfc_8617_arc_names(self):
        # Only the three ARC fields of RFC 8617 are excluded.
        self.assertTrue(should_exclude_header('ARC-Seal'))
        self.assertTrue(should_exclude_header('ARC-Message-Signature'))
        self.assertTrue(should_exclude_header('ARC-Authentication-Results'))
        self.assertFalse(should_exclude_header('ARC-Something-Else'))


class TestHashComputation(unittest.TestCase):
    """Header and body hash computation."""

    layer = ConfigLayer

    def test_simple_message_hashes_deterministic(self):
        raw = _crlf(b"""\
From: sender@test1.dkim2.com
To: recipient@example.com
Subject: Simple test message
Date: Sat, 01 Mar 2026 12:00:00 +0000
Message-ID: <test-simple@test1.dkim2.com>

This is a simple test message.
""")
        msg = _msg_from_bytes(raw)
        h1 = compute_header_hash(msg)
        h2 = compute_header_hash(msg)
        self.assertEqual(h1, h2)
        b1 = compute_body_hash(msg)
        b2 = compute_body_hash(msg)
        self.assertEqual(b1, b2)

    def test_known_answer_hashes(self):
        # A vector shared with other DKIM2 implementations.  The m=1 hashes
        # are the reference signer's, so this checks Mailman's hash
        # computation against theirs rather than against itself.
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
        instance_values = msg.get_all('message-instance', [])
        self.assertTrue(len(instance_values) > 0, 'No instance')
        stored_h, stored_b = _decode_hashes(instance_values[0])
        self.assertEqual(
            stored_h, 'SLtzk6LO68CCaX4edrJ6yfpWbp3hwgvI8IdMBRLDk+Y=')
        self.assertEqual(
            stored_b, 'SgG5fNGEg1x24MwItCUYGDHQkWKng06W1/IvTGBdwzU=')
        computed_h = b64(compute_header_hash(msg))
        computed_b = b64(compute_body_hash(msg))
        self.assertEqual(stored_h, computed_h)
        self.assertEqual(stored_b, computed_b)

    def test_excluded_headers_dont_affect_hash(self):
        raw = _crlf(b"""\
From: anne@example.com
To: bart@example.com
Subject: test

body
""")
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
        raw = _crlf(b"""\
From: Jos\xe9 <jose@example.com>
To: list@example.com
Subject: hi
Message-ID: <x@example.com>

body
""")
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
        self.assertEqual(dict(collect_headers(msg))['From'],
                         _octets(b'Jos\xe9 <jose@example.com>'))
        self.assertNotIn('unknown-8bit', dict(collect_headers(msg))['From'])

    def test_body_hash_trailing_blank_lines(self):
        raw1 = b'From: anne@example.com\r\n\r\nbody\r\n'
        raw2 = b'From: anne@example.com\r\n\r\nbody\r\n\r\n\r\n'
        msg1 = _msg_from_bytes(raw1)
        msg2 = _msg_from_bytes(raw2)
        self.assertEqual(compute_body_hash(msg1), compute_body_hash(msg2))


class TestBodyLines(unittest.TestCase):
    layer = ConfigLayer

    def test_trailing_empty_lines_are_not_numbered(self):
        # Body canonicalization drops trailing empty lines, so a Recipe
        # does not count them.
        msg = _msg_from_bytes(
            b'From: anne@example.com\r\n\r\nline1\r\nline2\r\n\r\n\r\n')
        self.assertEqual(get_body_lines(msg), ['line1', 'line2'])
        # ... while an empty line inside the body is a line like any other.
        msg = _msg_from_bytes(
            b'From: anne@example.com\r\n\r\nline1\r\n\r\nline3\r\n')
        self.assertEqual(get_body_lines(msg), ['line1', '', 'line3'])

    def test_simple_body(self):
        msg = _msg_from_bytes(
            b'From: anne@example.com\r\n\r\nline1\r\nline2\r\n')
        self.assertEqual(get_body_lines(msg), ['line1', 'line2'])

    def test_empty_body(self):
        msg = _msg_from_bytes(b'From: anne@example.com\r\n\r\n')
        self.assertEqual(get_body_lines(msg), [])


class TestHeaderRecipe(unittest.TestCase):
    layer = ConfigLayer

    def test_no_changes(self):
        headers = [('From', 'anne@example.com'), ('To', 'bart@example.com')]
        self.assertIsNone(compute_header_recipe(headers, headers))

    def test_added_header(self):
        recipe = compute_header_recipe(
            [('From', 'anne@example.com'), ('List-Id', '<list.example.com>')],
            [('From', 'anne@example.com')])
        self.assertEqual(recipe['list-id'], [])

    def test_removed_header(self):
        recipe = compute_header_recipe(
            [('From', 'anne@example.com')],
            [('From', 'anne@example.com'), ('Reply-To', 'list@example.com')])
        self.assertEqual(recipe['reply-to'], [{'d': ['list@example.com']}])

    def test_modified_header(self):
        recipe = compute_header_recipe(
            [('Subject', '[List] Hello')],
            [('Subject', 'Hello')])
        self.assertEqual(recipe['subject'], [{'d': ['Hello']}])

    def test_excluded_headers_ignored(self):
        self.assertIsNone(compute_header_recipe(
            [('From', 'anne@example.com'), ('Received', 'from mx')],
            [('From', 'anne@example.com'), ('Received', 'from mx2')]))

    def test_8bit_literal_is_a_u_step(self):
        # A raw EUC-KR Subject (not UTF-8) restored literally goes in a
        # "u" step as base64 of its octets.
        euckr = b'(\xb1\xa4---\xb0\xed) \xc0\xcc\xb8\xe1 500'
        recipe = compute_header_recipe(
            [('Subject', '[Ant] ' + _octets(euckr))],
            [('Subject', _octets(euckr))])
        self.assertEqual(recipe['subject'], [{'u': [b64(euckr)]}])
        _assert_no_surrogates_in_json(self, recipe)
        self.assertEqual(base64.b64decode(recipe['subject'][0]['u'][0]),
                         euckr)

    def test_utf8_literal_is_a_d_step(self):
        # A raw UTF-8 Subject restored literally goes in a "d" step.
        recipe = compute_header_recipe(
            [('Subject', 'x')],
            [('Subject', _octets('caf\u00e9'.encode('utf-8')))])
        self.assertEqual(recipe, {'subject': [{'d': ['caf\u00e9']}]})

    def test_mixed_literals_alternate_d_and_u_steps(self):
        latin1 = b'Caf\xe9'
        recipe = compute_header_recipe(
            [('Comments', 'x')],
            [('Comments', 'plain'), ('Comments', _octets(latin1)),
             ('Comments', 'also plain')])
        # Bottom-up: 'also plain', latin1, 'plain'.
        self.assertEqual(recipe['comments'], [
            {'d': ['also plain']}, {'u': [b64(latin1)]}, {'d': ['plain']},
        ])

    def test_reordered_duplicates_never_descend(self):
        # Two instances swapped.  Bottom-up the current message is
        # 1='two', 2='one'; the previous was 1='one', 2='two'.  'one' is
        # copied from instance 2, after which 'two' at instance 1 cannot be
        # copied, as "c" starts ascend, so it is restored literally.
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

    def test_body_only_change_yields_no_header_recipe(self):
        # Identical header sets give no header Recipe at all, not a null
        # "h" value.
        headers = [('From', 'anne@example.com'), ('Subject', 'hi')]
        self.assertIsNone(compute_header_recipe(headers, headers))

    def test_header_change_yields_dict_not_null(self):
        cur = [('From', 'anne@example.com'), ('Subject', '[list] hi')]
        prev = [('From', 'anne@example.com'), ('Subject', 'hi')]
        recipe = compute_header_recipe(cur, prev)
        self.assertIsInstance(recipe, dict)
        self.assertIsNotNone(recipe)
        self.assertIn('subject', recipe)

    def test_copy_ranges_always_ascend(self):
        # Whatever the permutation, every emitted "c" starts above the end
        # of the one before it.
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


class TestInstanceHeaderValue(unittest.TestCase):
    layer = ConfigLayer

    def test_v1_no_recipe(self):
        value = build_instance_value(1, b'\x00' * 32, b'\x01' * 32)
        self.assertTrue(value.startswith('m=1;'))
        self.assertIn('h=', value)
        self.assertNotIn('r=', value)

    def test_v2_with_recipe_roundtrips(self):
        value = build_instance_value(
            2, b'\x00' * 32, b'\x01' * 32,
            header_recipe={'list-id': []},
            body_recipe=[[2, 5]])
        self.assertIn('r=', value)
        recipe = _decode_recipe(value)
        self.assertEqual(recipe['h'], {'list-id': []})
        self.assertEqual(recipe['b'], [[2, 5]])


class TestRecipeUndo(unittest.TestCase):
    """Recipe validation and application by undo and verify."""

    layer = ConfigLayer

    RAW = _crlf(b"""\
From: anne@example.com
To: list@example.com
Subject: hello
Comments: one
Comments: two
Message-ID: <x@example.com>

line one
line two
line three
""")

    def _with_recipe(self, header_recipe=None, body_recipe=None):
        """The fixed message carrying an m=2 whose hashes match it and whose
        r= holds exactly these (possibly invalid) recipe objects."""
        msg = _msg_from_bytes(self.RAW)
        mi2 = build_instance_value(
            2, compute_header_hash(msg), compute_body_hash(msg),
            header_recipe, body_recipe)
        msg.prepend_header('Message-Instance', mi2)
        return msg

    def _assert_rejected(self, msg, what):
        before = msg.as_bytes()
        self.assertEqual(undo_message_instance(msg), 0, what)
        self.assertEqual(msg.as_bytes(), before,
                         'a rejected Recipe must leave the message alone')
        self.assertEqual(get_max_instance_version(msg), 2)
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
            'u not base64': [{'u': ['not base64!']}],
            'u bad padding': [{'u': ['YQ']}],
            'u with whitespace': [{'u': ['YW Jj']}],
            'u non-zero pad bits': [{'u': ['QR==']}],
            'u non-zero pad bits, one =': [{'u': ['YWJ=']}],
            'u non-ascii': [{'u': ['caf\u00e9']}],
            'u decodes to CRLF': [{'u': [b64(b'a\r\nb')]}],
            'u decodes to LF': [{'u': [b64(b'a\nb')]}],
            'u decodes to CR': [{'u': ['YQ1i']}],
            'd with LF': [{'d': ['a\nb']}],
            'd with CR': [{'d': ['a\rb']}],
            'd lone surrogate': [{'d': ['caf\udce9']}],
            'd non-string item': [{'d': [1]}],
            'u non-string item': [{'u': [1]}],
            'd empty': [{'d': []}],
            'u empty': [{'u': []}],
            # There is no "b" step.
            'b step': [{'b': ['eA==']}],
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
            'two keys, d and u': [{'d': ['x'], 'u': ['eA==']}],
            'empty object': [{}],
            'number': [7],
            # A step is an object, not a bare range or string.
            'bare range': [[1, 1]],
            'bare string': ['legacy'],
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

    def _with_raw_recipe(self, recipe_json):
        """The fixed message carrying an m=2 whose hashes match it and whose
        r= is exactly this JSON text."""
        msg = _msg_from_bytes(self.RAW)
        msg.prepend_header('Message-Instance', 'm=2; h=sha256:{}:{}; '
                           'r={};'.format(b64(compute_header_hash(msg)),
                                          b64(compute_body_hash(msg)),
                                          b64(recipe_json.encode())))
        return msg

    def test_bad_recipe_objects_are_rejected(self):
        # "h" with no properties, and an object with neither "h" nor "b".
        cases = {
            'empty h': '{"h":{}}',
            'neither h nor b': '{"x":1}',
            'empty object': '{}',
        }
        for what, text in cases.items():
            with self.subTest(what):
                self._assert_rejected(self._with_raw_recipe(text), what)

    def test_instance_support_rejects_duplicate_keys(self):
        # A key twice in one object is invalid.
        text = '{"b":[{"c":[1,1]}],"b":[{"c":[1,2]}]}'
        self._assert_rejected(self._with_raw_recipe(text), 'exact duplicate')

    def test_uppercase_keys_are_invalid(self):
        # A JSON key with a capital letter is invalid, at every level.
        cases = {
            'header names': '{"h":{"Subject":[{"c":[1,1]}]}}',
            'top level': '{"H":{"subject":[{"c":[1,1]}]}}',
            'step keys': '{"b":[{"C":[1,2]}]}',
        }
        for what, text in cases.items():
            with self.subTest(what):
                self._assert_rejected(self._with_raw_recipe(text), what)
        lower = self._with_raw_recipe(
            '{"h":{"subject":[{"c":[1,1]}],"comments":[{"d":["x"]}]},'
            '"b":[{"c":[1,2]},{"u":["Y2Fm6Q=="]}]}')
        self.assertEqual(undo_message_instance(lower), 2)
        self.assertEqual(lower.get_all('Comments'), ['x'])

    def test_unparseable_r_is_rejected(self):
        msg = _msg_from_bytes(self.RAW)
        not_json = b64(b'not json')
        msg.prepend_header('Message-Instance', 'm=2; h=sha256:{}:{}; '
                           'r={};'.format(b64(compute_header_hash(msg)),
                                          b64(compute_body_hash(msg)),
                                          not_json))
        self._assert_rejected(msg, 'r= is not JSON')

    def test_u_and_d_steps_undo_to_the_original_octets(self):
        latin1 = b'caf\xe9'
        utf8 = 'w\u00f6rld'.encode('utf-8')
        msg = self._with_recipe(
            header_recipe={'subject': [{'u': [b64(latin1)]}]},
            body_recipe=[{'c': [1, 1]}, {'u': [b64(latin1)]},
                         {'d': ['w\u00f6rld']}])
        self.assertEqual(verify_message_instance(msg)[0], 2)
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(get_body_lines(msg),
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
        # "b": null says the body cannot be recreated.  build_instance_value
        # omits a None body recipe, so put the null in by hand.
        msg = self._with_raw_recipe(
            json.dumps({'h': {'comments': []}, 'b': None}))
        self.assertEqual(verify_message_instance(msg)[0], 2)
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(get_body_lines(msg),
                         ['line one', 'line two', 'line three'])
        self.assertIsNone(msg.get_all('Comments'))

    def test_utf8_d_step_undoes_byte_exactly(self):
        # Valid UTF-8 in "d" (a body line and a header value) undoes to the
        # same octets.
        original = _crlf(b"""\
From: anne@example.com
Subject: na\xc3\xafve
Message-ID: <x@example.com>
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit

na\xc3\xafve line
ascii line
""")
        orig = _msg_from_bytes(original)
        h1, b1 = compute_header_hash(orig), compute_body_hash(orig)
        msg = _msg_from_bytes(original)
        msg.replace_header('Subject', 'x')
        msg.set_payload('x\r\nascii line\r\n')
        header_recipe = compute_header_recipe(
            collect_headers(msg), collect_headers(orig))
        body_recipe = [{'d': ['na\u00efve line']}, {'c': [2, 2]}]
        self.assertEqual(header_recipe, {'subject': [{'d': ['na\u00efve']}]})
        msg.prepend_header('Message-Instance', build_instance_value(
            2, compute_header_hash(msg), compute_body_hash(msg),
            header_recipe, body_recipe))
        self.assertEqual(verify_message_instance(msg), (2, None))
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(b64(compute_header_hash(msg)), b64(h1))
        self.assertEqual(b64(compute_body_hash(msg)), b64(b1))
        self.assertIn(b'\nna\xc3\xafve line\n',
                      msg.as_bytes().replace(b'\r\n', b'\n'))

    def test_8bit_message_round_trips_through_u_steps(self):
        # End to end on the producer side: a message with a raw EUC-KR body
        # line and swapped Comments is changed the way a list would change
        # it; the recipe is "u"/"d"/"c" only, encodes without surrogates,
        # and undoing it restores the m=1 hashes exactly.
        original = _crlf(b"""\
From: anne@example.com
Subject: hello
Comments: two
Comments: one
Message-ID: <x@example.com>
Content-Type: text/plain; charset=euc-kr
Content-Transfer-Encoding: 8bit

\xc0\xcc\xb8\xe1 line
ascii line
""")
        orig = _msg_from_bytes(original)
        h1, b1 = compute_header_hash(orig), compute_body_hash(orig)
        msg = _msg_from_bytes(original)
        msg.replace_header('Subject', '[Ant] hello')
        del msg['Comments']
        msg['Comments'] = 'one'
        msg['Comments'] = 'two'
        msg.set_payload(msg.get_payload() + 'footer\r\n')
        header_recipe = compute_header_recipe(
            collect_headers(msg), collect_headers(orig))
        body_recipe = [{'u': [b64(b'\xc0\xcc\xb8\xe1 line')]},
                       {'c': [2, 2]}]
        self.assertEqual(header_recipe['comments'],
                         [{'c': [2, 2]}, {'d': ['two']}])
        _assert_no_surrogates_in_json(
            self, {'h': header_recipe, 'b': body_recipe})
        mi2 = build_instance_value(
            2, compute_header_hash(msg), compute_body_hash(msg),
            header_recipe, body_recipe)
        msg.prepend_header('Message-Instance', build_instance_value(1, h1, b1))
        msg.prepend_header('Message-Instance', mi2)
        self.assertEqual(verify_message_instance(msg), (2, None))
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertEqual(b64(compute_header_hash(msg)), b64(h1))
        self.assertEqual(b64(compute_body_hash(msg)), b64(b1))
        self.assertEqual(verify_message_instance(msg), (1, None))

    def test_copied_raw_8bit_header_keeps_its_octets(self):
        # A raw Latin-1 From that the Recipe copies must come back out as
        # the same octets, not re-encoded, so the header hash still matches
        # the wire after undo.
        raw = _crlf(b"""\
From: Jos\xe9 <jose@example.com>
To: list@example.com
Comments: one
Message-ID: <x@example.com>

body
""")
        msg = _msg_from_bytes(raw)
        before = compute_header_hash(msg)
        mi2 = build_instance_value(
            2, before, compute_body_hash(msg),
            {'from': [{'c': [1, 1]}], 'comments': [{'c': [1, 1]}]})
        msg.prepend_header('Message-Instance', mi2)
        self.assertEqual(undo_message_instance(msg), 2)
        self.assertRegex(msg.as_bytes(),
                         rb'(?i)\nfrom: Jos\xe9 <jose@example.com>\n')
        self.assertEqual(compute_header_hash(msg), before)
        self.assertEqual(compute_header_hash(_msg_from_bytes(msg.as_bytes())),
                         before)


class TestParseInstanceFolding(unittest.TestCase):
    """Folded Message-Instance values parse.

    RFC 5322 FWS is CRLF followed by one or more WSP.
    """

    layer = ConfigLayer

    UNFOLDED = ('m=1; h=sha256:' + 'A' * 43 + '=:' + 'B' * 43 + '=;')

    def _fold_h(self, fws):
        # Insert the fold six characters into the header hash.
        prefix, rest = self.UNFOLDED.split('h=sha256:', 1)
        return prefix + 'h=sha256:' + rest[:6] + fws + rest[6:]

    def test_unfolded_baseline(self):
        version, hashes, recipe = parse_instance(self.UNFOLDED)
        self.assertEqual(version, 1)
        self.assertEqual(hashes['h'][1], 'A' * 43 + '=')
        self.assertEqual(hashes['b'][1], 'B' * 43 + '=')
        self.assertIsNone(recipe)

    def test_every_legal_fws_run_parses(self):
        for fws in ('\r\n\t', '\r\n ', '\r\n\t\t', '\r\n  ', '\r\n \t ',
                    '\n\t', '\n  '):
            with self.subTest(fws=repr(fws)):
                version, hashes, _ = parse_instance(self._fold_h(fws))
                self.assertEqual(version, 1)
                self.assertIsNotNone(
                    hashes, 'h= tag did not parse across the fold')
                self.assertEqual(hashes['h'][1], 'A' * 43 + '=')
                self.assertEqual(hashes['b'][1], 'B' * 43 + '=')

    def test_value_unfolded_to_a_space_parses(self):
        # verify_instance_raw takes the value from the raw octets, unfolded the
        # RFC 5322 way: the fold becomes WSP inside the base64 hash.
        for wsp in (' ', '\t', '  '):
            with self.subTest(wsp=repr(wsp)):
                _, hashes, _ = parse_instance(self._fold_h(wsp))
                self.assertIsNotNone(hashes, 'h= tag did not parse')
                self.assertEqual(hashes['h'][1], 'A' * 43 + '=')
                self.assertEqual(hashes['b'][1], 'B' * 43 + '=')

    def test_folded_recipe_parses(self):
        value = build_instance_value(
            2, b'\x00' * 32, b'\x01' * 32, header_recipe={'subject': []})
        r_start = value.index('r=') + 2
        folded = value[:r_start + 8] + '\r\n  ' + value[r_start + 8:]
        _, _, recipe = parse_instance(folded)
        self.assertEqual(recipe['h'], {'subject': []})


class TestParseInstanceSyntax(unittest.TestCase):
    """The whole Message-Instance value is a tag-list.

    A fragment that is not a tag, a byte outside x-tag-char, a repeated
    tag or a malformed hash-set makes the field a syntax error; it is
    not skipped with the rest verified.
    """

    layer = ConfigLayer

    H = 'sha256:' + 'A' * 43 + '=:' + 'B' * 43 + '='

    def _ok(self, value):
        return parse_instance(value)

    def _bad(self, value):
        with self.assertRaises(MalformedInstance) as cm:
            parse_instance(value)
        return str(cm.exception)

    def test_baseline_and_tolerated_forms(self):
        for value in ('m=1; h={};'.format(self.H),
                      'm=1;; h={};;'.format(self.H),
                      ' m = 1 ; h = {} ; x_ext9=a b'.format(self.H),
                      # Tag names are case-sensitive, so M= and H= are
                      # unknown tags beside m= and h=.
                      'm=1; M=7; h={0}; H=x'.format(self.H),
                      'm=1; h=sha256 :{}'.format(self.H[7:]),
                      'm=1; h= sha256\r\n\t:{}'.format(self.H[7:]),
                      'm=1; h=sha512:AAAA:AAAA, {}'.format(self.H),
                      # "|" groups of "," lists, with FWS around both.
                      'm=1; h=sha512:AAAA:AAAA | {}'.format(self.H),
                      'm=1; h=foo:AAAA:AAAA,{0}|sha512:CCCC:CCCC'.format(
                          self.H)):
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
        for value in ('m=1; h={0}; h={0}', 'm=1; m=1; h={0}'):
            with self.subTest(value=value):
                self._bad(value.format(self.H))

    def test_uppercase_tag_is_unknown(self):
        # "M=1" alone is no m= at all.
        self._bad('M=1; h={0}'.format(self.H))
        self._bad('m=1; H={0}'.format(self.H))

    def test_bad_hash_sets(self):
        for hset in ('sha 256:{}', 'sha\r\n 256:{}', 'sha256:{}:'):
            with self.subTest(hset=hset):
                self._bad('m=1; h=' + hset.format(self.H[7:]))
        self._bad('m=1; h=sha256:' + 'A' * 43 + '=')
        self._bad('m=1; h={0},{0}'.format(self.H))
        # A hash name appears once in the whole tag, and no member is
        # empty.
        self._bad('m=1; h={0}|{0}'.format(self.H))
        for value in ('{0}|', '|{0}', '{0}||sha512:AAAA:AAAA',
                      '{0},|sha512:AAAA:AAAA', 'foo:@@:!!|{0}'):
            with self.subTest(value=value):
                self._bad('m=1; h=' + value.format(self.H))

    def test_verify_instance_raw_reports_syntax_error(self):
        # A correctly hashed instance with a junk fragment does not verify.
        plain = b'From: anne@example.com\r\nSubject: hi\r\n\r\nHello.\r\n'
        mi1 = build_instance_value(1, compute_header_hash_raw(plain),
                                   compute_body_hash_raw(plain))
        good = b'Message-Instance: ' + mi1.encode('ascii') + b'\r\n' + plain
        self.assertEqual(verify_instance_raw(good), (1, None))
        bad = good.replace(b'm=1;', b'm=1; junk;', 1)
        self.assertEqual(verify_instance_raw(bad),
                         (0, 'Message-Instance m=1 syntax error'))

    def test_verify_instance_raw_hash_groups(self):
        # Every hash-set of a "," list must pass, and one "|" group must.
        # Mailman checks sha256 only; any other hash-set is not a pass.
        plain = b'From: anne@example.com\r\nSubject: hi\r\n\r\nHello.\r\n'
        hh = b64(compute_header_hash_raw(plain))
        bh = b64(compute_body_hash_raw(plain))
        good = 'sha256:{}:{}'.format(hh, bh)
        wrong = 'sha256:{}:{}'.format(hh, 'B' * 43 + '=')
        cases = {
            'sha512 | good sha256': ('sha512:AAAA:AAAA|' + good, 1),
            'bad sha256 | sha512': (wrong + '|sha512:AAAA:AAAA', 0),
            'good sha256, sha512 (unchecked)': (good + ',sha512:AAAA:AAAA', 0),
        }
        for what, (h, want) in cases.items():
            with self.subTest(what):
                raw = ('Message-Instance: m=1; h={};\r\n'.format(h)
                       .encode('ascii') + plain)
                self.assertEqual(verify_instance_raw(raw)[0], want)


class TestGetMaxInstanceVersion(unittest.TestCase):
    layer = ConfigLayer

    def test_no_instance(self):
        msg = _msg_from_bytes(b'From: anne@example.com\r\n\r\nbody\r\n')
        self.assertEqual(get_max_instance_version(msg), 0)

    def test_single_instance(self):
        msg = _msg_from_bytes(b'From: anne@example.com\r\n\r\nbody\r\n')
        msg['Message-Instance'] = 'm=1; h=abc'
        self.assertEqual(get_max_instance_version(msg), 1)

    def test_multiple_instance(self):
        msg = _msg_from_bytes(b'From: anne@example.com\r\n\r\nbody\r\n')
        msg['Message-Instance'] = 'm=1; h=abc'
        msg['Message-Instance'] = 'm=3; h=def'
        msg['Message-Instance'] = 'm=2; h=ghi'
        self.assertEqual(get_max_instance_version(msg), 3)


class TestFoldInstanceValue(unittest.TestCase):
    """fold_instance_value on a very large value."""

    layer = ConfigLayer

    def test_large_value_is_sane(self):
        b64 = base64.b64encode(bytes(range(256)) * 6 * 1024).decode('ascii')
        value = ('m=2; h=sha256:' + 'A' * 43 + '=:' + 'B' * 43 +
                 '=; r=' + b64 + ';')
        self.assertGreater(len(value), 2 * 1024 * 1024)
        folded = fold_instance_value(value)
        lines = folded.split('\r\n\t')
        self.assertLessEqual(len('Message-Instance: ') + len(lines[0]), 77)
        for line in lines[1:]:
            self.assertLessEqual(8 + len(line), 77)
        # A break at a tag boundary drops the space after the ";".
        unfolded = re.sub(r';\r\n\t', '; ', folded).replace('\r\n\t', '')
        self.assertEqual(unfolded, value)


class TestDraftVersion(unittest.TestCase):
    layer = ConfigLayer

    def test_draft_version_is_09(self):
        self.assertEqual(DKIM2_DRAFT, 'ietf-dkim-dkim2-spec-09')
        self.assertEqual(DKIM2_DATE, '2026-11-13')


class TestHeaderWireForm(unittest.TestCase):
    """The header hash and Recipe see the headers as they go on the wire."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.preferred_language = 'en'
        resources = ExitStack()
        self.addCleanup(resources.close)
        resources.enter_context(configuration('mta', message_instance='yes'))

    def test_header_hash_covers_the_wire_form_of_an_encoded_subject(self):
        # subject_prefix stores a non-ASCII Subject as a Header object; the
        # hash must be over what the generator emits (RFC 2047 encoded
        # words), not str(Header), which is the decoded text.
        raw = _crlf(b"""\
To: ant@example.com
From: aperson@example.com
Message-ID: <alpha>
Subject: =?ISO-2022-JP?B?UmU6IBskQiUkJXMlLSVlJVkhPCU/QjQ2SD1gSHcbKEI=?=
Content-Type: text/plain; charset=iso-2022-jp
Content-Transfer-Encoding: 7bit

\x1b$B%F%9%H\x1b(B
""")
        msg = _msg_from_bytes(raw)
        self._mlist.subject_prefix = '[Ant] '
        config.handlers['subject-prefix'].process(self._mlist, msg, {})
        self.assertNotIsInstance(msg['subject'], str)
        wire = email.message_from_bytes(msg.as_bytes(), Message)
        self.assertIn(b'=?iso-2022-jp?', msg.as_bytes())
        self.assertEqual(compute_header_hash(msg), compute_header_hash(wire))
        # And the Recipe side sees the same value the verifier will.
        collected = dict(collect_headers(msg))
        self.assertEqual(collected['Subject'], str(wire['subject']))

    def test_collected_header_values_are_unfolded(self):
        # A long encoded-word Subject arrives folded; the parser keeps the
        # fold in the value, but Recipe "d" strings may not contain CR or
        # LF.
        raw = _crlf(b"""\
To: ant@example.com
From: aperson@example.com
Message-ID: <alpha>
Subject: =?UTF-8?B?UmU6IEZsaW5rS2Fma2FQcm9kdWNlciDlvIDlkK9FeGNhdGx5IE9uY2U=?=
 =?UTF-8?B?5LmL5ZCOIOWIneWni+WMluS6i+WKoeeKtuaAgei2heaXtueahOmXrumimA==?=

body
""")
        msg = _msg_from_bytes(raw)
        for name, value in collect_headers(msg):
            self.assertNotIn('\n', value, name)
            self.assertNotIn('\r', value, name)
        subject = dict(collect_headers(msg))['Subject']
        self.assertIn('?= =?UTF-8?B?', subject)

    def test_header_hash_covers_the_wire_form_of_a_raw_8bit_subject(self):
        # Raw non-UTF-8 bytes in a Subject (2003-era spam, some current
        # senders): the prefix handler re-encodes it as unknown-8bit, and
        # the hash must follow the bytes actually sent.
        raw = _crlf(b"""\
To: ant@example.com
From: aperson@example.com
Message-ID: <alpha>
Subject: (\xb1\xa4---\xb0\xed) \xc0\xcc\xb8\xe1 500
Content-Type: text/plain; charset=euc-kr

body
""")
        msg = _msg_from_bytes(raw)
        self._mlist.subject_prefix = '[Ant] '
        config.handlers['subject-prefix'].process(self._mlist, msg, {})
        wire = email.message_from_bytes(msg.as_bytes(), Message)
        self.assertEqual(compute_header_hash(msg), compute_header_hash(wire))


class TestMessageInstanceFlow(unittest.TestCase):
    """Ingress, modification, egress, undo and verify."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.preferred_language = 'en'
        resources = ExitStack()
        self.addCleanup(resources.close)
        temporary_dir = resources.enter_context(TemporaryDirectory())
        resources.enter_context(
            configuration('paths.testing', template_dir=temporary_dir))
        resources.enter_context(configuration('mta', message_instance='yes'))
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
        _arrive(msg, self.RECEIVED)
        return msg

    def test_ingress_hashes_the_received_octets(self):
        msg = self._make_received_msg()
        # The premise: re-serializing is not byte-faithful here.
        self.assertNotEqual(msg.as_bytes().replace(b'\n', b'\r\n')
                            .replace(b'\r\r\n', b'\r\n'), self.RECEIVED)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        snapshot = msgdata['dkim2_snapshot']
        self.assertEqual(snapshot['header_hash'],
                         compute_header_hash_raw(self.RECEIVED))
        self.assertEqual(snapshot['body_hash'],
                         compute_body_hash_raw(self.RECEIVED))
        # Only the digests are kept, never the octets.
        self.assertFalse(hasattr(msg, 'original_bytes'))

    def test_unsigned_post_leaves_with_a_single_m1(self):
        # The list's MTA signs a post with no DKIM2-Signature as i=1.  It
        # leaves with one Message-Instance, m=1, over the message as sent,
        # whose Recipe goes back to the post as received.
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 0)
        self.assertIsNone(msg.get_all('x-dkim2-info'))
        self.assertEqual(msgdata['dkim2_snapshot']['version'], 0)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        mis = msg.get_all('message-instance')
        self.assertEqual(len(mis), 1)
        self.assertEqual(instance_version(mis[0]), 1)
        self.assertEqual(len(msg.get_all('x-dkim2-info')), 1)
        # Its hashes are the outgoing message's, and its Recipe gives back
        # the post as received.
        _, hashes, recipe = parse_instance(mis[0])
        self.assertEqual(hashes['h'][1], b64(compute_header_hash(msg)))
        self.assertEqual(hashes['b'][1],
                         b64(compute_body_hash_raw(wire_bytes(msg))))
        self.assertIn('b', recipe)
        _assert_undoes_to_snapshot(self, msg, msgdata)
        self.assertEqual(get_body_lines(msg),
                         body_lines_raw(self.RECEIVED))

    def test_unsigned_post_unchanged_still_gets_m1(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        mis = msg.get_all('message-instance')
        self.assertEqual(len(mis), 1)
        self.assertEqual(instance_version(mis[0]), 1)
        self.assertIsNone(_decode_recipe(mis[0]))
        self.assertEqual(verify_message_instance(msg), (1, None))

    def test_unsigned_instances_are_dropped(self):
        # An instance a sender (or inbound milter) added with no signature
        # over it is dropped: the post still leaves with a single m=1.
        msg = self._make_7bit_msg()
        msg['Message-Instance'] = build_instance_value(
            1, compute_header_hash(msg), compute_body_hash(msg))
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 0)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(len(msg.get_all('message-instance')), 1)
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_ingress_makes_the_arrival_record_without_the_lmtp_runner(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        raw = serialize_message(msg)
        self.assertEqual(msg.dkim2_arrival['body_hash'],
                         compute_body_hash_raw(raw))
        self.assertEqual(msg.header_log, {})
        self.assertFalse(hasattr(msg, 'original_bytes'))

    def test_no_instance_cache_directory(self):
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)
        self.assertFalse(
            os.path.exists(os.path.join(config.VAR_DIR, 'mi-cache')))

    def test_snapshot_survives_a_queue_pickle(self):
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msg, msgdata = pickle.loads(pickle.dumps((msg, msgdata)))
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_recipe_rebuilds_the_received_octets(self):
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        snapshot = msgdata['dkim2_snapshot']
        hashes1 = {'h': [None, b64(snapshot['header_hash'])],
                   'b': [None, b64(snapshot['body_hash'])]}
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)
        _, _, recipe = parse_instance(msg['message-instance'])
        body_lines, headers = plan_undo(msg, recipe)
        # Applied to what Mailman is about to send, the Recipe gives back
        # the octets that arrived, trailing space and bare boundary included.
        self.assertEqual(body_lines, body_lines_raw(self.RECEIVED))
        pairs = []
        seen = set()
        for name, value in collect_headers(msg):
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
        self.assertEqual(b64(hash_header_pairs(pairs)), hashes1['h'][1])
        rebuilt_body = ('\r\n'.join(body_lines) + '\r\n').encode(
            'utf-8', 'surrogateescape')
        self.assertEqual(
            b64(hashlib.sha256(rebuilt_body).digest()), hashes1['b'][1])

    def test_egress_records_bcc_removal(self):
        # smtplib.send_message drops Bcc; egress removes it first so the
        # Recipe says so and undoing it gives back what arrived.
        raw = (b'To: ant@example.com\r\n'
               b'From: aperson@example.com\r\n'
               b'Bcc: \r\n'
               b'Resent-Bcc: hidden@example.com\r\n'
               b'Message-ID: <alpha>\r\n'
               b'Subject: has a Bcc\r\n'
               b'\r\n'
               b'Hello world.\r\n')
        msg = _msg_from_bytes(raw)
        _arrive(msg, raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertIsNone(msg['bcc'])
        self.assertIsNone(msg['resent-bcc'])
        _, hashes2, recipe = parse_instance(msg['message-instance'])
        self.assertEqual(recipe['h']['bcc'], [{'d': ['']}])
        self.assertEqual(recipe['h']['resent-bcc'],
                         [{'d': ['hidden@example.com']}])
        # m=1 describes the message as it will be sent ...
        self.assertEqual(hashes2['h'][1], b64(compute_header_hash(msg)))
        # ... and its Recipe rebuilds the post as received.
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_egress_after_a_retry_adds_no_second_instance(self):
        # BulkDelivery decorates and runs egress on the queued message
        # itself; a temporary failure re-enqueues that stamped message to
        # the retry queue (with nodecorate), and egress runs again on it.
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)
        msg, msgdata = pickle.loads(pickle.dumps((msg, msgdata)))
        msgdata['nodecorate'] = True
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(len(msg.get_all('message-instance')), 1)
        self.assertEqual(len(msg.get_all('x-dkim2-info')), 1)
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_ingress_drops_the_arrival_record_when_the_list_opts_out(self):
        # A list with Message-Instance off on a site with it on carries
        # neither the arrival record nor the header log through the queues.
        self._mlist.dkim2_message_instance = False
        msg = self._make_received_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertFalse(hasattr(msg, 'dkim2_arrival'))
        self.assertIsNone(msg.header_log)
        self.assertEqual(get_max_instance_version(msg), 0)
        self.assertNotIn('dkim2_snapshot', msgdata)

    def test_ingress_without_an_arrival_record_still_works(self):
        # A message that did not come through the LMTP runner has no
        # arrival record: the parsed form is the baseline.
        msg = self._make_7bit_msg()
        self.assertFalse(hasattr(msg, 'dkim2_arrival'))
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_injected_message_stamp_goes_into_the_recipe(self):
        # `mailman inject` stamps Message-ID-Hash before the pipeline and
        # keeps no received octets.  The snapshot leaves Mailman's stamp out,
        # as the LMTP runner's octets do, and the m=1 Recipe records it.
        msg = self._make_7bit_msg()
        add_message_hash(msg)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertIn('message-id-hash', _recipe_at(msg, 1)['h'])
        _assert_undoes_to_snapshot(self, msg, msgdata)
        self.assertNotIn('Message-ID-Hash', msg)

    def test_injected_message_with_an_instance_records_the_stamp(self):
        # An injected message whose existing instance covers content with no
        # Message-ID-Hash: Mailman's stamp goes into the egress Recipe, and
        # undoing it gets back to the existing instance.
        msg = self._make_7bit_msg()
        msg['Message-Instance'] = build_instance_value(
            1, compute_header_hash(msg), compute_body_hash(msg))
        _sign_upstream(msg)
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
        msg['Message-Instance'] = build_instance_value(
            1, compute_header_hash(msg), compute_body_hash(msg))
        _sign_upstream(msg)
        msgdata = {}
        with self.assertLogs('mailman.dkim2', 'WARNING') as logs:
            self._ingress.process(self._mlist, msg, msgdata)
        self.assertIn('m=1 does not describe the message as received',
                      logs.output[0])
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)

    def test_ingress_adds_no_instance_to_an_unsigned_post(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 0)
        self.assertEqual(msgdata['dkim2_snapshot']['version'], 0)

    def test_info_is_a_tag_list(self):
        # X-DKIM2-Info is a tag-list in the DKIM2 syntax, every tag followed
        # by ';', and the action is mi-m=<N>.
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        # Unfold, then drop the whitespace a consumer ignores next to ';'
        # and ','.
        info = re.sub(r'([;,])\s+', r'\1', msg['x-dkim2-info']
                      .replace('\r\n\t', ' '))
        info = info.replace(';', '; ').rstrip()
        self.assertTrue(info.endswith(';'), info)
        self.assertRegex(info, r'^draft=\S+; repo=\S+; date=\d{4}-\d\d-\d\d; '
                               r'sw=mailman; action=mi-m=1; arrival=none; '
                               r'hc=\d+; hn=\S+;$')

    def test_ingress_does_not_add_v1_if_instance_exists(self):
        # A message arriving with an instance does not get a second m=1.
        msg = self._make_7bit_msg()
        # Simulate a message that already has m=1 from a prior hop.
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        existing_instance = build_instance_value(1, h_hash, b_hash)
        msg['Message-Instance'] = existing_instance
        _sign_upstream(msg)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # Should still be exactly one Message-Instance header.
        instance_values = msg.get_all('message-instance')
        self.assertEqual(len(instance_values), 1)
        # And it should be the original one.
        self.assertEqual(str(instance_values[0]), existing_instance)
        # Snapshot should still be created.
        self.assertIn('dkim2_snapshot', msgdata)

    def test_ingress_keeps_corrupted_instance_and_flags_it_stale(self):
        # A stale incoming Message-Instance, which a DKIM2-Signature may
        # cover, is kept as it is: no second instance is added, and no
        # X-DKIM2-Info either, as accepting an instance adds no header.
        msg = self._make_7bit_msg()
        # Build a well-formed Message-Instance m=1 but corrupt the hashes.
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        good_instance = build_instance_value(1, h_hash, b_hash)
        corrupt_instance = good_instance.replace(
            b64(h_hash), 'AAAA', 1).replace(b64(b_hash), 'BBBB', 1)
        msg['Message-Instance'] = corrupt_instance
        _sign_upstream(msg)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # The corrupt instance is still there, untouched, and alone.
        instance_values = msg.get_all('message-instance')
        self.assertEqual(len(instance_values), 1,
                         'ingress must not add a second instance')
        self.assertEqual(str(instance_values[0]), corrupt_instance,
                         'ingress must not rewrite an incoming instance')
        # It still does not verify -- that is the point.
        version, error = verify_message_instance(msg)
        self.assertEqual(version, 0)
        self.assertIsNotNone(error)
        # No X-DKIM2-Info is emitted for accepting an existing instance.
        self.assertEqual(msg.get_all('x-dkim2-info', []), [])
        # Snapshot must still be present.
        self.assertIn('dkim2_snapshot', msgdata)

    def test_ingress_skips_digest(self):
        msg = self._make_7bit_msg()
        msgdata = {'isdigest': True}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 0)

    def test_ingress_runs_with_nodecorate(self):
        # The snapshot is still taken when nodecorate is set (e.g. owner
        # pipeline messages).  nodecorate controls decoration, not instance.
        msg = self._make_7bit_msg()
        msgdata = {'nodecorate': True}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertIn('dkim2_snapshot', msgdata)

    def test_egress_no_change_no_new_instance(self):
        # A signed post the list did not change gets no instance of its own.
        msg = self._make_7bit_msg()
        msg['Message-Instance'] = build_instance_value(
            1, compute_header_hash(msg), compute_body_hash(msg))
        _sign_upstream(msg)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)

    def test_egress_without_snapshot_adds_originator_v1(self):
        msg = self._make_7bit_msg()
        self._egress.process(self._mlist, msg, {})
        self.assertEqual(get_max_instance_version(msg), 1)

    def test_egress_adds_v1_after_decoration(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)
        version, error = verify_message_instance(msg)
        self.assertEqual(version, 1, error)

    def test_undo_7bit_restores_the_post(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_undo_7bit_with_added_headers_restores_the_post(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msg['List-Id'] = '<ant.example.com>'
        msg['List-Unsubscribe'] = '<mailto:ant-leave@example.com>'
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_undo_qp_unnecessarily_quoted_restores_the_post(self):
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
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_undo_base64_weird_lines_restores_the_post(self):
        content = b'Base64 with 50-char lines for DKIM2 instance undo test.'
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
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_undo_base64_very_short_lines_restores_the_post(self):
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
        _assert_undoes_to_snapshot(self, msg, msgdata)

    def test_existing_instance_preserved_and_v2_added(self):
        # A message with an existing m=1 gets m=2 at egress.
        msg = self._make_7bit_msg()
        # Add Message-Instance m=1 externally (simulating a prior hop).
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        external_instance = build_instance_value(1, h_hash, b_hash)
        msg['Message-Instance'] = external_instance
        _sign_upstream(msg)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # Ingress does not add another Message-Instance.
        self.assertEqual(len(msg.get_all('message-instance')), 1)
        # Modify and egress.
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        # It has m=1 and m=2.
        self.assertEqual(get_max_instance_version(msg), 2)
        self.assertEqual(len(msg.get_all('message-instance')), 2)
        # m=2 should verify.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)

    def test_existing_instance_undo_v2_then_v1_verifies(self):
        # Undo m=2 on a message that arrived with m=1.
        msg = self._make_7bit_msg()
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        external_instance = build_instance_value(1, h_hash, b_hash)
        msg['Message-Instance'] = external_instance
        _sign_upstream(msg)
        # Verify the external Message-Instance m=1 is valid.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        msg['List-Id'] = '<ant.example.com>'
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        # Undoing m=2 gives back the message m=1 describes.
        undo_message_instance(msg)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_existing_instance_undo_v2_restores_original_body(self):
        # After undo, the body matches what m=1 was computed over.
        msg = self._make_7bit_msg()
        original_body_hash = compute_body_hash(msg)
        h_hash = compute_header_hash(msg)
        external_instance = build_instance_value(1, h_hash, original_body_hash)
        msg['Message-Instance'] = external_instance
        _sign_upstream(msg)
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

    def test_existing_instance_undo_v2_restores_original_headers(self):
        # After undo, the header hash matches what m=1 covered.
        msg = self._make_7bit_msg()
        original_header_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        external_instance = build_instance_value(
            1, original_header_hash, b_hash)
        msg['Message-Instance'] = external_instance
        _sign_upstream(msg)
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

    def test_config_disabled_skips_ingress(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        with configuration('mta', message_instance='no'):
            self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 0)
        self.assertNotIn('dkim2_snapshot', msgdata)

    def test_config_disabled_skips_egress(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        with configuration('mta', message_instance='no'):
            self._egress.process(self._mlist, msg, msgdata)
        # No instance at all: egress skipped (ingress adds none to an
        # unsigned post).
        self.assertEqual(get_max_instance_version(msg), 0)

    def test_egress_adds_originator_v1_without_snapshot(self):
        # A message with no snapshot, as on the virgin pipeline, gets m=1.
        msg = self._make_7bit_msg()
        msgdata = {}
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_egress_originator_skips_if_instance_exists(self):
        msg = self._make_7bit_msg()
        msg['Message-Instance'] = 'm=1; h=existing'
        msgdata = {}
        self._egress.process(self._mlist, msg, msgdata)
        instance_values = msg.get_all('message-instance')
        self.assertEqual(len(instance_values), 1)

    def test_instance_header_is_prepended(self):
        # The newest Message-Instance comes before every header of the
        # original message.  Only X-DKIM2-Info, prepended after the instance
        # it describes, is above it.
        def first_instance_index(msg):
            keys = msg.keys()
            for i, name in enumerate(keys):
                if name.lower() == 'message-instance':
                    return i, keys
            self.fail('no Message-Instance header found')

        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # After egress with modifications, the new instance is on top.
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        idx, keys = first_instance_index(msg)
        self.assertTrue(
            all(k.lower() == 'x-dkim2-info' for k in keys[:idx]),
            f'unexpected headers above Message-Instance: {keys[:idx]}')
        self.assertNotIn('From', keys[:idx])

    def test_instance_header_line_lengths(self):
        # Every Message-Instance header line is at most 78 characters.
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
                    f'instance line too long ({len(line)}): {line!r}')

    def test_instance_header_no_orphaned_tails(self):
        # Folded instance values have no orphaned 1-2 character tails.
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
                            f'Orphaned tail in instance: {stripped!r}')

    def test_instance_header_no_leading_semicolon(self):
        # Continuation lines do not start with a semicolon.
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
        resources = ExitStack()
        self.addCleanup(resources.close)
        resources.enter_context(configuration('mta', message_instance='yes'))
        self._ingress = config.handlers['message-instance-ingress']
        self._egress = config.handlers['message-instance-egress']

    RAW = _crlf(b"""\
To: ant@example.com
From: aperson@example.com
Message-ID: <alpha>
MIME-Version: 1.0
Content-Type: multipart/mixed; boundary="b"

--b
Content-Type: text/plain

hello
--b
Content-Type: application/x-kitten
Content-Transfer-Encoding: base64

AAAA
--b--
""")

    def _msg(self):
        return _arrive(_msg_from_bytes(self.RAW), self.RAW)

    def _filtered_and_egressed(self):
        msg, msgdata = self._msg(), {}
        self._ingress.process(self._mlist, msg, msgdata)
        self._mlist.filter_content = True
        self._mlist.filter_types = ['application/x-kitten']
        config.handlers['mime-delete'].process(self._mlist, msg, msgdata)
        msg['Subject'] = '[ant] hi'
        self._egress.process(self._mlist, msg, msgdata)
        self.assertNotIn('body-modified', msgdata)
        return msg

    def test_egress_emits_null_body_recipe(self):
        msg = self._filtered_and_egressed()
        recipe = _recipe_at(msg, 1)
        self.assertIn('b', recipe)
        self.assertIsNone(recipe['b'])
        # Only "b" may be null; the header Recipe is real.
        self.assertIn('h', recipe)
        self.assertIsNotNone(recipe['h'])
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)
        # The kitten is not in the Recipe.
        self.assertNotIn('AAAA', msg['message-instance'])

    def test_dmarc_wrap_emits_null_body_recipe(self):
        msg, msgdata = self._msg(), {}
        self._ingress.process(self._mlist, msg, msgdata)
        wrap_message(self._mlist, msg, msgdata)
        self.assertNotIn('body-modified', msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        self.assertIsNone(_recipe_at(msg, 1)['b'])

    def test_undo_null_body_restores_headers_only(self):
        msg = self._filtered_and_egressed()
        self.assertEqual(undo_message_instance(msg), 1)
        self.assertIsNone(msg['subject'])

    def test_undecorated_from_line(self):
        # No decoration, body as received, but smtplib sends ">From".
        raw = _crlf(b"""\
To: ant@example.com
From: aperson@example.com
Message-ID: <beta>

Hi.
From here
""")
        msg, msgdata = _arrive(_msg_from_bytes(raw), raw), {}
        self._ingress.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        steps = _recipe_at(msg, 1)['b']
        self.assertEqual(steps, [{'c': [1, 1]}, {'d': ['From here']}])
        _assert_undoes_to_snapshot(
            self, _msg_from_bytes(_smtplib_wire(msg)), msgdata)


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
    """Arrive, ingress, decorate, egress, then undo the list's m=1.

    The message must come back as the post as received (by its digests).
    With layout, the post's lines are one copy range.  Returns the
    message as it would be sent.
    """
    msg = _arrive(_msg_from_bytes(raw), raw)
    msgdata = {}
    test._ingress.process(test._mlist, msg, msgdata)
    decorate.process(test._mlist, msg, msgdata)
    test.assertIn('dkim2-decoration', msgdata)
    test._egress.process(test._mlist, msg, msgdata)
    wire = normalize_crlf(msg.as_bytes())
    test.assertEqual(msg.get_content_type(), 'multipart/mixed')
    recipe = _recipe_at(msg, 1)
    if layout:
        test.assertEqual(len(recipe['b']), 1, recipe['b'])
        test.assertEqual(len(_copy_steps(recipe['b'])), 1, recipe['b'])
    _assert_undoes_to_snapshot(test, msg, msgdata)
    return wire


class TestDKIM2Wrap(unittest.TestCase):
    """On a DKIM2 list decorate wraps the body octets as they arrived."""

    layer = ConfigLayer

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.preferred_language = 'en'
        resources = ExitStack()
        self.addCleanup(resources.close)
        temporary_dir = resources.enter_context(TemporaryDirectory())
        resources.enter_context(
            configuration('paths.testing', template_dir=temporary_dir))
        resources.enter_context(configuration('mta', message_instance='yes'))
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

    HDRS = _crlf(b"""\
To: ant@example.com
From: aperson@example.com
Message-ID: <alpha>
MIME-Version: 1.0
""")

    def test_7bit(self):
        raw = self.HDRS + _crlf(b"""\
Content-Type: text/plain; charset=us-ascii
Content-Transfer-Encoding: 7bit

Hello.
""")
        wire = _wrap_round_trip(self, raw)
        self.assertIn(b'List Header', wire)
        self.assertIn(b'List Footer', wire)

    def test_from_line_is_a_literal(self):
        # smtplib sends ">From me"; the Recipe gives back "From me".
        raw = self.HDRS + _crlf(b"""\
Content-Type: text/plain

Hello.
From me
Bye.
""")
        msg = _arrive(_msg_from_bytes(raw), raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        steps = _recipe_at(msg, 1)['b']
        self.assertEqual(len(_copy_steps(steps)), 2, steps)
        self.assertEqual(_data_steps(steps), [{'d': ['From me']}])
        # As the recipient gets it.
        _assert_undoes_to_snapshot(
            self, _msg_from_bytes(_smtplib_wire(msg)), msgdata)

    def test_8bit_from_line_is_a_literal(self):
        # With 8-bit octets in the payload, CPython's BytesGenerator writes
        # its ">From " into the message itself (here, as the digest's MMDF
        # write would), so the message has lost the post's own text: the
        # Recipe still gives back "From ".
        raw = self.HDRS + _crlf(b"""\
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: 8bit

Caf\xc3\xa9.
From me
""")
        msg = _arrive(_msg_from_bytes(raw), raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        BytesGenerator(BytesIO()).flatten(msg)
        self.assertIn('>From me', msg.get_payload())
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        steps = _recipe_at(msg, 1)['b']
        self.assertEqual(_data_steps(steps), [{'d': ['From me']}], steps)
        _assert_undoes_to_snapshot(
            self, _msg_from_bytes(_smtplib_wire(msg)), msgdata)

    def test_qp_bytes_untouched(self):
        body = b'=48=65=6C=6C=6F=\r\n world\r\n'
        raw = self.HDRS + _crlf(b"""\
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: quoted-printable

""") + body
        wire = _wrap_round_trip(self, raw)
        self.assertIn(body, wire)

    def test_base64_bytes_untouched(self):
        body = b'SGVs\r\nbG8u\r\n'
        raw = self.HDRS + _crlf(b"""\
Content-Type: text/plain; charset=utf-8
Content-Transfer-Encoding: base64

""") + body
        self.assertIn(body, _wrap_round_trip(self, raw))

    def test_8bit_latin1_bytes_untouched(self):
        body = b'Gr\xfc\xdfe aus K\xf6ln\r\n'
        raw = self.HDRS + _crlf(b"""\
Content-Type: text/plain; charset=iso-8859-1
Content-Transfer-Encoding: 8bit

""") + body
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
        raw = _crlf(b"""\
To: ant@example.com
From: aperson@example.com
Message-ID: <alpha>

plain old mail
""")
        _wrap_round_trip(self, raw)

    def test_boundary_collision(self):
        # The generator picks the wrap's boundary and steps past one the
        # wrapped text already uses as a delimiter line.
        with patch('email.generator.random.randrange', return_value=0):
            taken = email.generator.Generator._make_boundary()
            raw = self.HDRS + (b'Content-Type: text/plain\r\n\r\n--'
                               + taken.encode() + b'\r\nnot a part\r\n')
            wire = _wrap_round_trip(self, raw)
        self.assertIn('boundary="{}.0"'.format(taken).encode(), wire)

    def test_list_flag_off_uses_upstream_decoration(self):
        self._mlist.dkim2_message_instance = False
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.\r\n'
        msg = _msg_from_bytes(raw)
        _arrive(msg, raw)
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg.get_content_type(), 'text/plain')
        self.assertIn('List Footer', msg.get_payload())

    def test_smtplib_wire_matches_hashed_body(self):
        # What smtplib.send_message puts on the wire must be what egress
        # hashed: check m=1's body hash against the CRLF wire body.  The
        # 8-bit octets survive; smtplib turns the From_ line into >From.
        body = b'K\xf6ln\r\nFrom here\r\n'
        raw = self.HDRS + _crlf(b"""\
Content-Type: text/plain; charset=iso-8859-1
Content-Transfer-Encoding: 8bit

""") + body
        msg = _msg_from_bytes(raw)
        _arrive(msg, raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        wire = _smtplib_wire(msg)
        self.assertIn(b'K\xf6ln\r\n>From here\r\n', wire)
        self.assertIsNone(re.search(rb'(?<!\r)\n', wire))
        v, err = verify_instance_raw(wire)
        self.assertEqual(v, 1, err)

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
        _arrive(msg, raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        wire = _smtplib_wire(msg)
        self.assertIn(b'>From the list', wire)
        v, err = verify_instance_raw(wire)
        self.assertEqual(v, 1, err)
        # And the Recipe still undoes to the post as received.  (m=1 itself
        # is checked on the wire bytes above: verify_message_instance
        # judges the parsed, unmangled view.)
        self.assertEqual(undo_message_instance(msg), 1)
        self.assertEqual(compute_header_hash(msg),
                         msgdata['dkim2_snapshot']['header_hash'])
        self.assertEqual(compute_body_hash(msg),
                         msgdata['dkim2_snapshot']['body_hash'])

    def test_inbound_from_line_without_received_octets(self):
        # A signed inbound m=1 made over the octets as sent, with a body line
        # starting "From ", on a message with no arrival record (not
        # through the LMTP runner): ingress must judge it unmangled, the
        # wire must match m=2, and undoing m=2 must give back what m=1
        # describes.
        plain = self.HDRS + _crlf(b"""\
Content-Type: text/plain

Hello.
From x
""")
        mi1 = build_instance_value(1, compute_header_hash_raw(plain),
                                   compute_body_hash_raw(plain))
        raw = (b'DKIM2-Signature: ' + UPSTREAM_SIGNATURE.encode('ascii')
               + b'\r\nMessage-Instance: ' + mi1.encode('ascii') + b'\r\n'
               + plain)
        msg = _msg_from_bytes(raw)
        self.assertEqual(verify_instance_raw(raw)[0], 1)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(msgdata['dkim2_snapshot']['version'], 1)
        decorate.process(self._mlist, msg, msgdata)
        self.assertEqual(msg.get_content_type(), 'multipart/mixed')
        self._egress.process(self._mlist, msg, msgdata)
        wire = _smtplib_wire(msg)
        v, err = verify_instance_raw(wire)
        self.assertEqual(v, 2, err)
        self.assertEqual(undo_message_instance(msg), 2)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)
        self.assertIn('From x', get_body_lines(msg))

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
        raw = self.HDRS + _crlf(b"""\
Content-Type: multipart/mixed; boundary="inner"

This is a multi-part message in MIME format.
--inner
Content-Type: text/plain

Hi
--inner
Content-Type: application/octet-stream
Content-Transfer-Encoding: base64

AAAA
--inner--
epilogue
""")
        # Header and footer parts added among the post's own lines: its
        # preamble, its parts, its close and epilogue are copy ranges.
        wire = _wrap_round_trip(self, raw, layout=False)
        steps = _recipe_at(_msg_from_bytes(wire), 1)['b']
        self.assertEqual(len(steps), 3, steps)
        self.assertEqual(len(_copy_steps(steps)), 3, steps)
        self.assertIn(b'--inner--\r\nepilogue\r\n', wire)
        self.assertIn(b'This is a multi-part message in MIME format.', wire)

    def test_broken_mime_missing_final_boundary(self):
        # No closing delimiter at all: the octets still go through as
        # they came, and the Recipe still rebuilds them.
        raw = self.HDRS + _crlf(b"""\
Content-Type: multipart/alternative; boundary="b"

--b
Content-Type: text/plain

Hi
--b
Content-Type: text/html

<p>Hi</p>
""")
        # The generator closes the multipart on the way out; the Recipe
        # copies only the post's own lines.
        _wrap_round_trip(self, raw)

    def test_body_that_is_also_the_list_header(self):
        # The post's one line is also the list header's: the Recipe copies
        # the post's own line.
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nList Header\r\n'
        _wrap_round_trip(self, raw)

    def test_body_with_trailing_empty_lines(self):
        # Trailing empty lines are not lines: the copy range stops before
        # them although the wrap carries them.
        raw = self.HDRS + _crlf(b"""\
Content-Type: text/plain

Hello.


""")
        _wrap_round_trip(self, raw)

    def test_body_without_final_line_break(self):
        raw = self.HDRS + b'Content-Type: text/plain\r\n\r\nHello.'
        _wrap_round_trip(self, raw)

    def test_boundaryless_multipart_ingress_wire_unchanged(self):
        # A multipart with no boundary is not multipart once parsed, so
        # serializing it before ingress resolves nothing and the wire is
        # the same either way.
        raw = self.HDRS + _crlf(b"""\
Content-Type: multipart/mixed

--x
Content-Type: text/plain

Hi
--x--
""")
        wires = []
        for serialize_first in (False, True):
            msg = _msg_from_bytes(raw)
            _arrive(msg, raw)
            if serialize_first:
                serialize_message(msg)
            self._ingress.process(self._mlist, msg, {})
            self.assertFalse(msg.is_multipart())
            wires.append(wire_bytes(msg))
        self.assertEqual(wires[0], wires[1])


class TestNormalizeCRLF(unittest.TestCase):
    layer = ConfigLayer

    @staticmethod
    def _three_pass(raw):
        raw = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
        return raw.replace(b'\n', b'\r\n')

    def test_pure_crlf_is_returned_uncopied(self):
        raw = b'a: b\r\n\r\nline\r\n'
        self.assertIs(normalize_crlf(raw), raw)

    def test_same_octets_as_three_passes(self):
        for raw in (b'', b'a', b'a\nb', b'a\rb', b'a\r\r\nb', b'a\n\rb',
                    b'\r\n\r', b'\n\n\r\r', b'x\r\n\n\ry\r', b'\r\n'):
            self.assertEqual(normalize_crlf(raw), self._three_pass(raw),
                             raw)


class TestDKIM2DistributionCopies(unittest.TestCase):
    """What a DKIM2 list does to every copy it distributes.

    Each copy carries the exploded hint for the signing milter, and a copy
    the list changed, or a post that arrived unsigned (the list's hop
    becomes i=1), leaves From the list's posting address so that From stays
    authentic for the list's signing domain.
    """

    layer = ConfigLayer

    RAW = _crlf(b"""\
To: ant@example.com
From: Anne <anne@example.com>
Message-ID: <dist>
Subject: hello
MIME-Version: 1.0
Content-Type: text/plain

Hello.
""")

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        resources = ExitStack()
        self.addCleanup(resources.close)
        resources.enter_context(configuration('mta', message_instance='yes'))

    def _signed(self, raw=None):
        """raw (RAW by default) with a matching m=1 under a DKIM2-Signature,
        as an upstream signer sends it.  (Neither header is hashed; the
        signature is not checked here.)"""
        if raw is None:
            raw = self.RAW
        mi1 = build_instance_value(1, compute_header_hash_raw(raw),
                                   compute_body_hash_raw(raw))
        return (b'DKIM2-Signature: i=1; m=1; t=1; d=example.com; '
                b'mf=<anne@example.com>; rt=<ant@example.com>; '
                b's1=sel; b1=AAAA;\r\n'
                b'Message-Instance: ' + mi1.encode('ascii') + b'\r\n'
                + raw)

    def _distribute(self, raw):
        """Post raw through the posting pipeline and deliver its copy:
        decoration, then Message-Instance egress, as Deliver does."""
        msg = _msg_from_bytes(raw)
        _arrive(msg, raw)
        process_pipeline(self._mlist, msg, {}, 'default-posting-pipeline')
        item = get_queue_messages('out', expected_count=1)[0]
        out, outdata = item.msg, item.msgdata
        decorate.process(self._mlist, out, outdata)
        config.handlers['message-instance-egress'].process(
            self._mlist, out, outdata)
        return out

    def _assert_from_rewritten(self, out):
        self.assertEqual(parseaddr(out['From'])[1],
                         self._mlist.posting_address)
        # The header Recipe holds the From the post arrived with ...
        recipe = _recipe_at(out, get_max_instance_version(out))
        self.assertEqual(len(recipe['h']['from']), 1)
        (literal,), = recipe['h']['from'][0].values()
        self.assertIn('anne@example.com', literal)
        # ... and undoing it gives back the instance below.
        version = get_max_instance_version(out)
        v, err = verify_message_instance(out)
        self.assertEqual(v, version, err)
        self.assertEqual(undo_message_instance(out), version)
        self.assertEqual(parseaddr(out['From'])[1],
                         'anne@example.com')
        v, err = verify_message_instance(out)
        self.assertEqual(v, version - 1, err)

    def test_distribution_copy_has_exploded_hint(self):
        out = self._distribute(self.RAW)
        self.assertEqual(out.get_all('X-DKIM2-Exploded'), ['yes'])

    def test_exploded_hint_once_after_a_retry(self):
        out = self._distribute(self.RAW)
        outdata = {'dkim2_snapshot': {
            'version': 1, 'header_hash': b'', 'body_hash': b'',
            'body_length': 0, 'body_lines': 0, 'from_lines': []}}
        config.handlers['message-instance-egress'].process(
            self._mlist, out, outdata)
        self.assertEqual(out.get_all('X-DKIM2-Exploded'), ['yes'])

    def test_no_exploded_hint_when_the_list_opts_out(self):
        self._mlist.dkim2_message_instance = False
        out = self._distribute(self.RAW)
        self.assertIsNone(out.get_all('X-DKIM2-Exploded'))

    def test_from_rewritten_when_list_modifies_or_originates(self):
        with self.subTest('signed post, modified (subject prefix)'):
            out = self._distribute(self._signed())
            self.assertTrue(str(out['Subject']).startswith('[Ant] '))
            self._assert_from_rewritten(out)
        with self.subTest('unsigned post'):
            out = self._distribute(self.RAW)
            self._assert_from_rewritten(out)

    def test_defer_to_mta_keeps_from_on_a_modified_signed_post(self):
        # The sending MTA takes From over and signs, so the list leaves the
        # author's From and records only its own changes, in an m=2 that
        # undoes cleanly to the author's m=1.
        self._mlist.dmarc_mitigate_action = DMARCMitigateAction.defer_to_mta
        out = self._distribute(self._signed())
        self.assertTrue(str(out['Subject']).startswith('[Ant] '))
        self.assertEqual(out['From'], 'Anne <anne@example.com>')
        self.assertIsNone(out['Reply-To'])
        self.assertEqual(get_max_instance_version(out), 2)
        self.assertNotIn('from', _recipe_at(out, 2)['h'])
        v, err = verify_message_instance(out)
        self.assertEqual(v, 2, err)
        self.assertEqual(undo_message_instance(out), 2)
        self.assertEqual(str(out['Subject']), 'hello')
        v, err = verify_message_instance(out)
        self.assertEqual(v, 1, err)

    def test_defer_to_mta_unsigned_post_gets_no_instance(self):
        # Nobody signed the post and the list does not take From over, so
        # an unsigned m=1 would only make DKIM2 verifiers report an error.
        # If the MTA takes From over it starts the chain itself.
        self._mlist.dmarc_mitigate_action = DMARCMitigateAction.defer_to_mta
        out = self._distribute(self.RAW)
        self.assertEqual(out['From'], 'Anne <anne@example.com>')
        self.assertIsNone(out.get_all('message-instance'))
        self.assertIsNone(out.get_all('x-dkim2-info'))
        # The copy is still one of many.
        self.assertEqual(out.get_all('X-DKIM2-Exploded'), ['yes'])

    def test_defer_to_mta_drops_unsigned_instances_on_arrival(self):
        # Unsigned instances an unsigned post arrives with are dropped, as
        # on any DKIM2 list, and none is added in their place.
        self._mlist.dmarc_mitigate_action = DMARCMitigateAction.defer_to_mta
        mi1 = build_instance_value(1, compute_header_hash_raw(self.RAW),
                                   compute_body_hash_raw(self.RAW))
        out = self._distribute(
            b'Message-Instance: ' + mi1.encode('ascii') + b'\r\n' + self.RAW)
        self.assertIsNone(out.get_all('message-instance'))

    def test_unsigned_post_leaves_with_one_m1_over_the_list_from(self):
        # The list's MTA signs this as i=1 with the list's domain, so the
        # message m=1 hashes carries the list's From.  (There is no DKIM2
        # signer here, so the structure is checked.)
        out = self._distribute(self.RAW)
        mis = out.get_all('message-instance')
        self.assertEqual(len(mis), 1)
        self.assertEqual(instance_version(mis[0]), 1)
        # Its hashes are the outgoing message's, with the list's From ...
        self.assertEqual(parseaddr(out['From'])[1],
                         self._mlist.posting_address)
        _, hashes, recipe = parse_instance(mis[0])
        self.assertEqual(hashes['h'][1], b64(compute_header_hash(out)))
        self.assertEqual(hashes['b'][1],
                         b64(compute_body_hash_raw(wire_bytes(out))))
        # ... and its Recipe restores the author's From and the post as
        # received.
        (literal,), = recipe['h']['from'][0].values()
        self.assertEqual(parseaddr(literal)[1], 'anne@example.com')
        self.assertEqual(undo_message_instance(out), 1)
        self.assertEqual(parseaddr(out['From'])[1], 'anne@example.com')
        self.assertEqual(compute_header_hash(out),
                         compute_header_hash_raw(self.RAW))
        self.assertEqual(compute_body_hash(out),
                         compute_body_hash_raw(self.RAW))

    def test_signed_post_keeps_its_chain_and_gets_m2(self):
        out = self._distribute(self._signed())
        self.assertEqual(
            sorted(instance_version(v)
                   for v in out.get_all('message-instance')), [1, 2])

    def test_from_kept_on_an_unmodified_signed_post(self):
        # A signed post the list forwards unchanged keeps its From: the
        # author's signature still covers it.
        msg = _msg_from_bytes(self._signed())
        msgdata = {'original_sender': 'anne@example.com', 'nodecorate': True}
        config.handlers['message-instance-ingress'].process(
            self._mlist, msg, msgdata)
        config.handlers['dmarc'].process(self._mlist, msg, msgdata)
        self.assertEqual(msg['From'], 'Anne <anne@example.com>')

    def test_decorated_list_decides_without_a_body_hash(self):
        # A list that adds a header or footer changes every copy anyway.
        # The From decision never serializes the body.
        msg = _msg_from_bytes(self._signed())
        msgdata = {'original_sender': 'anne@example.com'}
        config.handlers['message-instance-ingress'].process(
            self._mlist, msg, msgdata)
        with patch('mailman.handlers.dmarc.decorate',
                   return_value='The footer'), \
                patch('mailman.handlers.message_instance.wire_bytes',
                      side_effect=AssertionError('body serialized')):
            config.handlers['dmarc'].process(self._mlist, msg, msgdata)
        self.assertEqual(parseaddr(msg['From'])[1],
                         self._mlist.posting_address)

    def test_from_rewritten_when_the_body_was_rewritten(self):
        # No header change and no decoration, but a handler rewrote the
        # body: egress will record that, so From must be the list's.
        msg = _msg_from_bytes(self._signed())
        msgdata = {'original_sender': 'anne@example.com', 'nodecorate': True}
        config.handlers['message-instance-ingress'].process(
            self._mlist, msg, msgdata)
        msg.set_payload('Rewritten.\n')
        config.handlers['dmarc'].process(self._mlist, msg, msgdata)
        self.assertEqual(parseaddr(msg['From'])[1],
                         self._mlist.posting_address)

    def test_from_rewritten_when_smtplib_will_escape_a_from_line(self):
        # Nothing changed, but smtplib will send "From " as ">From ".
        raw = self.RAW.replace(b'Hello.\r\n', b'Hello.\r\nFrom here.\r\n')
        msg = _msg_from_bytes(self._signed(raw))
        msgdata = {'original_sender': 'anne@example.com', 'nodecorate': True}
        config.handlers['message-instance-ingress'].process(
            self._mlist, msg, msgdata)
        config.handlers['dmarc'].process(self._mlist, msg, msgdata)
        self.assertEqual(parseaddr(msg['From'])[1],
                         self._mlist.posting_address)

    def test_from_kept_when_content_filtering_changed_nothing(self):
        # Content filtering sets the payload back unchanged.
        msg = _msg_from_bytes(self._signed())
        msgdata = {'original_sender': 'anne@example.com', 'nodecorate': True}
        config.handlers['message-instance-ingress'].process(
            self._mlist, msg, msgdata)
        msg.set_payload(msg.get_payload())
        config.handlers['dmarc'].process(self._mlist, msg, msgdata)
        self.assertEqual(msg['From'], 'Anne <anne@example.com>')

    def test_from_rewritten_on_an_unsigned_post_even_if_unmodified(self):
        msg = _msg_from_bytes(self.RAW)
        msgdata = {'original_sender': 'anne@example.com', 'nodecorate': True}
        config.handlers['message-instance-ingress'].process(
            self._mlist, msg, msgdata)
        config.handlers['dmarc'].process(self._mlist, msg, msgdata)
        self.assertEqual(parseaddr(msg['From'])[1],
                         self._mlist.posting_address)

    def test_from_rewritten_when_decoration_is_to_come(self):
        # Nothing changed yet, but delivery will add the footer.
        msg = _msg_from_bytes(self._signed())
        msgdata = {'original_sender': 'anne@example.com'}
        config.handlers['message-instance-ingress'].process(
            self._mlist, msg, msgdata)
        with patch('mailman.handlers.dmarc.decorate',
                   return_value='a footer'):
            config.handlers['dmarc'].process(self._mlist, msg, msgdata)
        self.assertEqual(parseaddr(msg['From'])[1],
                         self._mlist.posting_address)

    def test_from_kept_when_the_list_has_no_dkim2(self):
        self._mlist.dkim2_message_instance = False
        msg = _msg_from_bytes(self.RAW)
        msgdata = {'original_sender': 'anne@example.com'}
        config.handlers['dmarc'].process(self._mlist, msg, msgdata)
        self.assertEqual(msg['From'], 'Anne <anne@example.com>')


ARRIVAL_RAW = _crlf(b"""\
From: anne@example.com
To: ant@example.com
Message-ID: <arr>
Subject: hi

Hello.
""")


def _with_instance(raw, *versions):
    """raw under a matching Message-Instance for each of these versions."""
    h = compute_header_hash_raw(raw)
    b = compute_body_hash_raw(raw)
    heads = b''.join(
        b'Message-Instance: ' + build_instance_value(v, h, b).encode() +
        b'\r\n' for v in versions)
    return heads + raw


class TestArrivalRecord(unittest.TestCase):
    """arrival_record: the digests of a post as received, and whether the
    Message-Instance it arrived with (if any) describes it."""

    layer = ConfigLayer

    RAW = ARRIVAL_RAW

    def test_no_instance(self):
        record = arrival_record(self.RAW)
        self.assertEqual(record, {
            'header_hash': compute_header_hash_raw(self.RAW),
            'body_hash': compute_body_hash_raw(self.RAW),
            'body_length': 6, 'body_lines': 1, 'from_lines': [],
            'version': 0, 'status': 'none'})

    def test_body_shape(self):
        # Trailing empty lines do not count; "From " lines are found by
        # line and by offset into the body.
        raw = _crlf(b"""\
From: anne@example.com

From the top.
>From escaped.
Middle.
From the end.


""")
        record = arrival_record(raw)
        self.assertEqual(record['body_lines'], 4)
        self.assertEqual(record['body_length'], 53)
        self.assertEqual(record['from_lines'], [[1, 0], [4, 40]])

    def test_empty_body(self):
        record = arrival_record(b'From: anne@example.com\r\n\r\n')
        self.assertEqual(record['body_lines'], 0)
        self.assertEqual(record['body_length'], 0)
        self.assertEqual(record['from_lines'], [])

    def test_matching_instance(self):
        record = arrival_record(_with_instance(self.RAW, 1))
        self.assertEqual(record['status'], 'match')
        self.assertEqual(record['version'], 1)
        # Message-Instance is not hashed: same digests as without it.
        self.assertEqual(record['header_hash'],
                         compute_header_hash_raw(self.RAW))

    def test_edited_after_instance(self):
        raw = _with_instance(self.RAW, 1).replace(b'Hello.', b'Goodbye.')
        record = arrival_record(raw)
        self.assertEqual(record['status'], 'mismatch')
        self.assertEqual(record['version'], 1)

    def test_duplicate_m_is_malformed(self):
        self.assertEqual(
            arrival_record(_with_instance(self.RAW, 1, 1))['status'],
            'malformed')

    def test_unparseable_is_malformed(self):
        raw = b'Message-Instance: m=1; garbage\r\n' + self.RAW
        self.assertEqual(arrival_record(raw)['status'], 'malformed')


class TestArrivalIngress(unittest.TestCase):
    """The ingress handler takes its snapshot from the arrival record."""

    layer = ConfigLayer

    RAW = ARRIVAL_RAW

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        resources = ExitStack()
        self.addCleanup(resources.close)
        resources.enter_context(configuration('mta', message_instance='yes'))
        self._ingress = config.handlers['message-instance-ingress']

    def _arrived(self, raw):
        # As the LMTP runner leaves it.
        msg = _msg_from_bytes(raw)
        msg.dkim2_arrival = arrival_record(raw)
        msg.start_header_log()
        return msg

    def test_snapshot_from_arrival_record(self):
        msg = self._arrived(self.RAW)
        # A change after arrival does not move the snapshot.
        msg['Subject'] = 'changed'
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(msgdata['dkim2_snapshot'], {
            'version': 0,
            'header_hash': compute_header_hash_raw(self.RAW),
            'body_hash': compute_body_hash_raw(self.RAW),
            'body_length': 6, 'body_lines': 1, 'from_lines': []})
        self.assertEqual(msgdata['dkim2_arrival_status'], 'none')

    def test_signed_post_mismatch_is_logged(self):
        raw = _with_instance(self.RAW, 1)
        raw = (b'DKIM2-Signature: i=1; m=1; d=example.com\r\n'
               + raw.replace(b'Hello.', b'Goodbye.'))
        msg = self._arrived(raw)
        msgdata = {}
        with self.assertLogs('mailman.dkim2', 'WARNING') as logs:
            self._ingress.process(self._mlist, msg, msgdata)
        self.assertIn('mismatch', logs.output[0])
        self.assertEqual(msgdata['dkim2_arrival_status'], 'mismatch')
        self.assertEqual(msgdata['dkim2_snapshot']['version'], 1)

    def test_injected_message_gets_a_record_and_a_log(self):
        msg = _msg_from_bytes(self.RAW)
        add_message_hash(msg)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(msg.dkim2_arrival['header_hash'],
                         compute_header_hash_raw(self.RAW))
        self.assertEqual(msg.header_log, {'message-id-hash': []})
        self.assertEqual(msgdata['dkim2_arrival_status'], 'none')

    def test_list_opt_out_drops_record_and_log(self):
        self._mlist.dkim2_message_instance = False
        msg = self._arrived(self.RAW)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertFalse(hasattr(msg, 'dkim2_arrival'))
        self.assertIsNone(msg.header_log)
        self.assertNotIn('dkim2_snapshot', msgdata)


class TestHeaderRecipeFromLog(unittest.TestCase):
    """Egress builds the header Recipe from the header log: logged names
    get back the values they arrived with, every other field is as it
    arrived.  A Recipe that does not rebuild the arrival digests is never
    emitted."""

    layer = ConfigLayer

    RAW = _crlf(b"""\
From: anne@example.com
To: ant@example.com
Cc: bart@example.com
Cc: cate@example.com
Message-ID: <log>
Subject: hello
References: <one@example.com>
 <two@example.com>

Hello.
""")

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        resources = ExitStack()
        self.addCleanup(resources.close)
        resources.enter_context(configuration('mta', message_instance='yes'))
        self._ingress = config.handlers['message-instance-ingress']
        self._egress = config.handlers['message-instance-egress']

    def _arrive(self, raw, signed=False):
        if signed:
            h = compute_header_hash_raw(raw)
            b = compute_body_hash_raw(raw)
            raw = (b'DKIM2-Signature: i=1; m=1; d=example.com\r\n'
                   b'Message-Instance: '
                   + build_instance_value(1, h, b).encode() + b'\r\n'
                   + raw)
        msg = _msg_from_bytes(raw)
        _arrive(msg, raw)
        msg.dkim2_arrival = arrival_record(raw)
        msg.start_header_log()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        return msg, msgdata

    def _edit(self, msg):
        msg.replace_header('Subject', '[Ant] hello')
        msg['Reply-To'] = 'ant@example.com'
        del msg['cc']

    def _recipe(self, msg):
        _, _, recipe = parse_instance(msg['message-instance'])
        return recipe

    def test_logged_changes_are_restored(self):
        msg, msgdata = self._arrive(self.RAW, signed=True)
        self._edit(msg)
        self._egress.process(self._mlist, msg, msgdata)
        recipe = self._recipe(msg)['h']
        self.assertEqual(recipe['subject'], [{'d': ['hello']}])
        self.assertEqual(recipe['reply-to'], [])
        # Bottom-up, as the spec numbers fields.
        self.assertEqual(recipe['cc'],
                         [{'d': ['cate@example.com', 'bart@example.com']}])
        # The folded References was never touched.
        self.assertNotIn('references', recipe)
        _assert_undoes_to_snapshot(self, msg, msgdata, version=2)

    def test_signed_post_unlogged_change_adds_no_instance(self):
        msg, msgdata = self._arrive(self.RAW, signed=True)
        self._edit(msg)
        # A change made behind the log's back.
        email.message.Message.replace_header(msg, 'From', 'sneaky')
        with self.assertLogs('mailman.dkim2', 'ERROR'):
            self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)

    def test_unsigned_post_unlogged_change_omits_header_recipe(self):
        msg, msgdata = self._arrive(self.RAW)
        self._edit(msg)
        email.message.Message.replace_header(msg, 'From', 'sneaky')
        with self.assertLogs('mailman.dkim2', 'ERROR'):
            self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)
        self.assertNotIn('h', self._recipe(msg) or {})

    def test_no_log_counts_as_mismatch(self):
        # A message queued before the header log existed.
        msg, msgdata = self._arrive(self.RAW, signed=True)
        msg.stop_header_log()
        self._edit(msg)
        with self.assertLogs('mailman.dkim2', 'ERROR'):
            self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_instance_version(msg), 1)

    def test_previous_header_pairs(self):
        msg, msgdata = self._arrive(self.RAW)
        self.assertIsNotNone(previous_header_pairs(msg))
        self._edit(msg)
        self.assertEqual(hash_header_pairs(previous_header_pairs(msg)),
                         msgdata['dkim2_snapshot']['header_hash'])
        msg.stop_header_log()
        self.assertIsNone(previous_header_pairs(msg))


class TestWireBytesLeavesTheLogAlone(unittest.TestCase):
    """wire_bytes drops Bcc from a copy, as smtplib does; the copy must
    not write that into the message's own header log."""

    layer = ConfigLayer

    def test_bcc_not_logged(self):
        msg = _msg_from_bytes(b'From: anne@example.com\r\n'
                              b'Bcc: bart@example.com\r\n\r\nHi.\r\n')
        msg.start_header_log()
        wire_bytes(msg)
        self.assertEqual(msg.header_log, {})
        self.assertEqual(msg['bcc'], 'bart@example.com')


class TestArrivalInInfo(unittest.TestCase):
    """X-DKIM2-Info beside the list's instance reports the arrival check."""

    layer = ConfigLayer

    RAW = ARRIVAL_RAW

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        resources = ExitStack()
        self.addCleanup(resources.close)
        resources.enter_context(configuration('mta', message_instance='yes'))
        self._ingress = config.handlers['message-instance-ingress']
        self._egress = config.handlers['message-instance-egress']

    def _info(self, raw):
        msg = _arrive(_msg_from_bytes(raw), raw)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        msg['Subject'] = '[Ant] hi'
        self._egress.process(self._mlist, msg, msgdata)
        return re.sub(r'\s+', ' ', msg['x-dkim2-info'])

    def _signed(self, edit=False):
        raw = _with_instance(self.RAW, 1)
        if edit:
            raw = raw.replace(b'Hello.', b'Goodbye.')
        return b'DKIM2-Signature: i=1; m=1; d=example.com\r\n' + raw

    def test_match(self):
        self.assertIn(' arrival=match;', self._info(self._signed()))

    def test_mismatch(self):
        self.assertIn(' arrival=mismatch;',
                      self._info(self._signed(edit=True)))

    def test_unsigned_post(self):
        self.assertIn(' arrival=none;', self._info(self.RAW))

    def test_originator_has_no_arrival(self):
        msg = _msg_from_bytes(self.RAW)
        self._egress.process(self._mlist, msg, {})
        self.assertNotIn('arrival=', msg['x-dkim2-info'])


class TestArrivalRecordHostileInput(unittest.TestCase):
    """The arrival check runs in the LMTP runner, where an exception
    tempfails the post for every recipient: hostile instances must come
    back 'malformed', never raise."""

    layer = ConfigLayer

    RAW = ARRIVAL_RAW

    def test_huge_m(self):
        raw = (b'Message-Instance: m=' + b'9' * 5000
               + b'; h=sha256:AAAA:AAAA;\r\n' + self.RAW)
        self.assertEqual(arrival_record(raw)['status'], 'malformed')

    def test_three_digit_m(self):
        raw = b'Message-Instance: m=100; h=sha256:AAAA:AAAA;\r\n' + self.RAW
        self.assertEqual(arrival_record(raw)['status'], 'malformed')

    def test_deeply_nested_recipe(self):
        recipe = base64.b64encode(b'[' * 200000 + b']' * 200000)
        raw = (b'Message-Instance: m=1; h=sha256:AAAA:AAAA; r='
               + recipe + b';\r\n' + self.RAW)
        self.assertEqual(arrival_record(raw)['status'], 'malformed')


class TestWireBytesSerializesOnce(unittest.TestCase):
    """wire_bytes settles a multipart's boundary on the message without
    writing the whole message out an extra time."""

    layer = ConfigLayer

    def test_boundary_resolved_without_as_bytes(self):
        msg = _msg_from_bytes(b'From: anne@example.com\r\n\r\nHi.\r\n')
        outer = Message()
        outer['From'] = 'anne@example.com'
        outer['Content-Type'] = 'multipart/mixed'
        outer.set_payload([msg])
        with patch.object(Message, 'as_bytes',
                          side_effect=AssertionError('extra serialization')):
            wire = wire_bytes(outer)
        boundary = outer.get_boundary()
        self.assertIsNotNone(boundary)
        self.assertIn('--{}--'.format(boundary).encode(), wire)

    def test_same_octets_as_smtplib(self):
        # Written with LF and made CRLF, as smtplib writes it with CRLF.
        raws = [
            b'From: anne@example.com\r\nSubject: ' + b'word ' * 40 + b'\r\n'
            b'Content-Type: text/plain; charset=utf-8\r\n'
            b'Content-Transfer-Encoding: 8bit\r\n\r\n'
            b'Caf\xc3\xa9\r\nFrom here\r\nbare\rcr\r\nbare\nlf\r\n',
            b'From: anne@example.com\r\nContent-Type: multipart/mixed;\r\n'
            b' boundary="b"\r\n\r\npre\r\n--b\r\nContent-Type: text/plain'
            b'; charset="us-ascii" \r\n\r\nHi\r\n--b--\r\nepi',
            b'From: anne@example.com\r\n\r\n',
        ]
        for raw in raws:
            with self.subTest(raw=raw[:40]):
                msg = _msg_from_bytes(raw)
                buf = BytesIO()
                BytesGenerator(buf).flatten(copy.deepcopy(msg),
                                            linesep='\r\n')
                self.assertEqual(wire_bytes(msg), buf.getvalue())


ORIGINAL = _crlf(b"""\
From: Anne Person <anne@example.com>
To: test@lists.example.com
Subject: hello there
Message-ID: <orig-1@example.com>
Date: Wed, 24 Jun 2026 20:00:00 +0000
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: 7bit

Original body line one.
Original body line two.
""")


# A bare post, with no MIME headers at all.
BARE_ORIGINAL = _crlf(b"""\
From: Anne Person <anne@example.com>
To: test@lists.example.com
Subject: hello there
Message-ID: <orig-1@example.com>
Date: Wed, 24 Jun 2026 20:00:00 +0000

Capture body.
""")


def _round_trip(modify, original=ORIGINAL):
    """Apply modify to the original, build the egress recipe, undo it, and
    return (ok, detail) for whether the result matches m=1."""
    orig = _msg_from_bytes(original)
    h1 = compute_header_hash(orig)
    b1 = compute_body_hash(orig)
    prev_headers = collect_headers(orig)
    prev_body = get_body_lines(orig)
    mod = _msg_from_bytes(orig.as_bytes())
    modify(mod)
    header_recipe = compute_header_recipe(collect_headers(mod), prev_headers)
    # The previous body as literals: these tests are about the headers.
    body_recipe = (None if get_body_lines(mod) == prev_body
                   else [{'d': prev_body}])
    mi2 = build_instance_value(2, compute_header_hash(mod),
                               compute_body_hash(mod), header_recipe,
                               body_recipe)
    mi1 = build_instance_value(1, h1, b1)
    # Prepend m=1 then m=2 (highest first), as on the wire.
    mod.prepend_header('Message-Instance', mi1)
    mod.prepend_header('Message-Instance', mi2)
    undo_message_instance(mod)
    got_h = b64(compute_header_hash(mod))
    got_b = b64(compute_body_hash(mod))
    detail = []
    if got_h != b64(h1):
        detail.append('header hash {} != {}'.format(got_h, b64(h1)))
    if got_b != b64(b1):
        detail.append('body hash {} != {}'.format(got_b, b64(b1)))
    return (not detail), '; '.join(detail)


class TestInstanceRoundTrip(unittest.TestCase):
    """The egress Recipe for a list's changes undoes to the m=1 hashes."""

    layer = ConfigLayer

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
        # The author sends a bare message with no MIME headers, and Mailman
        # adds them (plus list headers, subject prefix and footer).  Undo
        # removes the headers Mailman added.
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
        ok, detail = _round_trip(m, BARE_ORIGINAL)
        self.assertTrue(ok, detail)

    def test_full_list_transformation(self):
        # Everything a list does at once.
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


class _CapturingDeliver(Deliver):
    """Deliver, keeping each copy instead of sending it."""

    def __init__(self):
        super().__init__()
        self.copies = []

    def _deliver_to_recipients(self, mlist, msg, msgdata, recipients):
        self.copies.append((msg, msgdata))
        return []


class TestEveryHeaderFeature(unittest.TestCase):
    """Every copy of a post on a list using every header-changing feature
    undoes to the post as received."""

    layer = ConfigLayer

    RAW = _crlf(b"""\
From: Carl <carl@example.com>
To: ant@example.com
Cc: bart@example.com, someone@example.net
Subject: every feature
Message-ID: <every@example.com>
Date: Fri, 13 Nov 2026 12:00:00 +0000
Return-Receipt-To: carl@example.com
Disposition-Notification-To: carl@example.com
MIME-Version: 1.0
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: 8bit

Caf\xc3\xa9.
From the list
""")

    def setUp(self):
        self._mlist = create_list('ant@example.com')
        self._mlist.preferred_language = 'en'
        self._mlist.subject_prefix = '[Ant] '
        self._mlist.reply_goes_to_list = ReplyToMunging.explicit_header
        self._mlist.reply_to_address = 'discuss@example.com'
        self._mlist.first_strip_reply_to = True
        self._mlist.dmarc_mitigate_action = DMARCMitigateAction.munge_from
        self._mlist.dmarc_mitigate_unconditionally = True
        self._mlist.personalize = Personalization.full
        subscribe(self._mlist, 'Anne', email='anne@example.com')
        subscribe(self._mlist, 'Bart', email='bart@example.com')
        resources = ExitStack()
        self.addCleanup(resources.close)
        temporary_dir = resources.enter_context(TemporaryDirectory())
        resources.enter_context(
            configuration('paths.testing', template_dir=temporary_dir))
        resources.enter_context(configuration('mta', message_instance='yes'))
        site_dir = os.path.join(config.TEMPLATE_DIR, 'site', 'en')
        os.makedirs(site_dir)
        for name, text in (('h.txt', 'Header for $user_address'),
                           ('f.txt', 'Footer for $user_address')):
            with open(os.path.join(site_dir, name), 'w',
                      encoding='utf-8') as fp:
                print(text, file=fp)
        manager = getUtility(ITemplateManager)
        manager.set('list:member:regular:header', None, 'mailman:///h.txt')
        manager.set('list:member:regular:footer', None, 'mailman:///f.txt')

    def test_every_copy_undoes_to_the_post(self):
        self._check(self.RAW, version=1)

    def test_signed_post(self):
        mi1 = build_instance_value(
            1, arrival_record(self.RAW)['header_hash'],
            arrival_record(self.RAW)['body_hash'])
        raw = (b'DKIM2-Signature: i=1; m=1; d=example.com\r\n'
               b'Message-Instance: ' + mi1.encode() + b'\r\n' + self.RAW)
        self._check(raw, version=2)

    def _check(self, raw, version):
        msg = email.message_from_bytes(raw, Message)
        msg.dkim2_arrival = arrival_record(raw)
        msg.start_header_log()
        add_message_hash(msg)
        process_pipeline(self._mlist, msg, {}, 'default-posting-pipeline')
        item = get_queue_messages('out', expected_count=1)[0]
        agent = _CapturingDeliver()
        agent.deliver(self._mlist, item.msg, item.msgdata)
        self.assertEqual(len(agent.copies), 2)
        for copy_msg, _ in agent.copies:
            # As the recipient gets it.
            wire = email.message_from_bytes(wire_bytes(copy_msg), Message)
            with self.subTest(to=str(copy_msg['To'])):
                self.assertTrue(str(wire['Subject']).startswith('[Ant]'))
                recipe = [parse_instance(v)[2]
                          for v in wire.get_all('message-instance')
                          if 'm={};'.format(version) in v][0]
                # The body comes back from the wrap, From line and all.
                self.assertEqual(_data_steps(recipe['b']),
                                 [{'d': ['From the list']}])
                self.assertEqual(get_max_instance_version(wire), version)
                self.assertEqual(verify_message_instance(wire),
                                 (version, None))
                self.assertEqual(undo_message_instance(wire), version)
                self.assertEqual(compute_header_hash(wire),
                                 msg.dkim2_arrival['header_hash'])
                self.assertEqual(compute_body_hash(wire),
                                 msg.dkim2_arrival['body_hash'])
