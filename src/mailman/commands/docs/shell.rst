==========================
Operating on mailing lists
==========================

The ``shell`` (alias: ``withlist``) command is a pretty powerful way to
operate on mailing lists from the command line.  This command allows you to
interact with a list at a Python prompt, or process one or more mailing lists
through custom made Python functions.


Getting detailed help
=====================

Because ``shell`` is so complex, you might want to read the detailed help.
::

    >>> from mailman.testing.documentation import cli   
    >>> command = cli('mailman.commands.cli_withlist.shell')

    >>> command('mailman shell --details')
    This script provides you with a general framework for interacting with a
    mailing list.
    ...


Running a function
==================

By putting a Python function somewhere on your ``sys.path``, you can have
``shell`` call that function on a given mailing list.

    >>> from mailman.config import config
    >>> import os, sys
    >>> old_path = sys.path[:]
    >>> sys.path.insert(0, config.VAR_DIR)

.. cleanup
    >>> ignore = cleanups.callback(setattr, sys, 'path', old_path)

The function takes at least a single argument, the mailing list.
::

    >>> with open(os.path.join(config.VAR_DIR, 'showme.py'), 'w') as fp:
    ...     print("""\
    ... def showme(mlist):
    ...     print("The list's name is", mlist.fqdn_listname)
    ...
    ... def displayname(mlist):
    ...     print("The list's display name is", mlist.display_name)
    ...
    ... def changeme(mlist, display_name):
    ...     mlist.display_name = display_name
    ... """, file=fp)

If the name of the function is the same as the module, then you only need to
name the function once.

    >>> from mailman.app.lifecycle import create_list
    >>> mlist = create_list('ant@example.com')
    >>> command('mailman shell -l ant@example.com --run showme')
    The list's name is ant@example.com

The function's name can also be different than the modules name.  In that
case, just give the full module path name to the function you want to call.

    >>> command('mailman shell -l ant@example.com --run showme.displayname')
    The list's display name is Ant


Passing arguments
=================

Your function can also accept an arbitrary number of arguments.  Every command
line argument after the callable name is passed as a positional argument to
the function.  For example, to change the mailing list's display name, you can
do this::

    >>> command('mailman shell -l ant@example.com --run showme.changeme ANT!')
    >>> print(mlist.display_name)
    ANT!


Multiple lists
==============

You can run a command over more than one list by using a regular expression in
the ``listname`` argument.  To indicate a regular expression is used, the
string must start with a caret.
::

    >>> mlist_2 = create_list('badger@example.com')
    >>> mlist_3 = create_list('badboys@example.com')

    >>> command('mailman shell --run showme.displayname -l ^.*example.com')
    The list's display name is ANT!
    The list's display name is Badboys
    The list's display name is Badger

    >>> command('mailman shell --run showme.displayname -l ^bad.*')
    The list's display name is Badboys
    The list's display name is Badger

    >>> command('mailman shell --run showme.displayname -l ^foo')


Interactive use
===============

You can also get an interactive prompt which allows you to inspect a live
Mailman system directly.  Through the ``mailman.cfg`` file, you can set the
prompt and banner, and you can choose between the standard Python REPL_ or
IPython.

If the `GNU readline`_ library is available, it will be enabled automatically,
giving you command line editing and other features.  You can also set the
``[shell]history_file`` variable in the ``mailman.cfg`` file and when the
normal Python REPL is used, your interactive commands will be written to and
read from this file.

Note that the ``$PYTHONSTARTUP`` environment variable will also be honored if
set, and any file named by this variable will be read at start up time.  It's
common practice to *also* enable GNU readline history in a ``$PYTHONSTARTUP``
file and if you do this, be aware that it will interact badly with
``[shell]history_file``, causing your history to be written twice.  To disable
this when using the interactive ``shell`` command, do something like::

    $ PYTHONSTARTUP= mailman shell

to temporarily unset the environment variable.


IPython
-------

You can use IPython_ as the interactive shell by setting the
``[shell]use_ipython`` variables in your `mailman.cfg` file to ``yes``.
IPython must be installed and available on your system

When using IPython, the ``[shell]history_file`` is not used.


.. _IPython: https://ipython.org/
.. _REPL: https://en.wikipedia.org/wiki/REPL
.. _`GNU readline`: https://docs.python.org/3/library/readline.html
