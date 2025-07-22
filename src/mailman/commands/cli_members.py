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

"""The 'members' subcommand."""

import click

from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.member import DeliveryMode, DeliveryStatus, MemberRole
from mailman.utilities.options import I18nCommand
from operator import attrgetter
from public import public
from zope.component import getUtility
from zope.interface import implementer


def display_members(ctx, mlist, role, regular, digest,
                    nomail, outfp, email_only, count_only):
    # Which type of digest recipients should we display?
    if digest == 'any':
        digest_types = [
            DeliveryMode.plaintext_digests,
            DeliveryMode.mime_digests,
            DeliveryMode.summary_digests,
            ]
    elif digest == 'mime':
        # Include summary with mime as they are currently treated alike.
        digest_types = [
            DeliveryMode.mime_digests,
            DeliveryMode.summary_digests,
            ]
    elif digest is not None:
        digest_types = [DeliveryMode[digest + '_digests']]
    else:
        # Don't filter on digest type.
        pass
    # Which members with delivery disabled should we display?
    if nomail is None:
        # Don't filter on delivery status.
        pass
    elif nomail == 'byadmin':
        status_types = [DeliveryStatus.by_moderator]
    elif nomail.startswith('by'):
        status_types = [DeliveryStatus['by_' + nomail[2:]]]
    elif nomail == 'enabled':
        status_types = [DeliveryStatus.enabled]
    elif nomail == 'unknown':
        status_types = [DeliveryStatus.unknown]
    elif nomail == 'any':
        status_types = [
            DeliveryStatus.by_user,
            DeliveryStatus.by_bounces,
            DeliveryStatus.by_moderator,
            DeliveryStatus.unknown,
            ]
    else:                                           # pragma: nocover
        # click should enforce a valid nomail option.
        raise AssertionError(nomail)
    # Which roles should we display?
    if role is None:
        # By default, filter on members.
        roster = mlist.members
    elif role == 'administrator':
        roster = mlist.administrators
    elif role == 'any':
        roster = mlist.subscribers
    else:
        # click should enforce a valid member role.
        roster = mlist.get_roster(MemberRole[role])
    # Print; outfp will be either the file or stdout to print to.
    addresses = list(roster.addresses)
    if len(addresses) == 0:
        print(0 if count_only else _('${mlist.list_id} has no members'),
              file=outfp)
        return
    if count_only:
        print(roster.member_count, file=outfp)
        return
    for address in sorted(addresses, key=attrgetter('email')):
        member = roster.get_member(address.email)
        if regular:
            if member.delivery_mode != DeliveryMode.regular:
                continue
        if digest is not None:
            if member.delivery_mode not in digest_types:
                continue
        if nomail is not None:
            if member.delivery_status not in status_types:
                continue
        # Non-members may have empty address.display_name and member.user can
        # be None resulting in AttributeError on member.user.display_name.
        try:
            dn = address.display_name or member.user.display_name
        except AttributeError:
            dn = ''
        if email_only or not dn:
            print(address.original_email, file=outfp)
        else:
            print(f'{dn} <{address.original_email}>',
                  file=outfp)


@click.command(
    cls=I18nCommand,
    help=_("""\
    Display a mailing list's members.
    Filtering along various criteria can be done when displaying.
    With no options given, displaying mailing list members
    to stdout is the default mode.
    """))
@click.option(
    '--add', '-a', 'add_infp', metavar='FILENAME',
    type=click.File(encoding='utf-8'),
    help=_("""\
    [MODE] Add all member addresses in FILENAME.  This option is removed.
    Use 'mailman addmembers' instead."""))
@click.option(
    '--delete', '-x', 'del_infp', metavar='FILENAME',
    type=click.File(encoding='utf-8'),
    help=_("""\
    [MODE] Delete all member addresses found in FILENAME.
    This option is removed. Use 'mailman delmembers' instead."""))
@click.option(
    '--sync', '-s', 'sync_infp', metavar='FILENAME',
    type=click.File(encoding='utf-8'),
    help=_("""\
    [MODE] Synchronize all member addresses of the specified mailing list
    with the member addresses found in FILENAME.
    This option is removed. Use 'mailman syncmembers' instead."""))
@click.option(
    '--output', '-o', 'outfp', metavar='FILENAME',
    type=click.File(mode='w', encoding='utf-8', atomic=True),
    help=_("""\
    Display output to FILENAME instead of stdout.  FILENAME
    can be '-' to indicate standard output."""))
@click.option(
    '--role', '-R',
    type=click.Choice(('any', 'owner', 'moderator', 'nonmember', 'member',
                       'administrator')),
    help=_("""\
    Display only members with a given ROLE.
    The role may be 'any', 'member', 'nonmember', 'owner', 'moderator',
    or 'administrator' (i.e. owners and moderators).
    If not given, then 'member' role is assumed."""))
@click.option(
    '--regular', '-r',
    is_flag=True, default=False,
    help=_("""\
    Display only regular delivery members."""))
@click.option(
    '--email-only', '-e', 'email_only',
    is_flag=True, default=False,
    help=("""\
    Display member addresses only, without the display name.
    """))
@click.option(
    '--count-only', '-c', 'count_only',
    is_flag=True, default=False,
    help=("""\
    Display members count only.
    """))
@click.option(
    '--no-change', '-N', 'no_change',
    is_flag=True, default=False,
    help=_("""\
    This option has no effect. It exists for backwards compatibility only."""))
@click.option(
    '--digest', '-d', metavar='kind',
    # baw 2010-01-23 summary digests are not really supported yet.
    type=click.Choice(('any', 'plaintext', 'mime')),
    help=_("""\
    Display only digest members of kind.
    'any' means any digest type, 'plaintext' means only plain text (rfc 1153)
    type digests, 'mime' means MIME type digests."""))
@click.option(
    '--nomail', '-n', metavar='WHY',
    type=click.Choice(('enabled', 'any', 'unknown',
                       'byadmin', 'byuser', 'bybounces')),
    help=_("""\
    Display only members with a given delivery status.
    'enabled' means all members whose delivery is enabled, 'any' means
    members whose delivery is disabled for any reason, 'byuser' means
    that the member disabled their own delivery, 'bybounces' means that
    delivery was disabled by the automated bounce processor,
    'byadmin' means delivery was disabled by the list
    administrator or moderator, and 'unknown' means that delivery was disabled
    for unknown (legacy) reasons."""))
@click.argument('listspec')
@click.pass_context
def members(ctx, add_infp, del_infp, sync_infp, outfp,
            role, regular, no_change, digest, nomail, listspec,
            email_only, count_only):
    mlist = getUtility(IListManager).get(listspec)
    if mlist is None:
        ctx.fail(_('No such list: ${listspec}'))
    if add_infp is not None:
        ctx.fail('The --add option is removed. '
                 'Use `mailman addmembers` instead.')
    elif del_infp is not None:
        ctx.fail('The --delete option is removed. '
                 'Use `mailman delmembers` instead.')
    elif sync_infp is not None:
        ctx.fail('The --sync option is removed. '
                 'Use `mailman syncmembers` instead.')
    elif role == 'any' and (regular or digest or nomail):
        ctx.fail('The --regular, --digest and --nomail options are '
                 'incompatible with role=any.')
    elif email_only and count_only:
        ctx.fail('The --email_only and --count_only options are '
                 'mutually exclusive.')
    else:
        display_members(ctx, mlist, role, regular,
                        digest, nomail, outfp, email_only, count_only)


@public
@implementer(ICLISubCommand)
class Members:
    name = 'members'
    command = members
