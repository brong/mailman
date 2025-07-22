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

"""The email command 'who'."""

import re

from email.utils import formataddr
from mailman.core.i18n import _
from mailman.interfaces.command import ContinueProcessing, IEmailCommand
from mailman.interfaces.member import DeliveryMode, DeliveryStatus
from mailman.model.roster import RosterVisibility
from public import public
from zope.interface import implementer


NEWLINE = '\n'


@public
@implementer(IEmailCommand)
class Who:
    """The email 'who' command."""

    name = 'who'
    argument_description = ('[delivery=<enabled|disabled>] '
                            '[mode=<digest|regular>]')
    description = _("""\
Produces a list of member names and email addresses.

The optional delivery= and mode= arguments can be used to limit the report
to those members with matching delivery status and/or delivery mode.  If
either delivery= or mode= is specified more than once, only the last occurrence
is used.
""")
    short_description = _('Get a list of the list members.')

    def process(self, mlist, msg, msgdata, arguments, results):
        """See `IEmailCommand`."""
        show_roster = mlist.member_roster_visibility == RosterVisibility.public
        show_roster |= (mlist.member_roster_visibility ==
                        RosterVisibility.members and
                        (bool(mlist.members.get_member(msg.sender)) or
                         bool(mlist.administrators.get_member(msg.sender))))
        show_roster |= (mlist.member_roster_visibility ==
                        RosterVisibility.moderators and
                        bool(mlist.administrators.get_member(msg.sender)))
        if not show_roster:
            print(_('You are not authorized to see the membership list.'),
                  file=results)
            return ContinueProcessing.no
        delivery = mode = None
        unrecognized = list()
        for arg in arguments:
            mo_deliv = re.match('^delivery=(enabled|disabled)$', arg)
            if mo_deliv is not None:
                delivery = mo_deliv.group(1)
            mo_mode = re.match('^mode=(digest|regular)$', arg)
            if mo_mode is not None:
                mode = mo_mode.group(1)
            if mo_deliv is None and mo_mode is None:
                unrecognized.append(arg)
        if len(unrecognized) > 0:
            print(_('Unrecognized or invalid argument(s):\n{}').format(
                  NEWLINE.join(unrecognized)), file=results)
            return ContinueProcessing.no
        members = list()
        for member in mlist.members.members:
            if delivery is not None:
                if (delivery == 'enabled' and
                        member.delivery_status != DeliveryStatus.enabled):
                    continue
                elif (delivery != 'enabled' and
                        member.delivery_status == DeliveryStatus.enabled):
                    continue
            if mode is not None:
                if (mode == 'regular' and
                        member.delivery_mode != DeliveryMode.regular):
                    continue
                elif (mode != 'regular' and
                        member.delivery_mode == DeliveryMode.regular):
                    continue
            members.append((member.display_name, member.address.email))
        members.sort(key=lambda x: x[1])
        for i in range(len(members)):
            members[i:i+1] = ['    ' + formataddr(members[i])]
        print(_('Members of the {} mailing list:\n{}').format(
              mlist.fqdn_listname, NEWLINE.join(members)), file=results)
        return ContinueProcessing.yes
