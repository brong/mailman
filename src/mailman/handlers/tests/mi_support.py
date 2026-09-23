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

"""Verify and undo Message-Instance headers, for the DKIM2 tests.

Mailman adds Message-Instance headers; it never checks or applies one
beyond the hash check at ingress (verify_mi_raw).  The tests use these to
show that what Mailman emits verifies and that its Recipes undo, as a
downstream verifier would.  They work on a parsed message, in the PARSED
view (_serialize_msg).
"""

import base64
import logging

from mailman.handlers.message_instance import (
    MalformedInstance, MalformedRecipe, _b64, _hash_body_bytes,
    _get_mi_version, _parse_mi, _serialize_msg, compute_header_hash,
    get_max_mi_version)


log = logging.getLogger('mailman.dkim2')


def _get_raw_body(msg):
    """Get the raw body bytes from a message (everything after headers)."""
    raw = _serialize_msg(msg)
    sep = b'\r\n\r\n'
    idx = raw.find(sep)
    if idx == -1:
        return b''
    return raw[idx + len(sep):]


def compute_body_hash(msg):
    """Compute SHA-256 body hash per DKIM2 spec-06 §6.1.

    Uses simple body canonicalization: strip trailing empty lines,
    ensure body ends with exactly one CRLF.
    Returns raw SHA-256 digest bytes.
    """
    return _hash_body_bytes(_get_raw_body(msg))


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


def _raw_values(msg, name):
    """The stored values of this header, top-down, as the generator sees
    them (see _wire_value for why not msg.get_all)."""
    lname = name.lower()
    return [value for hname, value in msg.raw_items()
            if hname.lower() == lname]


# ---------------------------------------------------------------------------
# Recipe validation and application (spec-06 §5)
# ---------------------------------------------------------------------------

def _compile_steps(steps, where):
    """Check one step list and return it as ('c', start, end) / ('d', [str])
    tuples, "b" items decoded to the surrogateescape str form that
    message_instance uses for header values and body lines.  The upper
    bound of a "c" range is checked by _apply_steps, which knows the item
    count."""
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
    changed (no "b") or cannot be recreated ("b": null, spec-06 §4.2).
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
            except MalformedInstance as error:
                return 0, str(error)
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
    except (MalformedInstance, MalformedRecipe) as error:
        log.warning('Message-Instance m=%d is malformed (%s); '
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
