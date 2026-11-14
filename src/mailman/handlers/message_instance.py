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

"""DKIM2 Message-Instance headers (draft-ietf-dkim-dkim2-spec-09)."""

import re
import copy
import json
import base64
import hashlib
import logging

from email.generator import BytesGenerator
from io import BytesIO
from lazr.config import as_boolean
from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.handler import IHandler
from public import public
from zope.interface import implementer


log = logging.getLogger('mailman.dkim2')

# Reported in X-DKIM2-Info.
DKIM2_DRAFT = 'ietf-dkim-dkim2-spec-09'
DKIM2_REPO = 'github.com/brong/mailman'
DKIM2_DATE = '2026-11-13'
DKIM2_SOFTWARE = 'mailman'


# The hint egress puts on every distribution copy, for the signing milter.
EXPLODED_HINT = 'X-DKIM2-Exploded'
public(EXPLODED_HINT=EXPLODED_HINT)

_INFO_HEADER_NAME_LEN = len('X-DKIM2-Info: ')
_INFO_TAB_WIDTH = 8
_INFO_MAX_LINE = 78


def _dkim2_info(action, **extras):
    """Build an X-DKIM2-Info header value.

    Keyword arguments other than None are added as tag=value pairs.  Every
    tag ends with ';', so a ';' inside a value becomes ','.  The value is
    folded after a ';' (or a ',' in an over-long tag) to keep lines under
    78 characters.
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
    # name; continuation lines start with a tab, counted as 8 columns.
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


# Header fields DKIM2 leaves out of the header hash.
_EXCLUDED_NAMES = frozenset({
    'apparently-to', 'arc-authentication-results', 'arc-message-signature',
    'arc-seal', 'authentication-results', 'delivered-to',
    'dkim-signature', 'dkim2-signature', 'dl-expansion-history',
    'message-instance', 'original-recipient', 'received', 'return-path',
    'sio-label-history', 'x400-received', 'x400-trace',
})

_EXCLUDED_PREFIXES = ('x-', 'received-')


@public
def should_exclude_header(name):
    """Return True if this header should be excluded from the header hash."""
    name_lower = name.lower()
    if name_lower in _EXCLUDED_NAMES:
        return True
    for prefix in _EXCLUDED_PREFIXES:
        if name_lower.startswith(prefix):
            return True
    return False


def _get_hashed_headers(msg):
    """Return (count, names) of the hashed headers, in hash order."""
    return _hashed_header_names(list(msg.raw_items()))


@public
def b64(data):
    """Base64-encode bytes, return str with no newlines."""
    return base64.b64encode(data).decode('ascii')


def _b64json(obj):
    """JSON-encode then base64-encode."""
    return b64(json.dumps(obj, separators=(',', ':')).encode('utf-8'))


def _canonicalize_header_field(name, value):
    """Return the DKIM2 canonical form of a header field, as bytes."""
    full = '{}: {}'.format(name, value)
    # Unfold.
    full = re.sub(r'\r?\n([ \t])', r'\1', full)
    colon = full.find(':')
    if colon == -1:
        hname, hvalue = full, ''
    else:
        hname, hvalue = full[:colon], full[colon + 1:]
    # Lowercase the name, collapse whitespace runs to one space, and strip
    # whitespace at the end of the value and around the colon.
    hname = hname.lower()
    hname = re.sub(r'[ \t]+', ' ', hname).strip()
    hvalue = re.sub(r'[ \t]+', ' ', hvalue)
    hvalue = hvalue.rstrip(' \t')
    hvalue = hvalue.lstrip(' \t')
    return (hname + ':' + hvalue).encode('utf-8', errors='surrogateescape')


def _wire_value(msg, name, value):
    """Return a header's value as the generator will put it on the wire.

    A Header object (subject_prefix makes one) goes out RFC 2047 encoded,
    not as its str().  The value must come from msg.raw_items(): items()
    would turn raw 8-bit octets into an unknown-8bit encoded word.
    """
    try:
        wire = msg.policy.fold_binary(name, value)
    except Exception:                               # pragma: nocover
        return str(value)
    text = wire.decode('utf-8', errors='surrogateescape')
    colon = text.find(':')
    if colon != -1:
        text = text[colon + 1:]
    # Unfold (a Recipe string has no CR or LF), then strip as the parser
    # would: no leading whitespace, no line ending.
    text = re.sub(r'\r?\n[ \t]+', ' ', text.rstrip('\r\n'))
    return text.lstrip(' \t')


@public
def hash_header_pairs(pairs):
    """The SHA-256 header hash of (name, value) pairs in document order."""
    canon_headers = []
    for name, value in pairs:
        canon = _canonicalize_header_field(name, value)
        canon_headers.append((name.lower().encode(), canon))
    # Bottom-up, then stable sorted by name, each line ending in CRLF.
    canon_headers.reverse()
    canon_headers.sort(key=lambda x: x[0])
    data = b'\r\n'.join(ch for _, ch in canon_headers)
    if data:
        data += b'\r\n'
    return hashlib.sha256(data).digest()


@public
def compute_header_hash(msg):
    """The SHA-256 header hash of a message as it will be sent."""
    return hash_header_pairs(collect_headers(msg))


@public
def wire_bytes(msg):
    """The octets smtplib.SMTP.send_message will send, with CRLFs.

    Unlike msg.as_bytes(), send_message drops Bcc and Resent-Bcc and turns
    a body line starting "From " into ">From ".
    """
    # copy.copy() of a Message shares its __dict__ (see __setstate__), so
    # the copy gets its own, without the header log.
    msg_copy = object.__new__(type(msg))
    msg_copy.__dict__.update(msg.__dict__)
    msg_copy.stop_header_log()
    del msg_copy['Bcc']
    del msg_copy['Resent-Bcc']
    buf = BytesIO()
    try:
        # Written with LF and made CRLF below: the generator's check that a
        # new boundary does not appear in the text only works with LF.
        BytesGenerator(buf).flatten(msg_copy)
    except UnicodeEncodeError:
        # https://bugs.python.org/issue41307, as Message.as_bytes works
        # around it; send_message would fail on this message anyway.
        return serialize_message(msg)
    # Keep any boundary the generator picked, so the message goes out with
    # the boundary hashed here.
    if msg.is_multipart() and msg.get_boundary() is None:
        msg.set_boundary(msg_copy.get_boundary())
    return normalize_crlf(buf.getvalue())


@public
def serialize_message(msg):
    """The message as parsed, with CRLFs; see wire_bytes for outgoing mail.

    This sets a boundary on any multipart that lacks one, so call it before
    hashing the headers.
    """
    return normalize_crlf(msg.as_bytes())


@public
def hash_body_bytes(body, start=0):
    """The SHA-256 body hash of body[start:]."""
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


_LINE_BREAK = re.compile(rb'\r\n|\r|\n')


@public
def normalize_crlf(raw):
    """Return raw with every line break as CRLF."""
    crlf = raw.count(b'\r\n')
    if raw.count(b'\r') == crlf and raw.count(b'\n') == crlf:
        return raw
    return _LINE_BREAK.sub(b'\r\n', raw)


def _raw_head(raw):
    idx = raw.find(b'\r\n\r\n')
    return raw if idx == -1 else raw[:idx]


def _body_offset(raw):
    idx = raw.find(b'\r\n\r\n')
    return len(raw) if idx == -1 else idx + 4


@public
def raw_header_pairs(raw):
    """(name, value) for each header field in raw, shaped as _wire_value."""
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
    return [(n, v) for n, v in pairs if not should_exclude_header(n)]


@public
def compute_header_hash_raw(raw):
    return hash_header_pairs(_hashed_pairs(raw_header_pairs(raw)))


@public
def compute_body_hash_raw(raw):
    return hash_body_bytes(raw, _body_offset(raw))


_FROM_LINE = re.compile(rb'^From ', re.MULTILINE)


def _body_shape(raw):
    """The length and line count of raw's body, and its "From " lines.

    The body is taken less its trailing empty lines, as it is hashed.  Each
    "From " line is [line number, offset], both from the start of the body.
    """
    start = _body_offset(raw)
    end = _body_end(raw, start)
    from_lines = []
    line = 1
    last = start
    for match in _FROM_LINE.finditer(raw, start, end):
        line += raw.count(b'\r\n', last, match.start())
        last = match.start()
        from_lines.append([line, match.start() - start])
    lines = raw.count(b'\r\n', start, end) + 1 if end > start else 0
    return end - start, lines, from_lines


def _hashed_header_names(pairs):
    """(count, names) of the hashed headers, for X-DKIM2-Info."""
    names = [name.lower() for name, _ in _hashed_pairs(pairs)]
    names.reverse()
    names.sort()
    return len(names), ','.join(names)


@public
def arrival_record(raw):
    """The digests of a message as received, and its arrival status.

    Returns the header and body hashes of raw, the shape of its body (see
    _body_shape), the highest m= it arrived with (0 for none), and whether
    that instance's hashes match: 'none', 'match', 'mismatch' or
    'malformed'.
    """
    body_length, body_lines, from_lines = _body_shape(raw)
    record = {
        'header_hash': compute_header_hash_raw(raw),
        'body_hash': compute_body_hash_raw(raw),
        'body_length': body_length,
        'body_lines': body_lines,
        'from_lines': from_lines,
        'version': 0,
        'status': 'none',
    }
    values = [v for n, v in raw_header_pairs(raw)
              if n.lower() == 'message-instance']
    if not values:
        return record
    versions = [instance_version(v) for v in values]
    record['version'] = max(versions)
    if len(set(versions)) != len(versions) or 0 in versions:
        record['status'] = 'malformed'
        return record
    top = values[versions.index(record['version'])]
    try:
        _, hashes, _ = parse_instance(top)
    except Exception:
        # Anything a hostile instance makes the parser raise: this runs in
        # the LMTP runner, where an exception would tempfail the post.
        record['status'] = 'malformed'
        return record
    if hashes is None:
        record['status'] = 'malformed'
        return record
    computed = (b64(record['header_hash']), b64(record['body_hash']))
    # Every hash-set of a "," list must match, in one "|" group.  Only
    # sha256 is computed here.
    for group in hashes['groups']:
        if all(name == 'sha256' and tuple(digests[:2]) == computed
               for name, *digests in group):
            record['status'] = 'match'
            return record
    record['status'] = 'mismatch'
    return record


def _literal_steps(values):
    """Recipe steps that emit these literal header values or body lines.

    UTF-8 text goes in a "d" step; anything else in a "u" step, base64
    encoded.  Consecutive literals of one kind share a step.
    """
    steps = []
    for value in values:
        octets = value.encode('utf-8', errors='surrogateescape')
        try:
            kind, item = 'd', octets.decode('utf-8')
        except UnicodeDecodeError:
            kind, item = 'u', b64(octets)
        if steps and kind in steps[-1]:
            steps[-1][kind].append(item)
        else:
            steps.append({kind: [item]})
    return steps


# The body Recipe when the previous body cannot be rebuilt; "b": null.
NULL_BODY_RECIPE = object()


def _flatten(msg):
    """msg as smtplib's generator writes it, with CRLFs."""
    buf = BytesIO()
    BytesGenerator(buf).flatten(msg)
    return normalize_crlf(buf.getvalue())


