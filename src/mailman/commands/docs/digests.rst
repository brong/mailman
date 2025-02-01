===============
Sending Digests
===============

When a list's ``digests_enabled`` setting is ``True``, Mailman accumulates
list posts in a mailbox for eventual sending to digest members.  When the size
of this mailbox reaches the list's ``digest_size_threshold``, a digest is sent
to the digest members and the mailbox is cleared.

A list may also have its ``digest_send_periodic`` setting ``True`` in which
case accumulated digests should be sent periodically even if
``digest_size_threshold`` hasn't been reached.

The ``mailman digests`` command is run by cron to do the sending of periodic
digests.  For this use, the ``--periodic`` option is used with the command
run by cron at intervals determined by the site, but normally daily.

There is also a ``--send`` option which will send accummulated digest for a
list or lists even if the list's ``digest_send_periodic`` setting is ``False``.

Complete information about command options may be obtained by running
``mailman digests --help``.
::

    >>> from mailman.testing.documentation import cli   
    >>> command = cli('mailman.commands.cli_digests.digests')

    >>> command('mailman digests --help')
    Usage: digests [OPTIONS]
    <BLANKLINE>
      Operate on digests.
    <BLANKLINE>
    Options:
      -l, --list list  Operate on this mailing list.  Multiple --list options can be
                       given.  The argument can either be a List-ID or a fully
                       qualified list name.  Without this option, operate on the
                       digests for all mailing lists.
      -s, --send       Send any collected digests right now, even if the size
                       threshold has not yet been met.
      -b, --bump       Increment the digest volume number and reset the digest
                       number to one.  If given with --send, the volume number is
                       incremented before any current digests are sent.
      -n, --dry-run    Don't actually do anything, but in conjunction with
                       --verbose, show what would happen.
      -v, --verbose    Print some additional status.
      -p, --periodic   Send any collected digests for the List only if their
                       digest_send_periodic is set to True.
      -h, --help       Show this message and exit.
