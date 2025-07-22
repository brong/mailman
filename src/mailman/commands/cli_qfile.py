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

"""Getting information out of a qfile."""

import click
import pickle

from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.utilities.interact import interact
from mailman.utilities.options import I18nCommand
from pprint import PrettyPrinter
from public import public
from zope.interface import implementer


# This is deliberately called 'm' for use with --interactive.
m = None


@click.command(
    cls=I18nCommand,
    help=_('Get information out of a queue file.'))
@click.option(
    '--print/--no-print', '-p/-n', 'doprint',
    default=True,
    help=_("""\
    Don't attempt to pretty print the object.  This is useful if there is some
    problem with the object and you just want to get an unpickled
    representation.  Useful with 'mailman qfile -i <file>'.  In that case, the
    list of unpickled objects will be left in a variable called 'm'."""))
@click.option(
    '--interactive', '-i',
    is_flag=True, default=False,
    help=_("""\
    Start an interactive Python session, with a variable called 'm'
    containing the list of unpickled objects."""))
@click.argument('qfile')
def qfile(doprint, interactive, qfile):
    global m
    # Reinitialize 'm' every time this command is run.  This isn't normally
    # needed for command line use, but is important for the test suite.
    m = []
    printer = PrettyPrinter(indent=4)
    with open(qfile, 'rb') as fp:
        while True:
            try:
                m.append(pickle.load(fp))
            except EOFError:
                break
    if doprint:
        print(_('[----- start pickle -----]'))
        for i, obj in enumerate(m):
            count = i + 1
            print(_('<----- start object ${count} ----->'))
            if isinstance(obj, (bytes, str)):
                print(obj)
            else:
                printer.pprint(obj)
        print(_('[----- end pickle -----]'))
    count = len(m)                              # noqa: F841
    banner = _("Number of objects found (see the variable 'm'): ${count}")
    if interactive:
        interact(banner=banner)


@public
@implementer(ICLISubCommand)
class QFile:
    name = 'qfile'
    command = qfile
