========================
The mailing list manager
========================

The ``IListManager`` is how you create, delete, and retrieve mailing list
objects.

    >>> from mailman.interfaces.listmanager import IListManager
    >>> from zope.component import getUtility
    >>> list_manager = getUtility(IListManager)


Creating a mailing list
=======================

Creating the list returns the newly created IMailList object.

    >>> from mailman.interfaces.mailinglist import IMailingList
    >>> mlist = list_manager.create('ant@example.com')
    >>> IMailingList.providedBy(mlist)
    True

All lists with identities have a short name, a host name, a fully qualified
listname, and an `RFC 2369`_ list id.  This latter will not change even if the
mailing list moves to a different host, so it is what uniquely distinguishes
the mailing list to the system.

    >>> print(mlist.list_name)
    ant
    >>> print(mlist.mail_host)
    example.com
    >>> print(mlist.fqdn_listname)
    ant@example.com
    >>> print(mlist.list_id)
    ant.example.com


Deleting a mailing list
=======================

Use the list manager to delete a mailing list.

    >>> list_manager.delete(mlist)
    >>> sorted(list_manager.names)
    []

After deleting the list, you can create it again.

    >>> mlist = list_manager.create('ant@example.com')
    >>> print(mlist.fqdn_listname)
    ant@example.com


Retrieving a mailing list
=========================

When a mailing list exists, you can ask the list manager for it and you will
always get the same object back.

    >>> list_manager.get('ant@example.com')
    <mailing list "ant@example.com" at ...>

The ``.get()`` method is ambidextrous, so it also accepts ``List-ID``'s.

    >>> list_manager.get('ant.example.com')
    <mailing list "ant@example.com" at ...>

You can get a mailing list specifically by its ``List-ID``.

    >>> list_manager.get_by_list_id('ant.example.com')
    <mailing list "ant@example.com" at ...>

And you can get a mailing list specifically by its fully-qualified list name.

    >>> list_manager.get_by_fqdn('ant@example.com')
    <mailing list "ant@example.com" at ...>

If you try to get a list that doesn't existing yet, you get ``None``.

    >>> print(list_manager.get('bee@example.com'))
    None
    >>> print(list_manager.get_by_list_id('bee.example.com'))
    None

You also get ``None`` if the list name is invalid.

    >>> print(list_manager.get('foo'))
    None


Iterating over all mailing lists
================================

Once you've created a bunch of mailing lists, you can use the list manager to
iterate over the mailing list objects, the list posting addresses, or the list
address components.
::

    >>> mlist_3 = list_manager.create('cat@example.com')
    >>> mlist_4 = list_manager.create('dog@example.com')

    >>> for name in sorted(list_manager.names):
    ...     print(name)
    ant@example.com
    cat@example.com
    dog@example.com

    >>> for list_id in sorted(list_manager.list_ids):
    ...     print(list_id)
    ant.example.com
    cat.example.com
    dog.example.com

    >>> for fqdn_listname in sorted(m.fqdn_listname
    ...                             for m in list_manager.mailing_lists):
    ...     print(fqdn_listname)
    ant@example.com
    cat@example.com
    dog@example.com

    >>> for list_name, mail_host in sorted(list_manager.name_components):
    ...     print(list_name, '@', mail_host)
    ant @ example.com
    cat @ example.com
    dog @ example.com


.. _`RFC 2369`: https://datatracker.ietf.org/doc/html/rfc2369
