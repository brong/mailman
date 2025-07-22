# Copyright (C) 2010-2025 by the Free Software Foundation, Inc.
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

"""Moderation chain.

When a member or nonmember posting to the mailing list has a moderation action
that is not `defer`, the built-in chain jumps to this chain.  This chain then
determines the disposition of the message based on the member's or nonmember's
moderation action.

For example, these actions jump to the appropriate terminal chain:

    * accept - the message is immediately accepted
    * hold - the message is held for moderator approval
    * reject - the message is bounced
    * discard - the message is immediately thrown away

Note that if the moderation action is `defer` then the normal decisions are
made as to the disposition of the message.  `defer` is the default for
members, while `hold` is the default for nonmembers.
"""

from mailman.chains.base import JumpChainBase
from mailman.core.i18n import _
from mailman.interfaces.action import Action
from public import public


@public
class ModerationChain(JumpChainBase):
    """Dynamically produce a link jumping to the appropriate terminal chain.

    The terminal chain will be one of the Accept, Hold, Discard, or Reject
    chains, based on the member's or nonmember's moderation action setting.
    """
    name = 'moderation'
    description = _('Moderation chain')

    def jump_to(self, mlist, msg, msgdata):
        # Get the moderation action from the message metadata.  It can only be
        # one of the expected values (i.e. not Action.defer).  See the
        # moderation.py rule for details.  This is stored in the metadata as a
        # string so that it can be stored in the pending table.
        action = Action[msgdata.get('member_moderation_action')]
        # defer is not a valid moderation action.
        jump_chain = {
            Action.accept: 'accept',
            Action.discard: 'discard',
            Action.hold: 'hold',
            Action.reject: 'reject',
            }.get(action)
        assert jump_chain is not None, (
            '{}: Invalid moderation action: {} for sender: {}'.format(
                mlist.list_id, action,
                msgdata.get('moderation_sender', '(unknown)')))
        return jump_chain
