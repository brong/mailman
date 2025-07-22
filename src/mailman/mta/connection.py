# Copyright (C) 2009-2025 by the Free Software Foundation, Inc.
#
# This file is part of GNU Mailman.
#
# GNU Mailman is free software: you can redistribute it and/or modify it under
# the terms of the GNU General Public License as published by the Free
# Software Foundation, either version 3 of the License, or (at your option)
# any later version.
#
# GNU Mailman is distributed in the hope that it will be useful, but WITHOUT
# ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or
# FITNESS FOR A PARTICULAR PURPOSE.  See the GNU General Public License for
# more details.
#
# You should have received a copy of the GNU General Public License along with
# GNU Mailman.  If not, see <https://www.gnu.org/licenses/>.

"""MTA connections."""

import ssl
import enum
import socket
import logging
import smtplib

from contextlib import suppress
from email.message import Message
from lazr.config import as_boolean
from mailman.config import config
from mailman.interfaces.configuration import InvalidConfigurationError
from public import public


log = logging.getLogger('mailman.smtp')


@public
class SecureMode(enum.Enum):
    INSECURE = 'smtp'
    IMPLICIT = 'smtps'
    STARTTLS = 'starttls'
    # STARTTLS can be invoked prior to any message submission, but we don't do
    # that -- we invoke immediately.


@public
def as_SecureMode(s):
    """
    Convert a string to an enum value.  Accepts any case.
    """
    s = s.lower()

    try:
        return SecureMode(s)
    except ValueError:
        raise InvalidConfigurationError('smtp_secure_mode', repr(s))


class Connection:
    """Manage a connection to the SMTP server."""
    def __init__(self, host, port, sessions_per_connection,
                 smtp_user=None, smtp_pass=None,
                 secure_mode=SecureMode.INSECURE,
                 verify_cert=True, verify_hostname=True):
        """Create a connection manager.

        :param host: The host name of the SMTP server to connect to.
        :type host: string
        :param port: The port number of the SMTP server to connect to.
        :type port: integer
        :param sessions_per_connection: The number of SMTP sessions per
            connection to the SMTP server.  After this number of sessions
            has been reached, the connection is closed and a new one is
            opened.  Set to zero for an unlimited number of sessions per
            connection (i.e. your MTA has no limit).
        :type sessions_per_connection: integer
        :param smtp_user: Optional SMTP authentication user name.  If given,
            `smtp_pass` must also be given.
        :type smtp_user: str
        :param smtp_pass: Optional SMTP authentication password.  If given,
            `smtp_user` must also be given.
        :type smtp_pass: str
        :param secure_mode: Whether to use implicit TLS (SMTPS), STARTTLS, or
            an insecure connection.
        :type secure_mode: SecureMode
        :param verify_cert: Whether to require a server cert and verify it.
            Verification in this context means that the server needs to supply
            a valid certificate signed by a CA from a set of the system's
            default CA certs.
        :type verify_cert: bool
        :param verify_hostname: Whether to check that the server certificate
            specifies the hostname as passed to this constructor.
            RFC 2818 and RFC 6125 rules are followed.
        :type verify_hostname: bool

        With the exception of the parameters specified here, this class
        uses the defaults provided by your version of the Python 'ssl'
        module.

        If either of smtp_pass or smtp_user is omitted, the other will
        be ignored.  If secure_mode is INSECURE, verify_hostname and
        verify_cert will be ignored. If secure_mode is not INSECURE,
        verify_hostname will be ignored unless verify_cert is true.
        """
        self._host = host
        self._port = port
        self._sessions_per_connection = sessions_per_connection
        self.secure_mode = secure_mode
        self.verify_cert = verify_cert
        self.verify_hostname = verify_hostname and verify_cert
        self._username = smtp_user
        self._password = smtp_pass

        self._session_count = None
        self._connection = None
        if self.secure_mode == SecureMode.INSECURE:
            self._tls_context = None
        else:
            self._tls_context = self._get_tls_context(self.verify_cert,
                                                      self.verify_hostname)

    def sendmail(self, envsender, recipients, msg):
        """Mimic `smtplib.SMTP.sendmail`."""
        if as_boolean(config.devmode.enabled):
            # Force the recipients to the specified address, but still deliver
            # to the same number of recipients.
            recipients = [config.devmode.recipient] * len(recipients)
        if self._connection is None:
            self._connect()
            self._login()
        # We accept a string, bytes or a Message object for the msg argument.
        # A string is converted to ascii bytes with errors replaced and
        # passed to smtplib.SMTP.sendmail. Bytes are passed as is and a
        # Message is passed to smtplib.SMTP.send_message. Passing a Message
        # object is preferred because of the treatment of line endings.
        if isinstance(msg, str):
            msg = msg.encode('ascii', 'replace')
        assert isinstance(msg, bytes) or isinstance(msg, Message), \
            'Connection.sendmail received an invalid msg arg.'
        try:
            log.debug('envsender: %s, recipients: %s, size(msg): %s',
                      envsender, recipients, len(msg))
            if isinstance(msg, Message):
                results = self._connection.send_message(msg, envsender,
                                                        recipients)
            else:
                results = self._connection.sendmail(envsender, recipients, msg)
        except smtplib.SMTPException:
            # For safety, close this connection.  The next send attempt will
            # automatically re-open it.  Pass the exception on up.
            self.quit()
            raise
        # This session has been successfully completed.
        self._session_count -= 1
        # By testing exactly for equality to 0, we automatically handle the
        # case for SMTP_MAX_SESSIONS_PER_CONNECTION <= 0 meaning never close
        # the connection.  We won't worry about wraparound <wink>.
        if self._session_count == 0:
            self.quit()
        return results

    def quit(self):
        """Mimic `smtplib.SMTP.quit`."""
        if self._connection is None:
            return
        with suppress(smtplib.SMTPException):
            self._connection.quit()
        self._connection = None

    def _connect(self):
        """Open a new connection."""
        try:
            if self.secure_mode == SecureMode.IMPLICIT:
                log.debug('Connecting to %s:%s with implicit TLS',
                          self._host, self._port)
                self._connection = smtplib.SMTP_SSL(self._host, self._port,
                                                    context=self._tls_context)
            else:
                log.debug('Connecting to %s:%s', self._host, self._port)
                self._connection = smtplib.SMTP(self._host, self._port)
                if self.secure_mode == SecureMode.STARTTLS:
                    log.debug('Starttls')
                    self._connection.starttls(context=self._tls_context)
        except (socket.error, IOError, smtplib.SMTPException):
            self.quit()
            raise
        except Exception as error:
            # This exception is kept here intentionally to make sure we log
            # only when an exception other than 3 caught above happens and
            # can't be handled.
            # If ANYTHING fails here, after ensuring
            # connection is closed, we'll let the exception bubble up so a
            # message in process will be shunted.
            log.error('while connecting to SMTP: ' + str(error))
            self.quit()
            raise
        self._session_count = self._sessions_per_connection

    def _login(self):
        """Send login if both username and password are specified."""
        if self._username is not None and self._password is not None:
            log.debug('logging in')
            try:
                self._connection.login(self._username, self._password)
            except smtplib.SMTPException:
                # Ensure connection is closed and pass to BaseDelivery.
                self.quit()
                raise

    def _get_tls_context(self, verify_cert, verify_hostname):
        """Create and return a new SSLContext."""
        ssl_context = ssl.create_default_context()
        ssl_context.check_hostname = verify_hostname
        if verify_cert:
            ssl_context.verify_mode = ssl.CERT_REQUIRED
        else:
            ssl_context.verify_mode = ssl.CERT_NONE
        return ssl_context
