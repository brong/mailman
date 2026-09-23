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
import hashlib
import json
import logging
import re

from contextlib import contextmanager
from email.generator import BytesGenerator
from io import BytesIO
from lazr.config import as_boolean
from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.handler import IHandler
from public import public
from zope.interface import implementer


log = logging.getLogger('mailman.dkim2')

# DKIM2 implementation metadata — update DKIM2_DATE on each change.
DKIM2_DRAFT = 'ietf-dkim-dkim2-spec-06'
DKIM2_REPO = 'github.com/brong/mailman'
DKIM2_DATE = '2026-10-07'
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


# Two serializations, not to be mixed up:
#
#   _serialize_msg(msg)  the PARSED view, as_bytes() CRLF-normalized: the
#                        stand-in for the octets a parsed message arrived
#                        as, which the ingress fallback (no received octets)
#                        takes the snapshot from.
#   _wire_bytes(msg)     the OUTGOING view: what smtplib will send.  Only
#                        the hashes and lines of the instance Mailman adds
#                        for the message it sends (egress m=N+1, originator
#                        m=1) come from it.
#
# They differ where smtplib's generator mangles a body line starting
# "From " to ">From ": an inbound signer hashed "From ", the recipient of
# Mailman's copy gets ">From ".

def _wire_bytes(msg):
    """The octets smtplib.SMTP.send_message sends for this message, CRLF-
    normalized.  Use for the OUTGOING message only (see above).

    Mailman hands the Message to send_message, which flattens a copy
    without Bcc and Resent-Bcc through a BytesGenerator built with no
    policy or mangle_from_ argument: for compat32 that mangles a body line
    starting "From " to ">From ", which msg.as_bytes() does not.  Hashing
    anything else would describe octets the recipient never sees.  Only
    the plain path is mirrored: with an internationalized envelope
    (SMTPUTF8) send_message flattens with policy.clone(utf8=True) instead.
    """
    # The generator picks a boundary for a multipart that lacks one, and
    # on the copy that would be lost: resolve it on the message itself.
    if msg.is_multipart() and msg.get_boundary() is None:
        msg.as_bytes()
    # send_message works on copy.copy(msg), but Mailman's Message.__setstate__
    # makes that copy share the original's __dict__, so deleting from it
    # would rebind the original's header list.  A real shallow copy flattens
    # to the same octets.
    msg_copy = object.__new__(type(msg))
    msg_copy.__dict__.update(msg.__dict__)
    del msg_copy['Bcc']
    del msg_copy['Resent-Bcc']
    buf = BytesIO()
    try:
        BytesGenerator(buf).flatten(msg_copy, linesep='\r\n')
    except UnicodeEncodeError:
        # https://bugs.python.org/issue41307, as Message.as_bytes works
        # around it; send_message would fail on this message anyway.
        return _serialize_msg(msg)
    return _normalize_crlf(buf.getvalue())


def _serialize_msg(msg):
    """The PARSED view of a message: as_bytes(), CRLF-normalized.  Not for
    the hashes of the message Mailman sends: use _wire_bytes (see above).

    IMPORTANT: calling as_bytes() has side effects on multipart messages —
    it auto-generates boundary parameters on Content-Type headers.  Always
    call this before computing header hashes to ensure the Content-Type
    is fully resolved.
    """
    return _normalize_crlf(msg.as_bytes())


def _hash_body_bytes(body, start=0):
    """SHA-256 body hash (spec-06 §6.1) of body[start:]: trailing empty
    lines ignored, one CRLF at the end.  Hashed in place, not sliced: the
    body can be most of a large message."""
    end = _body_end(body, start)
    digest = hashlib.sha256(memoryview(body)[start:end])
    digest.update(b'\r\n')
    return digest.digest()


def _body_end(raw, start):
    """The end of raw[start:] with its trailing CRLFs (empty lines) off."""
    end = len(raw)
    while end - start >= 2 and raw.endswith(b'\r\n', start, end):
        end -= 2
    return end


