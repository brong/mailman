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

"""Message-Instance header support for DKIM2.

Implements Message-Instance header computation as defined in
draft-ietf-dkim-dkim2-spec-06.  A Message-Instance header records cryptographic
hashes of the message headers and body at a point in the delivery chain, along
with optional diff recipes that allow undoing changes made at each hop.
"""

import base64
import copy
import email
import hashlib
import json
import logging
import os
import re

from difflib import SequenceMatcher
from lazr.config import as_boolean
from mailman.config import config
from mailman.core.i18n import _
from mailman.email.message import Message
from mailman.interfaces.handler import IHandler
from mailman.utilities.filesystem import makedirs, safe_remove
from public import public
from zope.interface import implementer


log = logging.getLogger('mailman.dkim2')

# DKIM2 implementation metadata — update DKIM2_DATE on each change.
DKIM2_DRAFT = 'ietf-dkim-dkim2-spec-06'
DKIM2_REPO = 'github.com/brong/mailman'
DKIM2_DATE = '2026-10-04'
DKIM2_SOFTWARE = 'mailman'


_INFO_HEADER_NAME_LEN = len('X-DKIM2-Info: ')
_INFO_TAB_WIDTH = 8
_INFO_MAX_LINE = 78


def _dkim2_info(action, **extras):
    """Build an X-DKIM2-Info header value.

    Extra keyword arguments are appended as additional tag=value pairs.
    None values are silently omitted.

    Per draft-gondwana-dkim2-debug-header-01 the value is a tag-list in the
    DKIM2 syntax: every tag, the last included, is followed by ';'.  A ';'
    ends a tag and has no escape, so one inside a value becomes ','.

    The value is folded only after a ';' (or, for an over-long single tag,
    after a ',') so no line exceeds the RFC 5322 recommendation of 78
    characters -- the hn= list of hashed header names on its own can run well
    past that -- and never inside a token.  X-DKIM2-Info is excluded from the
    header hash by the X-* prefix rule, so how it is folded never affects a
    signature.
    """
    segments = [
        'draft={}'.format(DKIM2_DRAFT),
        'repo={}'.format(DKIM2_REPO),
        'date={}'.format(DKIM2_DATE),
        'sw={}'.format(DKIM2_SOFTWARE),
        'action={}'.format(action),
    ]
    for k in sorted(extras):
        if extras[k] is not None:
            segments.append('{}={}'.format(k, extras[k]))

    # Terminate every segment with ';', then split any piece that cannot fit
    # a line of its own at commas.
    pieces = []
    for seg in segments:
        piece = seg.replace(';', ',') + ';'
        budget = _INFO_MAX_LINE - _INFO_TAB_WIDTH
        if len(piece) <= budget or ',' not in piece:
            pieces.append(piece)
            continue
        chunk = ''
        for part in piece.split(','):
            candidate = part if not chunk else chunk + ',' + part
            if chunk and len(candidate) > budget:
                pieces.append(chunk + ',')
                chunk = part
            else:
                chunk = candidate
        if chunk:
            pieces.append(chunk)

    # Greedily pack pieces into lines.  The first line is shorter by the field
    # name; continuation lines carry a leading tab, which renders ~8 columns.
    lines = []
    current = ''
    budget = _INFO_MAX_LINE - _INFO_HEADER_NAME_LEN
    for piece in pieces:
        if not current:
            current = piece
        elif len(current) + 1 + len(piece) <= budget:
            current += ' ' + piece
        else:
            lines.append(current)
            current = piece
            budget = _INFO_MAX_LINE - _INFO_TAB_WIDTH
    if current:
        lines.append(current)
    return '\r\n\t'.join(lines)


# ---------------------------------------------------------------------------
# Headers excluded from the header hash per DKIM2 spec Section 4
# ---------------------------------------------------------------------------

# Unsigned header fields per DKIM2 spec-06 §4, §4.1. spec-05 narrowed the old
# 'arc-' prefix to the three RFC 8617 field names and added a 'received-'
# prefix rule. x400-received / x400-trace match neither prefix.
_EXCLUDED_NAMES = frozenset({
    'apparently-to', 'arc-authentication-results', 'arc-message-signature',
    'arc-seal', 'authentication-results', 'auto-submitted', 'delivered-to',
    'dkim-signature', 'dkim2-signature', 'dl-expansion-history',
    'message-instance', 'original-recipient', 'received', 'return-path',
    'sio-label-history', 'vbr-info', 'x400-received', 'x400-trace',
})

_EXCLUDED_PREFIXES = ('x-', 'received-')


def _should_exclude_header(name):
    """Return True if this header should be excluded from the header hash."""
    name_lower = name.lower()
    if name_lower in _EXCLUDED_NAMES:
        return True
    for prefix in _EXCLUDED_PREFIXES:
        if name_lower.startswith(prefix):
            return True
    return False


def _get_hashed_headers(msg):
    """Return (count, names_str) of headers that will be included in h_digest.

    The names are in the same order as they are fed to the SHA-256 hash:
    reversed (bottom-up) then stable-sorted by name, matching
    compute_header_hash.
    """
    return _hashed_header_names(list(msg.raw_items()))


# ---------------------------------------------------------------------------
# Encoding helpers
# ---------------------------------------------------------------------------

def _b64(data):
    """Base64-encode bytes, return str with no newlines."""
    return base64.b64encode(data).decode('ascii')


def _b64json(obj):
    """JSON-encode then base64-encode."""
    return _b64(json.dumps(obj, separators=(',', ':')).encode('utf-8'))


# ---------------------------------------------------------------------------
# Header canonicalization (spec-06 §6.2)
# ---------------------------------------------------------------------------

