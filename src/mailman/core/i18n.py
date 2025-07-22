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

"""Internationalization."""

import mailman.messages

from flufl.i18n import PackageStrategy, registry
from mailman.interfaces.configuration import ConfigurationUpdatedEvent
from public import public


public(_=None)


@public
def initialize(application=None):
    """Initialize the i18n subsystem.

    :param application: An optional `flufl.i18n.Application` instance to use
        as the translation context.  This primarily exists to support the
        testing environment.
    :type application: `flufl.i18n.Application`
    """
    global _
    if application is None:
        strategy = PackageStrategy('mailman', mailman.messages)
        application = registry.register(strategy)
    _ = application._


@public
def handle_ConfigurationUpdatedEvent(event):
    if isinstance(event, ConfigurationUpdatedEvent):
        _.default = event.config.mailman.default_language


@public
def format_reasons(reasons):
    """Translate and format hold and rejection reasons.

    :param reasons: A list of reasons from the rules that hit.  Each reason is
        a string to be translated or a tuple consisting of a string with {}
        replacements and one or more replacement values.
    :returns: A list of the translated and formatted strings.
    """
    new_reasons = []
    for reason in reasons:
        if isinstance(reason, tuple):
            new_reasons.append(_(reason[0]).format(*reason[1:]))
        else:
            new_reasons.append(_(reason))
    return new_reasons
