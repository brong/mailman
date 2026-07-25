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
    _should_exclude_header,
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
        # Test vector from the DKIM2 interop suite (simple-ed25519.eml).
        # The MI v=1 hashes were computed by the reference Python signer.
        raw = (
            b'DKIM2-Signature: i=1; v=1; t=1740000000; '
            b'd=test1.dkim2.com; '
            b'm=eyJtZiI6InNlbmRlckB0ZXN0MS5ka2ltMi5jb20iLCJydCI6'
            b'WyJyZWNpcGllbnRAZXhhbXBsZS5jb20iXX0=; '
            b's=W1siZWQyNTUxOSIsImVkMjU1MTkiLCJZT0V0Q1l0d2U4NUl6'
            b'UXlscENhYm9abDdnamN3aUlFUjE2cHhZdWx2TlpIYTBMemFD'
            b'dmMwZ0lJTlZTbFdDeDcxTUNTVURaTnlmTnJOUVlxV2Fyd29B'
            b'QT09Il1d\r\n'
            b'Message-Instance: v=1; '
            b'h=eyJoIjpbInNoYTI1NiIsIlNMdHprNkxPNjhDQ2FYNGVkcko2'
            b'eWZwV2JwM2h3Z3ZJOElkTUJSTERrK1k9Il0sImIiOlsic2hh'
            b'MjU2IiwiU2dHNWZOR0VnMXgyNE13SXRDVVlHREhRa1dLbmcw'
            b'NlcxL0l2VEdCZHd6VT0iXX0=\r\n'
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
        msg2['Message-Instance'] = 'v=1; h=xxx'
        msg2['DKIM-Signature'] = 'v=1; ...'
        self.assertEqual(h1, compute_header_hash(msg2))

    def test_body_hash_trailing_blank_lines(self):
        raw1 = b'From: a@b.com\r\n\r\nbody\r\n'
        raw2 = b'From: a@b.com\r\n\r\nbody\r\n\r\n\r\n'
        msg1 = _msg_from_bytes(raw1)
        msg2 = _msg_from_bytes(raw2)
        self.assertEqual(compute_body_hash(msg1), compute_body_hash(msg2))


class TestBodyLines(unittest.TestCase):

    def test_simple_body(self):
        msg = _msg_from_bytes(
            b'From: a@b.com\r\n\r\nline1\r\nline2\r\n')
        self.assertEqual(_get_body_lines(msg), ['line1', 'line2'])

    def test_empty_body(self):
        msg = _msg_from_bytes(b'From: a@b.com\r\n\r\n')
        self.assertEqual(_get_body_lines(msg), [])


# =====================================================================
# Unit tests for recipe computation
# =====================================================================

class TestBodyRecipe(unittest.TestCase):

    def test_identical_bodies(self):
        lines = ['Hello', 'World']
        self.assertIsNone(compute_body_recipe(lines, lines))

    def test_pure_append(self):
        # Fast path: appended lines only — recipe is a single copy of the
        # original N lines, no diff required.
        recipe = compute_body_recipe(
            ['Hello', 'World', 'Footer appended by list'],
            ['Hello', 'World'])
        self.assertEqual(recipe, [{'c': [1, 2]}])

    def test_append_to_empty_original(self):
        # Previous body was empty; recipe is empty (discard all appended lines).
        recipe = compute_body_recipe(['Footer added by list'], [])
        self.assertEqual(recipe, [])

    def test_prepend_and_append(self):
        recipe = compute_body_recipe(
            ['Header', 'Hello', 'World', 'Footer'],
            ['Hello', 'World'])
        self.assertEqual(recipe, [[2, 3]])

    def test_body_completely_different(self):
        recipe = compute_body_recipe(
            ['New line 1', 'New line 2'],
            ['Old line 1', 'Old line 2'])
        self.assertEqual(recipe, ['Old line 1', 'Old line 2'])

    def test_interleaved_changes(self):
        recipe = compute_body_recipe(
            ['A', 'X', 'B', 'Y', 'C'],
            ['A', 'B', 'C'])
        self.assertEqual(recipe, [[1, 1], [3, 3], [5, 5]])


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
        self.assertEqual(recipe['reply-to'], ['list@example.com'])

    def test_modified_header(self):
        recipe = compute_header_recipe(
            [('Subject', '[List] Hello')],
            [('Subject', 'Hello')])
        self.assertEqual(recipe['subject'], ['Hello'])

    def test_excluded_headers_ignored(self):
        self.assertIsNone(compute_header_recipe(
            [('From', 'a@b.com'), ('Received', 'from mx')],
            [('From', 'a@b.com'), ('Received', 'from mx2')]))


class TestMIHeaderValue(unittest.TestCase):

    def test_v1_no_recipe(self):
        value = build_mi_header_value(1, b'\x00' * 32, b'\x01' * 32)
        self.assertTrue(value.startswith('v=1;'))
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


class TestParseMIFolding(unittest.TestCase):
    """Folded Message-Instance values must parse (spec-04 §2.12).

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
        msg['Message-Instance'] = 'v=1; h=abc'
        self.assertEqual(get_max_mi_version(msg), 1)

    def test_multiple_mi(self):
        msg = _msg_from_bytes(b'From: a@b.com\r\n\r\nbody\r\n')
        msg['Message-Instance'] = 'v=1; h=abc'
        msg['Message-Instance'] = 'v=3; h=def'
        msg['Message-Instance'] = 'v=2; h=ghi'
        self.assertEqual(get_max_mi_version(msg), 3)


class TestDraftVersion(unittest.TestCase):

    def test_draft_version_is_04(self):
        from mailman.handlers.message_instance import DKIM2_DRAFT, DKIM2_DATE
        self.assertEqual(DKIM2_DRAFT, 'ietf-dkim-dkim2-spec-04')
        self.assertEqual(DKIM2_DATE, '2026-07-05')


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
            b'This is a very long line that the original sender chose to brea=\r\n'
            b'k at this specific point for some reason.\r\n'
        )
        msg = _msg_from_bytes(raw)
        decorate.process(self._mlist, msg, {})
        self.assertEqual(msg['content-transfer-encoding'], 'quoted-printable')
        payload = msg.get_payload()
        self.assertIn(
            'This is a very long line that the original sender chose to brea=\n'
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

    def test_ingress_adds_v1(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 1)
        self.assertIn('mi_snapshot', msgdata)

    def test_ingress_v1_verifies(self):
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        version, error = verify_message_instance(msg)
        self.assertEqual(version, 1)
        self.assertIsNone(error)

    def test_ingress_does_not_add_v1_if_mi_exists(self):
        """A message arriving with an existing MI must not get a second v=1."""
        msg = self._make_7bit_msg()
        # Simulate a message that already has MI v=1 from a prior hop.
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

    def test_ingress_strips_corrupted_mi_and_adds_fresh_v1(self):
        """A corrupt incoming MI must be stripped; a fresh v=1 replaces it."""
        msg = self._make_7bit_msg()
        # Build a well-formed MI v=1 but corrupt the hashes.
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        good_mi = build_mi_header_value(1, h_hash, b_hash)
        # Inject deliberately wrong hashes by replacing the real ones.
        corrupt_mi = good_mi.replace(_b64(h_hash), 'AAAA', 1).replace(
            _b64(b_hash), 'BBBB', 1)
        msg['Message-Instance'] = corrupt_mi
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # The corrupted MI should have been stripped and replaced.
        mi_values = msg.get_all('message-instance')
        self.assertEqual(len(mi_values), 1,
                         'Expected exactly one MI header after reset')
        # The replacement MI v=1 must verify.
        version, error = verify_message_instance(msg)
        self.assertEqual(version, 1, error)
        self.assertIsNone(error)
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
        """Egress adds MI v=1 for originated messages (no snapshot)."""
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
    # Undo tests: undo MI v=2, verify MI v=1
    # -----------------------------------------------------------------

    def test_undo_7bit_then_v1_verifies(self):
        """After undoing MI v=2, MI v=1 must verify against the message."""
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        # Sanity: v=2 verifies before undo.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        # Undo MI v=2.
        undone = undo_message_instance(msg)
        self.assertEqual(undone, 2)
        self.assertEqual(get_max_mi_version(msg), 1)
        # MI v=1 must now verify.
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
        # Undo and verify v=1.
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
        # Undo and verify v=1.
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
    # Pre-existing MI: message arrives with MI v=1 from prior hop
    # -----------------------------------------------------------------

    def test_existing_mi_preserved_and_v2_added(self):
        """A message with an existing MI v=1 should get MI v=2 at egress."""
        msg = self._make_7bit_msg()
        # Add MI v=1 externally (simulating a prior hop).
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
        # Should now have v=1 and v=2.
        self.assertEqual(get_max_mi_version(msg), 2)
        self.assertEqual(len(msg.get_all('message-instance')), 2)
        # v=2 should verify.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)

    def test_existing_mi_undo_v2_then_v1_verifies(self):
        """Undo MI v=2 on a message that arrived with MI v=1."""
        msg = self._make_7bit_msg()
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        external_mi = build_mi_header_value(1, h_hash, b_hash)
        msg['Message-Instance'] = external_mi
        # Verify the external MI v=1 is valid.
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        decorate.process(self._mlist, msg, msgdata)
        msg['List-Id'] = '<ant.example.com>'
        self._egress.process(self._mlist, msg, msgdata)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 2, err)
        # Undo v=2 → message should revert to the state where v=1 verifies.
        undo_message_instance(msg)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_existing_mi_undo_v2_restores_original_body(self):
        """After undo, the body must match what MI v=1 was computed over."""
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
        """After undo, the header hash must match what MI v=1 covered."""
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
        for val in msg.get_all('message-instance', []):
            if re.search(r'v\s*=\s*2', str(val)):
                recipe = _decode_mi_recipe(val)
                break
        else:
            self.fail('MI v=2 not found')
        body_recipe = recipe.get('b', [])
        self.assertEqual(len(body_recipe), 1)
        self.assertIsInstance(body_recipe[0], list)

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
        for val in msg.get_all('message-instance', []):
            if re.search(r'v\s*=\s*2', str(val)):
                recipe = _decode_mi_recipe(val)
                break
        body_recipe = recipe.get('b', [])
        literals = [r for r in body_recipe if isinstance(r, str)]
        self.assertEqual(len(literals), 0,
                         f'Recipe should have no literals: {literals}')

    def test_base64_recipe_has_one_literal_for_last_line(self):
        # Base64 re-encoding at the original line width produces
        # matching lines for all complete blocks, but the last line
        # changes (original had padding, new doesn't).  The recipe
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
        for val in msg.get_all('message-instance', []):
            if re.search(r'v\s*=\s*2', str(val)):
                recipe = _decode_mi_recipe(val)
                break
        body_recipe = recipe.get('b', [])
        ranges = [r for r in body_recipe if isinstance(r, list)]
        literals = [r for r in body_recipe if isinstance(r, str)]
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
            # Should still be v=1 only — egress skipped.
            self.assertEqual(get_max_mi_version(msg), 1)
        finally:
            config.pop('mi_off')

    def test_egress_adds_originator_v1_without_snapshot(self):
        """Messages without a snapshot (e.g. VirginPipeline) get MI v=1."""
        msg = self._make_7bit_msg()
        msgdata = {}  # No snapshot — simulates VirginPipeline path.
        self._egress.process(self._mlist, msg, msgdata)
        self.assertEqual(get_max_mi_version(msg), 1)
        v, err = verify_message_instance(msg)
        self.assertEqual(v, 1, err)

    def test_egress_originator_skips_if_mi_exists(self):
        """Egress originator path does not duplicate existing MI."""
        msg = self._make_7bit_msg()
        msg['Message-Instance'] = 'v=1; h=existing'
        msgdata = {}
        self._egress.process(self._mlist, msg, msgdata)
        # Should not have added another MI header.
        mi_values = msg.get_all('message-instance')
        self.assertEqual(len(mi_values), 1)

    def test_mi_header_is_prepended(self):
        """MI headers should appear at the top, not the bottom."""
        msg = self._make_7bit_msg()
        msgdata = {}
        self._ingress.process(self._mlist, msg, msgdata)
        # MI v=1 should be the first header.
        first_header = msg.keys()[0]
        self.assertEqual(first_header, 'Message-Instance')
        # After egress with modifications, MI v=2 should also be first.
        decorate.process(self._mlist, msg, msgdata)
        self._egress.process(self._mlist, msg, msgdata)
        first_header = msg.keys()[0]
        self.assertEqual(first_header, 'Message-Instance')

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
