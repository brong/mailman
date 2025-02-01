=================
Content filtering
=================

Mailman can filter the content of messages posted to a mailing list by
stripping MIME subparts, and possibly reorganizing the MIME structure of a
message.

    >>> from mailman.app.lifecycle import create_list
    >>> mlist = create_list('test@example.com')

Several mailing list options control content filtering.  First, the feature
must be enabled, then there are two options that control which MIME types get
filtered and which get passed.  Finally, there is an option to control whether
``text/html`` parts will get converted to plain text.  Let's set up some
defaults for these variables, then we'll explain them in more detail below.

    >>> mlist.filter_content = True
    >>> mlist.filter_types = []
    >>> mlist.pass_types = []
    >>> mlist.filter_extensions = []
    >>> mlist.pass_extensions = []
    >>> mlist.convert_html_to_plaintext = False


Filtering the outer content type
================================

A simple filtering setting will just search the content types of the messages
parts, discarding all parts with a matching MIME type.  If the message's outer
content type matches the filter, the entire message will be discarded.
However, if we turn off content filtering altogether, then the handler
short-circuits.
::

    >>> from mailman.interfaces.mime import FilterAction

    >>> mlist.filter_types = ['image/jpeg']
    >>> mlist.filter_action = FilterAction.discard
    >>> from mailman.testing.helpers import (specialized_message_from_string
    ...   as message_from_string)
    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: image/jpeg
    ... MIME-Version: 1.0
    ...
    ... xxxxx
    ... """)

    >>> from mailman.config import config    
    >>> process = config.handlers['mime-delete'].process
    >>> mlist.filter_content = False
    >>> msgdata = {}
    >>> process(mlist, msg, msgdata)
    >>> print(msg.as_string())
    From: aperson@example.com
    Content-Type: image/jpeg
    MIME-Version: 1.0
    <BLANKLINE>
    xxxxx
    >>> msgdata
    {}

Similarly, no content filtering is performed on digest messages, which are
crafted internally by Mailman.

    >>> mlist.filter_content = True
    >>> msgdata = {'isdigest': True}
    >>> process(mlist, msg, msgdata)
    >>> print(msg.as_string())
    From: aperson@example.com
    Content-Type: image/jpeg
    MIME-Version: 1.0
    <BLANKLINE>
    xxxxx
    >>> from mailman.testing.documentation import dump_msgdata    
    >>> dump_msgdata(msgdata)
    isdigest: True


Simple multipart filtering
==========================

If one of the subparts in a ``multipart`` message matches the filter type,
then just that subpart will be stripped.  If that leaves just a single subpart,
the ``multipart`` will be replaced by the subpart.
::

    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/mixed; boundary=BOUNDARY
    ... MIME-Version: 1.0
    ...
    ... --BOUNDARY
    ... Content-Type: image/jpeg
    ... MIME-Version: 1.0
    ...
    ... xxx
    ...
    ... --BOUNDARY
    ... Content-Type: image/gif
    ... MIME-Version: 1.0
    ...
    ... yyy
    ... --BOUNDARY--
    ... """)

    >>> process(mlist, msg, {})
    >>> print(msg.as_string())
    From: aperson@example.com
    MIME-Version: 1.0
    Content-Type: image/gif
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    yyy


Collapsing multipart/alternative messages
=========================================

When content filtering encounters a ``multipart/alternative`` part, and the
results of filtering leave only one of the subparts, then the
``multipart/alternative`` may be collapsed.  For example, in the following
message, the outer content type is a ``multipart/mixed``.  Inside this part is
just a single subpart that has a content type of ``multipart/alternative``.
This inner multipart has two subparts, a jpeg and a gif.

