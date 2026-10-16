.. _command-line-message-injection:

==============================
Command line message injection
==============================

You can inject a message directly into a queue directory via the command
line.

    >>> from mailman.testing.documentation import cli
    >>> command = cli('mailman.commands.cli_inject.inject')

It's easy to find out which queues are available.

    >>> command('mailman inject --show')
    Available queues:
        archive
        bad
        bounces
        command
        digest
        in
        nntp
        out
        pipeline
        retry
        shunt
        virgin

Usually, the text of the message to inject is in a file.

    >>> from tempfile import NamedTemporaryFile
    >>> filename = cleanups.enter_context(NamedTemporaryFile()).name
    >>> with open(filename, 'w', encoding='utf-8') as fp:
    ...     print("""\
    ... From: aperson@example.com
    ... To: ant@example.com
    ... Subject: testing
    ... Message-ID: <aardvark>
    ...
    ... This is a test message.
    ... """, file=fp)

Create a mailing list to inject this message into.

    >>> from mailman.app.lifecycle import create_list
    >>> mlist = create_list('ant@example.com')
    >>> from mailman.config import config
    >>> transaction = config.db    
    >>> transaction.commit()

The mailing list's incoming queue is empty.

    >>> from mailman.testing.helpers import get_queue_messages
    >>> get_queue_messages('in')
    []

By default, messages are injected into the incoming queue.

    >>> command('mailman inject --filename ' + filename + ' ant@example.com')
    >>> items = get_queue_messages('in')
    >>> len(items)
    1
    >>> print(items[0].msg.as_string())
    From: aperson@example.com
    To: ant@example.com
    Subject: testing
    Message-ID: ...
    Date: ...
    <BLANKLINE>
    This is a test message.
    <BLANKLINE>
    <BLANKLINE>

And the message is destined for ant@example.com.

    >>> from mailman.testing.documentation import dump_msgdata
    >>> dump_msgdata(items[0].msgdata)
    _parsemsg    : False
    listid       : ant.example.com
    original_size: 252
    version      : 3

But a different queue can be specified on the command line.
::

    >>> command('mailman inject --queue virgin --filename ' +
    ...         filename + ' ant@example.com')

    >>> get_queue_messages('in')
    []
    >>> items = get_queue_messages('virgin')
    >>> len(items)
    1
    >>> print(items[0].msg.as_string())
    From: aperson@example.com
    To: ant@example.com
    Subject: testing
    Message-ID: ...
    Date: ...
    <BLANKLINE>
    This is a test message.
    <BLANKLINE>
    <BLANKLINE>

    >>> dump_msgdata(items[0].msgdata)
    _parsemsg    : False
    listid       : ant.example.com
    original_size: 252
    version      : 3


Standard input
==============

The message text can also be provided on standard input.
::

    >>> stdin = """\
    ... From: bperson@example.com
    ... To: ant@example.com
    ... Subject: another test
    ... Message-ID: <badger>
    ...
    ... This is another test message.
    ... """

    >>> command('mailman inject --filename - ant@example.com', input=stdin)
    >>> items = get_queue_messages('in')
    >>> len(items)
    1
    >>> print(items[0].msg.as_string())
    From: bperson@example.com
    To: ant@example.com
    Subject: another test
    Message-ID: ...
    Date: ...
    <BLANKLINE>
    This is another test message.
    <BLANKLINE>
    <BLANKLINE>

    >>> dump_msgdata(items[0].msgdata)
    _parsemsg    : False
    listid       : ant.example.com
    original_size: 260
    version      : 3


Metadata
========

Additional metadata keys can be provided on the command line.  These key/value
pairs get added to the message metadata dictionary when the message is
injected.
::

    >>> command('mailman inject --filename ' + filename +
    ...         ' -m foo=one -m bar=two ant@example.com')

    >>> items = get_queue_messages('in')
    >>> dump_msgdata(items[0].msgdata)
    _parsemsg    : False
    bar          : two
    foo          : one
    listid       : ant.example.com
    original_size: 252
    version      : 3