def _canonicalize_header_field(name, value):
    """Canonicalize a single header field per DKIM2 spec-06 §6.2.

    Takes the header name and value as separate strings.
    Returns canonical form as bytes.
    """
    full = '{}: {}'.format(name, value)
    # Step 3: Unfold continuation lines (CRLF or LF before WSP)
    full = re.sub(r'\r?\n([ \t])', r'\1', full)
    # Split at first colon
    colon = full.find(':')
    if colon == -1:
        hname, hvalue = full, ''
    else:
        hname, hvalue = full[:colon], full[colon + 1:]
    # Step 2: Lowercase the field name
    hname = hname.lower()
    # Step 4: Collapse all WSP sequences to single SP
    hname = re.sub(r'[ \t]+', ' ', hname).strip()
    hvalue = re.sub(r'[ \t]+', ' ', hvalue)
    # Step 5: Strip trailing WSP from value
    hvalue = hvalue.rstrip(' \t')
    # Step 6: Strip WSP around colon
    hname = hname.strip()
    hvalue = hvalue.lstrip(' \t')
    return (hname + ':' + hvalue).encode('utf-8', errors='surrogateescape')


# ---------------------------------------------------------------------------
# Hash computation
# ---------------------------------------------------------------------------

def _wire_value(msg, name, value):
    """Return a header's value as the generator will put it on the wire.

    Mailman's handlers store some headers as email.header.Header objects
    (subject_prefix does, for any Subject it has to encode).  str() of a
    Header is the DECODED text, but what reaches the wire is
    policy.fold_binary(): the RFC 2047 encoded-word form that
    BytesGenerator -- and so smtplib.send_message -- writes.  Hashing
    str(value) recorded an m=2 header hash that no verifier could reproduce
    from the delivered bytes for every Subject that needed encoding (found
    2026-10-04 replaying real list mail; a quarter of it failed this way).

    A plain str comes back unchanged apart from folding, which
    canonicalization undoes; bytes that are not valid UTF-8 (surrogate-
    escaped by the parser) pass straight through.

    Callers must hand in the value from msg.raw_items(), which is what
    BytesGenerator folds.  msg.items() runs compat32's header_fetch_parse,
    which wraps a raw 8-bit value in a Header(charset=unknown-8bit): folding
    that gives an =?unknown-8bit?b?...?= encoded word the wire never
    carries, so a Latin-1 From display name Mailman never rewrote hashed
    differently from the delivered bytes (found 2026-10-04).
    """
    try:
        wire = msg.policy.fold_binary(name, value)
    except Exception:                               # pragma: nocover
        return str(value)
    text = wire.decode('utf-8', errors='surrogateescape')
    colon = text.find(':')
    if colon != -1:
        text = text[colon + 1:]
    # Unfold (spec-06 §5.1: Recipe strings MUST NOT contain CR or LF; the
    # hash collapses WSP runs anyway, so a fold is one SP), then as the
    # parser would hand it back: no leading WSP, no line ending.
    text = re.sub(r'\r?\n[ \t]+', ' ', text.rstrip('\r\n'))
    return text.lstrip(' \t')


def _hash_header_pairs(pairs):
    """SHA-256 header hash (spec-06 §6.2) over (name, wire value) pairs in
    document order; excluded names (§4) are the caller's business."""
    canon_headers = []
    for name, value in pairs:
        canon = _canonicalize_header_field(name, value)
        canon_headers.append((name.lower().encode(), canon))
    # Step 7: Reverse for bottom-up numbering, then stable sort by name
    canon_headers.reverse()
    canon_headers.sort(key=lambda x: x[0])
    # Step 8: Concatenate with CRLF + trailing CRLF
    data = b'\r\n'.join(ch for _, ch in canon_headers)
    if data:
        data += b'\r\n'
    return hashlib.sha256(data).digest()


@public
def compute_header_hash(msg):
    """Compute SHA-256 header hash per DKIM2 spec-06 §6.2 of a message as
    the generator will emit it.

    Excludes headers listed in the spec
    (see _EXCLUDED_NAMES / _EXCLUDED_PREFIXES — spec-06 §4).
    Returns raw SHA-256 digest bytes.
    """
    return _hash_header_pairs(_collect_headers(msg))


def _serialize_msg(msg):
    """Serialize a message to CRLF-normalized bytes.

    IMPORTANT: calling as_bytes() has side effects on multipart messages —
    it auto-generates boundary parameters on Content-Type headers.  Always
    call this before computing header hashes to ensure the Content-Type
    is fully resolved.
    """
    raw = msg.as_bytes()
    raw = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    raw = raw.replace(b'\n', b'\r\n')
    return raw


def _get_raw_body(msg):
    """Get the raw body bytes from a message (everything after headers)."""
    raw = _serialize_msg(msg)
    sep = b'\r\n\r\n'
    idx = raw.find(sep)
    if idx == -1:
        return b''
    return raw[idx + len(sep):]


@public
def compute_body_hash(msg):
    """Compute SHA-256 body hash per DKIM2 spec-06 §6.1.

    Uses simple body canonicalization: strip trailing empty lines,
    ensure body ends with exactly one CRLF.
    Returns raw SHA-256 digest bytes.
    """
    return _hash_body_bytes(_get_raw_body(msg))


def _hash_body_bytes(body):
    """SHA-256 body hash (spec-06 §6.1): trailing empty lines ignored, one
    CRLF at the end."""
    while body.endswith(b'\r\n'):
        body = body[:-2]
    body += b'\r\n'
    return hashlib.sha256(body).digest()


# ---------------------------------------------------------------------------
# Body line extraction (for Recipe computation)
# ---------------------------------------------------------------------------

def _get_body_lines(msg):
    """Get the body as a list of line strings (without line endings).

    Uses the CRLF-normalized raw body.  Trailing empty lines are not
    lines: the §6.1 body canonicalization ignores them, so they are not
    part of what a Recipe has to rebuild, and a Recipe must never number
    or restore one.  Counting a trailing empty line here made the Recipe
    copy "lines N-N+1 of N" for a body that ended in a blank line, which
    every other implementation rejects.
    """
    body = _get_raw_body(msg)
    # Normalize to LF for splitting
    body_str = body.replace(b'\r\n', b'\n').replace(
        b'\r', b'\n').decode('utf-8', errors='surrogateescape')
    body_str = body_str.rstrip('\n')
    if not body_str:
        return []
    return body_str.split('\n')


