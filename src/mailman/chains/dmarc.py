# Copyright (C) 2017-2025 by the Free Software Foundation, Inc.
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

"""DMARC mitigation chain."""

from mailman.chains.base import JumpChainBase
from mailman.core.i18n import _
from public import public


@public
class DMARCMitigationChain(JumpChainBase):
    """Perform DMARC mitigation."""

    name = 'dmarc'
    description = _('Process DMARC reject or discard mitigations')

    def jump_to(self, mlist, msg, msgdata):
        # Which action should be taken?
        jump_chain = msgdata['dmarc_action']
        assert jump_chain in ('discard', 'reject'), (
            '{}: Invalid DMARC action: {} for sender: {}'.format(
                mlist.list_id, jump_chain,
                msgdata.get('moderation_sender', '(unknown)')))
        return jump_chain