def _with_payload(msg, payload):
    """A shallow copy of msg with another payload, for flattening."""
    stand_in = object.__new__(type(msg))
    stand_in.__dict__.update(msg.__dict__)
    stand_in.stop_header_log()
    stand_in._payload = payload
    return stand_in


def _flattened_body(msg):
    octets = _flatten(msg)
    return octets[_body_offset(octets):]


def _trimmed_lines(octets):
    """The number of lines in octets, less trailing empty lines."""
    end = _body_end(octets, 0)
    return octets.count(b'\r\n', 0, end) + 1 if end > 0 else 0


# Stands in for the post's body when flattening what decoration put around it.
_POST = 'dkim2-post-body'


def _post_layout(msg, body, record, body_lines):
    """Where the post's body lies in body, the outgoing message's body.

    Everything decoration added is small, so it is flattened on its own,
    with a stand-in for the post.  Returns a list of pieces of the post, in
    order, as (start, end, first line, last line, inserted bytes, inserted
    lines): body[start:end] holds lines first to last of the post, after
    what decoration inserted before it.  Returns None when body is not laid
    out that way.
    """
    crlf = b'\r\n'
    if record is None:
        return [(0, len(body), 1, body_lines, 0, 0)]
    parts = msg.get_payload()
    if record['shape'] == 'wrapped':
        # The post is one part of a new multipart/mixed.
        stand_ins = list(parts)
        index = 1 if record['header'] else 0
        stand_ins[index] = _with_payload(parts[index], _POST)
        around = _flattened_body(_with_payload(msg, stand_ins))
        before, found, after = around.partition(_POST.encode('ascii'))
        end = len(body) - len(after)
        if (not found or body[:len(before)] != before or
                body[end:] != after):
            return None
        return [(len(before), end, 1, body_lines,
                 len(before), before.count(crlf))]
    # 'mixed': the header part went in after the post's first delimiter and
    # the footer part before its close delimiter, so the post is in three
    # pieces.  Flattened with no parts, the post is the first and the last
    # of those, with nothing between.
    delimiter = '--{}'.format(msg.get_boundary()).encode('ascii') + crlf
    empty = _flattened_body(_with_payload(msg, []))
    first = empty.find(delimiter)
    if first == -1:
        return None
    first += len(delimiter)
    # The close delimiter's line break belongs to it, so the last piece
    # starts after that, and the footer part goes in before it.
    last = len(empty) - first - 2
    header = (_flatten(parts[0]) + crlf + delimiter
              if record['header'] else b'')
    footer = (delimiter + _flatten(parts[-1]) + crlf
              if record['footer'] else b'')
    middle_end = len(body) - last - len(footer)
    if (body[:first] != empty[:first] or
            body[len(body) - last:] != empty[len(empty) - last:] or
            body[first:first + len(header)] != header or
            body[middle_end:middle_end + len(footer)] != footer):
        return None
    first_lines = empty.count(crlf, 0, first)
    last_lines = _trimmed_lines(empty[len(empty) - last:])
    middle_last = body_lines - last_lines
    return [
        (0, first, 1, first_lines, 0, 0),
        (first + len(header), middle_end, first_lines + 1, middle_last,
         len(header), header.count(crlf)),
        (middle_end + len(footer), len(body), middle_last + 1, body_lines,
         len(footer), footer.count(crlf)),
    ]


