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

"""Verify and undo Message-Instance headers, as a downstream verifier would."""

import re
import base64

from mailman.handlers.message_instance import (
    b64,
    compute_body_hash_raw,
    compute_header_hash,
    compute_header_hash_raw,
    get_max_instance_version,
    hash_body_bytes,
    instance_version,
    MalformedInstance,
    MalformedRecipe,
    parse_instance,
    raw_header_pairs,
    serialize_message,
)


# A "u" string: base64 alphabet and padding, a whole number of quanta, and
# zero pad bits ("QR==" is not canonical).
_U_PATTERNS = (
    re.compile(r'[A-Za-z0-9+/]*={0,2}'),
    re.compile(r'(?:.{4})*', re.DOTALL),
    re.compile(r'[^=]*|.*[AQgw]==|.*[AEIMQUYcgkosw048]=', re.DOTALL),
)


def _get_raw_body(msg):
    """Get the raw body bytes from a message (everything after headers)."""
    raw = serialize_message(msg)
    sep = b'\r\n\r\n'
    idx = raw.find(sep)
    if idx == -1:
        return b''
    return raw[idx + len(sep):]


def compute_body_hash(msg):
    """The SHA-256 body hash of msg, as raw digest bytes.

    Uses simple body canonicalization: strip trailing empty lines,
    ensure body ends with exactly one CRLF.
    """
    return hash_body_bytes(_get_raw_body(msg))


def get_body_lines(msg):
    """Get the body as a list of line strings (without line endings).

    Uses the CRLF-normalized raw body.  Body canonicalization ignores
    trailing empty lines, so they are not lines a Recipe numbers or
    restores.
    """
    body = _get_raw_body(msg)
    # Normalize to LF for splitting
    body_str = body.replace(b'\r\n', b'\n').replace(
        b'\r', b'\n').decode('utf-8', errors='surrogateescape')
    body_str = body_str.rstrip('\n')
    if not body_str:
        return []
    return body_str.split('\n')


def body_lines_raw(raw):
    """The body lines of raw octets, less trailing empty lines."""
    head, sep, body = raw.partition(b'\r\n\r\n')
    text = body.decode('utf-8', errors='surrogateescape').rstrip('\r\n')
    if not text:
        return []
    return text.split('\r\n')


def _raw_values(msg, name):
    """The stored values of this header, top-down, as the generator sees
    them."""
    lname = name.lower()
    return [value for hname, value in msg.raw_items()
            if hname.lower() == lname]


def _compile_steps(steps, where):
    """Check one step list and return it as ('c', start, end) / ('d', [str])
    tuples, every literal as its octets in the surrogateescape str form
    the email parser uses for header values.  Step keys are lowercase
    (parse_instance refuses any other).  The upper bound of a "c" range is
    checked by _apply_steps, which knows the item count."""
    if not isinstance(steps, list):
        raise MalformedRecipe('{}: steps are not an array'.format(where))
    compiled = []
    last_end = 0
    for step in steps:
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
        if kind not in ('d', 'u'):
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
            if kind == 'u':
                if not all(p.fullmatch(item) for p in _U_PATTERNS):
                    raise MalformedRecipe(
                        '{}: "u" item is not canonical base64'.format(where))
                octets = base64.b64decode(item, validate=True)
            elif any(0xd800 <= ord(ch) <= 0xdfff for ch in item):
                # A JSON \ud800-style escape: not UTF-8 text.
                raise MalformedRecipe(
                    '{}: "d" item is not valid UTF-8'.format(where))
            else:
                octets = item.encode('utf-8')
            # The octets as the parser stores 8-bit header octets, which
            # the generator writes back unchanged (a str with real
            # non-ASCII characters would be RFC 2047 encoded instead).
            item = octets.decode('ascii', errors='surrogateescape')
            if '\r' in item or '\n' in item:
                raise MalformedRecipe(
                    '{}: "{}" item contains CR or LF'.format(where, kind))
            values.append(item)
        compiled.append(('d', values))
    return compiled


def _compile_recipe(recipe):
    """Check a decoded r= Recipe object and return (header_steps, body_steps).

    header_steps maps each lower-cased header name under "h" to its
    compiled steps; body_steps is the compiled "b" list, or None when the
    body was not changed (no "b") or cannot be recreated ("b": null).
    Keys are lowercase (parse_instance refuses any other).
    """
    if recipe is None:
        return {}, None
    if not isinstance(recipe, dict):
        raise MalformedRecipe('Recipe is not an object')
    if 'h' not in recipe and 'b' not in recipe:
        raise MalformedRecipe('Recipe has neither "h" nor "b"')
    header_steps = {}
    if 'h' in recipe:
        if not isinstance(recipe['h'], dict):
            raise MalformedRecipe('"h" is not an object')
        if not recipe['h']:
            raise MalformedRecipe('"h" has no properties')
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


