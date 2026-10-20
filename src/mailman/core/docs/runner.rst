=======
Runners
=======

The *runners* are the processes that perform long-running tasks, such as
moving messages around the Mailman queues.  Some runners don't manage queues,
such as the LMTP and REST API handling runners.  Each runner that manages a
queue directory though, is responsible for a slice of the hash space.  It
processes all the files in its slice, sleeps a little while, then wakes up and
runs through its queue files again.


Basic architecture
==================

The basic architecture of runner is implemented in the base class that all
runners inherit from.  This base class implements a ``.run()`` method that
runs continuously in a loop until the ``.stop()`` method is called.

    >>> from mailman.app.lifecycle import create_list
    >>> mlist = create_list('test@example.com')

Here is a very simple derived runner class.  Runners use a configuration
section in the configuration files to determine run characteristics, such as
the queue directory to use.  Here we push a configuration section for the test
runner.
::

    >>> from mailman.config import config   
    >>> config.push('test-runner', """
    ... [runner.test]
    ... max_restarts: 1
    ... """)

    >>> from mailman.core.runner import Runner
    >>> class TestableRunner(Runner):
    ...     def _dispose(self, mlist, msg, msgdata):
    ...         self.msg = msg
    ...         self.msgdata = msgdata
    ...         return False
    ...
    ...     def _do_periodic(self):
    ...         self.stop()
    ...
    ...     def _snooze(self, filecnt):
    ...         return

    >>> runner = TestableRunner('test')

This runner doesn't do much except run once, storing the message and metadata
on instance variables.

    >>> from mailman.testing.helpers import (specialized_message_from_string
    ...   as message_from_string)
    >>> msg = message_from_string("""\
    ... From: aperson@example.com
    ... To: test@example.com
    ...
    ... A test message.
    ... """)
    >>> switchboard = config.switchboards['test']
    >>> filebase = switchboard.enqueue(msg, listid=mlist.list_id,
    ...                                foo='yes', bar='no')
    >>> runner.run()
    >>> print(runner.msg.as_string())
    From: aperson@example.com
    To: test@example.com
    <BLANKLINE>
    A test message.
    <BLANKLINE>
    >>> from mailman.testing.documentation import dump_msgdata    
    >>> dump_msgdata(runner.msgdata)
    _parsemsg: False
    bar      : no
    foo      : yes
    lang     : en
    listid   : test.example.com
    version  : 3

XXX More of the Runner API should be tested.

..
    Clean up.
    >>> config.pop('test-runner')


Resetting database sessions on SIGHUP
=====================================

By default, SIGHUP only reopens the base runner's log files.  For integration
tests that reset the database while runners remain running, enable session
reset in the test configuration file using ``mailman.cfg`` syntax::

    [runner.in]
    reset_db_session_on_sighup: yes

The default is ``no``.  This configuration is read at startup; SIGHUP does
not reload configuration.  The OS signal handler only queues the signal.
During normal queue processing, the runner handles queued signals after
the current message transaction has committed or rolled back; signals can
also be handled while idle.  Periodic work in the runners included with
Mailman commits its own changes, so session reset needs no additional commit
or rollback.  Any changes still uncommitted at this point, for example by a
custom runner's ``_do_periodic()``, are discarded (rolled back) when the
session is closed.  Its purpose
is to discard ORM identities that can survive commit, not to interrupt a
message transaction.  Normal log reopening is preserved.  After successful
session closure the runner logs
``in runner database session reset after SIGHUP.`` (with its runner name).

Session closure detaches ORM objects and releases connections.  It does not
delete database records, restart the runner, or reset other threads' or
processes' sessions.  Enable this option only in test configurations.  The
default preserves existing SIGHUP behavior and compatibility with code that
retains attached ORM objects between messages.  Code using this option must
not rely on ORM objects remaining attached across SIGHUP.
This also applies to SIGHUP sent for log rotation, not just test resets.

This option applies only to the base runner's signal processing.  It does
not reset the LMTP controller thread's session or Gunicorn REST workers.
In particular, forwarding SIGHUP through the master still invokes Gunicorn's
normal worker reload behavior.  Tests needing only the incoming session reset
should signal the incoming runner directly.

Although session closure happens at a safe point in the runner, signal
delivery is asynchronous and does not pause message processing.
Tests must stop submitting work, drain the queue, send SIGHUP, and wait for
session closure to complete before resetting shared data.  A successful
``kill()`` or the earlier log-reopening message is not a completion
acknowledgement.  This is not a coordinated, all-runner database reset API.
