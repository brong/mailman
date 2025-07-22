# Copyright (C) 2002-2025 by the Free Software Foundation, Inc.
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

"""The email commands 'join' and 'subscribe'."""

from email.header import decode_header, make_header
from email.utils import formataddr, parseaddr
from mailman.core.i18n import _
from mailman.interfaces.address import InvalidEmailAddressError
from mailman.interfaces.command import ContinueProcessing, IEmailCommand
from mailman.interfaces.member import (
    AlreadySubscribedError,
    DeliveryMode,
    MembershipIsBannedError,
)
from mailman.interfaces.subscriptions import (
    ISubscriptionManager,
    SubscriptionPendingError,
)
from mailman.interfaces.usermanager import IUserManager
from public import public
from zope.component import getUtility
from zope.interface import implementer


def match_subscriber(email, display_name):
    # Return something matching the email which should be used as the
    # subscriber by the ISubscriptionManager interface.
    manager = getUtility(IUserManager)
    # Is there a user with a preferred address matching the email?
    user = manager.get_user(email)
    if user is not None:
        preferred = user.preferred_address
        if preferred is not None and preferred.email == email.lower():
            return user
    # Is there an address matching the email?
    address = manager.get_address(email)
    if address is not None:
        return address
    # Make a new user and subscribe their first (and only) address.  We can't
    # make the first address their preferred address because it hasn't been
    # verified yet.
    user = manager.make_user(email, display_name)
    return list(user.addresses)[0]


@public
@implementer(IEmailCommand)
class Join:
    """The email 'join' command."""

    name = 'join'
    # XXX 2012-02-29 BAW: DeliveryMode.summary is not yet supported.
    argument_description = ('[digest=<no|mime|plain>] '
                            '[address=user@example.com]')
    description = _("""\
You will be asked to confirm your subscription request and you may be issued a
provisional password.

By using the 'digest' option, you can specify whether you want digest delivery
or not.  If not specified, the mailing list's default delivery mode will be
used.  You can use the 'address' option to request subscription of an address
other than the sender of the command.
""")
    short_description = _('Join this mailing list.')

    def process(self, mlist, msg, msgdata, arguments, results):
        """See `IEmailCommand`."""
        # Parse the arguments.
        delivery_mode, address = self._parse_arguments(arguments, results)
        if delivery_mode is ContinueProcessing.no:
            return ContinueProcessing.no
        if not address:
            # RFC 2047 decode the From: header.
            if msg['from'] is None:
                display_name, email = ('', '')
            else:
                # Parse before decoding in case decoded display_name contains
                # a comma.
                display_name, email = parseaddr(msg['from'])
                display_name = str(make_header(decode_header(display_name)))
        else:
            display_name, email = ('', address)
        # Address could be None or the empty string.
        if not email:
            email = msg.sender
        if not email:
            print(_('${self.name}: No valid address found to subscribe'),
                  file=results)
            return ContinueProcessing.no
        if isinstance(email, bytes):
            email = email.decode('ascii')
        # Have we already seen one join request from this user during the
        # processing of this email?
        joins = getattr(results, 'joins', set())
        if email in joins:
            # Do not register this join.
            return ContinueProcessing.yes
        joins.add(email)
        results.joins = joins
        person = formataddr((display_name, email))                # noqa: F841
        try:
            subscriber = match_subscriber(email, display_name)
        except InvalidEmailAddressError as e:
            print('Invalid email address: {}'.format(e), file=results)
            return ContinueProcessing.yes
        try:
            ISubscriptionManager(mlist).register(subscriber,
                                                 delivery_mode=delivery_mode)
        except (AlreadySubscribedError, InvalidEmailAddressError,
                MembershipIsBannedError) as e:
            print(str(e), file=results)
            results.send_response = True
        except SubscriptionPendingError:
            # SubscriptionPendingError doesn't return an error message.
            listname = mlist.fqdn_listname                        # noqa: F841
            print(_('${person} has a pending subscription for ${listname}'),
                  file=results)
            results.send_response = True
        else:
            print(_('Confirmation email sent to ${person}'), file=results)
        return ContinueProcessing.yes

    def _parse_arguments(self, arguments, results):
        """Parse command arguments.

        :param arguments: The sequences of arguments as given to the
            `process()` method.
        :param results: The results object.
        :return: A tuple (delivery mode, address) or
            (ContinueProcessing.no, None) on error.
        """
        mode = DeliveryMode.regular
        address = None
        for argument in arguments:
            parts = argument.split('=', 1)
            if len(parts) != 2 or parts[0] not in ('digest', 'address'):
                print(self.name, _('bad argument: ${argument}'),
                      file=results)
                return (ContinueProcessing.no, None)
            if parts[0] == 'digest':
                mode = {
                    'no': DeliveryMode.regular,
                    'plain': DeliveryMode.plaintext_digests,
                    'mime': DeliveryMode.mime_digests,
                    }.get(parts[1])
                if mode is None:
                    print(self.name, _('bad argument: ${argument}'),
                          file=results)
                    return (ContinueProcessing.no, None)
            if parts[0] == 'address':
                address = parts[1]
        return (mode, address)