Content filtering will remove the jpeg part, leaving the
``multipart/alternative`` with only a single gif subpart.  Because there's
only one subpart left, the MIME structure of the message will be reorganized,
removing the inner ``multipart/alternative`` so that the outer
``multipart/mixed`` has just a single gif subpart, and then the multipart is
recast as just the subpart.

    >>> mlist.collapse_alternatives = True
    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/mixed; boundary=BOUNDARY
    ... MIME-Version: 1.0
    ...
    ... --BOUNDARY
    ... Content-Type: multipart/alternative; boundary=BOUND2
    ... MIME-Version: 1.0
    ...
    ... --BOUND2
    ... Content-Type: image/jpeg
    ... MIME-Version: 1.0
    ...
    ... xxx
    ...
    ... --BOUND2
    ... Content-Type: image/gif
    ... MIME-Version: 1.0
    ...
    ... yyy
    ... --BOUND2--
    ...
    ... --BOUNDARY--
    ... """)
    >>> process(mlist, msg, {})
    >>> print(msg.as_string())
    From: aperson@example.com
    MIME-Version: 1.0
    Content-Type: image/gif
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    yyy

When the outer part is a ``multipart/alternative`` and filtering leaves this
outer part with just one subpart, the entire message is converted to the left
over part's content type.  In other words, the left over inner part is
promoted to being the outer part.
::

    >>> mlist.filter_types = ['image/jpeg', 'text/html']
    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/alternative; boundary=AAA
    ...
    ... --AAA
    ... Content-Type: text/html
    ...
    ... <b>This is some html</b>
    ... --AAA
    ... Content-Type: text/plain
    ...
    ... This is plain text
    ... --AAA--
    ... """)

    >>> process(mlist, msg, {})
    >>> print(msg.as_string())
    From: aperson@example.com
    MIME-Version: 1.0
    Content-Transfer-Encoding: 7bit
    Content-Type: text/plain
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    This is plain text

Clean up.

    >>> mlist.filter_types = ['image/jpeg']


Conversion to plain text
========================

Some mailing lists prohibit HTML email, and in fact, such email can be a
phishing or spam vector.  However, many mail readers will send HTML email by
default because users think it looks pretty.  One approach to handling this
would be to filter out ``text/html`` parts and rely on
``multipart/alternative`` collapsing to leave just a plain text part.  This
works because many mail readers that send HTML email actually send a plain
text part in the second subpart of such ``multipart/alternatives``.

While this is a good suggestion for plain text-only mailing lists, often a
mail reader will send only a ``text/html`` part with no plain text
alternative.  in this case, the site administer can enable ``text/html`` to
``text/plain`` conversion by defining a conversion command.  A list
administrator still needs to enable such conversion for their list though.

    >>> mlist.convert_html_to_plaintext = True

By default, Mailman sends the message through lynx, but since this program is
not guaranteed to exist, we'll craft a simple, but stupid script to simulate
the conversion process.  The script expects a single argument, which is the
name of the file containing the message payload to filter.
::

    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: text/html
    ... MIME-Version: 1.0
    ...
    ... <html><head></head>
    ... <body></body></html>
    ... """)

    >>> from mailman.handlers.tests.test_mimedel import dummy_script
    >>> with dummy_script():
    ...     process(mlist, msg, {})
    >>> print(msg.as_string())
    From: aperson@example.com
    Content-Transfer-Encoding: 7bit
    MIME-Version: 1.0
    Content-Type: text/plain; charset="us-ascii"
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    Converted text/html to text/plain
    Filename: ...
    <BLANKLINE>


Discarding empty parts
======================

Similarly, if after filtering a multipart section ends up empty, then the
entire multipart is discarded.  For example, here's a message where an inner
``multipart/mixed`` contains two jpeg subparts.  Both jpegs are filtered out,
so the entire inner ``multipart/mixed`` is discarded.
::

    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/mixed; boundary=AAA
    ...
    ... --AAA
    ... Content-Type: multipart/mixed; boundary=BBB
    ...
    ... --BBB
    ... Content-Type: image/jpeg
    ...
    ... xxx
    ... --BBB
    ... Content-Type: image/jpeg
    ...
    ... yyy
    ... --BBB---
    ... --AAA
    ... Content-Type: multipart/alternative; boundary=CCC
    ...
    ... --CCC
    ... Content-Type: text/html
    ...
    ... <h2>This is a header</h2>
    ...
    ... --CCC
    ... Content-Type: text/plain
    ...
    ... A different message
    ... --CCC--
    ... --AAA
    ... Content-Type: image/gif
    ...
    ... zzz
    ... --AAA
    ... Content-Type: image/gif
    ...
    ... aaa
    ... --AAA--
    ... """)

    >>> with dummy_script():
    ...     process(mlist, msg, {})

    >>> print(msg.as_string())
    From: aperson@example.com
    Content-Type: multipart/mixed; boundary=AAA
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    --AAA
    Content-Transfer-Encoding: 7bit
    MIME-Version: 1.0
    Content-Type: text/plain; charset="us-ascii"
    <BLANKLINE>
    Converted text/html to text/plain
    Filename: ...
    <BLANKLINE>
    --AAA
    Content-Type: image/gif
    <BLANKLINE>
    zzz
    --AAA
    Content-Type: image/gif
    <BLANKLINE>
    aaa
    --AAA--
    <BLANKLINE>


Passing MIME types
==================