@public
def structural_body_recipe(msg, wire, record, snapshot):
    """The body Recipe back to the post as received, or None.

    record is how decoration laid out the post (None when nothing was
    added) and snapshot gives the post's body as it arrived: its length,
    its line count and its "From " lines, which smtplib writes as ">From ".
    The Recipe copies the post's lines from where they are and restores
    each "From " line as a literal.  It is checked against the snapshot's
    body hash first; None means it does not match.
    """
    start = _body_offset(wire)
    body = memoryview(wire)[start:]
    layout = _post_layout(msg, body, record, snapshot['body_lines'])
    if layout is None:
        return None
    from_lines = snapshot['from_lines']
    digest = hashlib.sha256()
    remaining = snapshot['body_length']
    steps = []
    literals = []

    def copy(first, last):
        if first > last:
            return
        if len(literals) > 0:
            steps.extend(_literal_steps(literals))
            literals.clear()
        if (len(steps) > 0 and 'c' in steps[-1] and
                steps[-1]['c'][1] == first - 1):
            steps[-1]['c'][1] = last
        else:
            steps.append({'c': [first, last]})

    byte_shift = 0
    line_shift = 0
    escaped = 0
    k = 0
    for begin, end, first, last, inserted, inserted_lines in layout:
        byte_shift += inserted
        line_shift += inserted_lines
        position = begin
        line = first
        while k < len(from_lines) and from_lines[k][0] <= last:
            from_line, offset = from_lines[k]
            mark = offset + byte_shift + escaped
            if body[mark:mark + 6] != b'>From ':
                return None
            # Hash up to the escape, and resume after it.
            take = min(mark - position, remaining)
            digest.update(body[position:position + take])
            remaining -= take
            position = mark + 1
            copy(line + line_shift, from_line - 1 + line_shift)
            eol = wire.find(b'\r\n', start + position)
            if eol == -1:
                eol = len(wire)
            literals.append(bytes(body[position:eol - start]).decode(
                'utf-8', errors='surrogateescape'))
            line = from_line + 1
            escaped += 1
            k += 1
        copy(line + line_shift, last + line_shift)
        take = min(end - position, remaining)
        digest.update(body[position:position + take])
        remaining -= take
    if len(literals) > 0:
        steps.extend(_literal_steps(literals))
    digest.update(b'\r\n')
    if remaining != 0 or digest.digest() != snapshot['body_hash']:
        return None
    return steps


