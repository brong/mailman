=========
 Plugins
=========

.. seealso:: See an `example Mailman plugin
             <https://gitlab.com/mailman/example-mailman-plugin>`_
             as a starting point for writing a new plugin.

Mailman defines a plugin as a Python package on ``sys.path`` that provides
components matching the ``IPlugin`` interface.  ``IPlugin`` implementations
can define a *pre-hook*, a *post-hook*, and a *REST resource*.  Plugins are
enabled by adding a section to your ``mailman.cfg`` file, such as:

.. literalinclude:: ../testing/hooks.cfg

.. note::
   Because of a `design limitation`_ in the underlying configuration library,
   you cannot name a plugin "master".  Specifically you cannot define a
   section in your ``mailman.cfg`` file named ``[plugin.master]``.

We have such a configuration file handy.

    >>> from importlib.resources import path
    >>> config_file = str(cleanups.enter_context(
    ...     path('mailman.plugins.testing', 'hooks.cfg')))

The section must at least define the class implementing the ``IPlugin``
interface, using a Python dotted-name import path.  For the import to work,
you must include the top-level directory on ``sys.path``.

    >>> import os
    >>> plugin_dir = str(cleanups.enter_context(
    ...     path('mailman.plugins', '__init__.py')))
    >>> plugin_path = os.path.join(os.path.dirname(plugin_dir), 'testing')


Hooks
=====

Plugins can add initialization hooks, which will be run at two stages in the
initialization process - one before the database is initialized and one after.
These correspond to methods the plugin defines, a ``pre_hook()`` method and a
``post_hook()`` method.  Each of these methods must be present, together with
the ``resource`` property, for the plugin to be operational.

Here is a plugin that defines these hooks:

.. literalinclude:: ../testing/example/hooks.py

To illustrate how the hooks work, we'll invoke a simple Mailman command to be
run in a subprocess.  The plugin itself supports debugging hooking invocation
when an environment variable is set.

    >>> from mailman.testing.documentation import run_mailman as run
    >>> proc = run(['-C', config_file, 'info'],
    ...            DEBUG_HOOKS='1',
    ...            PYTHONPATH=plugin_path)
    >>> print(proc.stdout)
    I'm in my pre-hook
    I'm in my post-hook
    ...


Components
==========

Plugins can also add components such as rules, chains, list styles, etc.  By
default, components are searched for in the package matching the plugin's
name.  So in the case above, the plugin is named ``example`` (because the
section is called ``[plugin.example]``, and there is a subpackage called
``rules`` under the ``example`` package.  The file system layout looks like
this::

    example/
        __init__.py
        hooks.py
        rules/
            __init__.py
            rules.py

And the contents of ``rules.py`` looks like:

.. literalinclude:: ../testing/example/rules/rules.py

To see that the plugin's rule get added, we invoke Mailman as an external
process, running a script that prints out all the defined rule names,
including our plugin's ``example-rule``.

    >>> proc = run(['-C', config_file, 'withlist', '-r', 'showrules'],
    ...            PYTHONPATH=plugin_path)
    >>> print(proc.stdout)
    administrivia
    ...
    example-rule
    ...

Component directories can live under any importable path, not just one named
after the plugin.  By adding a ``component_package`` section to your plugin's
configuration, you can name an alternative location to search for components.

.. literalinclude:: ../testing/alternate.cfg

We use this configuration file and the following file system layout::

    example/
        __init__.py
        hooks.py
    alternate/
        rules/
            __init__.py
            rules.py

Here, ``rules.py`` likes like:

.. literalinclude:: ../testing/alternate/rules/rules.py

You can see that this rule has a different name.  If we use the
``alternate.cfg`` configuration file from above::

    >>> config_file = str(cleanups.enter_context(path(
    ...     'mailman.plugins.testing', 'alternate.cfg')))

we'll pick up the alternate rule when we print them out.

    >>> proc = run(['-C', config_file, 'withlist', '-r', 'showrules'],
    ...            PYTHONPATH=plugin_path)
    >>> print(proc.stdout)
    administrivia
    alternate-rule
    ...


REST
====

Plugins can also supply REST routes.  Let's say we have a plugin defined like
so:

.. literalinclude:: ../testing/example/rest.py

which we can enable with the following configuration file:

.. literalinclude:: ../testing/rest.cfg

The plugin defines a ``resource`` attribute that exposes the root of the
plugin's resource tree.  The plugin will show up when we navigate to the
``plugin`` resource.
::

    >>> from mailman.testing.documentation import dump_json   
    >>> dump_json('http://localhost:9001/3.1/plugins')
    entry 0:
        class: example.rest.ExamplePlugin
        enabled: True
        http_etag: "..."
        name: example
    http_etag: "..."
    start: 0
    total_size: 1

The plugin may provide a ``GET`` on the resource itself.
::

    >>> dump_json('http://localhost:9001/3.1/plugins/example')
    http_etag: "..."
    my-child-resources: yes, no, echo
    my-name: example-plugin

And it may provide child resources.
::

    >>> dump_json('http://localhost:9001/3.1/plugins/example/yes')
    http_etag: "..."
    yes: True

Plugins and their child resources can support any HTTP method, such as
``GET``...
::

    >>> dump_json('http://localhost:9001/3.1/plugins/example/echo')
    http_etag: "..."
    number: 0

... or ``POST`` ...
::

    >>> dump_json('http://localhost:9001/3.1/plugins/example/echo',
    ...           dict(number=7))
    date: ...
    server: ...
    status: 204

    >>> dump_json('http://localhost:9001/3.1/plugins/example/echo')
    http_etag: "..."
    number: 7

... or ``DELETE``.
::

    >>> dump_json('http://localhost:9001/3.1/plugins/example/echo',
    ...           method='DELETE')
    date: ...
    server: ...
    status: 204

    >>> dump_json('http://localhost:9001/3.1/plugins/example/echo')
    http_etag: "..."
    number: 0

It's up to the plugin of course.


.. _`design limitation`: https://bugs.launchpad.net/lazr.config/+bug/310619
