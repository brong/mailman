============================
Display configuration values
============================

Just like the `Postfix command postconf(1)`_, the ``mailman conf`` command
lets you dump one or more Mailman configuration variables to standard output
or a file.

Mailman's configuration is divided in multiple sections which contain multiple
key-value pairs.  The ``mailman conf`` command allows you to display a
specific key-value pair, or several key-value pairs.

    >>> from mailman.testing.documentation import cli
    >>> command = cli('mailman.commands.cli_conf.conf')

To get a list of all key-value pairs of any section, you need to call the
command without any options.

    >>> command('mailman conf')
    [ARC] authserv_id:
    ...
    [logging.bounce] level: info
    ...
    [mailman] site_owner: noreply@example.com
    ...

You can list all the key-value pairs of a specific section.

    >>> command('mailman conf --section shell')
    [shell] banner: Welcome to the GNU Mailman shell
    Use commit() to commit changes.
    Use abort() to discard changes since the last commit.
    Exit with ctrl+D does an implicit commit() but exit() does not.
    [shell] history_file:
    [shell] prompt: >>>
    [shell] use_ipython: no

You can also pass a key and display all key-value pairs matching the given
key, along with the names of the corresponding sections.

    >>> command('mailman conf --key path')
    [logging.archiver] path: mailman.log
    [logging.bounce] path: bounce.log
    [logging.config] path: mailman.log
    [logging.database] path: mailman.log
    [logging.debug] path: debug.log
    [logging.error] path: mailman.log
    [logging.fromusenet] path: mailman.log
    [logging.gunicorn] path: mailman.log
    [logging.http] path: mailman.log
    [logging.locks] path: mailman.log
    [logging.mischief] path: mailman.log
    [logging.plugins] path: plugins.log
    [logging.root] path: mailman.log
    [logging.runner] path: mailman.log
    [logging.smtp] path: smtp.log
    [logging.subscribe] path: mailman.log
    [logging.task] path: mailman.log
    [logging.vette] path: mailman.log
    [runner.archive] path: $QUEUE_DIR/$name
    [runner.bad] path: $QUEUE_DIR/$name
    [runner.bounces] path: $QUEUE_DIR/$name
    [runner.command] path: $QUEUE_DIR/$name
    [runner.digest] path: $QUEUE_DIR/$name
    [runner.in] path: $QUEUE_DIR/$name
    [runner.lmtp] path:
    [runner.nntp] path: $QUEUE_DIR/$name
    [runner.out] path: $QUEUE_DIR/$name
    [runner.pipeline] path: $QUEUE_DIR/$name
    [runner.rest] path:
    [runner.retry] path: $QUEUE_DIR/$name
    [runner.shunt] path: $QUEUE_DIR/$name
    [runner.task] path:
    [runner.virgin] path: $QUEUE_DIR/$name


If you specify both a section and a key, you will get the corresponding value.

    >>> command('mailman conf --section mailman --key site_owner')
    noreply@example.com


.. _`Postfix command postconf(1)`: https://www.postfix.org/postconf.1.html