@public
def compute_header_recipe(current_headers, previous_headers):
    """The header Recipe from current back to previous (name, value) pairs.

    Returns None if no hashed header changed.  The "c" ranges for a name
    must ascend, so a value that cannot be copied in order is restored as a
    literal.
    """
    # Collect by lowercase name, preserving order
    def collect(headers):
        result = {}
        for name, value in headers:
            lname = name.lower()
            if should_exclude_header(lname):
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
            # The lowest matching field above everything copied so far;
            # none means a literal.
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
            if (hrecipe and 'c' in hrecipe[-1] and
                    idx == hrecipe[-1]['c'][1] + 1):
                hrecipe[-1]['c'][1] = idx
            else:
                hrecipe.append({'c': [idx, idx]})
            last_end = idx
        if pending_data:
            hrecipe.extend(_literal_steps(pending_data))
        recipe[name] = hrecipe

    return recipe if recipe else None


_INSTANCE_HEADER_NAME_LEN = len('Message-Instance: ')


@public
def fold_instance_value(value, target=72, hard_max=77):
    """Fold a Message-Instance header value.

    Lines aim for target characters, breaking after a '; ' when one falls
    before hard_max, and never leave two or fewer characters on a line.
    """
    # First line budget is reduced by the header name.
    first_target = target - _INSTANCE_HEADER_NAME_LEN
    first_max = hard_max - _INSTANCE_HEADER_NAME_LEN
    # Continuation lines start with a tab, counted as 8 columns.
    cont_target = target - _INFO_TAB_WIDTH
    cont_max = hard_max - _INFO_TAB_WIDTH
    # Walk by index; slicing would be quadratic on a large Recipe.
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
            # No tag boundary, so break mid-base64 at the target.
            lines.append(value[pos:pos + tgt])
            pos += tgt
        tgt = cont_target
        mx = cont_max
    if pos < end:
        lines.append(value[pos:])
    return '\r\n\t'.join(lines)


