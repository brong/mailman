=========================
Command line list removal
=========================

A system administrator can remove mailing lists by the command line.
Note that this does not remove any archives.
::

    >>> from mailman.app.lifecycle import create_list   
    >>> create_list('ant@example.com')
    <mailing list "ant@example.com" at ...>

    >>> from mailman.testing.documentation import cli    
    >>> command = cli('mailman.commands.cli_lists.remove')
    >>> command('mailman remove ant@example.com')
    Removed list: ant@example.com

    >>> from mailman.interfaces.listmanager import IListManager
    >>> from zope.component import getUtility
    >>> list_manager = getUtility(IListManager)
    >>> print(list_manager.get('ant@example.com'))
    None

You can also remove lists quietly.
::

    >>> create_list('ant@example.com')
    <mailing list "ant@example.com" at ...>

    >>> command('mailman remove ant@example.com --quiet')

    >>> print(list_manager.get('ant@example.com'))
    None