# ---------------------------------------------------------------------------
# The message as received: octets, not a re-serialization
# ---------------------------------------------------------------------------
# The LMTP runner keeps the bytes a message arrived as in msg.original_bytes.
# Everything the ingress side says about "the message as received" -- the
# m=1 hashes, and the snapshot the egress Recipe is computed against -- is
# taken from those bytes.  Re-serializing the parsed message is not
# byte-faithful for multipart: a part header loses a trailing space or is
# refolded, a final boundary without a line ending gains one, and the
# inbound signer hashed what was sent.  The egress side keeps using
# as_bytes(): that is what goes on the wire next.

def _normalize_crlf(raw):
    raw = raw.replace(b'\r\n', b'\n').replace(b'\r', b'\n')
    return raw.replace(b'\n', b'\r\n')


def _received_bytes(msg):
    """The octets this message arrived as, CRLF-normalized, or None for a
    message Mailman made itself (or one that came through an older queue)."""
    raw = getattr(msg, 'original_bytes', None)
    return None if raw is None else _normalize_crlf(raw)


def _split_raw(raw):
    idx = raw.find(b'\r\n\r\n')
    if idx == -1:
        return raw, b''
    return raw[:idx], raw[idx + 4:]


def _raw_header_pairs(raw):
    """(name, value) for every header field in the raw octets, the value in
    the shape _wire_value gives: unfolded, no leading WSP, no line ending,
    bytes outside UTF-8 surrogate-escaped."""
    head, _ = _split_raw(raw)
    pairs = []
    for field in re.split(rb'\r\n(?![ \t])', head):
        name, sep, value = field.partition(b':')
        if not sep:
            continue
        text = value.decode('utf-8', errors='surrogateescape')
        text = re.sub(r'\r?\n[ \t]+', ' ', text).lstrip(' \t')
        pairs.append((name.decode('utf-8', errors='surrogateescape').strip(),
                      text))
    return pairs


def _hashed_pairs(pairs):
    return [(n, v) for n, v in pairs if not _should_exclude_header(n)]


def compute_header_hash_raw(raw):
    return _hash_header_pairs(_hashed_pairs(_raw_header_pairs(raw)))


def compute_body_hash_raw(raw):
    return _hash_body_bytes(_split_raw(raw)[1])


def _body_lines_raw(raw):
    """Body lines of the raw octets, as _get_body_lines gives them for a
    message: trailing empty lines are not lines."""
    body = _split_raw(raw)[1].decode('utf-8', errors='surrogateescape')
    body = body.rstrip('\r\n')
    if not body:
        return []
    return body.split('\r\n')


def _hashed_header_names(pairs):
    """(count, names) of the hashed headers, in hash order (bottom-up,
    stable-sorted by name), for the X-DKIM2-Info hc= and hn= tags."""
    names = [name.lower() for name, _ in _hashed_pairs(pairs)]
    names.reverse()
    names.sort()
    return len(names), ','.join(names)


def verify_mi_raw(raw):
    """Check the highest Message-Instance in the raw octets against them.

    Returns (version, None) when its hashes match, else (0, why).  Only the
    hashes are checked here: an instance's Recipe is applied by the egress
    side's verifiers, not at ingress.
    """
    values = [v for n, v in _raw_header_pairs(raw)
              if n.lower() == 'message-instance']
    if not values:
        return 0, 'no Message-Instance headers'
    top = max(values, key=_get_mi_version)
    try:
        _, hashes, _ = _parse_mi(top)
    except MalformedRecipe as error:
        return 0, 'malformed Recipe: {}'.format(error)
    if hashes is None:
        return 0, 'could not parse h= tag'
    computed_h = _b64(compute_header_hash_raw(raw))
    computed_b = _b64(compute_body_hash_raw(raw))
    if hashes['h'][1] != computed_h:
        return 0, 'header hash mismatch ({} != {})'.format(
            hashes['h'][1], computed_h)
    if hashes['b'][1] != computed_b:
        return 0, 'body hash mismatch ({} != {})'.format(
            hashes['b'][1], computed_b)
    return _get_mi_version(top), None


# ---------------------------------------------------------------------------
# Recipe computation
# ---------------------------------------------------------------------------

def _literal_steps(values):
    """Recipe steps that emit these literal header values or body lines.

    Pure-ASCII literals go in a {"d": [...]} step as spec-06 §5 has them.
    A literal whose octets include a byte >= 0x80 -- the str form here is
    the surrogateescape decoding of the raw octets -- goes in a {"b": [...]}
    step: the raw octets base64-encoded inside the JSON string.  JSON text
    cannot carry Latin-1, EUC-KR or any other non-UTF-8 octets, and the
    U+DCxx lone surrogates json.dumps would otherwise emit are undecodable
    by every other implementation.  (Extension to spec-06 §5, proposed to
    the WG.)

    Consecutive literals of one kind share a step; a mixed run alternates.
    """
    steps = []
    for value in values:
        if value.isascii():
            kind, item = 'd', value
        else:
            kind = 'b'
            item = _b64(value.encode('utf-8', errors='surrogateescape'))
        if steps and kind in steps[-1]:
            steps[-1][kind].append(item)
        else:
            steps.append({kind: [item]})
    return steps


