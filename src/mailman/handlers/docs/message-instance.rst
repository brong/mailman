======================
DKIM2 Message-Instance
======================

DKIM2 (draft-ietf-dkim-dkim2-spec-09) lets a verifier undo the changes a
mailing list makes to a message, such as a subject prefix, ``List-*`` headers
or a footer, so that the signatures of earlier hops can still be checked.
Each hop that changes a message adds a ``Message-Instance`` header recording
the SHA-256 hashes of the message's headers and body, an instance number, and
a *Recipe* that rebuilds the previous instance from the current one::

    Message-Instance: m=2; h=sha256:<header-hash>:<body-hash>; r=<recipe>;

Mailman only adds ``Message-Instance`` headers.  Signing them with a
``DKIM2-Signature`` is left to the MTA.


Configuration
=============

Message-Instance headers are off by default.  To turn them on for the site,
add this to ``mailman.cfg``::

    [mta]
    message_instance: yes

Each list also has a ``dkim2_message_instance`` flag, ``True`` by default and
exposed through the REST list configuration resource, so that a list can opt
out.


How it works
============

When a post arrives over LMTP, the LMTP runner hashes the octets it received
and checks the highest ``Message-Instance`` the post came with against them.
Only the two hashes, the length and line count of the body, the lines that
start with ``From``, the instance number and the outcome (``none``,
``match``, ``mismatch`` or ``malformed``) are kept with the message.  The
runner also starts the message's header log, which records the original
values of every header field Mailman changes from then on, and marks any
part whose payload Mailman changes.

The ``message-instance-ingress`` handler, early in the posting and owner
pipelines, takes its snapshot from that record.  A message that did not come
in over LMTP gets its record here, from the parsed message.  The instances of
a signed post are never changed.  An unsigned post leaves with a single
``m=1``, and any unsigned instance it arrived with is dropped.  Digests are
skipped.

On a list with Message-Instance headers, decoration never re-encodes a post.
Single-part ``text/plain`` is wrapped in a ``multipart/mixed`` like every
other content type, whatever ``[mailman] decorate_plain_text_in_place`` says,
and the shape of the decoration is recorded so the post can be found again
without a diff.

The egress step runs as part of delivery, after decoration, personalization
and ARC signing.  It hashes the message as smtplib will send it and, if the
message changed (or the post was unsigned), adds ``m=N+1``:

* The header Recipe gives each header field in the header log back its
  original values.  A header change the log missed cannot be described, so a
  signed post then gets no instance, and an unsigned post gets an ``m=1``
  with no header Recipe.

* The body Recipe copies the post's lines from where decoration put them.
  Everything decoration added is small, so egress finds the post's place by
  flattening those parts alone, and takes the post's length and line count
  from the arrival record, without scanning the message.  Any ``From``
  lines that smtplib escapes as ``>From`` are restored as literals.
  If Mailman rewrote the body itself (content filtering, or the DMARC wrap
  mitigation), the body Recipe is ``null``.

Both Recipes are checked against the snapshot's hashes before they are
written.  A retry of a copy that was already stamped adds nothing.  Every
copy is marked ``X-DKIM2-Exploded: yes`` so that the signing MTA sets the
*exploded* flag.  Messages Mailman originates itself, such as notifications,
get an ``m=1`` of their own.

On a list with Message-Instance headers, From becomes the list's posting
address whenever the list changes or originates a post, whatever the list's
DMARC settings.  Whether the body will change is known without serializing
it: a handler changed a payload, delivery will add a header or footer, or
smtplib will escape a ``From`` line.  The header Recipe keeps the original
From.

Every instance Mailman adds comes with an ``X-DKIM2-Info`` header
(draft-gondwana-dkim2-debug-header-01) naming the draft, the hashed header
fields and the arrival outcome, for diagnosing interoperability problems.


Limitations
===========

The ``mail-archive`` archiver sends its copy through the out queue without a
snapshot, so that copy gets no instance of its own.
