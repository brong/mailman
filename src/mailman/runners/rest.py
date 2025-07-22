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

"""Start the administrative HTTP server."""


from contextlib import suppress
from mailman.core.runner import Runner
from mailman.interfaces.runner import RunnerInterrupt
from mailman.rest.gunicorn import make_gunicorn_server
from public import public


@public
class RESTRunner(Runner):

    is_queue_runner = False

    def run(self):
        """See `IRunner`."""
        with suppress(RunnerInterrupt):
            make_gunicorn_server().run()
