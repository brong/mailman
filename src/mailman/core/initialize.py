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

"""Initialize all global state.

Every entrance into the Mailman system, be it by command line, mail program,
or cgi, must call the initialize function here in order for the system's
global state to be set up properly.  Typically this is called after command
line argument parsing, since some of the initialization behavior is controlled
by the command line arguments.
"""

import os
import sys
import logging
import mailman.core.logging
import mailman.config.config

from importlib.resources import read_text
from mailman.interfaces.database import IDatabaseFactory
from public import public
from zope.component import getUtility
from zope.configuration import xmlconfig


# The test infrastructure uses this to prevent the search and loading of any
# existing configuration file.  Otherwise the existence of say a
# ~/.mailman.cfg file can break tests.
INHIBIT_CONFIG_FILE = object()
public(INHIBIT_CONFIG_FILE=INHIBIT_CONFIG_FILE)


def search_for_configuration_file():
    """Search the file system for a configuration file to use.

    This is only called if the -C command line argument was not given.
    """
    config_path = os.getenv('MAILMAN_CONFIG_FILE')
    # Both None and the empty string are considered "missing".
    if config_path and os.path.exists(config_path):
        return os.path.abspath(config_path)
    # ./mailman.cfg
    config_path = os.path.abspath('mailman.cfg')
    if os.path.exists(config_path):
        return config_path
    # As a special case, look in ./var/etc/mailman.cfg.  We can't do this in
    # the Configuration.load() method because that depends on the
    # configuration system, which of course is not set up at that time!
    config_path = os.path.abspath(os.path.join('var', 'etc', 'mailman.cfg'))
    if os.path.exists(config_path):
        return config_path
    # ~/.mailman.cfg
    config_path = os.path.join(os.getenv('HOME', '~'), '.mailman.cfg')
    if os.path.exists(config_path):
        return os.path.abspath(config_path)
    # /etc/mailman.cfg
    config_path = '/etc/mailman.cfg'
    if os.path.exists(config_path):
        return os.path.abspath(config_path)
    # /etc/mailman3/mailman.cfg
    config_path = '/etc/mailman3/mailman.cfg'
    if os.path.exists(config_path):
        return os.path.abspath(config_path)
    # $argv0/../../etc/mailman.cfg
    bindir = os.path.dirname(sys.argv[0])
    parent = os.path.dirname(bindir)
    config_path = os.path.join(parent, 'etc', 'mailman.cfg')
    if os.path.exists(config_path):
        return os.path.abspath(config_path)
    # Are there any others we should search by default?
    return None


# These initialization calls are separated for the testing framework, which
# needs to do some internal calculations after config file loading and log
# initialization, but before database initialization.  Generally all other
# code will just call initialize().

@public
def initialize_1(config_path=None):
    """First initialization step.

    * Zope component architecture
    * The configuration system
    * Run-time directories

    :param config_path: The path to the configuration file.
    :type config_path: string
    """
    xmlconfig.string(read_text('mailman.config', 'configure.zcml'))
    # By default, set the umask so that only owner and group can read and
    # write our files.  Specifically we must have g+rw and we probably want
    # o-rwx although I think in most cases it doesn't hurt if other can read
    # or write the files.
    os.umask(0o007)
    # Initialize configuration event subscribers.  This must be done before
    # setting up the configuration system.
    from mailman.app.events import initialize as initialize_events
    initialize_events()
    # config_path will be set if the command line argument -C is given.  That
    # case overrides all others.  When not given on the command line, the
    # configuration file is searched for in the file system.
    if config_path is None:
        config_path = search_for_configuration_file()
    elif config_path is INHIBIT_CONFIG_FILE:
        # For the test suite, force this back to not using a config file.
        config_path = None
    mailman.config.config.load(config_path)
    # Use this environment variable to define an extra configuration file for
    # testing.  This is used by the tox.ini to run the full test suite under
    # PostgreSQL.
    extra_cfg_path = os.environ.get('MAILMAN_EXTRA_TESTING_CFG')
    if extra_cfg_path is not None:
        with open(extra_cfg_path, 'r', encoding='utf-8') as fp:
            extra_cfg = fp.read()
        mailman.config.config.push('extra testing config', extra_cfg)


@public
def run_plugin_pre_hook(config):
    """Run the plugin pre_hooks, if one fails, disable the offending plugin."""
    disabled_plugins = []
    for name in config.plugins:                     # pragma: nocover
        plugin = config.plugins[name]
        if hasattr(plugin, 'pre_hook'):
            try:
                plugin.pre_hook()
            except Exception:                                # pragma: nocover
                log = logging.getLogger('mailman.plugins')
                log.exception('Plugin failed to run its pre_hook: "{}". '
                              'It will be disabled and its components '
                              "won't be loaded.".format(name))
                disabled_plugins.append(name)

    if disabled_plugins:
        for plugin in disabled_plugins:
            del config.plugins[plugin]


@public
def initialize_2(debug=False, propagate_logs=None, testing=False):
    """Second initialization step.

    * Database
    * Logging
    * Plugin pre_hook()'s
    * Rules
    * Chains
    * Pipelines
    * Commands

    :param debug: Should the database layer be put in debug mode?
    :type debug: boolean
    :param propagate_logs: Should the log output propagate to stderr?
    :type propagate_logs: boolean or None
    """
    # Create the queue and log directories if they don't already exist.
    mailman.core.logging.initialize(propagate_logs)
    # Initialize plugins
    from mailman.plugins.initialize import initialize as initialize_plugins
    initialize_plugins()
    # Check for deprecated features in config.
    config = mailman.config.config
    if len(config.mailman.pre_hook) > 0:            # pragma: nocover
        log = logging.getLogger('mailman.plugins')
        log.warning(
            'The [mailman]pre_hook configuration value has been replaced '
            "by the plugins infrastructure, and won't be called.")
    run_plugin_pre_hook(config)
    # Instantiate the database class, ensure that it's of the right type, and
    # initialize it.  Then stash the object on our configuration object.
    utility_name = ('testing' if testing else 'production')
    config.db = getUtility(IDatabaseFactory, utility_name).create()
    # Initialize the rules and chains.  Do the imports here so as to avoid
    # circular imports.
    from mailman.app.commands import initialize as initialize_commands
    from mailman.core.chains import initialize as initialize_chains
    from mailman.core.pipelines import initialize as initialize_pipelines
    from mailman.core.rules import initialize as initialize_rules

    # Order here is somewhat important.
    initialize_rules()
    initialize_chains()
    initialize_pipelines()
    initialize_commands()


@public
def initialize_3():
    """Third initialization step.

    * Plugin post_hook()'s.
    """
    # Run the plugin post_hooks.
    config = mailman.config.config
    log = logging.getLogger('mailman.plugins')
    if len(config.mailman.post_hook) > 0:           # pragma: nocover
        log.warning(
            'The [mailman]post_hook configuration value has been replaced '
            "by the plugins infrastructure, and won't be called.")
    for plugin in config.plugins.values():          # pragma: nocover
        try:
            plugin.post_hook()
        except Exception:                         # pragma: nocover
            # A post_hook may fail, here we just hope for the best that the
            # plugin can work even if it post_hook failed as it's components
            # are already loaded.
            log.exception(
                'Plugin failed to run its post_hook: {}'.format(plugin.name))


@public
def initialize(config_path=None, propagate_logs=None):
    initialize_1(config_path)
    initialize_2(propagate_logs=propagate_logs)
    initialize_3()
