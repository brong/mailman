# Copyright (C) 2013-2025 by the Free Software Foundation, Inc.
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

"""Print the mailman configuration."""

import click

from lazr.config._config import Section
from mailman.config import config
from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.options import I18nCommand
from public import public
from zope.interface import implementer


def _section_exists(section):
    # Not all of the attributes in config are actual sections, so we have to
    # check the section's type.
    return (
        hasattr(config, section) and
        isinstance(getattr(config, section), Section)
        )


def _get_value(section, key):
    return getattr(getattr(config, section), key)


def _print_values_for_section(section, output):
    current_section = sorted(getattr(config, section))
    for key in current_section:
        value = _get_value(section, key)
        print('[{}] {}: {}'.format(section, key, value), file=output)


@click.command(
    cls=I18nCommand,
    help=_('Print the Mailman configuration.'))
@click.option(
    '--output', '-o',
    type=click.File(mode='w', encoding='utf-8', atomic=True),
    help=_("""\
    File to send the output to.  If not given, or if '-' is given, standard
    output is used."""))
@click.option(
    '--section', '-s',
    help=_("""\
    Section to use for the lookup.  If no key is given, all the key-value pairs
    of the given section will be displayed."""))
@click.option(
    '--key', '-k',
    help=_("""\
    Key to use for the lookup.  If no section is given, all the key-values pair
    from any section matching the given key will be displayed."""))
@click.pass_context
def conf(ctx, output, section, key):
    # Case 1: Both section and key are given, so we can look the value up
    # directly.
    if section is not None and key is not None:
        if not _section_exists(section):
            ctx.fail('No such section: {}'.format(section))
        elif not hasattr(getattr(config, section), key):
            ctx.fail('Section {}: No such key: {}'.format(section, key))
        else:
            print(_get_value(section, key), file=output)
    # Case 2: Section is given, key is not given.
    elif section is not None and key is None:
        if _section_exists(section):
            _print_values_for_section(section, output)
        else:
            ctx.fail('No such section: {}'.format(section))
    # Case 3: Section is not given, key is given.
    elif section is None and key is not None:
        for current_section in sorted([section.name for section in config]):
            # We have to ensure that the current section actually exists
            # and that it contains the given key.
            if (_section_exists(current_section) and
                    hasattr(getattr(config, current_section), key)):
                value = _get_value(current_section, key)
                print('[{}] {}: {}'.format(
                    current_section, key, value), file=output)
    # Case 4: Neither section nor key are given, just display all the
    # sections and their corresponding key/value pairs.
    elif section is None and key is None:
        for current_section in sorted([section.name for section in config]):
            # However, we have to make sure that the current sections and
            # key which are being looked up actually exist before trying
            # to print them.
            if _section_exists(current_section):
                _print_values_for_section(current_section, output)
    else:
        raise AssertionError('Unexpected combination')


@public
@implementer(ICLISubCommand)
class Conf:
    name = 'conf'
    command = conf
