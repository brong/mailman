# Copyright (C) 2023 by the Free Software Foundation, Inc.
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

"""Utilities to generate web urls for Email templates."""

from mailman.config import config
from mailman.utilities.string import expand


def web_urls(d, mlist):
    """Generate web urls for the MailingList templates."""
    base_url = mlist.domain.base_url
    if base_url is None:
        return d

    substitutions = {
        'base_url': base_url,
        'list_id': mlist.list_id,
        'domain': mlist.mail_host,
    }

    for key in config.urlpatterns:
        d[f'{key}_url'] = expand(config.urlpatterns[key], extras=substitutions)