# ---------------------------------------------------------------------------
# The message as received: octets, not a re-serialization
# ---------------------------------------------------------------------------
# With [mta] message_instance on, the LMTP runner keeps the bytes a message
# arrived as in msg.original_bytes.
# Everything the ingress side says about "the message as received" -- the
# m=1 hashes, and the snapshot the egress Recipe is computed against -- is
# taken from those bytes.  Re-serializing the parsed message is not
# byte-faithful for multipart: a part header loses a trailing space or is
# refolded, a final boundary without a line ending gains one, and the
# inbound signer hashed what was sent.  The egress side serializes the
# message the way smtplib will (_wire_bytes, see the two serializations
# above): that is what goes on the wire next.

_LINE_BREAK = re.compile(rb'\r\n|\r|\n')


def _normalize_crlf(raw):
    """raw with every line break -- CRLF, bare CR or bare LF -- as CRLF.
    Octets that already have only CRLFs (as received over LMTP) come back
    as they are, not copied."""
    crlf = raw.count(b'\r\n')
    if raw.count(b'\r') == crlf and raw.count(b'\n') == crlf:
        return raw
    return _LINE_BREAK.sub(b'\r\n', raw)


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


# _split_raw copies the body; these give the head alone, and where the body
# starts, for the many callers that need no copy of the body.

def _raw_head(raw):
    idx = raw.find(b'\r\n\r\n')
    return raw if idx == -1 else raw[:idx]


def _body_offset(raw):
    idx = raw.find(b'\r\n\r\n')
    return len(raw) if idx == -1 else idx + 4


def _raw_header_pairs(raw):
    """(name, value) for every header field in the raw octets, the value in
    the shape _wire_value gives: unfolded, no leading WSP, no line ending,
    bytes outside UTF-8 surrogate-escaped."""
    head = _raw_head(raw)
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
    return _hash_body_bytes(raw, _body_offset(raw))


def _body_lines_raw(raw):
    """Body lines of the raw octets.  Trailing empty lines are not lines:
    the spec-06 §6.1 body canonicalization ignores them, so a Recipe must
    never number or restore one."""
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
    except MalformedInstance as error:
        return 0, str(error)
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


# The body Recipe of an instance whose previous body cannot be rebuilt:
# Mailman rewrote it (content filtering, DMARC wrap) before decoration, or
# it differs from the current body by more than the body diff's limits.
# Emitted as "b": null (spec-06 §4.2).
NULL_BODY_RECIPE = object()

# The body diff's limits, shared by the DKIM2 implementations: a body
# Recipe restores at most MAX_RECIPE_LITERALS previous lines as literals
# (by default; [mta] message_instance_max_recipe_lines sets it),
# and the search gives up after MAX_DIFF_WORK units (one per diagonal
# visited, one per line compared along a snake).  Over either, the previous
# body is not recorded: the Recipe is "b": null.
MAX_RECIPE_LITERALS = 1000
MAX_DIFF_WORK = 4000000

# _body_diff's results other than a flat recipe.
BODY_IDENTICAL = object()
BODY_TOO_BIG = object()


