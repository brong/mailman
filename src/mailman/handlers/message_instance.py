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
draft-ietf-dkim-dkim2-spec-02.  A Message-Instance header records cryptographic
hashes of the message headers and body at a point in the delivery chain, along
with optional diff recipes that allow undoing changes made at each hop.
"""

import base64
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
DKIM2_DRAFT = 'ietf-dkim-dkim2-spec-02'
DKIM2_REPO = 'github.com/brong/mailman'
DKIM2_DATE = '2026-05-18'
DKIM2_SOFTWARE = 'mailman'


def _dkim2_info(action, **extras):
    """Build an X-DKIM2-Info header value.

    Extra keyword arguments are appended as additional tag=value pairs.
    None values are silently omitted.
    """
    val = ('draft={d};\r\n\trepo={r};\r\n\t'
           'date={dt}; sw={sw};\r\n\taction={a}').format(
        d=DKIM2_DRAFT, r=DKIM2_REPO, dt=DKIM2_DATE,
        sw=DKIM2_SOFTWARE, a=action)
    for k in sorted(extras):
        if extras[k] is not None:
            val += '; {}={}'.format(k, extras[k])
    return val


# ---------------------------------------------------------------------------
# Headers excluded from the header hash per DKIM2 spec Section 5.2
# ---------------------------------------------------------------------------

_EXCLUDED_NAMES = frozenset({
    'received', 'return-path', 'message-instance',
    'dkim2-signature', 'dkim-signature', 'authentication-results',
})

_EXCLUDED_PREFIXES = ('x-', 'arc-')


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
    reversed (bottom-up) then stable-sorted by name, matching compute_header_hash.
    """
    pairs = [(name.lower(), value) for name, value in msg.items()
             if not _should_exclude_header(name)]
    pairs.reverse()
    pairs.sort(key=lambda x: x[0])
    names = [name for name, _ in pairs]
    return len(names), ','.join(names)


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
# Header canonicalization (Section 5.2)
# ---------------------------------------------------------------------------

def _canonicalize_header_field(name, value):
    """Canonicalize a single header field per DKIM2 spec Section 5.2.

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

@public
def compute_header_hash(msg):
    """Compute SHA-256 header hash per DKIM2 spec Section 5.2.

    Excludes headers listed in the spec (Received, Return-Path,
    Message-Instance, DKIM*-Signature, ARC-*, X-*, Authentication-Results).
    Returns raw SHA-256 digest bytes.
    """
    canon_headers = []
    for name, value in msg.items():
        if _should_exclude_header(name):
            continue
        canon = _canonicalize_header_field(name, str(value))
        canon_headers.append((name.lower().encode(), canon))
    # Step 7: Reverse for bottom-up numbering, then stable sort by name
    canon_headers.reverse()
    canon_headers.sort(key=lambda x: x[0])
    # Step 8: Concatenate with CRLF + trailing CRLF
    data = b'\r\n'.join(ch for _, ch in canon_headers)
    if data:
        data += b'\r\n'
    return hashlib.sha256(data).digest()


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
    """Compute SHA-256 body hash per DKIM2 spec Section 5.1.

    Uses simple body canonicalization: strip trailing empty lines,
    ensure body ends with exactly one CRLF.
    Returns raw SHA-256 digest bytes.
    """
    body = _get_raw_body(msg)
    # Simple canonicalization
    while body.endswith(b'\r\n'):
        body = body[:-2]
    body += b'\r\n'
    return hashlib.sha256(body).digest()


# ---------------------------------------------------------------------------
# Body line extraction (for recipe computation)
# ---------------------------------------------------------------------------

def _get_body_lines(msg):
    """Get the body as a list of line strings (without line endings).

    Uses CRLF-normalized raw body, strips the final trailing newline
    for line splitting.
    """
    body = _get_raw_body(msg)
    # Normalize to LF for splitting
    body_str = body.replace(b'\r\n', b'\n').replace(
        b'\r', b'\n').decode('utf-8', errors='surrogateescape')
    # Strip single trailing newline (for clean line splitting)
    if body_str.endswith('\n'):
        body_str = body_str[:-1]
    if not body_str:
        return []
    return body_str.split('\n')


# ---------------------------------------------------------------------------
# Recipe computation
# ---------------------------------------------------------------------------

@public
def compute_body_recipe(current_lines, previous_lines):
    """Compute body recipe to undo current body back to previous.

    Uses line-level diff.  Returns a list of recipe instructions:
    - [start, end]: copy range from current body (1-based, inclusive)
    - "text": literal line content from previous body

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
                recipe.append({'d': pending_data})
                pending_data = []
            # Reference lines in the current body (1-based, inclusive end)
            recipe.append({'c': [i1 + 1, i2]})
        elif tag in ('replace', 'insert'):
            # Literal text from the previous body
            pending_data.extend(previous_lines[j1:j2])
        # 'delete': lines only in current, not needed for previous
    if pending_data:
        recipe.append({'d': pending_data})
    return recipe