@public
def compute_body_recipe(current_lines, previous_lines):
    """Compute body recipe to undo current body back to previous.

    Uses line-level diff.  Returns a list of Recipe steps (spec-06 §5):
    - {"c": [start, end]}: copy this range of lines from the current body
      (1-based, inclusive)
    - {"d": [line, ...]}: literal ASCII lines from the previous body
    - {"b": [base64, ...]}: literal 8-bit lines from the previous body,
      see _literal_steps

    Returns None if bodies are identical.
    """
    if current_lines == previous_lines:
        return None
    # Fast path: pure append — previous body is a prefix of the current body.
    # Count the original lines and emit a single copy instruction.
    n = len(previous_lines)
    if n == 0:
        return []
    if len(current_lines) > n and current_lines[:n] == previous_lines:
        return [{'c': [1, n]}]
    sm = SequenceMatcher(None, current_lines, previous_lines, autojunk=False)
    recipe = []
    pending_data = []
    for tag, i1, i2, j1, j2 in sm.get_opcodes():
        if tag == 'equal':
            # Flush any pending data lines
            if pending_data:
                recipe.extend(_literal_steps(pending_data))
                pending_data = []
            # Reference lines in the current body (1-based, inclusive end).
            # Opcodes walk the current body forwards, so these ranges
            # ascend without overlapping as spec-06 §5.2 requires.
            recipe.append({'c': [i1 + 1, i2]})
        elif tag in ('replace', 'insert'):
            # Literal text from the previous body
            pending_data.extend(previous_lines[j1:j2])
        # 'delete': lines only in current, not needed for previous
    if pending_data:
        recipe.extend(_literal_steps(pending_data))
    return recipe


@public
def compute_header_recipe(current_headers, previous_headers):
    """Compute header recipe to undo current headers back to previous.

    Both arguments are lists of (name, value) tuples.
    Returns a dict mapping lowercase header names to Recipe step lists
    ({"c": [i, j]} copies by bottom-up index, {"d": [...]} / {"b": [...]}
    restore literal values -- see _literal_steps),
    or None if no non-excluded headers changed.

    spec-06 §5.1 requires the "c" ranges of one header name to ascend
    without overlapping, so a previous value can only be copied from a
    current instance below every instance already copied; a value whose
    instances were reordered (or one of two identical instances when the
    other has already been used) is restored literally instead.
    """
    # Collect by lowercase name, preserving order
    def collect(headers):
        result = {}
        for name, value in headers:
            lname = name.lower()
            if _should_exclude_header(lname):
                continue
            result.setdefault(lname, []).append(str(value))
        return result

    cur = collect(current_headers)
    prev = collect(previous_headers)
    all_names = set(cur.keys()) | set(prev.keys())
    recipe = {}

    for name in sorted(all_names):
        cur_vals = cur.get(name, [])
        prev_vals = prev.get(name, [])
        # Canonicalize for comparison
        cur_canon = [_canonicalize_header_field(name, v) for v in cur_vals]
        prev_canon = [_canonicalize_header_field(name, v) for v in prev_vals]
        if cur_canon == prev_canon:
            continue
        # Bottom-up numbering: reverse the lists
        cur_canon_rev = list(reversed(cur_canon))
        prev_vals_rev = list(reversed(prev_vals))
        prev_canon_rev = [_canonicalize_header_field(name, v)
                          for v in prev_vals_rev]
        # Map each canonical current value to its bottom-up indexes
        # (1-based, ascending).
        positions = {}
        for idx, cv in enumerate(cur_canon_rev, 1):
            positions.setdefault(cv, []).append(idx)
        # Build Recipe for this header name using {"c":...}/{"d":...}
        hrecipe = []
        pending_data = []
        last_end = 0
        for pval, pcanon in zip(prev_vals_rev, prev_canon_rev):
            # The lowest matching instance above everything copied so far;
            # none means a literal (§5.1 ranges must ascend).
            idx = next((i for i in positions.get(pcanon, ())
                        if i > last_end), None)
            if idx is None:
                pending_data.append(pval)
                continue
            # Flush pending data
            if pending_data:
                hrecipe.extend(_literal_steps(pending_data))
                pending_data = []
            # Combine adjacent copy ranges
            if (hrecipe and 'c' in hrecipe[-1]
                    and idx == hrecipe[-1]['c'][1] + 1):
                hrecipe[-1]['c'][1] = idx
            else:
                hrecipe.append({'c': [idx, idx]})
            last_end = idx
        if pending_data:
            hrecipe.extend(_literal_steps(pending_data))
        recipe[name] = hrecipe

    return recipe if recipe else None


# ---------------------------------------------------------------------------
# MI header construction
# ---------------------------------------------------------------------------

_MI_HEADER_NAME_LEN = len('Message-Instance: ')


def _fold_mi_value(value, target=72, hard_max=77):
    """Fold a Message-Instance header value for RFC 5322.

    Lines target 72 characters, which is comfortable under the RFC 5322
    recommendation of 78.  If a '; ' tag boundary falls between the
    target and hard_max, we'll break there instead of mid-base64.

    The first line is shorter to account for the header field name
    ('Message-Instance: ').  Continuation lines start with a tab.

    Avoids orphaned tails (<=2 chars on the next line) and avoids
    putting a semicolon as the first char of a continuation.
    """
    # First line budget is reduced by the header name.
    first_target = target - _MI_HEADER_NAME_LEN
    first_max = hard_max - _MI_HEADER_NAME_LEN
    # Continuation lines have a tab prefix which renders as 8 spaces
    # in most displays.  Use tab + 65 chars so lines look ~73 wide,
    # matching the visual width of the first line.
    _TAB_WIDTH = 8
    cont_target = target - _TAB_WIDTH
    cont_max = hard_max - _TAB_WIDTH
    lines = []
    current = value
    tgt = first_target
    mx = first_max
    while len(current) > tgt + 2:
        # Don't wrap if only 2 or fewer chars would go on the next line.
        # Try to break at a '; ' boundary.  Search up to hard_max first
        # (allows slightly longer lines to land on a clean boundary),
        # then fall back to breaking at the target.
        brk_min = 10 if not lines else tgt - 5
        break_at = current.rfind('; ', brk_min, mx)
        if break_at > 0:
            # Include the ';' on this line, skip the space.
            lines.append(current[:break_at + 1])
            current = current[break_at + 2:]
        else:
            # No tag boundary — break mid-base64 at the target.
            lines.append(current[:tgt])
            current = current[tgt:]
        tgt = cont_target
        mx = cont_max
    if current:
        lines.append(current)
    return '\r\n\t'.join(lines)