def _body_diff(cur, prev, max_literals=MAX_RECIPE_LITERALS):
    """Diff the current body lines against the previous ones.

    Returns BODY_IDENTICAL, BODY_TOO_BIG (more than max_literals literal
    lines would be needed, or the search ran over MAX_DIFF_WORK), or the
    flat recipe that rebuilds prev from cur: [from, to] lists, 1-based and
    inclusive copy ranges of cur, and literal str lines of prev, in prev
    order.

    Every DKIM2 implementation runs this same algorithm, step for step, so
    that all of them produce the same recipe: trim the common prefix and
    suffix, drop the lines that cannot match, then Myers' greedy O(ND)
    search ("An O(ND) Difference Algorithm and Its Variations", 1986) with
    a fixed tie-break.  The literal cap bounds the edit distance searched.
    Lines compare exactly.
    """
    C, P = len(cur), len(prev)
    pre = 0
    while pre < C and pre < P and cur[pre] == prev[pre]:
        pre += 1
    if pre == C and pre == P:
        return BODY_IDENTICAL
    suf = 0
    while (suf < C - pre and suf < P - pre
           and cur[C - 1 - suf] == prev[P - 1 - suf]):
        suf += 1
    a = cur[pre:C - suf]
    b = prev[pre:P - suf]
    cnt_a, cnt_b = {}, {}
    for line in a:
        cnt_a[line] = cnt_a.get(line, 0) + 1
    for line in b:
        cnt_b[line] = cnt_b.get(line, 0) + 1
    # Lines only in one body can never be matched: drop them, keeping the
    # indexes of the rest.  Equal lines get equal ids.
    ids = {}
    A, ai = [], []
    for i, line in enumerate(a):
        if line in cnt_b:
            A.append(ids.setdefault(line, len(ids)))
            ai.append(i)
    B, bj = [], []
    for j, line in enumerate(b):
        if line in cnt_a:
            B.append(ids.setdefault(line, len(ids)))
            bj.append(j)
    N, M = len(A), len(B)
    u = len(b) - M
    # No alignment restores fewer lines than the surplus of each line in b.
    floor = u + sum(max(0, cnt_b.get(line, 0) - count)
                    for line, count in cnt_a.items())
    if floor > max_literals:
        return BODY_TOO_BIG
    match = [None] * M
    if N > 0 and M > 0:
        dmax = N - M + 2 * (max_literals - u)
        if dmax > N + M:
            dmax = N + M
        if dmax < 0:
            return BODY_TOO_BIG
        # V[k] lives at V[off + k], k in [-dmax-1, dmax+1].
        off = dmax + 1
        V = [0] * (2 * dmax + 3)
        # trace[d] holds V[-d-1 .. d+1], the only part round d reads.
        trace = []
        work = 0
        found = None
        for d in range(dmax + 1):
            trace.append(V[off - d - 1:off + d + 2])
            for k in range(-d, d + 1, 2):
                if k == -d or (k != d and V[off + k - 1] < V[off + k + 1]):
                    x = V[off + k + 1]
                else:
                    x = V[off + k - 1] + 1
                y = x - k
                while x < N and y < M and A[x] == B[y]:
                    x += 1
                    y += 1
                    work += 1
                V[off + k] = x
                work += 1
                if work > MAX_DIFF_WORK:
                    return BODY_TOO_BIG
                if x == N and y == M:
                    found = d
                    break
            if found is not None:
                break
        if found is None:
            return BODY_TOO_BIG
        x, y = N, M
        for d in range(found, 0, -1):
            T = trace[d]
            # V[k] of round d is T[k + d + 1].
            k = x - y
            down = k == -d or (k != d and T[k + d] < T[k + d + 2])
            pk = k + 1 if down else k - 1
            px = T[pk + d + 1]
            py = px - pk
            if down:
                sx, sy = px, py + 1
            else:
                sx, sy = px + 1, py
            while x > sx:
                x -= 1
                y -= 1
                match[y] = x
            x, y = px, py
        while x > 0:
            x -= 1
            y -= 1
            match[y] = x
    # Where each previous line comes from in cur (0-based), if anywhere.
    src = [None] * P
    for j in range(pre):
        src[j] = j
    for j in range(P - suf, P):
        src[j] = j - P + C
    for y, x in enumerate(match):
        if x is not None:
            src[pre + bj[y]] = pre + ai[x]
    recipe = []
    literals = 0
    for j, i in enumerate(src):
        if i is None:
            recipe.append(prev[j])
            literals += 1
        elif recipe and isinstance(recipe[-1], list) and recipe[-1][1] == i:
            recipe[-1][1] = i + 1
        else:
            recipe.append([i + 1, i + 1])
    if literals > max_literals:
        return BODY_TOO_BIG
    return recipe


