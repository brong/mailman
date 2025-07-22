==================================
 Installing and running Mailman 3
==================================

Copyright (C) 2008-2025 by the Free Software Foundation, Inc.


Requirements
============

For the Core, Python 3.7 or newer is required.  It can either be the default
'python3' on your ``$PATH`` or it can be accessible via the ``python3.6`` or
``python3.7`` binary.  If your operating system does not include Python 3, see
https://www.python.org for information about downloading installers (where
available) and installing it from source (when necessary or preferred).
Python 2 is not supported by the Core.

You may need some additional dependencies, which are either available from
your OS vendor, or can be downloaded automatically from the `Python
Cheeseshop`_.


Documentation
=============

The documentation for Mailman 3 is distributed throughout the sources.  The
core documentation (such as this file) is found in the ``src/mailman/docs``
directory, but much of the documentation is in module-specific places.  Online
version of the `Mailman 3 Core documentation`_ is available.

Also helpful might be Mark Sapiro's documentation on `building out the
mailman3.org`_ server.


Get the sources
===============

The Mailman 3 source code is version controlled using Git. You can get a
local copy by running this command::

    $ git clone https://gitlab.com/mailman/mailman.git

or if you have a GitLab account and prefer ssh::

    $ git clone git@gitlab.com:mailman/mailman.git


Running Mailman 3
=================

.. ATTENTION::
   The Mailman command line interface requires a properly configured UTF-8
   locale.  This is because the Core is implemented in `Python 3 and uses the
   click`_ command line argument parsing library.  Generally this will not be
   an issue since your shell is probably set up correctly.  However, in
   certain environments such as init scripts and cron scripts, your locale may
   not be UTF-8 compatible.  This can cause Mailman to mysteriously fail to
   run (since often, proper logging may also not be set up).  If you're seeing
   weird behavior in these types of environments, be sure they are UTF-8
   compatible, e.g. by setting ``export LANG=en_US.UTF-8``.

You will need to set up a configuration file to override the defaults and set
things up for your environment.  Mailman is configured using an "ini"-style
configuration system.  Usually this means creating a ``mailman.cfg`` file and
putting it in a standard search location.  See the :ref:`configuration
<configuration>` documentation for details.

By default, all runtime files are put under a ``var`` directory in the current
working directory.

Run the ``mailman info`` command to see which configuration file Mailman is
using, and where it will put its database file.  The first time you run this,
Mailman will also create any necessary run-time directories and log files.

Try ``mailman --help`` for more details.  You can use the commands
``mailman start`` to start the runner subprocess daemons, and of course
``mailman stop`` to stop them.

Note that you can also run Mailman from one of the virtual environments
created by tox, e.g.::

    $ tox -e py39-nocov --notest -r
    $ .tox/py39-nocov/bin/mailman info


Mailman Shell
=============

This documentation has examples which use the Mailman shell to interact with
Mailman.  To start the shell type ``mailman shell`` in your terminal.

There are some testings functions which need to be imported first before you
use them.  They can be imported from the modules available in
``mailman.testing``.  For example, to use ``dump_list`` you first need to
import it from the ``mailman.testing.documentation`` module.

.. Of course, *this* doctest doesn't have these preloaded...
   >>> from zope.component import getUtility
   >>> from mailman.interfaces.listmanager import IListManager

The shell automatically initializes the Mailman system, loads all the
available interfaces, and configures the `Zope Component Architecture`_ (ZCA)
which is used to access all the software components in Mailman.  So for
example, if you wanted to get access to the list manager component, you could
do::

    $ mailman shell
    Welcome to the GNU Mailman shell
    Use commit() to commit changes.
    Use abort() to discard changes since the last commit.
    Exit with ctrl+D does an implicit commit() but exit() does not.

    >>> list_manager = getUtility(IListManager)


.. _`Python Cheeseshop`: https://pypi.org/
.. _`Mailman 3 Core documentation`: https://mailman.readthedocs.io
.. _`Zope Component Architecture`: https://pypi.python.org/pypi/zope.component
.. _`building out the mailman3.org`: https://wiki.list.org/DOC/Mailman%203%20installation%20experience
.. _`Python 3 and uses the click`: https://click.palletsprojects.com/en/7.x/python3/