def _mi_enabled(mlist=None):
    """Return True if Message-Instance support is enabled.

    Checks the global config first; if disabled globally, returns False.
    If mlist is provided, also checks the per-list dkim2_message_instance
    flag (defaults to True if the attribute is not set, e.g. on old lists
    that predate the migration).
    """
    if not as_boolean(config.mta.message_instance):
        return False
    if mlist is not None:
        per_list = getattr(mlist, 'dkim2_message_instance', None)
        if per_list is not None and not per_list:
            return False
    return True


@public
def build_mi_header_value(version, header_hash, body_hash,
                          header_recipe=None, body_recipe=None):
    """Build a Message-Instance header field value string.

    Returns the value part (without the field name), folded for RFC 5322.
    """
    value = 'm={}; h=sha256:{}:{}'.format(version, _b64(header_hash),
                                         _b64(body_hash))
    # Build Recipe if there are any changes
    r = {}
    if header_recipe is not None:
        r['h'] = header_recipe
    if body_recipe is not None:
        r['b'] = body_recipe
    if r:
        value += '; r={}'.format(_b64json(r))
    value += ';'
    return _fold_mi_value(value)


@public
def get_max_mi_version(msg):
    """Get the highest MI revision number from a message, or 0 if none."""
    max_m = 0
    for val in msg.get_all('message-instance', []):
        match = re.search(r'm\s*=\s*(\d+)', str(val))
        if match:
            max_m = max(max_m, int(match.group(1)))
    return max_m


def _get_mi_version(mi_value):
    """Extract the m= revision number from an MI header value string."""
    match = re.search(r'm\s*=\s*(\d+)', str(mi_value))
    return int(match.group(1)) if match else 0


def _parse_mi(mi_value):
    """Parse a Message-Instance header value into (version, hashes, recipe).

    Returns (version, hashes_dict, recipe_dict_or_None).
    h= tag format: sha256:header_hash:body_hash
    r= tag format: base64-encoded JSON
    """
    val = str(mi_value)
    # Unfold continuation lines.  FWS is CRLF followed by one *or more* WSP
    # (RFC 5322; draft-ietf-dkim-dkim2-spec-06 §2.12), and spec-06 §2.14 says
    # FWS within a base64string is ignored when the value is used, so drop
    # the whole WSP run -- leaving even one behind would truncate the h=
    # match below at the fold.
    val = re.sub(r'\r?\n[ \t]+', '', val)
    version = _get_mi_version(val)
    hashes = None
    recipe = None
    # Parse h= tag: sha256:header_hash:body_hash
    h_match = re.search(r'h=(\S+)', val)
    if h_match:
        parts = h_match.group(1).rstrip(';').split(':')
        if len(parts) == 3:
            hashes = {
                'h': [parts[0], parts[1]],
                'b': [parts[0], parts[2]],
            }
    # Parse r= tag: base64-encoded JSON.  The JSON is returned as decoded;
    # _compile_recipe checks it against spec-06 §5.
    r_match = re.search(r'r=([A-Za-z0-9+/=\s]+)', val)
    if r_match:
        b64_clean = re.sub(r'\s', '', r_match.group(1))
        try:
            recipe = json.loads(base64.b64decode(b64_clean))
        except ValueError as error:
            raise MalformedRecipe('r= is not base64 JSON: {}'.format(error))
    return version, hashes, recipe


# ---------------------------------------------------------------------------
# Recipe validation and application (spec-06 §5)
# ---------------------------------------------------------------------------

@public
class MalformedRecipe(ValueError):
    """A Message-Instance Recipe that cannot be applied.

    Raised for anything spec-06 §5 (plus the "b" literal step, see
    _literal_steps) does not allow: an r= that is not base64 JSON, a step
    that is not a one-key object, a "c" that is not two integers or whose
    range is below 1, descending, overlapping an earlier range or past the
    items available, an empty "d"/"b", a "d"/"b" string holding CR or LF,
    a "b" string that is not base64.  verify and undo treat it as an
    instance that does not verify; it never escapes them.
    """


def _compile_steps(steps, where):
    """Check one step list and return it as ('c', start, end) / ('d', [str])
    tuples, "b" items decoded to the surrogateescape str form the rest of
    this module uses for header values and body lines.  The upper bound of
    a "c" range is checked by _apply_steps, which knows the item count."""
    if not isinstance(steps, list):
        raise MalformedRecipe('{}: steps are not an array'.format(where))
    compiled = []
    last_end = 0
    for step in steps:
        # Pre-spec-06 forms: a bare [start, end] array, a bare string.
        if isinstance(step, list):
            step = {'c': step}
        elif isinstance(step, str):
            step = {'d': [step]}
        if not isinstance(step, dict) or len(step) != 1:
            raise MalformedRecipe(
                '{}: step is not an object with one key'.format(where))
        (kind, arg), = step.items()
        if not isinstance(arg, list):
            raise MalformedRecipe(
                '{}: "{}" is not an array'.format(where, kind))
        if kind == 'c':
            # bool is an int subclass; JSON true is not an integer here.
            if len(arg) != 2 or any(type(v) is not int for v in arg):
                raise MalformedRecipe(
                    '{}: "c" is not two integers'.format(where))
            start, end = arg
            if start < 1 or end < start:
                raise MalformedRecipe(
                    '{}: bad "c" range [{}, {}]'.format(where, start, end))
            if start <= last_end:
                raise MalformedRecipe(
                    '{}: "c" range [{}, {}] does not ascend past {}'.format(
                        where, start, end, last_end))
            last_end = end
            compiled.append(('c', start, end))
            continue
        if kind not in ('d', 'b'):
            raise MalformedRecipe(
                '{}: unknown step type "{}"'.format(where, kind))
        if not arg:
            # Schema minItems 1; an empty step list means "remove all",
            # an empty step means nothing.
            raise MalformedRecipe('{}: "{}" is empty'.format(where, kind))
        values = []
        for item in arg:
            if not isinstance(item, str):
                raise MalformedRecipe(
                    '{}: "{}" item is not a string'.format(where, kind))
            if kind == 'b':
                try:
                    octets = base64.b64decode(item, validate=True)
                except ValueError:
                    raise MalformedRecipe(
                        '{}: "b" item is not base64'.format(where))
                item = octets.decode('utf-8', errors='surrogateescape')
            if '\r' in item or '\n' in item:
                raise MalformedRecipe(
                    '{}: "{}" item contains CR or LF'.format(where, kind))
            values.append(item)
        compiled.append(('d', values))
    return compiled