@public
def compute_body_recipe(current_lines, previous_lines,
                        max_literals=MAX_RECIPE_LITERALS):
    """Compute body recipe to undo current body back to previous.

    Uses the capped line diff, _body_diff.  Returns a list of Recipe steps
    (spec-06 §5):
    - {"c": [start, end]}: copy this range of lines from the current body
      (1-based, inclusive)
    - {"d": [line, ...]}: literal ASCII lines from the previous body
    - {"b": [base64, ...]}: literal 8-bit lines from the previous body,
      see _literal_steps

    Returns None if bodies are identical, and NULL_BODY_RECIPE if the
    previous body would take more than max_literals literal lines to
    restore, or more than MAX_DIFF_WORK to diff.
    """
    flat = _body_diff(current_lines, previous_lines, max_literals)
    if flat is BODY_IDENTICAL:
        return None
    if flat is BODY_TOO_BIG:
        return NULL_BODY_RECIPE
    recipe = []
    pending_data = []
    for step in flat:
        if isinstance(step, str):
            pending_data.append(step)
            continue
        if pending_data:
            recipe.extend(_literal_steps(pending_data))
            pending_data = []
        recipe.append({'c': step})
    if pending_data:
        recipe.extend(_literal_steps(pending_data))
    return recipe


def _wrapped_body_recipe(wire, snap_raw, boundary):
    """The body Recipe for a message decorate's DKIM2 wrap spliced the
    original body into, taken from where the wrap put it rather than by
    splitting both bodies into lines and comparing them.

    The wrap writes the original body, its final line break dropped,
    straight before the line break and delimiter of its boundary.  If the
    first occurrence of the original body's octets (trailing empty lines
    off, as spec-06 §6.1 has it) in the wire body starts a line and is
    followed by that delimiter, it is the first run of current lines equal
    to the previous lines, and k and n are counts of CRLFs.  (That is a
    valid Recipe, though not always the one compute_body_recipe gives: when
    the original body starts with a line the wrap's preamble also has, a
    blank one say, the diff may copy that line from the preamble.)
    Returns None when the octets are not there as expected (the caller
    then diffs), or the body is empty.
    """
    start = _body_offset(snap_raw)
    end = _body_end(snap_raw, start)
    if end == start:
        return None
    trailing = (len(snap_raw) - end) // 2
    original = memoryview(snap_raw)[start:end]
    wire_start = _body_offset(wire)
    pos = wire.find(original, wire_start)
    if pos == -1:
        return None
    if pos > wire_start and not wire.endswith(b'\r\n', wire_start, pos):
        return None
    delimiter = b'\r\n' * max(trailing, 1) + b'--' + boundary.encode('ascii')
    if not wire.startswith(delimiter, pos + len(original)):
        return None
    k = wire.count(b'\r\n', wire_start, pos)
    n = snap_raw.count(b'\r\n', start, end) + 1
    return [{'c': [k + 1, k + n]}]


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
        # How far into each value's positions the scan has got: last_end
        # only rises, so an index passed over once is never wanted again
        # and each list is walked once (a run of identical fields stays
        # linear).
        scanned = dict.fromkeys(positions, 0)
        # Build Recipe for this header name using {"c":...}/{"d":...}
        hrecipe = []
        pending_data = []
        last_end = 0
        for pval, pcanon in zip(prev_vals_rev, prev_canon_rev):
            # The lowest matching instance above everything copied so far;
            # none means a literal (§5.1 ranges must ascend).
            idx = None
            if pcanon in positions:
                found = positions[pcanon]
                k = scanned[pcanon]
                while k < len(found) and found[k] <= last_end:
                    k += 1
                scanned[pcanon] = k
                if k < len(found):
                    idx = found[k]
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
    # Walk the value by index: slicing off the rest each time made this
    # quadratic, minutes of CPU for a multi-megabyte Recipe.
    lines = []
    pos = 0
    end = len(value)
    tgt = first_target
    mx = first_max
    while end - pos > tgt + 2:
        # Don't wrap if only 2 or fewer chars would go on the next line.
        # Try to break at a '; ' boundary.  Search up to hard_max first
        # (allows slightly longer lines to land on a clean boundary),
        # then fall back to breaking at the target.
        brk_min = 10 if not lines else tgt - 5
        break_at = value.rfind('; ', pos + brk_min, pos + mx)
        if break_at > pos:
            # Include the ';' on this line, skip the space.
            lines.append(value[pos:break_at + 1])
            pos = break_at + 2
        else:
            # No tag boundary — break mid-base64 at the target.
            lines.append(value[pos:pos + tgt])
            pos += tgt
        tgt = cont_target
        mx = cont_max
    if pos < end:
        lines.append(value[pos:])
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
        r['b'] = None if body_recipe is NULL_BODY_RECIPE else body_recipe
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


