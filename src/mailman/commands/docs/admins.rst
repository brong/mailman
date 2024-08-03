==============================
Managing Owners and Moderators
==============================

The ``mailman admins`` command allows a site administrator to add and/or
delete owners and moderators of a mailing list.

    >>> from mailman.testing.documentation import cli
    >>> command = cli('mailman.commands.cli_admins.admins')

Usage
-----

Here is the complete usage for the command.
::

    >>> command('mailman admins --help')
    Usage: admins [OPTIONS] LISTSPEC
    <BLANKLINE>
      Add and/or delete owners and moderators of a list.
    <BLANKLINE>
    Options:
      -a, --add TEXT                Email address of the user to add with the given
                                    role. May be repeated to add multiple users.
      -d, --delete TEXT             Email address of the user to be removed from the
                                    given role. May be repeated to delete multiple
                                    users.
      -r, --role [owner|moderator]  The role to add/delete. This may be 'owner' or
                                    'moderator'. If not given, then 'owner' role is
                                    assumed.
      --help                        Show this message and exit.

Examples
--------

You can add owners or moderators to a mailing list from the command line.
::

    >>> from mailman.app.lifecycle import create_list    
    >>> bee = create_list('bee@example.com')
    >>> command('mailman admins --add aperson@example.com bee.example.com')
    >>> from mailman.testing.documentation import dump_list
    >>> from operator import attrgetter
    >>> dump_list(bee.owners.addresses, key=attrgetter('email'))
    aperson@example.com

    >>> command('mailman admins --add bperson@example.com '
    ...         '--role moderator bee.example.com')
    >>> dump_list(bee.moderators.addresses, key=attrgetter('email'))
    bperson@example.com

You can delete owners or moderators from a mailing list from the command line.
::

    >>> command('mailman admins --delete aperson@example.com bee.example.com')
    >>> dump_list(bee.owners.addresses, key=attrgetter('email'))
    *Empty*

You can add and delete in one command.
::

    >>> command('mailman admins --delete bperson@example.com '
    ...         '--add cperson@example.com --role moderator bee.example.com')
    >>> dump_list(bee.moderators.addresses, key=attrgetter('email'))
    cperson@example.com

Adding addesses which already have that role just results in a warning being
printed.
::

    >>> command('mailman admins --add cperson@example.com '
    ...         '--role moderator bee.example.com')
    cperson@example.com is already an moderator of bee@example.com

Likewise, removing an address which doesn't have that role just results in a
warning being printed.
::

    >>> command('mailman admins --delete aperson@example.com bee.example.com')
    aperson@example.com is not an owner of bee@example.com