All of the above examples set only ``filter_types`` which will cause parts
with matching MIME types to be removed.  There is also a ``pass_types``
setting which if non-empty, causes parts with non-matching MIME types to be
removed.  I.e., it lists only those MIME types that will be kept.  Also, note
that if we want to keep elemental parts including those that might be
sub-parts of some ``multipart`` or ``message/rfc822`` part, we also need to
include those types in ``pass_types``.  Here we use the example above to
illustrate ``pass_types`` instead.  Note that we want to keep both
``multipart/mixed`` and ``multipart/alternative`` to be able to keep some of
their sub-parts so we just keep all ``multipart`` types.  Likewise, we'll
keep all ``text`` parts even though in this case the original ``text/plain``
part will be removed by ``collapse_alternatives``.  Finally, we'll keep only
the ``image/gif`` image parts.
::

    >>> mlist.filter_types = []
    >>> mlist.pass_types = ['multipart', 'text', 'image/gif']
    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/mixed; boundary=AAA
    ...
    ... --AAA
    ... Content-Type: multipart/mixed; boundary=BBB
    ...
    ... --BBB
    ... Content-Type: image/jpeg
    ...
    ... xxx
    ... --BBB
    ... Content-Type: image/jpeg
    ...
    ... yyy
    ... --BBB---
    ... --AAA
    ... Content-Type: multipart/alternative; boundary=CCC
    ...
    ... --CCC
    ... Content-Type: text/html
    ...
    ... <h2>This is a header</h2>
    ...
    ... --CCC
    ... Content-Type: text/plain
    ...
    ... A different message
    ... --CCC--
    ... --AAA
    ... Content-Type: image/gif
    ...
    ... zzz
    ... --AAA
    ... Content-Type: image/gif
    ...
    ... aaa
    ... --AAA--
    ... """)

    >>> with dummy_script():
    ...     process(mlist, msg, {})

    >>> print(msg.as_string())
    From: aperson@example.com
    Content-Type: multipart/mixed; boundary=AAA
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    --AAA
    Content-Transfer-Encoding: 7bit
    MIME-Version: 1.0
    Content-Type: text/plain; charset="us-ascii"
    <BLANKLINE>
    Converted text/html to text/plain
    Filename: ...
    <BLANKLINE>
    --AAA
    Content-Type: image/gif
    <BLANKLINE>
    zzz
    --AAA
    Content-Type: image/gif
    <BLANKLINE>
    aaa
    --AAA--
    <BLANKLINE>

Interaction between passing and filtering
=========================================

If both ``pass_types`` and ``filter_types`` are non-empty, any parts with types
in ``filter_types`` are removed and any remaining parts with types not in
``pass_types`` are removed as well.  Here's an example.
::

    >>> mlist.filter_types = ['text/rtf']
    >>> mlist.pass_types = ['multipart/mixed', 'text']
    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/mixed; boundary=AAA
    ...
    ... --AAA
    ... Content-Type: text/plain
    ...
    ... plain text
    ... --AAA
    ... Content-Type: text/html
    ...
    ...
    ... <h2>A header</h2>
    ...
    ... --AAA
    ... Content-Type: text/rtf
    ...
    ... will be removed
    ... --AAA
    ... Content-Type: text/plain
    ...
    ... another plain text
    ... --AAA
    ... Content-Type: image/gif
    ...
    ... image will be removed
    ... --AAA
    ... Content-Type: multipart/alternative; boundary="BBB"
    ...
    ... --BBB
    ... Content-Type: text/plain
    ...
    ... plain text
    ... --BBB
    ... Content-Type: text/html
    ...
    ...  <h2>Another header</h2>
    ...
    ... --BBB--
    ... --AAA--
    ...
    ... """)

    >>> with dummy_script():
    ...     process(mlist, msg, {})

    >>> print(msg.as_string())
    From: aperson@example.com
    Content-Type: multipart/mixed; boundary=AAA
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    --AAA
    Content-Type: text/plain
    <BLANKLINE>
    plain text
    --AAA
    Content-Transfer-Encoding: 7bit
    MIME-Version: 1.0
    Content-Type: text/plain; charset="us-ascii"
    <BLANKLINE>
    Converted text/html to text/plain
    Filename: ...
    <BLANKLINE>
    <BLANKLINE>
    <BLANKLINE>
    --AAA
    Content-Type: text/plain
    <BLANKLINE>
    another plain text
    --AAA--
    <BLANKLINE>
    <BLANKLINE>

Note that the html is still converted to plain text because we didn't change
that, the ``text/rtf`` part is removed due to ``filter_types`` and the image
and the entire ``multipart/alternative`` parts are removed as they aren't in
``pass_types``

Passing and filtering extensions
================================