# spec-06 §7: a tag is x-tag-name [FWS] "=" [FWS] [x-tag-value], the
# value x-tag-chars (%x21-3A / %x3C-7E) with FWS only between them.  After
# unfolding, FWS is a WSP run.
_MI_TAG = re.compile(r'[ \t]*([A-Za-z][A-Za-z0-9_]*)[ \t]*=[ \t]*'
                     r'([\x21-\x3a\x3c-\x7e \t]*)')
_MI_BASE64 = re.compile(r'(?:[A-Za-z0-9+/]{4})*'
                        r'(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?')


def _parse_mi(mi_value):
    """Parse a Message-Instance header value into (version, hashes, recipe).

    Returns (version, hashes_dict, recipe_dict_or_None); hashes is None when
    no sha256 hash-set is present.
    h= tag format: sha256:header_hash:body_hash[, more hash-sets]
    r= tag format: base64-encoded JSON
    Raises MalformedInstance for a value that is not a valid tag-list.
    """
    val = str(mi_value)
    # Unfold: FWS is CRLF followed by one *or more* WSP (RFC 5322;
    # draft-ietf-dkim-dkim2-spec-06 §2.12).  Only the line ending goes; the
    # WSP stays, so a fold inside a token (a hash name) is still WSP there
    # and a syntax error.  spec-06 §2.14: FWS within a base64string is
    # ignored when the value is used, so the base64 values drop all WSP.
    val = re.sub(r'\r?\n(?=[ \t])', '', val)
    syntax_error = MalformedInstance(
        'Message-Instance m={} syntax error'.format(_get_mi_version(val)))
    tags = {}
    for fragment in val.split(';'):
        if not fragment.strip(' \t'):
            continue
        match = _MI_TAG.fullmatch(fragment)
        if match is None:
            raise syntax_error
        # Tag names are case insignificant and appear at most once (§7).
        name = match.group(1).lower()
        if name in tags:
            raise syntax_error
        tags[name] = match.group(2).strip(' \t')
    version = tags.get('m', '')
    if not version.isascii() or not version.isdigit() or 'h' not in tags:
        raise syntax_error
    hashes = None
    recipe = None
    # h= is one or more hash-sets (§7.3): FWS around the hash name and the
    # colons and inside the base64 digests, never inside the hash name; a
    # set needs both digests, and a hash name appears once.
    seen = set()
    for hash_set in tags['h'].split(','):
        parts = hash_set.split(':')
        if len(parts) != 3:
            raise syntax_error
        hash_name = parts[0].strip(' \t')
        digests = [re.sub(r'[ \t]', '', part) for part in parts[1:]]
        if (not hash_name or re.search(r'[ \t]', hash_name)
                or hash_name in seen
                or not all(digest and _MI_BASE64.fullmatch(digest)
                           for digest in digests)):
            raise syntax_error
        seen.add(hash_name)
        if hash_name == 'sha256':
            hashes = {
                'h': [hash_name, digests[0]],
                'b': [hash_name, digests[1]],
            }
    # Parse r= tag: base64-encoded JSON, returned as decoded.
    if 'r' in tags:
        b64_clean = re.sub(r'[ \t]', '', tags['r'])
        try:
            recipe = json.loads(base64.b64decode(b64_clean, validate=True))
        except ValueError as error:
            raise MalformedRecipe('r= is not base64 JSON: {}'.format(error))
    return int(version), hashes, recipe