def plan_undo(msg, recipe):
    """Work out what applying recipe to msg produces, without changing msg.

    Returns (body_lines, headers): the rebuilt body lines, or None when the
    body is to be left alone, and a dict of header name to its rebuilt
    values in top-down order.  Raises MalformedRecipe.
    """
    header_steps, body_steps = _compile_recipe(recipe)
    body_lines = None
    if body_steps is not None:
        body_lines = _apply_steps(body_steps, get_body_lines(msg), 'b')
    headers = {}
    for hname, steps in header_steps.items():
        # Current values bottom-up, as the Recipe numbers them.  Raw, so a
        # copied 8-bit value is re-added as the octets it arrived as.
        cur_vals = list(reversed(_raw_values(msg, hname)))
        headers[hname] = list(reversed(
            _apply_steps(steps, cur_vals, 'h ' + hname)))
    return body_lines, headers


def verify_message_instance(msg):
    """Verify the highest instance matches the current message content.

    Returns the version number on success, or (0, error_message) on failure.
    """
    # Force serialization so that auto-generated parameters (e.g.
    # multipart boundaries) are resolved before hashing.
    serialize_message(msg)
    max_v = get_max_instance_version(msg)
    if max_v == 0:
        return 0, 'no Message-Instance headers'
    # Find the Message-Instance header with the highest version.
    for val in msg.get_all('message-instance', []):
        if instance_version(val) == max_v:
            try:
                _, hashes, recipe = parse_instance(val)
            except MalformedRecipe as error:
                return 0, 'malformed Recipe: {}'.format(error)
            except MalformedInstance as error:
                return 0, str(error)
            break
    if hashes is None:
        return 0, 'could not parse h= tag'
    stored_h = hashes['h'][1]
    stored_b = hashes['b'][1]
    computed_h = b64(compute_header_hash(msg))
    computed_b = b64(compute_body_hash(msg))
    if stored_h != computed_h:
        return 0, 'header hash mismatch ({} != {})'.format(
            stored_h, computed_h)
    if stored_b != computed_b:
        return 0, 'body hash mismatch ({} != {})'.format(
            stored_b, computed_b)
    # An instance whose Recipe cannot be applied to this message does not
    # verify either: nothing downstream could undo it.
    try:
        plan_undo(msg, recipe)
    except MalformedRecipe as error:
        return 0, 'malformed Recipe: {}'.format(error)
    return max_v, None


def undo_message_instance(msg):
    """Apply the recipe from the highest instance to reverse the message.

    Removes the highest instance from the message, applies body and header
    recipes to reconstruct the previous version, and returns the version
    number that was undone.  Returns 0 if there are no instances, or if
    the highest one carries a Recipe that cannot be applied -- the message
    is then left untouched.

    After a successful undo, verify_message_instance(msg) should return
    the version of the now-highest Message-Instance header.
    """
    max_v = get_max_instance_version(msg)
    if max_v == 0:
        return 0
    # Find and parse the highest Message-Instance header.
    highest_instance = None
    for val in msg.get_all('message-instance', []):
        if instance_version(val) == max_v:
            highest_instance = val
            break
    # Work out the whole result before touching the message, so a Recipe
    # that turns out to be malformed part-way through changes nothing.
    try:
        _, _, recipe = parse_instance(highest_instance)
        new_body_lines, new_headers = plan_undo(msg, recipe)
    except (MalformedInstance, MalformedRecipe):
        return 0
    # Remove the highest Message-Instance header, keep all others.
    # Python's email library doesn't support selective deletion of
    # duplicate headers by value, so we collect all, delete all,
    # and re-add the ones we want to keep.
    all_instances = msg.get_all('message-instance', [])
    # Delete all Message-Instance headers.
    while 'message-instance' in msg:
        del msg['message-instance']
    # Re-add all except the highest version.
    for val in all_instances:
        if instance_version(val) != max_v:
            msg['Message-Instance'] = val
    # Rebuild the body.  Setting the payload from its raw octets stores it
    # in the parser's own surrogateescape form, which the generator writes
    # back byte for byte whatever the octets are -- a str with real
    # non-ASCII characters (a UTF-8 line decoded by get_body_lines) would
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


def verify_instance_raw(raw):
    """Check the highest Message-Instance in the raw octets against them.

    Returns (version, None) when its hashes match, else (0, why).  Only the
    hashes are checked; the Recipe is not applied.
    """
    values = [v for n, v in raw_header_pairs(raw)
              if n.lower() == 'message-instance']
    if not values:
        return 0, 'no Message-Instance headers'
    top = max(values, key=instance_version)
    try:
        _, hashes, _ = parse_instance(top)
    except MalformedRecipe as error:
        return 0, 'malformed Recipe: {}'.format(error)
    except MalformedInstance as error:
        return 0, str(error)
    if hashes is None:
        return 0, 'could not parse h= tag'
    computed = (b64(compute_header_hash_raw(raw)),
                b64(compute_body_hash_raw(raw)))
    # Every hash-set of a "," list must pass, and one "|" group must.
    # Mailman computes sha256 only, so a set by any other algorithm is not
    # a pass here.
    why = None
    for group in hashes['groups']:
        for name, *digests in group:
            if name != 'sha256':
                why = why or 'hash algorithm {} not checked'.format(name)
                break
            if digests[0] != computed[0]:
                why = why or 'header hash mismatch ({} != {})'.format(
                    digests[0], computed[0])
                break
            if digests[1] != computed[1]:
                why = why or 'body hash mismatch ({} != {})'.format(
                    digests[1], computed[1])
                break
        else:
            return instance_version(top), None
    return 0, why
