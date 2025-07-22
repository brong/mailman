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

"""The 'confirm' email command."""

from mailman.app.moderator import handle_message
from mailman.core.i18n import _
from mailman.interfaces.action import Action
from mailman.interfaces.command import ContinueProcessing, IEmailCommand
from mailman.interfaces.member import (
    AlreadySubscribedError,
    MembershipIsBannedError,
)
from mailman.interfaces.pending import IPendings
from mailman.interfaces.subscriptions import ISubscriptionManager, TokenOwner
from mailman.rules.approved import Approved
from public import public
from zope.component import getUtility
from zope.interface import implementer


@public
@implementer(IEmailCommand)
class Confirm:
    """The email 'confirm' command."""

    name = 'confirm'
    argument_description = 'token'
    description = _('Confirm a subscription or held message request.')
    short_description = description

    def process(self, mlist, msg, msgdata, arguments, results):
        """See `IEmailCommand`."""
        # The token must be in the arguments.
        if len(arguments) == 0:
            print(_('No confirmation token found'), file=results)
            return ContinueProcessing.no
        # Make sure we don't try to confirm the same token more than once.
        token = arguments[0]
        tokens = getattr(results, 'confirms', set())
        if token in tokens:
            # Do not try to confirm this one again.
            return ContinueProcessing.no
        tokens.add(token)
        results.confirms = tokens
        # now figure out what this token is for.
        pendable = getUtility(IPendings).confirm(token, expunge=False)
        if pendable is None:
            print(_('Confirmation token did not match'), file=results)
            results.send_response = True
            return ContinueProcessing.no
        if pendable['type'] == 'held message':
            return self.process_held(
                mlist, msg, msgdata, token, pendable, results)
        try:
            new_token, token_owner, member = ISubscriptionManager(
                mlist).confirm(token)
            if new_token is None:
                assert token_owner is TokenOwner.no_one, token_owner
                # We can't assert anything about member.  It will be None when
                # the workflow we're confirming is a subscription request,
                # and non-None when we're confirming an unsubscription request.
                # This class doesn't know which is happening.
                succeeded = True
            elif token_owner is TokenOwner.moderator:
                # This must have been a confirm-then-moderate (un)subscription.
                assert new_token != token
                # We can't assert anything about member for the above reason.
                succeeded = True
            else:
                assert token_owner is not TokenOwner.no_one, token_owner
                assert member is None, member
                succeeded = False
        except LookupError:
            # The token must not exist in the database.
            succeeded = False
        except (MembershipIsBannedError, AlreadySubscribedError) as e:
            print(str(e), file=results)
            return ContinueProcessing.no
        if succeeded:
            print(_('Confirmed'), file=results)
            # After the 'confirm' command, do not process any other commands in
            # the email.
            return ContinueProcessing.no
        print(_('Confirmation token did not match'), file=results)
        return ContinueProcessing.no

    def process_held(self, mlist, msg, msgdata, token, pendable, results):
        # Is this confirmation approved?
        approved = Approved().check(mlist, msg, msgdata)
        if approved:
            action = Action.accept
            message = 'accepted'                          # noqa: F841
        elif not msgdata.get('has_approved', False):
            action = Action.discard
            message = 'discarded'                         # noqa: F841
        else:
            print(_('Invalid Approved: password'), file=results)
            return ContinueProcessing.no
        handle_message(mlist, pendable['id'], action)
        print(_('Message ${message}'), file=results)
        return ContinueProcessing.no