@public
def instances_enabled(mlist=None):
    """Is Message-Instance support enabled, for the site and the list?"""
    if not as_boolean(config.mta.message_instance):
        return False
    return mlist is None or mlist.dkim2_message_instance


@public
def build_instance_value(version, header_hash, body_hash,
                         header_recipe=None, body_recipe=None):
    """Build a Message-Instance header field value string.

    Returns the value part (without the field name), folded for RFC 5322.
    """
    value = 'm={}; h=sha256:{}:{}'.format(
        version, b64(header_hash), b64(body_hash))
    # Build Recipe if there are any changes
    r = {}
    if header_recipe is not None:
        r['h'] = header_recipe
    if body_recipe is not None:
        r['b'] = None if body_recipe is NULL_BODY_RECIPE else body_recipe
    if r:
        value += '; r={}'.format(_b64json(r))
    value += ';'
    return fold_instance_value(value)


@public
def get_max_instance_version(msg):
    """Get the highest instance number in a message, or 0 if none."""
    max_m = 0
    for val in msg.get_all('message-instance', []):
        max_m = max(max_m, instance_version(val))
    return max_m


@public
def instance_version(instance_value):
    """The m= of a Message-Instance value, or 0 if it has no valid one."""
    match = re.search(r'(?:^|;)[ \t\r\n]*m[ \t\r\n]*=[ \t\r\n]*(\d+)',
                      str(instance_value))
    if match is None or len(match.group(1)) > 2:
        return 0
    return int(match.group(1))


