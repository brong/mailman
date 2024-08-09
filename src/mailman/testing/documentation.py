# Copyright (C) 2007-2023 by the Free Software Foundation, Inc.
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

"""Harness for testing Mailman's documentation.

Note that doctest extraction does not currently work for zip file
distributions.  doctest discovery currently requires file system traversal.
"""

import os
import sys
import shlex

from click.testing import CliRunner
from contextlib import ExitStack
from importlib import import_module
from mailman.testing.helpers import call_api
from mailman.testing.layers import SMTPLayer
from public import public
from subprocess import PIPE, run, STDOUT
from urllib.error import HTTPError


DOT = '.'
COMMASPACE = ', '


def stop():
    """Call into pdb.set_trace()"""
    # Do the import here so that you get the wacky special hacked pdb instead
    # of Python's normal pdb.
    import pdb
    pdb.set_trace()


def dump_msgdata(msgdata, *additional_skips):
    """Dump in a more readable way a message metadata dictionary."""
    if len(msgdata) == 0:
        print('*Empty*')
        return
    skips = set(additional_skips)
    # Some stuff we always want to skip, because their values will always be
    # variable data.
    skips.add('received_time')
    longest = max(len(key) for key in msgdata if key not in skips)
    for key in sorted(msgdata):
        if key in skips:
            continue
        print('{0:{2}}: {1}'.format(key, msgdata[key], longest))


def dump_list(list_of_things, key=str):
    """Print items in a string to get rid of stupid u'' prefixes."""
    # List of things may be a generator.
    list_of_things = list(list_of_things)
    if len(list_of_things) == 0:
        print('*Empty*')
    if key is not None:
        list_of_things = sorted(list_of_things, key=key)
    for item in list_of_things:
        print(item)


def call_http(url, data=None, method=None, username=None, password=None):
    """'Call a URL with a given HTTP method and return the resulting object.

    The object will have been JSON decoded.

    :param url: The url to open, read, and print.
    :type url: string
    :param data: Data to use to POST to a URL.
    :type data: dict
    :param method: Alternative HTTP method to use.
    :type method: str
    :param username: The HTTP Basic Auth user name.  None means use the value
        from the configuration.
    :type username: str
    :param password: The HTTP Basic Auth password.  None means use the value
        from the configuration.
    :type username: str
    :return: The decoded JSON data structure.
    :raises HTTPError: when a non-2xx return code is received.
    """
    try:
        content, response = call_api(url, data, method, username, password)
    except HTTPError as ex:
        print(ex)
        return
    if content is None:
        # We used to use httplib2 here, which included the status code in the
        # response headers in the `status` key.  requests doesn't do this, but
        # the doctests expect it so for backward compatibility, include the
        # status code in the printed response.
        headers = dict(status=response.status_code)
        headers.update({
            field.lower(): response.headers[field]
            for field in response.headers
            })
        # Remove the connection: close header from the response.
        headers.pop('connection')
        for field in sorted(headers):
            print('{}: {}'.format(field, headers[field]))
        return None
    return content


def _print_dict(data, depth=0):
    for item, value in sorted(data.items()):
        if isinstance(value, dict):
            _print_dict(value, depth+1)
        print('    ' * depth + '{}: {}'.format(item, value))


def dump_json(url, data=None, method=None, username=None, password=None,
              sort_entries=None):
    """Print the JSON dictionary read from a URL.

    :param url: The url to open, read, and print.
    :type url: string
    :param data: Data to use to POST to a URL.
    :type data: dict
    :param method: Alternative HTTP method to use.
    :type method: str
    :param username: The HTTP Basic Auth user name.  None means use the value
        from the configuration.
    :type username: str
    :param password: The HTTP Basic Auth password.  None means use the value
        from the configuration.
    :type password: str
    :param sort_entries: The key to sort entiries.
    :type sort_entries: str
    """
    results = call_http(url, data, method, username, password)
    if results is None:
        return
    for key in sorted(results):
        value = results[key]
        if key == 'entries':
            entries = value
            if sort_entries:
                entries = sorted(entries, key=lambda x: x[sort_entries])
            for i, entry in enumerate(entries):
                # entry is a dictionary.
                print('entry %d:' % i)
                for entry_key in sorted(entry):
                    print('    {}: {}'.format(entry_key, entry[entry_key]))
        elif isinstance(value, list):
            printable_value = COMMASPACE.join(
                "'{}'".format(s) for s in sorted(value))
            print('{}: [{}]'.format(key, printable_value))
        elif isinstance(value, dict):
            print('{}:'.format(key))
            _print_dict(value, 1)
        else:
            print('{}: {}'.format(key, value))


@public
def cli(command_path):
    """Call a CLI command in doctests.

    Use this to invoke click commands in doctests.  This returns a partial
    that accepts a sequence of command line options, invokes the click
    command, and returns the results (unless the keyword argument 'quiet')
    is True.
    """
    package_path, dot, name = command_path.rpartition('.')
    command = getattr(import_module(package_path), name)
    def inner(command_string, quiet=False, input=None):           # noqa: E306
        args = shlex.split(command_string)
        assert args[0] == 'mailman', args
        assert args[1] == command.name, args
        # The first two will be `mailman <command>`.  That's just for
        # documentation purposes, and aren't useful for the test.
        result = CliRunner().invoke(command, args[2:], input=input)
        if not quiet:
            # Print the output, with any trailing newlines stripped, unless
            # the quiet flag is set.  The extra newlines just make the
            # doctests uglier and usually all we care about is the stdout
            # text.
            print(result.output.rstrip('\n'))
    return inner


@public
def run_mailman(args, **overrides):
    """Execute `mailman` command with the given arguments and return output."""
    exe = os.path.join(os.path.dirname(sys.executable), 'mailman')
    env = os.environ.copy()
    env.update(overrides)
    run_args = [exe]
    # When running tests as root, just add the flag to force run mailman
    # command without errors.
    if os.geteuid() == 0:
        run_args.append('--run-as-root')
    run_args.extend(args)
    proc = run(
        run_args, env=env, stdout=PIPE, stderr=STDOUT, universal_newlines=True)
    return proc


@public
def setup(testobj):
    """Test setup."""
    # In general, I don't like adding convenience functions, since I think
    # doctests should do the imports themselves.  It makes for better
    # documentation that way.  However, a few are really useful, or help to
    # hide some icky test implementation details.
    testobj.globs['smtpd'] = SMTPLayer.smtpd
    testobj.globs['stop'] = stop
    # Add this so that cleanups can be automatically added by the doctest.
    testobj.globs['cleanups'] = ExitStack()


@public
def teardown(testobj):
    testobj.globs['cleanups'].close()
