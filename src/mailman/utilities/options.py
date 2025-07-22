# Copyright (C) 2008-2025 by the Free Software Foundation, Inc.
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

"""Argument parsing utilities."""

import click

from mailman.core.i18n import _
from public import public


@public
def validate_runner_spec(ctx, param, value):
    # This validator handles two cases.  First, for the runner script where
    # only a single --runner option is allowed, and second the master script
    # where multiple --runner options are allowed.  When no --runner options
    # are given, we'll either get None or the empty tuple.  That's why we use
    # false-iness here.
    if not value:
        return value
    specs = []
    for spec in ([value] if isinstance(value, str) else value):
        parts = spec.split(':')
        if len(parts) == 1:
            specs.append((parts[0], 1, 1))
        elif len(parts) == 3:
            runner = parts[0]
            try:
                rslice = int(parts[1])
                rrange = int(parts[2])
            except ValueError:
                raise click.BadParameter(
                    _('slice and range must be integers: ${value}'))
            specs.append((runner, rslice, rrange))
        else:
            raise click.UsageError(_('Bad runner spec: ${value}'))
    return specs[0] if isinstance(value, str) else specs


@public
class I18nCommand(click.Command):                   # pragma: nocover
    # https://github.com/pallets/click/issues/834
    #
    # Note that this handles the case for the `mailman <subcommand> --help`
    # output.  To handle `mailman --help` we override the same method in the
    # `Subcommands` subclass over in src/mailman/bin/mailman.py.  The test
    # suite doesn't cover *this* copy of the method but who cares, since it
    # will hopefully go away some day.
    def format_options(self, ctx, formatter):
        """Writes all the options into the formatter if they exist."""
        opts = []
        for param in self.get_params(ctx):
            rv = param.get_help_record(ctx)
            if rv is not None:
                part_a, part_b = rv
                opts.append((part_a, part_b.replace('\n', ' ')))
        if opts:
            with formatter.section('Options'):
                formatter.write_dl(opts)
