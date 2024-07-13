# Copyright (C) 2009-2023 by the Free Software Foundation, Inc.
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

"""The 'findmember' subcommand."""

import re
import click

from mailman.core.i18n import _
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.member import MemberRole, SubscriptionMode
from mailman.interfaces.usermanager import IUserManager
from mailman.utilities.options import I18nCommand
from public import public
from zope.component import getUtility
from zope.interface import implementer


def _filter_role(member, role):
    role_list = dict(
        administrator=[MemberRole.owner, MemberRole.moderator],
        owner=[MemberRole.owner],
        moderator=[MemberRole.moderator],
        member=[MemberRole.member],
        nonmember=[MemberRole.nonmember],
        )
    if role is None or role == 'all':
        return member
    if member.role in role_list[role]:
        return member
    return None


def _get_member_email(member):
    if member.subscription_mode is SubscriptionMode.as_user:
        email = member.subscriber.preferred_address.email
    else:
        assert member.subscription_mode is SubscriptionMode.as_address
        email = member.subscriber.email
    return email


def _sort_key(member):
    list_id = member.mailing_list.list_id
    email = _get_member_email(member)
    role = str(member.role)
    return (email, list_id, role)


def _sql_pattern(pattern):
    pattern = re.sub(r'\.\*', '%', pattern)
    pattern = re.sub(r'(^|[^\\])\.', r'\1_', pattern)
    pattern = re.sub(r'\\\.', '.', pattern)
    if pattern.endswith('$'):
        pattern = pattern[:-1]
    else:
        pattern = pattern + '%'
    if pattern.startswith('^'):
        pattern = pattern[1:]
    elif not pattern.startswith('%'):
        pattern = '%' + pattern
    return pattern


@click.command(
    cls=I18nCommand,
    help=_("""\
    Display all memberships for a user or users with address matching a
    pattern. Because part of the process involves converting the pattern
    to a SQL query with wildcards, the pattern should be simple. A simple
    string works best.
    """))
@click.option(
    '--role', '-r',
    type=click.Choice(('all', 'owner', 'moderator', 'nonmember', 'member',
                       'administrator')),
    help=_("""\
    Display only memberships with the given role.  If not given, 'all' role,
    i.e. all roles, is the default."""))
@click.argument('pattern')
@click.pass_context
def findmember(ctx, role, pattern):
    result = list()
    user_manager = getUtility(IUserManager)
    for user in user_manager.find_users(_sql_pattern(pattern)):
        emails = [address.email for address in user.addresses]
        for email in emails:
            if re.search(pattern, email, re.I):
                for member in user.memberships.members:
                    if _filter_role(member, role):
                        result.append(member)
            break
    if len(result) == 0:
        return
    result.sort(key=_sort_key)
    last_email = last_list_id = last_role = ''
    for member in result:
        email = _get_member_email(member)
        if email != last_email:
            last_list_id = last_role = ''
            print(_('Email: {}').format(email))
            last_email = email
        if member.list_id != last_list_id:
            last_role = ''
            print(' '*4 + _('List: {}').format(member.list_id))
            last_list_id = member.list_id
        if member.role != last_role:
            print(' '*8 + _('{}').format(str(member.role)))
            last_role = member.role


@public
@implementer(ICLISubCommand)
class FindMember:
    name = 'findmember'
    command = findmember
