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
  The baseline is the message as received, the octets the LMTP runner
  kept, so everything Mailman has done since (the `Message-ID-Hash`
  stamp, a repaired `Message-ID`) goes into the egress Recipe.  If the
  instance does not verify against those octets, that is logged as a
  warning: the chain may not undo cleanly.
- When there are no received octets (`mailman inject`, a message
  posted through REST or made internally, or one queued by an older
  Mailman) the baseline is the parsed message with the
  `Message-ID-Hash` Mailman stamps on it removed, whether or not it
  carries an instance: the stamp is not part of the message as
  received, so `m=1` leaves it out, as it does for the LMTP runner's
  octets, and the egress Recipe records it.  An existing instance that
  does not verify against that baseline is logged as a warning, as
  above.  (A sender that put its own `Message-ID-Hash` under its
  instance and reached Mailman this way gets that warning: Mailman
  replaces the field.)
- In every case the baseline is kept as `msg.original_bytes` (which the
  queue pickles with the message) for Recipe computation at egress.
  Only the two hashes and the instance number go into
  `msgdata['mi_snapshot']`.
- Digests are skipped.  They are not a single authored message.
- On a list that has opted out of Message-Instance, the received
  octets are dropped so the queues do not carry them.

**At egress** (`MessageInstanceMixin` on `Deliver` and `BulkDelivery`,
after decoration, personalisation and ARC signing):

- Mailman recomputes the hashes.  If nothing changed since the
  snapshot, no header is added.
- Otherwise it computes header and body Recipes against the saved
  baseline (`msg.original_bytes`) and prepends `m=N+1`.  The hashes and
  Recipes are over the message as smtplib will send it.  After the
  DKIM2 wrap below, decorate records its boundary in
  `msgdata['dkim2-wrap']` and egress finds the original body's octets
  where the wrap put them, checks they are followed by that boundary,
  and counts line breaks for the single copy range, without splitting
  either body into lines.  Otherwise the body Recipe comes from a line
  diff every DKIM2 implementation runs the same way, so all of them give
  the same Recipe: the common prefix and suffix are trimmed, lines only
  one side has are set aside, and Myers' O(ND) search finds the rest.
  It is capped: a Recipe restores at most 1000 previous lines as
  literals by default (`message_instance_max_recipe_lines` sets it), and
  the search stops after a fixed amount of work.  Past either limit the
  body Recipe is `"b": null`, as below.
- A message that already carries an instance newer than the snapshot
  is left alone: a bulk delivery that failed temporarily is retried
  with the copy egress already stamped.
- When Mailman rewrote the body itself (content filtering removed or
  replaced parts, a filter report was attached, or the DMARC mitigation
  wrapped the message), the body Recipe is `"b": null`: the previous
  body cannot be rebuilt, and the Recipe says so rather than carrying
  the whole original as literal data.  The handlers record this as
  `msgdata['body-modified']`.

**For internally generated messages** (welcome messages, notifications,
anything through `VirginPipeline`): these never pass the ingress
handler, so the egress handler adds an originator `m=1` when the
message has no instance at all.

### X-DKIM2-Info

Every `Message-Instance` Mailman adds is accompanied by an
`X-DKIM2-Info` header recording the draft implemented, the repository,
the date of the implementation, the software name, the action
(`mi-m=1`, `mi-m=2`, ...), and the count and ordered names of the headers
that went into the hash.  It exists so
interop problems can be diagnosed from the message alone.  The format is
draft-gondwana-dkim2-debug-header-01: a DKIM2 tag-list with every tag
followed by `;`.  It is excluded from the hash by the `X-` rule and is
folded to 78 characters, only after a `;` or a `,`.

## Configuration

Enable site-wide in `mailman.cfg`:

```ini
[mta]
message_instance: yes
```

The default is `no`.  When disabled, neither handler does any work.

`message_instance_max_recipe_lines` (default 1000) is the
most previous-body lines a body Recipe may carry as literal text; a
body that would need more gets `"b": null`.

Each list also has a `dkim2_message_instance` attribute (default
`True`, exposed through the REST list configuration resource) so
individual lists can opt out.  Lists created before the migration that
adds the column are treated as enabled.

## Files

### New

- `src/mailman/handlers/message_instance.py`: hash computation, Recipe
  computation, header building, parsing, the hash check of an inbound
  instance, and the ingress and egress handlers.  Mailman never undoes
  a Recipe, so verifying and undoing a parsed message lives with the
  tests.
- `src/mailman/mta/message_instance.py`: the delivery mixin, following
  the `ARCSigningMixin` pattern.
- `src/mailman/database/alembic/versions/a1b2c3d4e5f6_dkim2_message_instance.py`:
  adds the per-list column.
- `src/mailman/handlers/tests/test_message_instance.py`: hashing against
  interop vectors, Recipe shapes, folding and unfolding, the DKIM2 wrap,
  the null body Recipe, and full ingress/decorate/egress/undo round
  trips for 7bit, 8bit, quoted-printable, base64 and multipart bodies.
