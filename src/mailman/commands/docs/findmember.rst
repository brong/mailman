===============
Finding members
===============

The ``mailman findmember`` command reports all members with email address
matching a case-insensitive supplied pattern by address, list and role.

    >>> from mailman.testing.documentation import cli
    >>> command = cli('mailman.commands.cli_findmember.findmember')

Usage
-----

Here is the complete usage for the command.
::

    >>> command('mailman findmember --help')
    Usage: findmember [OPTIONS] PATTERN
    <BLANKLINE>
      Display all memberships for a user or users with address matching a pattern.
      Because part of the process involves converting the pattern to a SQL query
      with wildcards, the pattern should be simple. A simple string works best.
    <BLANKLINE>
    Options:
      -r, --role [all|owner|moderator|nonmember|member|administrator]
                                      Display only memberships with the given role.
                                      If not given, 'all' role, i.e. all roles, is
                                      the default.
    <BLANKLINE>
      --help                          Show this message and exit.

Examples
--------

You can find all memberships for an address.

First we create some lists and add a few members as users or addresses and
with various roles.
::

    >>> from mailman.app.lifecycle import create_list
    >>> ant = create_list('ant@example.com')
    >>> bee = create_list('bee@example.com')
    >>> from mailman.interfaces.member import MemberRole
    >>> from mailman.testing.helpers import subscribe
    >>> subscribe(ant, 'Anne')
    <Member: Anne Person <aperson@example.com> on ant@example.com
             as MemberRole.member>
    >>> subscribe(ant, 'Bart', as_user=True)
    <Member: Bart Person <bperson@example.com> on ant@example.com
             as MemberRole.member>
    >>> subscribe(ant, 'Cate', role=MemberRole.owner)
    <Member: Cate Person <cperson@example.com> on ant@example.com
             as MemberRole.owner>
    >>> subscribe(ant, 'Doug', role=MemberRole.moderator)
    <Member: Doug Person <dperson@example.com> on ant@example.com
             as MemberRole.moderator>
    >>> subscribe(ant, 'Elly', role=MemberRole.nonmember)
    <Member: Elly Person <eperson@example.com> on ant@example.com
             as MemberRole.nonmember>
    >>> subscribe(ant, 'Fred', role=MemberRole.nonmember, as_user=True)
    <Member: Fred Person <fperson@example.com> on ant@example.com
             as MemberRole.nonmember>
    >>> subscribe(bee, 'Bart')
    <Member: Bart Person <bperson@example.com> on bee@example.com
             as MemberRole.member>
    >>> subscribe(bee, 'Cate', role=MemberRole.moderator)
    <Member: Cate Person <cperson@example.com> on bee@example.com
             as MemberRole.moderator>
    >>> subscribe(bee, 'Doug', role=MemberRole.owner)
    <Member: Doug Person <dperson@example.com> on bee@example.com
             as MemberRole.owner>
    >>> subscribe(bee, 'Elly', role=MemberRole.owner)
    <Member: Elly Person <eperson@example.com> on bee@example.com
             as MemberRole.owner>
    >>> subscribe(bee, 'Fred', role=MemberRole.nonmember, as_user=True)
    <Member: Fred Person <fperson@example.com> on bee@example.com
             as MemberRole.nonmember>
    >>> subscribe(bee, 'Greg', as_user=True)
    <Member: Greg Person <gperson@example.com> on bee@example.com
             as MemberRole.member>
    >>> subscribe(bee, 'Jeff')
    <Member: Jeff Person <jperson@example.com> on bee@example.com
             as MemberRole.member>

First, use a pattern of ``person`` and no ``--role`` option to get all members
and roles.
::

    >>> command('mailman findmember person')
    Email: aperson@example.com
        List: ant.example.com
            MemberRole.member
    Email: bperson@example.com
        List: ant.example.com
            MemberRole.member
        List: bee.example.com
            MemberRole.member
    Email: cperson@example.com
        List: ant.example.com
            MemberRole.owner
        List: bee.example.com
            MemberRole.moderator
    Email: dperson@example.com
        List: ant.example.com
            MemberRole.moderator
        List: bee.example.com
            MemberRole.owner
    Email: eperson@example.com
        List: ant.example.com
            MemberRole.nonmember
        List: bee.example.com
            MemberRole.owner
    Email: fperson@example.com
        List: ant.example.com
            MemberRole.nonmember
        List: bee.example.com
            MemberRole.nonmember
    Email: gperson@example.com
        List: bee.example.com
            MemberRole.member
    Email: jperson@example.com
        List: bee.example.com
            MemberRole.member

We can use a more specific pattern to get just one email address.
::

    >>> command('mailman findmember bperson@example.com')
    Email: bperson@example.com
        List: ant.example.com
            MemberRole.member
        List: bee.example.com
            MemberRole.member

Patterns are matched case insensitively.
::

    >>> command('mailman findmember BPerson@example.com')
    Email: bperson@example.com
        List: ant.example.com
            MemberRole.member
        List: bee.example.com
            MemberRole.member

We can select only specific roles.  Here we get all owners.
::

    >>> command('mailman findmember --role owner .')
    Email: cperson@example.com
        List: ant.example.com
            MemberRole.owner
    Email: dperson@example.com
        List: bee.example.com
            MemberRole.owner
    Email: eperson@example.com
        List: bee.example.com
            MemberRole.owner

We can use the administrator role to get owners and moderators.
::

    >>> command('mailman findmember --role administrator .')
    Email: cperson@example.com
        List: ant.example.com
            MemberRole.owner
        List: bee.example.com
            MemberRole.moderator
    Email: dperson@example.com
        List: ant.example.com
            MemberRole.moderator
        List: bee.example.com
            MemberRole.owner
    Email: eperson@example.com
        List: bee.example.com
            MemberRole.owner
