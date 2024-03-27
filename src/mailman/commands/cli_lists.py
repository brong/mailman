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

"""The 'lists' subcommand."""

import sys
import click

from mailman.app.lifecycle import create_list, remove_list
from mailman.core.constants import system_preferences
from mailman.core.i18n import _
from mailman.database.transaction import transaction
from mailman.email.message import UserNotification
from mailman.interfaces.address import (
    IEmailValidator,
    InvalidEmailAddressError,
)
from mailman.interfaces.command import ICLISubCommand
from mailman.interfaces.domain import (
    BadDomainSpecificationError,
    IDomainManager,
)
from mailman.interfaces.languages import ILanguageManager
from mailman.interfaces.listmanager import IListManager, ListAlreadyExistsError
from mailman.interfaces.styles import IStyleManager
from mailman.interfaces.template import ITemplateLoader
from mailman.utilities.options import I18nCommand
from mailman.utilities.string import expand, wrap
from operator import attrgetter
from public import public
from zope.component import getUtility
from zope.interface import implementer


COMMASPACE = ', '


@click.command(
    cls=I18nCommand,
    help=_('List all mailing lists.'))
@click.option(
    '--advertised', '-a',
    is_flag=True, default=False,
    help=_('List only those mailing lists that are publicly advertised'))
@click.option(
    '--names/--no-names', '-n/-N',
    is_flag=True, default=False,
    help=_('Show also the list names'))
@click.option(
    '--descriptions/--no-descriptions', '-d/-D',
    is_flag=True, default=False,
    help=_('Show also the list descriptions'))
@click.option(
    '--quiet', '-q',
    is_flag=True, default=False,
    help=_('Less verbosity'))
@click.option(
    '--domain', 'domains',
    multiple=True, metavar='DOMAIN',
    help=_("""\
    List only those mailing lists hosted on the given domain, which
    must be the email host name.  Multiple -d options may be given.
    """))
@click.pass_context
def lists(ctx, advertised, names, descriptions, quiet, domains):
    mailing_lists = set()
    list_manager = getUtility(IListManager)
    # Gather the matching mailing lists.
    for mlist in list_manager.mailing_lists:
        if advertised and not mlist.advertised:
            continue
        if len(domains) > 0 and mlist.mail_host not in domains:
            continue
        mailing_lists.add(mlist)
    # Maybe no mailing lists matched.
    if len(mailing_lists) == 0:
        if not quiet:
            print(_('No matching mailing lists found'))
        sys.exit()
    count = len(mailing_lists)                  # noqa: F841
    if not quiet:
        print(_('${count} matching mailing lists found:'))
    # Calculate the longest identifier.
    longest = 0
    output = []
    for mlist in sorted(mailing_lists, key=attrgetter('list_id')):
        if names:
            identifier = '{} [{}]'.format(
                mlist.fqdn_listname, mlist.display_name)
        else:
            identifier = mlist.fqdn_listname
        longest = max(len(identifier), longest)
        output.append((identifier, mlist.description))
    # Print it out.
    for identifier, description in output:
        if descriptions and description:
            print('{0:{1}}'.format(identifier, longest) + ' - ' + description)
            # to truncate use description[:70-longest]
        elif descriptions:
            print('{0:{1}}'.format(identifier, longest) + ' -')
        else:
            print(identifier)


@public
@implementer(ICLISubCommand)
class Lists:
    name = 'lists'
    command = lists


@click.command(
    cls=I18nCommand,
    help=_("""\
    Create a mailing list.

    The 'fully qualified list name', i.e. the posting address of the mailing
    list is required.  It must be a valid email address and the domain must be
    registered with Mailman.  List names are forced to lower case."""))
@click.option(
    '--language', metavar='CODE',
    help=_("""\
    Set the list's preferred language to CODE, which must be a registered two
    letter language code."""))
@click.option(
    '--owner', '-o', 'owners',
    multiple=True, metavar='OWNER',
    help=_("""\
    Specify a list owner email address.  If the address is not currently
    registered with Mailman, the address is registered and linked to a user.
    Mailman will send a confirmation message to the address, but it will also
    send a list creation notice to the address.  More than one owner can be
    specified."""))
