# DKIM2 Message-Instance Support

This documents the changes that add DKIM2 Message-Instance header support
to GNU Mailman, per draft-ietf-dkim-dkim2-spec-06.

## Overview

A DKIM2 verifier that receives a message from a mailing list needs to undo
the list's changes (subject prefix, list headers, footer) so that the
signatures of earlier hops can be re-verified.  The `Message-Instance`
header is how each hop records what it changed.  It carries SHA-256
hashes of the message headers and body at that point in the delivery
chain and, for every hop after the first, a Recipe that rebuilds the
previous instance from the current one.

Mailman is responsible only for adding `Message-Instance` headers.
DKIM2 signing (adding `DKIM2-Signature` headers) is expected to be done
by the MTA.

## Header format

```
Message-Instance: m=2; h=sha256:<header-hash>:<body-hash>; r=<recipe>;
```

- `m=` is the instance number.  The first hop to see the message
  writes `m=1`; each later hop that changes it adds `m=N+1`.
- `h=` carries the algorithm and the base64 header and body hashes.
- `r=` is present when `m>1` and holds the Recipe: base64-encoded JSON
  of the form `{"h": {<header-name>: [steps]}, "b": [steps]}`.  Either
  key is omitted when nothing in that part changed.
- Each step is either `{"c": [start, end]}`, copying a range from the
  current instance, or `{"d": [...]}`, supplying literal values from
  the previous one.  Body ranges are 1-based inclusive line numbers.
  Header ranges are 1-based bottom-up indexes among the current values
  of that header name.

Header values are folded for RFC 5322: lines target 72 characters and
never exceed 77, breaking at `; ` tag boundaries where possible and
inside the base64 otherwise.  The parser strips every run of folding
whitespace before use (spec-06 §2.12 and §2.14), so instances folded by
other implementations parse correctly.

## Hashing

Following spec-06 §4, these headers are excluded from the header hash:
Apparently-To, ARC-Authentication-Results, ARC-Message-Signature,
ARC-Seal, Authentication-Results, Auto-Submitted, Delivered-To,
DKIM-Signature, DKIM2-Signature, DL-Expansion-History, Message-Instance,
Original-Recipient, Received, Return-Path, SIO-Label-History, VBR-Info,
X400-Received, X400-Trace, and anything with an `X-` or `Received-`
prefix.

Each remaining header is canonicalised (unfolded, name lowercased,
whitespace runs collapsed to a single space, whitespace stripped around
the colon and at the end), the list is reversed and stably sorted by
name, and the result is joined with CRLF and hashed.  The body hash is
over the raw body with trailing empty lines removed and a single final
CRLF.

The hashes are checked against vectors shared with the other DKIM2
implementations in `test_message_instance.py`, so Mailman is pinned to
the interop result rather than to itself.

## What Mailman does

**At ingress** (`message-instance-ingress`, first in `OwnerPipeline`
and immediately after `validate-authenticity` in `PostingPipeline`):

- If the message has no `Message-Instance` header, Mailman adds `m=1`
  describing the message as received.
- If one is already present (added by an inbound milter, or by a
  signing sender) Mailman never modifies it: it may be covered by a
  `DKIM2-Signature`, and rewriting or dropping it would destroy the
  evidence of a broken chain and let Mailman pose as the originator.
  Instead Mailman works out which state the instance describes.  If it
  verifies against the current message, that is the baseline.  If it
  only verifies once the `Message-ID-Hash` that Mailman's message store
  stamps on arrival is removed, the unstamped message is the baseline,
  so the egress Recipe records the stamp as a reversible change.  A
  `Message-ID-Hash` that was part of the signed instance is left alone.
  Anything else is logged as a warning: the chain may not undo cleanly.
- In both cases the baseline is saved to a cache file for Recipe
  computation at egress.  Only the file path, the two hashes and the
  instance number go into `msgdata['mi_snapshot']`.
- Digests are skipped.  They are not a single authored message.

**At egress** (`MessageInstanceMixin` on `Deliver` and `BulkDelivery`,
after decoration, personalisation and ARC signing):

- Mailman recomputes the hashes.  If nothing changed since the
  snapshot, no header is added.
- Otherwise it computes header and body Recipes against the saved
  baseline and prepends `m=N+1`.  The common footer-append case is
  detected directly and emitted as a single copy range without running
  a full diff.
- The cache file is deleted once egress has finished with it.

**For internally generated messages** (welcome messages, notifications,
anything through `VirginPipeline`): these never pass the ingress
handler, so the egress handler adds an originator `m=1` when the
message has no instance at all.

### X-DKIM2-Info