- `src/mailman/handlers/tests/mi_support.py`: verify and undo of a
  parsed message (Recipe validation and application, spec-06 §5), as a
  downstream verifier does them, for the tests.
- `src/mailman/handlers/tests/test_mi_roundtrip.py`: round trips through
  the real list transformations.
- `src/mailman/handlers/tests/test_mi_null_recipe.py`: a body-only
  change produces no header Recipe rather than a null one.

### Modified

- `src/mailman/handlers/decorate.py`: the DKIM2 wrap (below).
- `src/mailman/handlers/to_archive.py`, `src/mailman/handlers/to_usenet.py`:
  queue their copy without `msg.original_bytes`.
- `src/mailman/handlers/mime_delete.py`, `src/mailman/handlers/dmarc.py`:
  set `msgdata['body-modified']` when they rewrite the body.
- `src/mailman/config/schema.cfg`: the `[mta] message_instance` option.
- `src/mailman/pipelines/builtin.py`: register the ingress handler.
- `src/mailman/mta/deliver.py`, `src/mailman/mta/bulk.py`: call egress.
- `src/mailman/interfaces/mailinglist.py`, `src/mailman/model/mailinglist.py`,
  `src/mailman/styles/base.py`, `src/mailman/rest/listconf.py`: the
  per-list flag.

## Decoration on a DKIM2 list

Upstream decoration concatenates a footer onto a single-part text/plain
body by re-encoding it, letting Python's email library choose a
Content-Transfer-Encoding from the charset (base64 for utf-8), and
MIME-wraps everything else after the email package has re-serialised
it.  Either way the body lines change, and the Recipe would have to
carry the whole original body as literal data.

On a list with Message-Instance enabled, every message that gets a
header or footer is wrapped instead, by splicing the received octets in
unchanged.  The original top-level `Content-*` fields and body, exactly
as they arrived in `msg.original_bytes`, become the middle part of a
new `multipart/mixed`, with the list header and footer as `text/plain`
parts either side of it.  The middle part is written out verbatim (no
refolding, no `From ` mangling, no decoding of 8-bit octets), and the
boundary is chosen so it does not occur in it.  The body Recipe for
this hop is therefore always literal lines, one copy range, literal
lines, whatever the original's encoding or structure.  A short preamble
explains the wrap to non-MIME readers.

The wrap is skipped, and upstream decoration used, when the list does
not have Message-Instance enabled, the message did not pass ingress
(no `original_bytes`), or Mailman already rewrote the body; the last
case gets the null body Recipe described above.  Lists without
Message-Instance decorate exactly as upstream does.

A site handler that rewrites the body before decoration must set
`msgdata['body-modified']` as `mime_delete` and `dmarc` do.  Otherwise
the wrap splices in the body as received, silently reverting the
handler's change.

## Resource impact

**Memory and disk.**  The baseline is `msg.original_bytes`, a second
copy of the message (the bytes as received, or as parsed when those were
not kept) that is pickled to the queue with the message and dropped
with it.  The LMTP runner keeps the received bytes only when
`[mta] message_instance` is on, and ingress drops them for a list that
has opted out, so a site or list without Message-Instance pays
nothing.  The copies queued for the archivers and the NNTP gateway
leave them out, as they never reach egress; the digest mbox never had
them.  There is no separate cache directory to clean up.  The pickled
`msgdata` holds only two hashes and a number.

Ingress hashes the received octets in place: they are not copied when
they already have CRLF line endings (as LMTP delivers them), and the
parsed message is not re-serialised.  The wrap holds the original part
as one bytes object, not one string per line.  Egress serialises the
message once, the way smtplib will, and hashes and locates the original
body in that.  For a post with a 10 MB attachment (13.7 MiB as
received) the peak traced allocation is under 1 MiB at ingress and
about 29 MiB for decoration and egress together, about twice the
message size: the outgoing serialisation and the generator's buffers.

**Wire size.**  Each message gains one or two `Message-Instance`
headers plus their `X-DKIM2-Info` headers, a few hundred bytes.

**CPU.**  A few milliseconds per message for a typical post, dominated
by serialisation and SHA-256.  Bulk delivery runs egress once on the
shared copy; individual delivery runs it once per recipient after
per-recipient decoration.

## Limitations

The Mail-Archive.com archiver (`mail-archive`) mails its archive copy
through the out queue with fresh metadata: no Message-Instance snapshot,
and (as for every archive copy) no `msg.original_bytes`.  Egress adds no
instance for it, so the instance from ingress no longer describes the
copy as sent and it is not DKIM2-signed.  The archive copy is the post as
archived, not a delivery; this is a known limitation.

## Code structure

`message_instance.py` keeps the library-grade code (canonicalisation,
hashing, Recipe computation, header building, folding and parsing) free
of Mailman imports and operating on `email.message.Message`, with the
Mailman-specific glue (config check, pipeline handlers) below it.  When
a standalone Python DKIM2 library exists, the first part can move there
and the handlers become a thin wrapper, as Sympa's implementation does
by delegating to `Mail::DKIM2::MessageInstance`.
