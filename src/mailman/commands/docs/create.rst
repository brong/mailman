==========================
Command line list creation
==========================

A system administrator can create mailing lists by the command line.

    >>> from mailman.testing.documentation import cli
    >>> command = cli('mailman.commands.cli_lists.create')

You can prevent creation of a mailing list in an unknown domain.

    >>> command('mailman create --no-domain ant@example.xx')
    Usage: create [OPTIONS] LISTNAME
    Try 'create --help' for help.
    <BLANKLINE>
    Error: Undefined domain: example.xx

By default, Mailman will create the domain if it doesn't exist.

    >>> command('mailman create ant@example.xx')
    Created mailing list: ant@example.xx

Now both the domain and the mailing list exist in the database.
::

    >>> from mailman.interfaces.listmanager import IListManager
    >>> from zope.component import getUtility
    >>> list_manager = getUtility(IListManager)
    >>> list_manager.get('ant@example.xx')
    <mailing list "ant@example.xx" at ...>

    >>> from mailman.interfaces.domain import IDomainManager
    >>> getUtility(IDomainManager).get('example.xx')
    <Domain example.xx>

The command can also operate quietly.
::

    >>> command('mailman create --quiet bee@example.com')
    >>> mlist = list_manager.get('bee@example.com')
    >>> mlist
    <mailing list "bee@example.com" at ...>


Setting the owner
=================

By default, no list owners are specified.

    >>> from mailman.testing.documentation import dump_list
    >>> dump_list(mlist.owners.addresses)
    *Empty*

But you can specify an owner address on the command line when you create the
mailing list.
::

    >>> command('mailman create --owner anne@example.com cat@example.com')
    Created mailing list: cat@example.com

    >>> mlist = list_manager.get('cat@example.com')
    >>> dump_list(repr(address) for address in mlist.owners.addresses)
    <Address: anne@example.com [not verified] at ...>

You can even specify more than one address for the owners.
::

    >>> command('mailman create '
    ...         '--owner anne@example.com '
    ...         '--owner bart@example.com '
    ...         '--owner cate@example.com '
    ...         'dog@example.com')
    Created mailing list: dog@example.com

    >>> mlist = list_manager.get('dog@example.com')
    >>> from operator import attrgetter
    >>> dump_list(repr(address) for address in mlist.owners.addresses)
    <Address: anne@example.com [not verified] at ...>
    <Address: bart@example.com [not verified] at ...>
    <Address: cate@example.com [not verified] at ...>

You can also set owners and moderators on an existing list with the
`mailman admins`_ command.

Setting the language
====================

You can set the default language for the new mailing list when you create it.
The language must be known to Mailman.
::

    >>> command('mailman create --language xx ewe@example.com')
    Usage: create [OPTIONS] LISTNAME
    Try 'create --help' for help.
    <BLANKLINE>
    Error: Invalid language code: xx

    >>> from mailman.interfaces.languages import ILanguageManager
    >>> getUtility(ILanguageManager).add('xx', 'iso-8859-1', 'Freedonian')
    <Language [xx] Freedonian>

    >>> command('mailman create --language xx ewe@example.com')
    Created mailing list: ewe@example.com

    >>> mlist = list_manager.get('ewe@example.com')
    >>> print(mlist.preferred_language)
    <Language [xx] Freedonian>


Notifications
=============

When told to, Mailman will notify the list owners of their new mailing list.

    >>> command('mailman create '
    ...         '--notify '
    ...         '--owner anne@example.com '
    ...         '--owner bart@example.com '
    ...         '--owner cate@example.com '
    ...         'fly@example.com')
    Created mailing list: fly@example.com

The notification message is in the virgin queue.
::

    >>> from mailman.testing.helpers import get_queue_messages
    >>> messages = get_queue_messages('virgin')
    >>> len(messages)
    1

    >>> for message in messages:
    ...     print(message.msg.as_string())
    MIME-Version: 1.0
    ...
    Subject: Your new mailing list: fly@example.com
    From: noreply@example.com
    To: anne@example.com, bart@example.com, cate@example.com
    ...
    <BLANKLINE>
    The mailing list 'fly@example.com' has just been created for you.
    The following is some basic information about your mailing list.
    <BLANKLINE>
    There is an email-based interface for users (not administrators) of
    your list; you can get info about using it by sending a message with
    just the word 'help' as subject or in the body, to:
    <BLANKLINE>
        fly-request@example.com
    <BLANKLINE>
    Please address all questions to noreply@example.com.
    <BLANKLINE>


.. _`mailman admins`: https://docs.mailman3.org/projects/mailman/en/latest/src/mailman/commands/docs/admins.html