def _compile_recipe(recipe):
    """Check a decoded r= Recipe object and return (header_steps, body_steps).

    header_steps maps each header name under "h" to its compiled steps;
    body_steps is the compiled "b" list, or None when the body was not
    changed (no "b") or cannot be recreated ("b": null, spec-06 §5.2).
    """
    if recipe is None:
        return {}, None
    if not isinstance(recipe, dict):
        raise MalformedRecipe('Recipe is not an object')
    header_steps = {}
    if 'h' in recipe:
        if not isinstance(recipe['h'], dict):
            raise MalformedRecipe('"h" is not an object')
        for hname, steps in recipe['h'].items():
            header_steps[hname] = _compile_steps(steps, 'h ' + hname)
    body_steps = None
    if recipe.get('b') is not None:
        body_steps = _compile_steps(recipe['b'], 'b')
    return header_steps, body_steps


def _apply_steps(compiled, items, where):
    """Emit the items a compiled step list produces from the current ones."""
    out = []
    for step in compiled:
        if step[0] == 'c':
            _, start, end = step
            if end > len(items):
                raise MalformedRecipe(
                    '{}: "c" range [{}, {}] exceeds the {} available'.format(
                        where, start, end, len(items)))
            out.extend(items[start - 1:end])
        else:
            out.extend(step[1])
    return out


def _plan_undo(msg, recipe):
    """Work out what applying recipe to msg produces, without changing msg.

    Returns (body_lines, headers): the rebuilt body lines, or None when the
    body is to be left alone, and a dict of header name to its rebuilt
    values in top-down order.  Raises MalformedRecipe.
    """
    header_steps, body_steps = _compile_recipe(recipe)
    body_lines = None
    if body_steps is not None:
        body_lines = _apply_steps(body_steps, _get_body_lines(msg), 'b')
    headers = {}
    for hname, steps in header_steps.items():
        # Current values bottom-up, as the Recipe numbers them.  Raw, so a
        # copied 8-bit value is re-added as the octets it arrived as.
        cur_vals = list(reversed(_raw_values(msg, hname)))
        headers[hname] = list(reversed(
            _apply_steps(steps, cur_vals, 'h ' + hname)))
    return body_lines, headers


# ---------------------------------------------------------------------------
# Verify and undo
# ---------------------------------------------------------------------------

@public
def verify_message_instance(msg):
    """Verify the highest MI header matches the current message content.

    Returns the version number on success, or (0, error_message) on failure.
    """
    # Force serialization so that auto-generated parameters (e.g.
    # multipart boundaries) are resolved before hashing.
    _serialize_msg(msg)
    max_v = get_max_mi_version(msg)
    if max_v == 0:
        return 0, 'no Message-Instance headers'
    # Find the MI header with the highest version.
    for val in msg.get_all('message-instance', []):
        if _get_mi_version(val) == max_v:
            try:
                _, hashes, recipe = _parse_mi(val)
            except MalformedRecipe as error:
                return 0, 'malformed Recipe: {}'.format(error)
            break
    if hashes is None:
        return 0, 'could not parse h= tag'
    stored_h = hashes['h'][1]
    stored_b = hashes['b'][1]
    computed_h = _b64(compute_header_hash(msg))
    computed_b = _b64(compute_body_hash(msg))
    if stored_h != computed_h:
        return 0, 'header hash mismatch ({} != {})'.format(
            stored_h, computed_h)
    if stored_b != computed_b:
        return 0, 'body hash mismatch ({} != {})'.format(
            stored_b, computed_b)
    # An instance whose Recipe cannot be applied to this message (spec-06
    # §5) does not verify either: nothing downstream could undo it.
    try:
        _plan_undo(msg, recipe)
    except MalformedRecipe as error:
        return 0, 'malformed Recipe: {}'.format(error)
    return max_v, None