@click.option(
    '--style-name', metavar='NAME',
    help=_("""\
    Specify a list style name."""))
@click.option(
    '--notify/-no-notify', '-n/-N',
    default=False,
    help=_("""\
    Notify the list owner by email that their mailing list has been
    created."""))
@click.option(
    '--quiet', '-q',
    is_flag=True, default=False,
    help=_('Print less output.'))
@click.option(
    '--domain/--no-domain', '-d/-D', 'create_domain',
    default=True,
    help=_("""\
    Register the mailing list's domain if not yet registered.  This is
    the default behavior, but these options are provided for backward
    compatibility.  With -D do not register the mailing list's domain."""))
@click.argument('fqdn_listname', metavar='LISTNAME')
@click.pass_context
def create(ctx, language, owners, style_name, notify, quiet, create_domain,
           fqdn_listname):
    language_code = (language if language is not None
                     else system_preferences.preferred_language.code)
    # Make sure that the selected language code is known.
    if language_code not in getUtility(ILanguageManager).codes:
        ctx.fail(_('Invalid language code: ${language_code}'))
    # Check to see if the domain exists or not.
    listname, at, domain = fqdn_listname.partition('@')
    domain_manager = getUtility(IDomainManager)
    if domain_manager.get(domain) is None and create_domain:
        domain_manager.add(domain)
    # Validate the owner email addresses.  The problem with doing this check in
    # create_list() is that you wouldn't be able to distinguish between an
    # InvalidEmailAddressError for the list name or the owners.  I suppose we
    # could subclass that exception though.
    if len(owners) > 0:
        validator = getUtility(IEmailValidator)
        invalid_owners = [owner for owner in owners
                          if not validator.is_valid(owner)]
        if invalid_owners:
            invalid = COMMASPACE.join(sorted(invalid_owners))  # noqa: F841
            ctx.fail(_('Illegal owner addresses: ${invalid}'))
    if style_name is not None:
        if getUtility(IStyleManager).get(style_name) is None:
            ctx.fail(_('Unknown list style name: ${style_name}'))
    try:
        mlist = create_list(fqdn_listname, owners, style_name)
    except InvalidEmailAddressError:
        ctx.fail(_('Illegal list name: ${fqdn_listname}'))
    except ListAlreadyExistsError:
        ctx.fail(_('List already exists: ${fqdn_listname}'))
    except BadDomainSpecificationError as domain:              # noqa: F841
        ctx.fail(_('Undefined domain: ${domain}'))
    # Find the language associated with the code, then set the mailing list's
    # preferred language to that.
    language_manager = getUtility(ILanguageManager)
    with transaction():
        mlist.preferred_language = language_manager[language_code]
    # Do the notification.
    if not quiet:
        print(_('Created mailing list: ${mlist.fqdn_listname}'))
    if notify:
        template = getUtility(ITemplateLoader).get(
            'domain:admin:notice:new-list', mlist)
        text = wrap(expand(template, mlist, dict(
            # For backward compatibility.
            requestaddr=mlist.request_address,
            siteowner=mlist.no_reply_address,
            )))
        # Set the I18N language to the list's preferred language so the header
        # will match the template language.  Stashing and restoring the old
        # translation context is just (healthy? :) paranoia.
        with _.using(mlist.preferred_language.code):
            msg = UserNotification(
                owners, mlist.no_reply_address,
                _('Your new mailing list: ${fqdn_listname}'),
                text, mlist.preferred_language)
            msg.send(mlist)


@public
@implementer(ICLISubCommand)
class Create:
    name = 'create'
    command = create


@click.command(
    cls=I18nCommand,
    help=_('Remove a mailing list.'))
@click.option(
    '--quiet', '-q',
    is_flag=True, default=False,
    help=_('Suppress status messages'))
@click.argument('listspec')
def remove(quiet, listspec):
    mlist = getUtility(IListManager).get(listspec)
    if mlist is None:
        if not quiet:
            print(_('No such list matching spec: ${listspec}'))
            sys.exit(0)
    with transaction():
        remove_list(mlist)
        if not quiet:
            print(_('Removed list: ${listspec}'))


@public
@implementer(ICLISubCommand)
class Remove:
    name = 'remove'
    command = remove