# A tag-spec, after unfolding.
_INSTANCE_TAG = re.compile(r'[ \t]*([A-Za-z][A-Za-z0-9_]*)[ \t]*=[ \t]*'
                           r'([\x21-\x3a\x3c-\x7e \t]*)')
_INSTANCE_BASE64 = re.compile(r'(?:[A-Za-z0-9+/]{4})*'
                              r'(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?')


def _unique_keys(pairs):
    """A json object_pairs_hook rejecting duplicate or uppercase keys."""
    seen = set()
    for key, _value in pairs:
        if re.search(r'[A-Z]', key):
            raise ValueError('key {!r} is not lowercase'.format(key))
        if key in seen:
            raise ValueError('duplicate key {!r}'.format(key))
        seen.add(key)
    return dict(pairs)


@public
def parse_instance(instance_value):
    """Parse a Message-Instance value into (version, hashes, recipe).

    hashes is None when there is no sha256 hash-set, and recipe is None
    when there is no r= tag.  Raises MalformedInstance or MalformedRecipe.
    """
    val = str(instance_value)
    # Unfold, keeping the whitespace: a fold inside a hash name is still
    # an error.  Whitespace inside base64 is dropped below.
    val = re.sub(r'\r?\n(?=[ \t])', '', val)
    syntax_error = MalformedInstance(
        'Message-Instance m={} syntax error'.format(instance_version(val)))
    tags = {}
    for fragment in val.split(';'):
        if not fragment.strip(' \t'):
            continue
        match = _INSTANCE_TAG.fullmatch(fragment)
        if match is None:
            raise syntax_error
        # Tag names appear at most once; one that is not lowercase is an
        # unknown tag.
        name = match.group(1)
        if name in tags:
            raise syntax_error
        if re.search(r'[A-Z]', name):
            continue
        tags[name] = match.group(2).strip(' \t')
    version = tags.get('m', '')
    if (not version.isascii() or not version.isdigit() or
            len(version) > 2 or 'h' not in tags):
        raise syntax_error
    hashes = None
    recipe = None
    # h= is "|" groups of "," lists of name:header:body hash-sets.  A hash
    # name appears once in the tag.
    seen = set()
    groups = []
    for group in tags['h'].split('|'):
        sets = []
        for hash_set in group.split(','):
            parts = hash_set.split(':')
            if len(parts) != 3:
                raise syntax_error
            hash_name = parts[0].strip(' \t')
            digests = [re.sub(r'[ \t]', '', part) for part in parts[1:]]
            if (not hash_name or re.search(r'[ \t]', hash_name) or
                    hash_name in seen or
                    not all(digest and _INSTANCE_BASE64.fullmatch(digest)
                            for digest in digests)):
                raise syntax_error
            seen.add(hash_name)
            sets.append((hash_name, *digests))
            if hash_name == 'sha256':
                hashes = {
                    'h': [hash_name, digests[0]],
                    'b': [hash_name, digests[1]],
                }
        groups.append(sets)
    if hashes is not None:
        hashes['groups'] = groups
    # Parse r= tag: base64-encoded JSON, returned as decoded.
    if 'r' in tags:
        b64_clean = re.sub(r'[ \t]', '', tags['r'])
        try:
            recipe = json.loads(base64.b64decode(b64_clean, validate=True),
                                object_pairs_hook=_unique_keys)
        except ValueError as error:
            raise MalformedRecipe('r= is not base64 JSON: {}'.format(error))
    return int(version), hashes, recipe