@public
def undo_message_instance(msg):
    """Apply the recipe from the highest MI header to reverse the message.

    Removes the highest MI header from the message, applies body and header
    recipes to reconstruct the previous version, and returns the version
    number that was undone.  Returns 0 if there are no MI headers, or if
    the highest one carries a Recipe that cannot be applied (spec-06 §5,
    see MalformedRecipe) -- the message is then left untouched.

    After a successful undo, verify_message_instance(msg) should return
    the version of the now-highest MI header.
    """
    max_v = get_max_mi_version(msg)
    if max_v == 0:
        return 0
    # Find and parse the highest MI header.
    highest_mi = None
    for val in msg.get_all('message-instance', []):
        if _get_mi_version(val) == max_v:
            highest_mi = val
            break
    # Work out the whole result before touching the message, so a Recipe
    # that turns out to be malformed part-way through changes nothing.
    try:
        _, _, recipe = _parse_mi(highest_mi)
        new_body_lines, new_headers = _plan_undo(msg, recipe)
    except MalformedRecipe as error:
        log.warning('Message-Instance m=%d has a malformed Recipe (%s); '
                    'not undone', max_v, error)
        return 0
    # Remove the highest MI header, keep all others.
    # Python's email library doesn't support selective deletion of
    # duplicate headers by value, so we collect all, delete all,
    # and re-add the ones we want to keep.
    all_mi = msg.get_all('message-instance', [])
    # Delete all MI headers.
    while 'message-instance' in msg:
        del msg['message-instance']
    # Re-add all except the highest version.
    for val in all_mi:
        if _get_mi_version(val) != max_v:
            msg['Message-Instance'] = val
    # Rebuild the body.  Setting the payload from its raw octets stores it
    # in the parser's own surrogateescape form, which the generator writes
    # back byte for byte whatever the octets are -- a str with real
    # non-ASCII characters (a UTF-8 line decoded by _get_body_lines) would
    # not survive the trip.
    if new_body_lines is not None:
        new_body = '\r\n'.join(new_body_lines) + '\r\n'
        msg.set_payload(new_body.encode('utf-8', errors='surrogateescape'))
    # Replace each header named in the Recipe: delete all, re-add top-down.
    for hname, new_vals in new_headers.items():
        while hname in msg:
            del msg[hname]
        for val in new_vals:
            msg[hname] = val
    return max_v


# ---------------------------------------------------------------------------
# Snapshot helpers — file-based original message cache
# ---------------------------------------------------------------------------

# Mailman's message store stamps Message-ID-Hash onto the in-flight message
# *after* an upstream signer / the inbound milter computed m=1, so the baseline
# that external MI describes does not include it. We only ever treat it as
# Mailman-stamped when removing it actually restores the external MI's hash, so
# a Message-ID-Hash that arrived as part of a (possibly signed) m=1 is left
# untouched. (X-Message-ID-Hash is already X- excluded from the hash, so it is
# deliberately not considered here.)
_MAILMAN_STAMPED = ('message-id-hash',)

# Header fields smtplib.send_message deletes on the way to the MTA.
_SEND_MESSAGE_DROPS = ('bcc', 'resent-bcc')


def _strip_stamped(msg):
    """Return a deep copy of msg with the Mailman-stamped header(s) removed."""
    clean = copy.deepcopy(msg)
    for name in list(clean.keys()):
        if name.lower() in _MAILMAN_STAMPED:
            del clean[name]
    return clean


def _collect_headers(msg):
    """Collect non-excluded headers as a list of (name, value) tuples.

    Values are the wire form (see _wire_value), so a Recipe's copy ranges
    refer to header values exactly as the verifier will see them.
    """
    headers = []
    for name, value in msg.raw_items():
        if not _should_exclude_header(name):
            headers.append((name, _wire_value(msg, name, value)))
    return headers


def _raw_values(msg, name):
    """The stored values of this header, top-down, as the generator sees
    them (see _wire_value for why not msg.get_all)."""
    lname = name.lower()
    return [value for hname, value in msg.raw_items()
            if hname.lower() == lname]


def _prepend_header(msg, name, value):
    """Insert a header at the top of the message headers.

    Unlike msg[name] = value which appends, this inserts at position 0
    so Message-Instance headers appear before the original headers.
    """
    msg._headers.insert(0, (name, value))


def _mi_cache_dir():
    """Return the directory for MI original message cache files."""
    return os.path.join(config.VAR_DIR, 'mi-cache')


@public
def save_mi_original(raw):
    """Save the message as received (CRLF-normalized octets) to a cache file
    for later recipe computation.

    Returns the file path.  Multiple queue entries referencing the same
    original share this file — the data is stored once on disk.
    """
    cache_dir = _mi_cache_dir()
    makedirs(cache_dir)
    # Use SHA-256 of the serialized message for a deterministic filename.
    # If two queue entries hold the same original, they share this file.
    h = hashlib.sha256(raw).hexdigest()[:40]
    path = os.path.join(cache_dir, h + '.orig')
    if not os.path.exists(path):
        tmp_path = path + '.tmp'
        with open(tmp_path, 'wb') as f:
            f.write(raw)
        os.rename(tmp_path, path)
    return path


@public
def load_mi_original(path):
    """Load the original message from a cache file.

    Returns (body_lines, headers) where body_lines is a list of strings
    and headers is a list of (name, value) tuples.  Returns None if the
    file is missing (e.g. cleaned up before egress ran).
    """
    try:
        with open(path, 'rb') as f:
            raw = f.read()
    except FileNotFoundError:
        return None
    # From the octets: parsing and re-serializing them here would put the
    # generator's reshaping of part headers into the "previous" side.
    return _body_lines_raw(raw), _hashed_pairs(_raw_header_pairs(raw))


def _cleanup_mi_original(path):
    """Remove the cache file.  Safe to call if already deleted."""
    safe_remove(path)


# ---------------------------------------------------------------------------
# Pipeline handlers
# ---------------------------------------------------------------------------

