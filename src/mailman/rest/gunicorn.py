# Copyright (C) 2019-2023 by the Free Software Foundation, Inc.
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

"""Gunicorn for Mailman's REST API."""

import os
import gunicorn.app.base

from mailman.config import config
from mailman.config.config import external_configuration
from mailman.rest.wsgiapp import make_application
from public import public


@public
class GunicornApplication(gunicorn.app.base.BaseApplication):

    def __init__(self, app, options=None):
        self.options = options or {}
        self.application = app
        super().__init__()

    def load_config(self):
        config = dict([(key, value) for key, value in self.options.items()
                       if key in self.cfg.settings and value is not None])
        for key, value in config.items():
            self.cfg.set(key.lower(), value)

    def load(self):
        return self.application


def _post_worker_init(worker):
    """Post worker initialize event handler for Gunicorn."""
    # Dispose engine connections whenever a new process is forked and
    # initialized so they don't step on each other's connections.
    # https://docs.gunicorn.org/en/latest/settings.html#post-worker-init
    config.db.engine.dispose()


@public
def make_gunicorn_server():
    """Create a gunicorn server.

    Use this to run the REST API using Gunicorn.
    """
    # We load some options from config.webservice, while other extended options
    # for gunicorn can be defined in configuration: section. We also load up
    # some logging options since gunicorn sets up it's own loggers.
    host = config.webservice.hostname
    port = int(config.webservice.port)
    log_path = os.path.join(config.LOG_DIR, config.logging.gunicorn['path'])
    options = {
        'proc_name': 'mailman: runner [rest]',
        'bind': '{}:{}'.format(host, port),
        'accesslog': log_path,
        'errorlog': log_path,
        'loglevel': config.logging.gunicorn['level'],
        'access_log_format': config.logging.gunicorn['format'],
        'disable_redirect_access_to_syslog': True,
        'workers': int(config.webservice.workers),
        'post_worker_init': _post_worker_init,
    }
    # Read the ini configuration and pass those values to the
    # GunicornApplication.
    gunicorn_config = external_configuration(config.webservice.configuration)
    for key in gunicorn_config['gunicorn']:
        options[key] = gunicorn_config['gunicorn'][key]

    return GunicornApplication(make_application(), options)