@public
def compute_header_recipe(current_headers, previous_headers):
    """Compute header recipe to undo current headers back to previous.

    Both arguments are lists of (name, value) tuples.
    Returns a dict mapping lowercase header names to recipe lists,
    or None if no non-excluded headers changed.
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
        # Map canonical current values to their bottom-up index (1-based)
        known = {}
        for idx, cv in enumerate(cur_canon_rev):
            if cv not in known:
                known[cv] = idx + 1
        # Build recipe for this header name using {"c":...}/{"d":...}
        hrecipe = []
        pending_data = []
        for pval, pcanon in zip(prev_vals_rev, prev_canon_rev):
            if pcanon in known:
                # Flush pending data
                if pending_data:
                    hrecipe.append({'d': pending_data})
                    pending_data = []
                idx = known[pcanon]
                # Combine adjacent copy ranges
                if (hrecipe and isinstance(hrecipe[-1], dict)
                        and 'c' in hrecipe[-1]
                        and idx == hrecipe[-1]['c'][1] + 1):
                    hrecipe[-1]['c'][1] = idx
                else:
                    hrecipe.append({'c': [idx, idx]})
            else:
                pending_data.append(pval)
        if pending_data:
            hrecipe.append({'d': pending_data})
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
    # Build recipe if there are any changes
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
    # Unfold continuation lines
    val = re.sub(r'\r?\n[ \t]', '', val)
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
    # Parse r= tag: base64-encoded JSON
    r_match = re.search(r'r=([A-Za-z0-9+/=\s]+)', val)
    if r_match:
        b64_clean = re.sub(r'\s', '', r_match.group(1))
        recipe = json.loads(base64.b64decode(b64_clean))
    return version, hashes, recipe


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
            _, hashes, _ = _parse_mi(val)
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
    return max_v, None


@public
def undo_message_instance(msg):
    """Apply the recipe from the highest MI header to reverse the message.

    Removes the highest MI header from the message, applies body and header
    recipes to reconstruct the previous version, and returns the version
    number that was undone.  Returns 0 if there are no MI headers.

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
    _, _, recipe = _parse_mi(highest_mi)
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
    # Apply body recipe.
    if recipe and 'b' in recipe and isinstance(recipe['b'], list):
        body_lines = _get_body_lines(msg)
        new_lines = []
        for cmd in recipe['b']:
            if isinstance(cmd, dict) and 'c' in cmd:
                start, end = cmd['c'][0] - 1, cmd['c'][1]
                new_lines.extend(body_lines[start:end])
            elif isinstance(cmd, dict) and 'd' in cmd:
                new_lines.extend(cmd['d'])
            elif isinstance(cmd, list):
                # Legacy bare array
                start, end = cmd[0] - 1, cmd[1]
                new_lines.extend(body_lines[start:end])
            else:
                # Legacy bare string
                new_lines.append(cmd)
        # Reconstruct the body.
        new_body = '\r\n'.join(new_lines) + '\r\n'
        if msg.is_multipart():
            msg.set_payload(
                new_body.encode('utf-8', errors='surrogateescape'))
        else:
            msg.set_payload(
                new_body.decode('utf-8', errors='surrogateescape')
                if isinstance(new_body, bytes) else new_body)
    # Apply header recipes.
    if recipe and 'h' in recipe and isinstance(recipe['h'], dict):
        for hname, hrecipe in recipe['h'].items():
            # Get current values for this header (bottom-up = reversed).
            cur_vals = list(reversed(msg.get_all(hname, [])))
            # Build new values from recipe.
            new_vals = []
            for cmd in hrecipe:
                if isinstance(cmd, dict) and 'c' in cmd:
                    start, end = cmd['c'][0] - 1, cmd['c'][1]
                    new_vals.extend(cur_vals[start:end])
                elif isinstance(cmd, dict) and 'd' in cmd:
                    new_vals.extend(cmd['d'])
                elif isinstance(cmd, list):
                    # Legacy bare array
                    start, end = cmd[0] - 1, cmd[1]
                    new_vals.extend(cur_vals[start:end])
                else:
                    # Legacy bare string
                    new_vals.append(cmd)
            # Replace headers: delete all, re-add in top-down order
            # (reverse of bottom-up).
            while hname in msg:
                del msg[hname]
            for val in reversed(new_vals):
                msg[hname] = val
    return max_v