``pass_extensions`` and ``filter_extensions`` work in exactly the same way.
These filters are applied following ``filter_types`` and ``pass_types`` and
filter based on the filename extension of any part that has an associated
filename with extension.  Parts that don't have an associated filename with
an extension are not affected.  Here's a simple example.
::

    >>> mlist.filter_types = []
    >>> mlist.pass_types = []
    >>> mlist.filter_extensions = ['pdf']
    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/mixed; boundary=AAA
    ...
    ... --AAA
    ... Content-Type: text/plain
    ...
    ... plain text
    ... --AAA
    ... Content-Type: application/pdf; name="file.pdf"
    ... Content-Disposition: attachment; filename="file.pdf"
    ...
    ... pdf to remove
    ... --AAA--
    ... """)

    >>> with dummy_script():
    ...     process(mlist, msg, {})

    >>> print(msg.as_string())
    From: aperson@example.com
    MIME-Version: 1.0
    Content-Transfer-Encoding: 7bit
    Content-Type: text/plain
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    plain text

Reporting Option
================

Finally, there is a global mailman.cfg setting to append a report of what was
filtered to the message.  Here's a simple example.
::

    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/mixed; boundary=AAA
    ...
    ... --AAA
    ... Content-Type: text/plain
    ...
    ... plain text
    ... --AAA
    ... Content-Type: application/pdf; name="file.pdf"
    ... Content-Disposition: attachment; filename="file.pdf"
    ...
    ... pdf to remove
    ... --AAA--
    ... """)

    >>> with dummy_script('report'):
    ...     process(mlist, msg, {})

    >>> print(msg.as_string())
    From: aperson@example.com
    MIME-Version: 1.0
    X-Content-Filtered-By: Mailman/MimeDel ...
    Content-Type: text/plain; charset="us-ascii"
    Content-Transfer-Encoding: 7bit
    <BLANKLINE>
    plain text
    <BLANKLINE>
    ___________________________________________
    Mailman's content filtering has removed the
    following MIME parts from this message.
    <BLANKLINE>
    Content-Type: application/pdf
        Name: file.pdf
    <BLANKLINE>

And here's a more complex one.  Note that if the filtered message is multipart,
the report is attached as a separate MIME part.
::


    >>> mlist.filter_types = ['text/rtf']
    >>> mlist.pass_types = ['multipart/mixed', 'text']
    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... Content-Type: multipart/mixed; boundary=AAA
    ...
    ... --AAA
    ... Content-Type: text/plain
    ...
    ... plain text
    ... --AAA
    ... Content-Type: text/html
    ...
    ...
    ... <h2>A header</h2>
    ...
    ... --AAA
    ... Content-Type: text/rtf
    ...
    ... will be removed
    ... --AAA
    ... Content-Type: text/plain
    ...
    ... another plain text
    ... --AAA
    ... Content-Type: image/gif
    ...
    ... image will be removed
    ... --AAA
    ... Content-Type: multipart/alternative; boundary="BBB"
    ...
    ... --BBB
    ... Content-Type: text/plain
    ...
    ... plain text
    ... --BBB
    ... Content-Type: text/html
    ...
    ...  <h2>Another header</h2>
    ...
    ... --BBB--
    ... --AAA--
    ...
    ... """)

    >>> with dummy_script('report'):
    ...     process(mlist, msg, {})

    >>> print(msg.as_string())
    From: aperson@example.com
    Content-Type: multipart/mixed; boundary=AAA
    X-Content-Filtered-By: Mailman/MimeDel ...
    <BLANKLINE>
    --AAA
    Content-Type: text/plain
    <BLANKLINE>
    plain text
    --AAA
    Content-Transfer-Encoding: 7bit
    MIME-Version: 1.0
    Content-Type: text/plain; charset="us-ascii"
    <BLANKLINE>
    Converted text/html to text/plain
    Filename: ...
    <BLANKLINE>
    <BLANKLINE>
    <BLANKLINE>
    --AAA
    Content-Type: text/plain
    <BLANKLINE>
    another plain text
    --AAA
    Content-Type: text/plain; charset="us-ascii"
    MIME-Version: 1.0
    Content-Transfer-Encoding: 7bit
    <BLANKLINE>
    <BLANKLINE>
    ___________________________________________
    Mailman's content filtering has removed the
    following MIME parts from this message.
    <BLANKLINE>
    Content-Type: text/rtf
    <BLANKLINE>
    Content-Type: image/gif
    <BLANKLINE>
    Content-Type: multipart/alternative
    <BLANKLINE>
    --AAA--
    <BLANKLINE>
    <BLANKLINE>