@public
class MalformedInstance(ValueError):
    """A Message-Instance value that is not a valid tag-list."""


@public
class MalformedRecipe(ValueError):
    """A Message-Instance Recipe that is not base64 JSON."""


# Header fields Mailman stamps on a message before the pipeline, which a
# snapshot of the parsed message leaves out.
_MAILMAN_STAMPED = ('message-id-hash',)

# Header fields smtplib.send_message deletes on the way to the MTA.
SEND_MESSAGE_DROPS = ('bcc', 'resent-bcc')
public(SEND_MESSAGE_DROPS=SEND_MESSAGE_DROPS)


def _strip_stamped(msg):
    """Return a deep copy of msg with the Mailman-stamped header(s) removed."""
    clean = copy.deepcopy(msg)
    for name in list(clean.keys()):
        if name.lower() in _MAILMAN_STAMPED:
            del clean[name]
    return clean


@public
def collect_headers(msg):
    """The hashed headers as (name, value) pairs, as they will be sent."""
    headers = []
    for name, value in msg.raw_items():
        if not should_exclude_header(name):
            headers.append((name, _wire_value(msg, name, value)))
    return headers


@public
def previous_header_pairs(msg):
    """The hashed headers as the message arrived, from its header log.

    Returns None when the message has no header log.
    """
    header_log = msg.header_log
    if header_log is None:
        return None
    pairs = [(name, value) for name, value in collect_headers(msg)
             if name.lower() not in header_log]
    for name, values in header_log.items():
        if should_exclude_header(name):
            continue
        # As received, unfolded: a Recipe string carries no CR or LF.
        pairs.extend((name, re.sub(r'\r?\n(?=[ \t])', '', str(value)))
                     for value in values)
    return pairs


def _snapshot(arrival, version):
    """The arrival record egress needs, with the instance number to follow."""
    snapshot = {name: arrival[name] for name in (
        'header_hash', 'body_hash', 'body_length', 'body_lines', 'from_lines')}
    snapshot['version'] = version
    return snapshot


@public
@implementer(IHandler)
class MessageInstanceIngress:
    """Snapshot the message as received, for Message-Instance egress."""

    name = 'message-instance-ingress'
    description = _('Snapshot the message for Message-Instance egress.')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        if not instances_enabled(mlist):
            # A list that opts out needs neither the arrival record nor the
            # header log the LMTP runner started.
            msg.stop_header_log()
            if hasattr(msg, 'dkim2_arrival'):
                del msg.dkim2_arrival
            return
        # A digest is not a single authored message.
        if msgdata.get('isdigest'):
            return
        # The snapshot is the arrival record the LMTP runner made, or for a
        # message that came in another way, one made here.
        arrival = getattr(msg, 'dkim2_arrival', None)
        if arrival is None:
            # Set any multipart boundaries before taking the copy.
            serialize_message(msg)
            arrival = msg.dkim2_arrival = arrival_record(
                serialize_message(_strip_stamped(msg)))
            msg.start_header_log()
            for name in _MAILMAN_STAMPED:
                if name in msg:
                    msg.header_log.setdefault(name, [])
        msgdata['dkim2_arrival_status'] = arrival['status']
        if msg.get_all('dkim2-signature') is None:
            # An unsigned post leaves with a single m=1, added at egress,
            # whose Recipe goes back to this snapshot.  Any unsigned
            # instance it came with is dropped.
            if get_max_instance_version(msg) > 0:
                log.debug('Dropping unsigned Message-Instance headers')
                del msg['message-instance']
            msgdata['dkim2_snapshot'] = _snapshot(arrival, 0)
            return
        # A signed post's instances are covered by its signature, so they
        # are left alone; egress adds m=N+1.
        existing_version = get_max_instance_version(msg)
        if arrival['status'] != 'match':
            log.warning('Message-Instance m=%d does not describe the '
                        'message as received (%s); chain may not undo '
                        'cleanly', existing_version, arrival['status'])
        log.debug('Accepted existing Message-Instance m=%d',
                  existing_version)
        msgdata['dkim2_snapshot'] = _snapshot(arrival, existing_version)