@public
class MalformedInstance(ValueError):
    """A Message-Instance value that is not a valid tag-list (spec-06 §7).

    _parse_mi raises it; verify_mi_raw reports it as an instance that does
    not verify.
    """


@public
class MalformedRecipe(ValueError):
    """A Message-Instance Recipe that cannot be applied (spec-06 §5).

    _parse_mi raises it for an r= that is not base64 JSON; verify_mi_raw
    reports that as an instance that does not verify.
    """


# ---------------------------------------------------------------------------
# Snapshot helpers
# ---------------------------------------------------------------------------

# Mailman stamps Message-ID-Hash onto the message (the LMTP runner after
# keeping the received octets, `mailman inject` before the pipeline), so the
# message as received does not include it.  A snapshot taken from the parsed
# message leaves it out.  (X-Message-ID-Hash is already X- excluded from the
# hash, so it is deliberately not considered here.)
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


def _prepend_header(msg, name, value):
    """Insert a header at the top of the message headers.

    Unlike msg[name] = value which appends, this inserts at position 0
    so Message-Instance headers appear before the original headers.
    """
    msg._headers.insert(0, (name, value))


_MISSING = object()


@public
@contextmanager
def without_received_bytes(msg):
    """Leave msg.original_bytes out of a copy of the message that never
    reaches Message-Instance egress (the archive and NNTP queues): pickled
    with it, the octets would be a second copy of the message in that
    queue, for nothing.  The message keeps them for its way to delivery.
    """
    saved = msg.__dict__.pop('original_bytes', _MISSING)
    try:
        yield msg
    finally:
        if saved is not _MISSING:
            msg.original_bytes = saved


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
            # The LMTP runner keeps the received octets when the site has
            # Message-Instance on; a list that opts out does not need them
            # pickled with the message through every queue.
            if hasattr(msg, 'original_bytes'):
                del msg.original_bytes
            return
        # Skip digests — they aggregate multiple messages and are not
        # a single authored message in the DKIM2 sense.
        if msgdata.get('isdigest'):
            return
        # The snapshot is the message as received: the octets the LMTP
        # runner kept or, for a message that came in otherwise (`mailman
        # inject`, an internal path, an older queue entry), the parsed
        # message less Mailman's own stamp.  The egress Recipe then records
        # everything Mailman has done since (the Message-ID-Hash stamp, a
        # repaired Message-ID).
        snap_raw = _received_bytes(msg)
        if snap_raw is None:
            # Serializing resolves auto-generated parameters (multipart
            # boundaries) on msg itself, before the copy is taken.
            _serialize_msg(msg)
            snap_raw = _serialize_msg(_strip_stamped(msg))
        msg.original_bytes = snap_raw
        existing_version = get_max_mi_version(msg)
        if existing_version > 0:
            # An MI is already present (inbound milter, or a signing sender).
            # NEVER modify it -- it may be signed.
            #
            # Nothing is emitted for this: X-DKIM2-Info records actions that
            # add a header, and accepting an existing instance adds none.
            matched, why = verify_mi_raw(snap_raw)
            if matched != existing_version:
                log.warning('Existing Message-Instance m=%d does not match '
                            'the message as received (%s); chain may not '
                            'undo cleanly', existing_version, why)
            log.debug('Accepted existing Message-Instance m=%d',
                      existing_version)
            msgdata['mi_snapshot'] = {
                'version': existing_version,
                'header_hash': compute_header_hash_raw(snap_raw),
                'body_hash': compute_body_hash_raw(snap_raw),
            }
            return
        # No MI headers present -- add m=1 documenting the message as
        # received.
        hcount, hnames = _hashed_header_names(_raw_header_pairs(snap_raw))
        h_hash = compute_header_hash_raw(snap_raw)
        b_hash = compute_body_hash_raw(snap_raw)
        value = build_mi_header_value(1, h_hash, b_hash)
        _prepend_header(msg, 'Message-Instance', value)
        _prepend_header(msg, 'X-DKIM2-Info', _dkim2_info(
            'mi-m=1', hc=hcount, hn=hnames))
        log.debug('Added Message-Instance m=1')
        msgdata['mi_snapshot'] = {
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
        if snapshot and get_max_mi_version(msg) > snapshot['version']:
            # Mailman already stamped this message: BulkDelivery decorates
            # and runs egress on the queued message itself, and a temporary
            # failure re-enqueues it, stamped, for a retry.
            return
        if not snapshot:
            # No snapshot means this message didn't go through an ingress
            # handler (e.g. internally generated via VirginPipeline).
            # If no MI headers are present, Mailman is the originator —
            # add MI m=1 so the message enters the DKIM2 ecosystem.
            if get_max_mi_version(msg) == 0:
                wire = _wire_bytes(msg)
                hcount, hnames = _get_hashed_headers(msg)
                h_hash = compute_header_hash(msg)
                b_hash = compute_body_hash_raw(wire)
                value = build_mi_header_value(1, h_hash, b_hash)
                _prepend_header(msg, 'Message-Instance', value)
                _prepend_header(msg, 'X-DKIM2-Info',
                                _dkim2_info('mi-m=1', hc=hcount, hn=hnames))
                log.debug('Added originator Message-Instance m=1')
            return
        # Serialize the way smtplib will (which also resolves multipart
        # boundaries before hashing): this instance describes what is sent.
        wire = _wire_bytes(msg)
        # Compute current hashes.
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash_raw(wire)
        # Check if anything changed.
        if (h_hash == snapshot['header_hash']
                and b_hash == snapshot['body_hash']):
            return
        # The snapshot is the bytes the message carries as original_bytes.
        # Work from the octets: parsing and re-serializing them here would
        # put the generator's reshaping of part headers into the "previous"
        # side.
        snap_raw = getattr(msg, 'original_bytes', None)
        if snap_raw is None:
            log.warning('Message-Instance snapshot missing (no '
                        'original_bytes) -- skipping MI egress')
            return
        snap_raw = _normalize_crlf(snap_raw)
        # Compute Recipes.
        body_recipe = None
        header_recipe = None
        if b_hash != snapshot['body_hash']:
            if msgdata.get('body-modified'):
                body_recipe = NULL_BODY_RECIPE
            else:
                # decorate's DKIM2 wrap records its boundary: the original
                # body is then found where the wrap put it, with no line
                # lists of two large bodies.  Anything else is diffed.
                boundary = msgdata.get('dkim2-wrap')
                if boundary is not None:
                    body_recipe = _wrapped_body_recipe(
                        wire, snap_raw, boundary)
                if body_recipe is None:
                    body_recipe = compute_body_recipe(
                        _body_lines_raw(wire), _body_lines_raw(snap_raw),
                        int(config.mta.message_instance_max_recipe_lines))
        if h_hash != snapshot['header_hash']:
            current_headers = _collect_headers(msg)
            header_recipe = compute_header_recipe(
                current_headers, _hashed_pairs(_raw_header_pairs(snap_raw)))
        hcount, hnames = _get_hashed_headers(msg)
        version = get_max_mi_version(msg) + 1
        value = build_mi_header_value(
            version, h_hash, b_hash, header_recipe, body_recipe)
        _prepend_header(msg, 'Message-Instance', value)
        _prepend_header(msg, 'X-DKIM2-Info', _dkim2_info(
            'mi-m={}'.format(version),
            hc=hcount, hn=hnames))
        log.debug('Added Message-Instance m=%d', version)
