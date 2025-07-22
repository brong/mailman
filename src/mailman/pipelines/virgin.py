# Copyright (C) 2006-2025 by the Free Software Foundation, Inc.
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

"""A virgin pipeline."""

from mailman.core.i18n import _
from mailman.pipelines.base import BasePipeline
from public import public


@public
class VirginPipeline(BasePipeline):
    """The processing pipeline for virgin messages.

    Virgin messages are those that are crafted internally by Mailman.
    """
    name = 'virgin'
    description = _('The virgin queue pipeline.')

    _default_handlers = (
        'cook-headers',
        'rfc-2369',
        'to-outgoing',
        )
