==============
Adding members
==============

The ``mailman addmembers`` command allows a site administrator to add members
to a mailing list.

    >>> from mailman.testing.documentation import cli
    >>> command = cli('mailman.commands.cli_addmembers.addmembers')

Usage
-----

Here is the complete usage for the command.
::

    >>> command('mailman addmembers --help')
    Usage: addmembers [OPTIONS] FILENAME LISTSPEC
    <BLANKLINE>
      Add all member addresses in FILENAME with delivery mode as specified with
      -d/--delivery.  FILENAME can be '-' to indicate standard input. Blank lines
      and lines that start with a '#' are ignored.
    <BLANKLINE>
    Options:
      -d, --delivery [regular|mime|plain|summary|disabled]
                                      Set the added members delivery mode to
                                      'regular', 'mime', 'plain', 'summary' or
                                      'disabled'.  I.e., one of regular, three modes
                                      of digest or no delivery.  If not given, the
                                      default is regular.  Ignored for invited
                                      members.
    <BLANKLINE>
      -i, --invite                    Send the added members an invitation rather
                                      than immediately adding them.
    <BLANKLINE>
      -w, --welcome-msg / -W, --no-welcome-msg
                                      Override the list's setting for
                                      send_welcome_message.
    <BLANKLINE>
      -n, --admin-notify / -N, --no-admin-notify
                                      Override the list's setting for
                                      admin_notify_mchanges.
    <BLANKLINE>
      --help                          Show this message and exit.

Examples
--------

You can add members to a mailing list from the command line.  To do so, you
need a file containing email addresses and optional display names that can be
parsed by ``email.utils.parseaddr()``.
::

    >>> from tempfile import NamedTemporaryFile
    >>> filename = cleanups.enter_context(NamedTemporaryFile()).name
    >>> from mailman.app.lifecycle import create_list    
    >>> bee = create_list('bee@example.com')
    >>> with open(filename, 'w', encoding='utf-8') as fp:
    ...     print("""\
    ... aperson@example.com
    ... Bart Person <bperson@example.com>
    ... cperson@example.com (Cate Person)
    ... """, file=fp)

    >>> command('mailman addmembers ' + filename + ' bee.example.com')

    >>> from mailman.testing.documentation import dump_list
    >>> from operator import attrgetter
    >>> dump_list(bee.members.addresses, key=attrgetter('email'))
    aperson@example.com
    Bart Person <bperson@example.com>
    Cate Person <cperson@example.com>

You can also specify ``-`` as the filename, in which case the addresses are
taken from standard input.
::

    >>> stdin = """\
    ... dperson@example.com
    ... Elly Person <eperson@example.com>
    ... fperson@example.com (Fred Person)
    ... """
    >>> command('mailman addmembers - bee.example.com', input=stdin)

    >>> dump_list(bee.members.addresses, key=attrgetter('email'))
    aperson@example.com
    Bart Person <bperson@example.com>
    Cate Person <cperson@example.com>
    dperson@example.com
    Elly Person <eperson@example.com>
    Fred Person <fperson@example.com>

Blank lines and lines that begin with '#' are ignored.
::

    >>> with open(filename, 'w', encoding='utf-8') as fp:
    ...     print("""\
    ... gperson@example.com
    ... # hperson@example.com
    ...
    ... iperson@example.com
    ... """, file=fp)

    >>> command('mailman addmembers ' + filename + ' bee.example.com')

    >>> dump_list(bee.members.addresses, key=attrgetter('email'))
    aperson@example.com
    Bart Person <bperson@example.com>
    Cate Person <cperson@example.com>
    dperson@example.com
    Elly Person <eperson@example.com>
    Fred Person <fperson@example.com>
    gperson@example.com
    iperson@example.com

Addresses which are already subscribed are ignored, although a warning is
printed.
::

    >>> with open(filename, 'w', encoding='utf-8') as fp:
    ...     print("""\
    ... gperson@example.com
    ... aperson@example.com
    ... jperson@example.com
    ... """, file=fp)

    >>> command('mailman addmembers ' + filename + ' bee.example.com')
    Already subscribed (skipping): gperson@example.com
    Already subscribed (skipping): aperson@example.com

    >>> dump_list(bee.members.addresses, key=attrgetter('email'))
    aperson@example.com
    Bart Person <bperson@example.com>
    Cate Person <cperson@example.com>
    dperson@example.com
    Elly Person <eperson@example.com>
    Fred Person <fperson@example.com>
    gperson@example.com
    iperson@example.com
    jperson@example.com