@public
@implementer(IHandler)
class MessageInstanceIngress:
    """Add Message-Instance m=1 at ingress and snapshot message state."""

    name = 'message-instance-ingress'
    description = _('Add Message-Instance header at ingress.')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        if not _mi_enabled(mlist):
            return
        # Skip digests — they aggregate multiple messages and are not
        # a single authored message in the DKIM2 sense.
        if msgdata.get('isdigest'):
            return
        received = _received_bytes(msg)
        # Force serialization so that auto-generated parameters (e.g.
        # multipart boundaries) are resolved before hashing.
        _serialize_msg(msg)
        existing_version = get_max_mi_version(msg)
        if existing_version > 0:
            # An MI is already present (inbound milter, or a signing sender).
            # NEVER modify it — it may be signed.  The snapshot the egress
            # Recipe is computed against must be the state that MI
            # describes: the message as received, when the LMTP runner kept
            # it, which also puts everything Mailman has done since (the
            # Message-ID-Hash stamp, a repaired Message-ID) into the Recipe.
            #
            # Nothing is emitted for this: X-DKIM2-Info records actions that
            # add a header, and accepting an existing instance adds none.
            if received is not None:
                matched, why = verify_mi_raw(received)
                if matched != existing_version:
                    log.warning('Existing Message-Instance m=%d does not '
                                'match the message as received (%s); chain '
                                'may not undo cleanly', existing_version, why)
                snap_raw = received
            else:
                # No received octets (an older queue entry, or an internal
                # path): fall back to the parsed message.  Mailman's message
                # store stamps Message-ID-Hash after the MI was computed, so
                # use verify as the oracle: if removing the stamp restores
                # the MI's hash, snapshot that baseline so the Recipe
                # records the stamp as a reversible change.
                matched, why = verify_message_instance(msg)
                snap_src = msg
                if matched != existing_version:
                    baseline = _strip_stamped(msg)
                    b_matched, _ = verify_message_instance(baseline)
                    if b_matched == existing_version:
                        snap_src = baseline
                        log.debug('Existing Message-Instance m=%d matches '
                                  'the unstamped baseline', existing_version)
                    else:
                        log.warning('Existing Message-Instance m=%d does not '
                                    'match content (%s); chain may not undo '
                                    'cleanly', existing_version, why)
                snap_raw = _serialize_msg(snap_src)
            log.debug('Accepted existing Message-Instance m=%d',
                      existing_version)
            mi_file = save_mi_original(snap_raw)
            msgdata['mi_snapshot'] = {
                'mi_file': mi_file,
                'version': existing_version,
                'header_hash': compute_header_hash_raw(snap_raw),
                'body_hash': compute_body_hash_raw(snap_raw),
            }
            return
        # No MI headers present — add m=1 documenting the message as
        # received (as parsed, when the octets were not kept).
        snap_raw = received if received is not None else _serialize_msg(msg)
        hcount, hnames = _hashed_header_names(_raw_header_pairs(snap_raw))
        h_hash = compute_header_hash_raw(snap_raw)
        b_hash = compute_body_hash_raw(snap_raw)
        value = build_mi_header_value(1, h_hash, b_hash)
        _prepend_header(msg, 'Message-Instance', value)
        mi_file = save_mi_original(snap_raw)
        _prepend_header(msg, 'X-DKIM2-Info', _dkim2_info(
            'mi-m=1', hc=hcount, hn=hnames,
            snaps=os.path.basename(mi_file)))
        log.debug('Added Message-Instance m=1')
        msgdata['mi_snapshot'] = {
            'mi_file': mi_file,
            'version': 1,
            'header_hash': h_hash,
            'body_hash': b_hash,
        }


@public
@implementer(IHandler)
class MessageInstanceEgress:
    """Add Message-Instance m=N+1 at egress if message changed."""

    name = 'message-instance-egress'
    description = _('Add Message-Instance header at egress.')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        if not _mi_enabled(mlist):
            return
        # smtplib.send_message, which hands the message to the MTA, deletes
        # Bcc and Resent-Bcc.  Both are signed header fields, so a removal
        # the Recipe did not record would leave this instance describing
        # headers the recipient never sees.  Remove them here instead, where
        # the Recipe records the change and an upstream verifier can undo it.
        for name in _SEND_MESSAGE_DROPS:
            del msg[name]
        snapshot = msgdata.get('mi_snapshot')
        if not snapshot:
            # No snapshot means this message didn't go through an ingress
            # handler (e.g. internally generated via VirginPipeline).
            # If no MI headers are present, Mailman is the originator —
            # add MI m=1 so the message enters the DKIM2 ecosystem.
            if get_max_mi_version(msg) == 0:
                _serialize_msg(msg)
                hcount, hnames = _get_hashed_headers(msg)
                h_hash = compute_header_hash(msg)
                b_hash = compute_body_hash(msg)
                value = build_mi_header_value(1, h_hash, b_hash)
                _prepend_header(msg, 'Message-Instance', value)
                _prepend_header(msg, 'X-DKIM2-Info',
                                _dkim2_info('mi-m=1', hc=hcount, hn=hnames))
                log.debug('Added originator Message-Instance m=1')
            return
        # Force serialization so that auto-generated parameters (e.g.
        # multipart boundaries) are resolved before hashing.
        _serialize_msg(msg)
        # Compute current hashes.
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash(msg)
        # Check if anything changed.
        if (h_hash == snapshot['header_hash']
                and b_hash == snapshot['body_hash']):
            _cleanup_mi_original(snapshot.get('mi_file', ''))
            return
        # Load the original message from the cache file.
        mi_file = snapshot.get('mi_file', '')
        original = load_mi_original(mi_file)
        if original is None:
            log.warning('MI cache file missing: %s — skipping MI egress',
                        mi_file)
            return
        prev_body_lines, prev_headers = original
        # Compute Recipes.
        body_recipe = None
        header_recipe = None
        if b_hash != snapshot['body_hash']:
            current_lines = _get_body_lines(msg)
            body_recipe = compute_body_recipe(current_lines, prev_body_lines)
        if h_hash != snapshot['header_hash']:
            current_headers = _collect_headers(msg)
            header_recipe = compute_header_recipe(
                current_headers, prev_headers)
        hcount, hnames = _get_hashed_headers(msg)
        version = get_max_mi_version(msg) + 1
        value = build_mi_header_value(
            version, h_hash, b_hash, header_recipe, body_recipe)
        _prepend_header(msg, 'Message-Instance', value)
        _prepend_header(msg, 'X-DKIM2-Info', _dkim2_info(
            'mi-m={}'.format(version),
            hc=hcount, hn=hnames,
            snapf=os.path.basename(mi_file)))
        log.debug('Added Message-Instance m=%d', version)
        _cleanup_mi_original(mi_file)
