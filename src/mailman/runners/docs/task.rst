===============
The Task runner
===============

Mailman has a Task runner that executes periodic tasks.


Configuration
=============

By default, the Task runner executes tasks once per hour to evict expired
pending requests, bounce events and cache entries and to delete any saved
workflow states and saved message store messages orphaned by deleting the
associated pending request.  This timing is configurable in the ``[mailman]``
section of the configuration.::

    # mailman.cfg
    [mailman]
    run_tasks_every: 1d

This will run the tasks once per day instead of once per hour.


Logging
=======

The Task runner writes some simple log messages to a new task log.

    >>> import doctest
    >>> doctest.ELLIPSIS_MARKER = 'xxx'
    >>> from mailman.testing import helpers
    >>> from mailman.config import config
    >>> from mailman.runners.task import TaskRunner
    >>> mark = helpers.LogFileMark('mailman.task')
    >>> runner = helpers.make_testable_runner(TaskRunner)
    >>> runner.run()
    >>> print(mark.read())
    xxx Task runner evicted 0 expired pendings
    xxx Task runner deleted 0 orphaned workflows
    xxx Task runner deleted 0 orphaned requests
    xxx Task runner deleted 0 orphaned messages
    xxx Task runner evicted 0 expired bounce events
    xxx Task runner evicted expired cache entries
    <BLANKLINE>
    >>> doctest.ELLIPSIS_MARKER = '...'

