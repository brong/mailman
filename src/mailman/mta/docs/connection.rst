===============
MTA connections
===============

Outgoing mail connections are made using the ubiquitous SMTP protocol
(specified in `RFC 5321`_ and documented in many books.  Python has
`smtplib`_ to manage this process for us, but it is complicated by
multiple approaches to secure connections using TLS (Transport Layer
Security).  In *almost all* cases, Mailman will only be sending mail
to the local MTA, so you'll configure security once through a handful
of configuration settings, and that's that.  If you have trouble,
check out the `details`__, and then call `Ghostbusters`_ (us).

__ #ssl-and-tls-and-submissions-oh-my
.. _`RFC 5321`: https://datatracker.ietf.org/doc/html/rfc5321
.. _Ghostbusters: mailto:mailman-users@mailman3.org
.. _smtplib: https://docs.python.org/3/library/smtplib.html

With the exception of the parameters specified here, this class uses
the defaults provided by your version of the Python 'ssl' module.
This is **a bad idea** (*i.e.*, it might be OK but we haven't verified
it to be so), but we don't have the expertise to do better at this
time.  If you are using secure mode, **please** use the most recent
available version of 'ssl', which will have appropriate (strict and
secure) defaults.

Outgoing connections to the outgoing mail transport agent (MTA) are
managed by the ``Connection`` class.  Each instance can transparently
manage multiple sessions in a single connection.

    >>> from mailman.mta.connection import Connection, as_SecureMode
    >>> from lazr.config import as_boolean

When a ``Connection`` object is created, the host (default localhost)
and port number of the SMTP server, as well as the maximum number of
sessions to be performed in a connection must be specified.  An
unlimited number of sessions per connection is specified as 0.

    >>> from mailman.config import config
    >>> connection = Connection(
    ...     config.mta.smtp_host, int(config.mta.smtp_port), 0)

At the start, there have been no connections to the server.

    >>> smtpd.get_connection_count()
    0

By sending a message to the server, a connection is opened.
::

    >>> connection.sendmail('anne@example.com', ['bart@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)
    {}

The return value indicates that all recipients were accepted, and the
server has accepted responsibility for the message.  If some had been
refused, the dict would be nonempty.  The connection remains open::

    >>> smtpd.get_connection_count()
    1

We can reset the connection count back to zero.
::

    >>> from smtplib import SMTP
    >>> def reset():
    ...     smtp = SMTP()
    ...     smtp.connect(config.mta.smtp_host, int(config.mta.smtp_port))
    ...     smtp.docmd('RSET')

    >>> reset()
    >>> smtpd.get_connection_count()
    0

    >>> connection.quit()

.. #### The interaction above makes no sense.  Shouldn't we just
   quit(), and then checking the connection count would return 0?

Secure installations
====================

In the current environment, even from localhost most sysadmins will
want authenticated connections, and even from the local network,
secure (i.e., encrypted) connections.  These are controlled by
optional arguments to the Connection constructor.

If an SMTP user name and password are provided in the configuration
file, Mailman will authenticate with the mail server after each new
connection.  If either is omitted, no authentication will be attempted.
::

    >>> config.push('auth', """
    ... [mta]
    ... smtp_user: testuser
    ... smtp_pass: testpass
    ... """)

    >>> connection = Connection(
    ...     config.mta.smtp_host, int(config.mta.smtp_port), 0,
    ...     config.mta.smtp_user, config.mta.smtp_pass)
    >>> connection.sendmail('anne@example.com', ['bart@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)
    {}
    >>> print(smtpd.get_authentication_credentials())
    AHRlc3R1c2VyAHRlc3RwYXNz

    >>> reset()
    >>> config.pop('auth')

SMTPS and STARTTLS support are invoked by providing a non-default
value for smtp_secure_mode.  SecureMode.IMPLICIT invokes SMTPS (i.e.,
TLS negotiation is performed before opening the SMTP connection), and
SecureMode.STARTTLS invokes STARTTLS immediately after making the SMTP
connection, switching the channel from clear to encrypted.

The verify_cert and verify_hostname arguments control whether the
``ssl`` module will validate the server's X.509 certificate and
ensure that the certificate hostname is identical to the hostname
expected by Mailman.  These default to True, and setting them to False
is strongly discouraged: fix the MTA host!  (They will be ignored if
TLS is not used, i.e., secure_mode is INSECURE. verify_hostname will be ignored
unless verify_cert is true.)
::

    >>> connection = Connection(
    ...     config.mta.smtp_host, int(config.mta.smtp_port), 0,
    ...     config.mta.smtp_user, config.mta.smtp_pass,
    ...	    as_SecureMode(config.mta.smtp_secure_mode),
    ...     as_boolean(config.mta.smtp_verify_cert),
    ...     as_boolean(config.mta.smtp_verify_hostname))


Sessions per connection
=======================

Let's say we specify a maximum number of sessions per connection of 2.  When
the third message is sent, the connection is torn down and a new one is
created.

The connection count starts at zero.
::

    >>> connection = Connection(
    ...     config.mta.smtp_host, int(config.mta.smtp_port), 2)

    >>> smtpd.get_connection_count()
    0

We send two messages through the ``Connection`` object.  Only one connection
is opened.
::

    >>> connection.sendmail('anne@example.com', ['bart@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)
    {}

    >>> smtpd.get_connection_count()
    1

    >>> connection.sendmail('anne@example.com', ['bart@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)
    {}

    >>> smtpd.get_connection_count()
    1

The third message would cause a third session, exceeding the maximum.  So the
current connection is closed and a new one opened.
::

    >>> connection.sendmail('anne@example.com', ['bart@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)
    {}

    >>> smtpd.get_connection_count()
    2

A fourth message does not cause a new connection to be made.
::

    >>> connection.sendmail('anne@example.com', ['bart@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)
    {}

    >>> smtpd.get_connection_count()
    2

But a fifth one does.
::

    >>> connection.sendmail('anne@example.com', ['bart@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)
    {}

    >>> smtpd.get_connection_count()
    3


No maximum
==========

A value of zero means that there is an unlimited number of sessions per
connection.

    >>> connection = Connection(
    ...     config.mta.smtp_host, int(config.mta.smtp_port), 0)
    >>> reset()

Even after ten messages are sent, there's still been only one connection to
the server.
::

    >>> connection.debug = True
    >>> for i in range(10):
    ...     # Ignore the results.
    ...     results = connection.sendmail(
    ...         'anne@example.com', ['bart@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)

    >>> smtpd.get_connection_count()
    1


Development mode
================

By putting Mailman into development mode, you can force the recipients to a
given hard-coded address.  This allows you to test Mailman without worrying
about accidental deliveries to unintended recipients.
::

    >>> config.push('devmode', """
    ... [devmode]
    ... enabled: yes
    ... recipient: zperson@example.com
    ... """)

    >>> smtpd.clear()
    >>> connection.sendmail(
    ...     'anne@example.com',
    ...     ['bart@example.com', 'cate@example.com'], """\
    ... From: anne@example.com
    ... To: bart@example.com
    ... Subject: aardvarks
    ...
    ... """)
    {}

    >>> messages = list(smtpd.messages)
    >>> len(messages)
    1
    >>> print(messages[0].as_string())
    From: anne@example.com
    To: bart@example.com
    Subject: aardvarks
    X-Peer: ...
    X-MailFrom: anne@example.com
    X-RcptTo: zperson@example.com, zperson@example.com
    <BLANKLINE>
    <BLANKLINE>

    >>> config.pop('devmode')

SSL and TLS and submissions, oh my!
===================================

Feel free to call `Ghostbusters`_ (the Mailman 3 community) if you
need help configuring your Mailman to speak securely with your MTA.  I
write that *first* because I really mean it.  You *may* read the
following if you want to know a little bit of the jargon and
complexity, which *may* be useful in mediating between Mailman people
and your postmaster.  Then again, it may not: this stuff is *complex*
and *confused* (I mean, the Internet doesn't yet have a consistent
approach).  Speaking for myself, I'd want to know, but I understand is
this is something you're afraid to ask.

Here we go!

TLS and SSL (Secure Socket Layer) are interchangeable as generic
descriptions, but the specific versions of the protocol labeled SSL,
SSL2, and SSL3 are now strongly deprecated, and recent versions of
Python's ssl module don't allow them to be used.  Early versions of
TLS are in the process of similarly being deprecated, as sufficient
number of servers are configured to use the recent, more secure,
versions.

The TLS protocol is implemented along with X.509 authentication in the
Python `ssl`_ module.  For the curious, TLS is sufficiently
complicated that it requires literally dozens of RFCs and other
specifications such as X.509 for authentication.  `RFC 5246`__
contains the TLS specification (not directly relevant to Mailman
development).

__ https://datatracker.ietf.org/doc/html/rfc5246
.. _ssl: https://docs.python.org/3/library/ssl.html

Connections come in three flavors, informally described as "SMTP",
denoted by INSECURE, "SMTPS" or "secure submission", denoted by SMTPS
(specified in `RFC 6409`_), and "opportunistically secure", denoted by
STARTTLS (specified in `RFC 3207`_).  The situation is extremely
confused, and I will describe here the current recommended approach
according to `RFC 8314`_, which also contains a *long* bibliography.

.. _`RFC 6409`: https://datatracker.ietf.org/doc/html/rfc6409
.. _`RFC 3207`: https://datatracker.ietf.org/doc/html/rfc3207
.. _`RFC 8314`: https://datatracker.ietf.org/doc/html/rfc8314

The SMTP flavor doesn't use TLS at all, and any agent with access to
the connection can read the contents of the messages flowing through,
as well as any authentication data that may be passed.  The remote
server listens on some pre-agreed port (invariably 25, as defined in
the /etc/services file on Unix-style platforms), responds to
connections with a greeting, and the client may immediately begin
issuing SMTP protocol commands.

In the SMTPS flavor, the remote server listens on a pre-agreed port
(usually 465, aliased to submissions in /etc/services; this port is
also registered to the ssmtp or smtps services, but these are
deprecated).  It issues a greeting, optionally authenticates itself
through the TLS protocol, optionally authenticates the client,
establishes a secure (encrypted) channel, and then issues the SMTP
greeting, and the client can start issuing SMTP protocol commands.

In the STARTTLS flavor, the remote server may listen on the same port
that it uses for SMTP, and initiate a TLS connection when the client
issues a STARTTLS command at some point, continuing the SMTP
conversation over TLS (that's why this is called "opportunistic".
Alternatively, the remote server may listen on a different port
(usually 587, registered as service submission -- note, this is
different from "submissions" with an "s", registered to 465!)
Typically such servers refuse message submission if TLS negotiation
has not been completed.

As of this writing, Mailman only allows configuration of TLS options
for one MTA, which handles all outgoing mail.  No more should be
needed, but let `us`__ know if you think you do.

__ `mailto:mailman-users@mailman3.org`