# ---------------------------------------------------------------------------
# Snapshot helpers — file-based original message cache
# ---------------------------------------------------------------------------

def _collect_headers(msg):
    """Collect non-excluded headers as a list of (name, value) tuples."""
    headers = []
    for name, value in msg.items():
        if not _should_exclude_header(name):
            headers.append((name, str(value)))
    return headers


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
def save_mi_original(msg):
    """Save the original message to a cache file for later recipe computation.

    Returns the file path.  The file contains the raw serialized message
    bytes.  Multiple queue entries referencing the same original can
    hardlink to this file — the data is stored once on disk.
    """
    cache_dir = _mi_cache_dir()
    makedirs(cache_dir)
    raw = _serialize_msg(msg)
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
    msg = email.message_from_bytes(raw, Message)
    return _get_body_lines(msg), _collect_headers(msg)


def _cleanup_mi_original(path):
    """Remove the cache file.  Safe to call if already deleted."""
    safe_remove(path)


# ---------------------------------------------------------------------------
# Pipeline handlers
# ---------------------------------------------------------------------------

@public
@implementer(IHandler)
class MessageInstanceIngress:
    """Add Message-Instance v=1 at ingress and snapshot message state."""

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
        # Force serialization so that auto-generated parameters (e.g.
        # multipart boundaries) are resolved before hashing.
        _serialize_msg(msg)
        existing_version = get_max_mi_version(msg)
        if existing_version > 0:
            # Existing MI present (added by the inbound milter).  Accept it as
            # authoritative — never strip a MI header.
            _prepend_header(msg, 'X-DKIM2-Info',
                            _dkim2_info('found-mi={}'.format(existing_version)))
            log.debug('Accepted existing Message-Instance v=%d', existing_version)
        else:
            # No MI headers present — add v=1 documenting the current state.
            hcount, hnames = _get_hashed_headers(msg)
            h_hash = compute_header_hash(msg)
            b_hash = compute_body_hash(msg)
            value = build_mi_header_value(1, h_hash, b_hash)
            _prepend_header(msg, 'Message-Instance', value)
            mi_file = save_mi_original(msg)
            _prepend_header(msg, 'X-DKIM2-Info', _dkim2_info(
                'mi-m1', hc=hcount, hn=hnames,
                snaps=os.path.basename(mi_file)))
            log.debug('Added Message-Instance v=1')
            msgdata['mi_snapshot'] = {
                'mi_file': mi_file,
                'version': get_max_mi_version(msg),
                'header_hash': compute_header_hash(msg),
                'body_hash': compute_body_hash(msg),
            }
            return
        # Save the original message to a cache file for egress recipe
        # computation.  Only the file path and hashes are stored in
        # msgdata — the body content lives on disk once, not in the
        # pickled queue metadata.
        mi_file = save_mi_original(msg)
        msgdata['mi_snapshot'] = {
            'mi_file': mi_file,
            'version': get_max_mi_version(msg),
            'header_hash': compute_header_hash(msg),
            'body_hash': compute_body_hash(msg),
        }


@public
@implementer(IHandler)
class MessageInstanceEgress:
    """Add Message-Instance v=N+1 at egress if message changed."""

    name = 'message-instance-egress'
    description = _('Add Message-Instance header at egress.')

    def process(self, mlist, msg, msgdata):
        """See `IHandler`."""
        if not _mi_enabled(mlist):
            return
        snapshot = msgdata.get('mi_snapshot')
        if not snapshot:
            # No snapshot means this message didn't go through an ingress
            # handler (e.g. internally generated via VirginPipeline).
            # If no MI headers are present, Mailman is the originator —
            # add MI v=1 so the message enters the DKIM2 ecosystem.
            if get_max_mi_version(msg) == 0:
                _serialize_msg(msg)
                hcount, hnames = _get_hashed_headers(msg)
                h_hash = compute_header_hash(msg)
                b_hash = compute_body_hash(msg)
                value = build_mi_header_value(1, h_hash, b_hash)
                _prepend_header(msg, 'Message-Instance', value)
                _prepend_header(msg, 'X-DKIM2-Info',
                                _dkim2_info('mi-m1', hc=hcount, hn=hnames))
                log.debug('Added originator Message-Instance v=1')
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
        # Compute recipes.
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
            'mi-m{}'.format(version),
            hc=hcount, hn=hnames,
            snapf=os.path.basename(mi_file)))
        log.debug('Added Message-Instance v=%d', version)
        _cleanup_mi_original(mi_file)