Every `Message-Instance` Mailman adds is accompanied by an
`X-DKIM2-Info` header recording the draft implemented, the repository,
the date of the implementation, the software name, the action
(`mi-m1`, `mi-m2`, ...), the count and ordered names of the headers that
went into the hash, and the snapshot file used.  It exists so interop
problems can be diagnosed from the message alone.  It is excluded from
the hash by the `X-` rule and is folded to 78 characters.

## Configuration

Enable site-wide in `mailman.cfg`:

```ini
[mta]
message_instance: yes
```

The default is `no`.  When disabled, neither handler does any work.

## Files

### New

- `src/mailman/handlers/message_instance.py`: hash computation, Recipe
  computation, header building, parsing, verify and undo, the file
  cache, and the ingress and egress handlers.
- `src/mailman/mta/message_instance.py`: the delivery mixin, following
  the `ARCSigningMixin` pattern.
- `src/mailman/handlers/tests/test_message_instance.py`: hashing against
  interop vectors, Recipe shapes, folding and unfolding, decoration
  encoding preservation, and full ingress/decorate/egress/undo round
  trips for 7bit, quoted-printable and base64 bodies.
- `src/mailman/handlers/tests/test_mi_roundtrip.py`: round trips through
  the real list transformations, including the milter-then-stamp seam.
- `src/mailman/handlers/tests/test_mi_null_recipe.py`: a body-only
  change produces no header Recipe rather than a null one.

### Modified

- `src/mailman/handlers/decorate.py` and `docs/decorate.rst`: preserve
  the Content-Transfer-Encoding when adding headers and footers (below).
- `src/mailman/config/schema.cfg`: the `[mta] message_instance` option.
- `src/mailman/pipelines/builtin.py`: register the ingress handler.
- `src/mailman/mta/deliver.py`, `src/mailman/mta/bulk.py`: call egress.

## Encoding-preserving decoration

Previously the decoration handler called `msg.set_payload(bytes,
charset)` for single-part text/plain messages, and Python's email
library then chose a Content-Transfer-Encoding from the charset: none
for us-ascii, quoted-printable for iso-8859-1, base64 for utf-8.  A
utf-8 message arriving as 7bit or 8bit was silently converted to base64
after decoration.  Every body line changed, so the Recipe would have
had to carry the whole original body as literal data.

The handler now keeps the original encoding:

- **7bit/8bit**: the decoded text is concatenated with the header and
  footer, encoded with the original charset, and stored with the same
  CTE.  7bit is upgraded to 8bit only if the footer introduces high
  bytes.
- **quoted-printable**: the original QP body is kept byte-for-byte and
  only the header and footer are freshly QP-encoded.  Unnecessarily
  quoted characters (`=48` for `H`) and unusual soft line break
  positions survive.
- **base64**: the text is re-encoded at the line width the original
  used.  Base64 is deterministic, so every complete line before the
  original's last line is unchanged; only that line and the appended
  footer differ.  The message stays single-part rather than becoming
  `multipart/mixed`.

Any other CTE, or a failure in one of these paths, falls through to
MIME wrapping as before.  These changes apply whether or not
Message-Instance is enabled, and readers of the raw message see text
rather than base64 where they previously did not.

## Resource impact

**Memory.**  The baseline is written to disk rather than kept in
`msgdata`, so the pickled queue metadata holds only a path, two hashes
and a number.  Hashing serialises the message a few times, a transient
allocation of a few times the message size.

**Disk.**  Baselines live in `$VAR_DIR/mi-cache/` under a name derived
from the SHA-256 of the serialised message, so identical baselines
share one file.  Files are removed after egress.  If delivery fails and
is retried, the file stays until egress succeeds.  Orphans left by a
crash are safe to delete once they are older than the queue retry
window.

**Wire size.**  Each message gains one or two `Message-Instance`
headers plus their `X-DKIM2-Info` headers, a few hundred bytes.

**CPU.**  A few milliseconds per message for a typical post, dominated
by serialisation and SHA-256.  Bulk delivery runs egress once on the
shared copy; individual delivery runs it once per recipient after
per-recipient decoration.

## Code structure

`message_instance.py` keeps the library-grade code (canonicalisation,
hashing, Recipe computation, header building, folding and parsing,
verify and undo) free of Mailman imports and operating on
`email.message.Message`, with the Mailman-specific glue (config check,
file cache, pipeline handlers) below it.  When a standalone Python
DKIM2 library exists, the first part can move there and the handlers
become a thin wrapper, as Sympa's implementation does by delegating to
`Mail::DKIM2::MessageInstance`.
