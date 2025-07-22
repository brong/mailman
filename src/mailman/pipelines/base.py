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

"""Base class for pipelines."""

from mailman.config import config
from mailman.interfaces.pipeline import IPipeline
from mailman.utilities.modules import abstract_component
from public import public
from zope.interface import implementer


@public
@abstract_component
@implementer(IPipeline)
class BasePipeline:
    """Base pipeline implementation."""

    _default_handlers = ()

    def __init__(self):
        self._handlers = []
        for handler_name in self._default_handlers:
            self._handlers.append(config.handlers[handler_name])

    def __iter__(self):
        """See `IPipeline`."""
        yield from self._handlers