@public
@implementer(IHandler)
class MessageInstanceEgress:
    """Add Message-Instance m=N+1 at egress if message changed."""

    name = 'message-instance-egress'
    description = _('Add Message-Instance header at egress.')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        if not instances_enabled(mlist):
            return
        # smtplib.send_message deletes these; do it here, where the Recipe
        # records it.
        for name in SEND_MESSAGE_DROPS:
            del msg[name]
        snapshot = msgdata.get('dkim2_snapshot')
        if snapshot:
            # The list sends copies to more than one address, so tell the
            # signing MTA to set the "exploded" flag.
            del msg[EXPLODED_HINT]
            msg[EXPLODED_HINT] = 'yes'
        if snapshot and get_max_instance_version(msg) > snapshot['version']:
            # Already stamped: a retry of a queued message.
            return
        if not snapshot:
            # A message Mailman originated (e.g. through the virgin
            # pipeline) gets an m=1 of its own.
            if get_max_instance_version(msg) == 0:
                wire = wire_bytes(msg)
                hcount, hnames = _get_hashed_headers(msg)
                h_hash = compute_header_hash(msg)
                b_hash = compute_body_hash_raw(wire)
                value = build_instance_value(1, h_hash, b_hash)
                msg.prepend_header('Message-Instance', value)
                msg.prepend_header('X-DKIM2-Info', _dkim2_info(
                    'mi-m=1', hc=hcount, hn=hnames))
                log.debug('Added originator Message-Instance m=1')
            return
        # wire_bytes() also sets any multipart boundary, so it comes first.
        wire = wire_bytes(msg)
        h_hash = compute_header_hash(msg)
        b_hash = compute_body_hash_raw(wire)
        # An unsigned post (snapshot version 0) gets its m=1 regardless.
        if (snapshot['version'] > 0 and
                h_hash == snapshot['header_hash'] and
                b_hash == snapshot['body_hash']):
            return
        body_recipe = None
        header_recipe = None
        if b_hash != snapshot['body_hash']:
            body_recipe = structural_body_recipe(
                msg, wire, msgdata.get('dkim2-decoration'), snapshot)
            if body_recipe is None:
                body_recipe = NULL_BODY_RECIPE
        if h_hash != snapshot['header_hash']:
            previous = previous_header_pairs(msg)
            if (previous is None or
                    hash_header_pairs(previous) != snapshot['header_hash']):
                # A header change the log did not see.  There is no null
                # header Recipe, so a signed post gets no instance, and an
                # unsigned post an m=1 with no header Recipe.
                log.error('Header log does not rebuild the headers as '
                          'received (logged: %s; expected h=%s)',
                          ','.join(sorted(msg.header_log or ())) or '-',
                          b64(snapshot['header_hash']))
                if snapshot['version'] > 0:
                    return
            else:
                header_recipe = compute_header_recipe(
                    collect_headers(msg), previous)
        hcount, hnames = _get_hashed_headers(msg)
        version = get_max_instance_version(msg) + 1
        value = build_instance_value(
            version, h_hash, b_hash, header_recipe, body_recipe)
        msg.prepend_header('Message-Instance', value)
        msg.prepend_header('X-DKIM2-Info', _dkim2_info(
            'mi-m={}'.format(version),
            hc=hcount, hn=hnames,
            arrival=msgdata.get('dkim2_arrival_status')))
        log.debug('Added Message-Instance m=%d', version)