@public
class Subscribe(Join):
    """The email 'subscribe' command (an alias for 'join')."""

    name = 'subscribe'
    description = _("An alias for 'join'.")
    short_description = description


@public
@implementer(IEmailCommand)
class Leave:
    """The email 'leave' command."""

    name = 'leave'
    argument_description = ''
    description = _("""Leave this mailing list.

You may be asked to confirm your request.""")
    short_description = _('Leave this mailing list.')

    def process(self, mlist, msg, msgdata, arguments, results):
        """See `IEmailCommand`."""
        email = msg.sender
        if not email:
            print(
                _('${self.name}: No valid email address found to unsubscribe'),
                file=results)
            return ContinueProcessing.no
        user_manager = getUtility(IUserManager)
        user = user_manager.get_user(email)
        if user is None:
            print(_('No registered user for email address: ${email}'),
                  file=results)
            results.send_response = True
            return ContinueProcessing.no
        # The address that the -leave command was sent from, must be verified.
        # Otherwise you could link a bogus address to anyone's account, and
        # then send a leave command from that address.
        if user_manager.get_address(email).verified_on is None:
            print(_('Invalid or unverified email address: ${email}'),
                  file=results)
            results.send_response = True
            return ContinueProcessing.no
        already_left = msgdata.setdefault('leaves', set())
        for user_address in user.addresses:
            # Only recognize verified addresses.
            if user_address.verified_on is None:
                continue
            member = mlist.members.get_member(user_address.email)
            if member is not None:
                break
        else:
            # There are two possible situations.  Either none of the user's
            # addresses are subscribed to this mailing list, or this command
            # email *already* unsubscribed the user from the mailing list.
            # E.g. if a message was sent to the -leave address and it
            # contained the 'leave' command.  Don't send a bogus response in
            # this case, just ignore subsequent leaves of the same address.
            if email not in already_left:
                print(_('${self.name}: ${email} is not a member of '
                        '${mlist.fqdn_listname}'), file=results)
                return ContinueProcessing.no
        if email in already_left:
            return ContinueProcessing.yes
        # Ignore any subsequent 'leave' commands.
        already_left.add(email)
        manager = ISubscriptionManager(mlist)
        token, token_owner, member = manager.unregister(user_address)
        person = formataddr((user.display_name, email))   # noqa: F841
        if member is None:
            print(_('${person} left ${mlist.fqdn_listname}'), file=results)
        else:
            print(_('Confirmation email sent to ${person} to leave'
                    ' ${mlist.fqdn_listname}'), file=results)
        return ContinueProcessing.yes


@public
class Unsubscribe(Leave):
    """The email 'unsubscribe' command (an alias for 'leave')."""

    name = 'unsubscribe'
    description = _("An alias for 'leave'.")
    short_description = description
