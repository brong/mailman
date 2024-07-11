=====================================
Poll the NNTP Server For New Messages
=====================================

The ``mailman gatenews`` command is normally run as a periodic task by the
NNTP runner to poll the configured NNTP server for new messages to
be posted to lists that are configured to gateway messages from a usenet group.
It is normally not run manually.

There are no options other than ``--help``.
::

    >>> from mailman.testing.documentation import cli   
    >>> command = cli('mailman.commands.cli_gatenews.gatenews')

    >>> command('mailman gatenews --help')
    Usage: gatenews [OPTIONS]
    <BLANKLINE>
      Poll the NNTP server for messages to be gatewayed to mailing lists.
    <BLANKLINE>
    Options:
      --help  Show this message and exit.
