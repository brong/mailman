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

"""The Mailman version."""

import click

from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.options import I18nCommand
from mailman.version import MAILMAN_VERSION_FULL
from public import public
from zope.interface import implementer


@click.command(
    cls=I18nCommand,
    help=_("Display Mailman's version."))
def version():
    print(MAILMAN_VERSION_FULL)


@public
@implementer(ICLISubCommand)
class Version:
    name = 'version'
    command = version
