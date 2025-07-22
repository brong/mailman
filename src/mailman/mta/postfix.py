# Copyright (C) 2001-2025 by the Free Software Foundation, Inc.
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

"""Creation/deletion hooks for the Postfix MTA."""

import os
import logging

from collections import defaultdict
from contextlib import contextmanager
from flufl.lock import Lock
from functools import lru_cache
from mailman.config import config
from mailman.config.config import external_configuration
from mailman.interfaces.domain import IDomainManager
from mailman.interfaces.listmanager import IListManager
from mailman.interfaces.mta import (
    IMailTransportAgentAliases,
    IMailTransportAgentLifecycle,
)
from mailman.utilities.datetime import now
from operator import attrgetter
from public import public
from zope.component import getUtility
from zope.interface import implementer


log = logging.getLogger('mailman.error')
ALIASTMPL = '{0:{2}}lmtp:[{1.mta.lmtp_host}]:{1.mta.lmtp_port}'
VMAPTMPL = '{0:{1}}{2}'
NL = '\n'


@contextmanager
def atomic(path):
    # Write a new file and then atomically rename it.
    new_path = path + '.new'
    try:
        with open(new_path, 'w', encoding='utf-8') as fp:
            yield fp
    except:                                      # noqa: E722 pragma: nocover
        os.remove(new_path)
        raise
    else:
        os.rename(new_path, path)


def _get_alias_domain(domain):
    domain_manager = getUtility(IDomainManager)
    d = domain_manager.get(domain)
    if d is not None and d.alias_domain:
        return d.alias_domain
    return domain


class _FakeListManager:
    """Faked list manager class with alias domain cache per an instance."""

    def __init__(self):
        self.get_alias_domain = lru_cache(maxsize=1000)(self._get_alias_domain)

    def get(self, list_name, true_mail_host):
        mail_host = self.get_alias_domain(true_mail_host)
        return _FakeList(list_name, mail_host, true_mail_host)

    def _get_alias_domain(self, mail_host):
        return _get_alias_domain(mail_host)


class _FakeList:
    """Duck-typed list for the `IMailTransportAgentAliases` interface."""

    def __init__(self, list_name, mail_host, true_mail_host):
        self.list_name = list_name
        self.mail_host = mail_host
        self.true_mail_host = true_mail_host
        self.posting_address = '{}@{}'.format(list_name, self.mail_host)


@public
@implementer(IMailTransportAgentLifecycle)
class LMTP:
    """Connect Mailman to Postfix via LMTP."""

    def __init__(self):
        # Locate and read the Postfix specific configuration file.
        mta_config = external_configuration(config.mta.configuration)
        self.transport_file_type = mta_config.get(
            'postfix', 'transport_file_type'
        )
        # 'regex' for backward compatibility with Mailman Core 3.3.10.
        if self.transport_file_type not in ('regexp', 'regex'):
            self.postmap_command = mta_config.get('postfix', 'postmap_command')

    def create(self, mlist):
        """See `IMailTransportAgentLifecycle`."""
        # We can ignore the mlist argument because for LMTP delivery, we just
        # generate the entire file every time.
        self.regenerate()

    delete = create

    def regenerate(self, directory=None):
        """See `IMailTransportAgentLifecycle`."""
        # Acquire a lock file to prevent other processes from racing us here.
        if directory is None:
            directory = config.DATA_DIR
        lock_file = os.path.join(config.LOCK_DIR, 'mta')
        with Lock(lock_file):
            lmtp_path = os.path.join(directory, 'postfix_lmtp')
            with atomic(lmtp_path) as fp:
                self._generate_lmtp_file(fp)
            domains_path = os.path.join(directory, 'postfix_domains')
            with atomic(domains_path) as fp:
                self._generate_domains_file(fp)
            vmap_path = os.path.join(directory, 'postfix_vmap')
            with atomic(vmap_path) as fp:
                vmap = self._generate_vmap_file(fp)
            if not vmap:
                # If we didn't write anything, remove the file.
                os.remove(vmap_path)
            # If the transport_file_type is 'hash' then run the postmap command
            # on newly generated file to convert them in to hash table like
            # Postfix wants.
            if self.postmap_command:
                errors = []
                files = [lmtp_path, domains_path]
                if vmap:
                    files.append(vmap_path)
                for path in files:
                    command = self.postmap_command
                    if self.transport_file_type == 'default':
                        # Follow the `default_database_type` setting in Postfix
                        command += f' {path}'
                    else:
                        command += f' {self.transport_file_type}:{path}'
                    status = (os.system(command) >> 8) & 0xff
                    if status:
                        msg = 'command failure: %s, %s, %s'
                        errstr = os.strerror(status)
                        log.error(msg, command, status, errstr)
                        errors.append(msg % (command, status, errstr))
                if errors:
                    raise RuntimeError(NL.join(errors))

    def _generate_lmtp_file(self, fp):
        # The format for Postfix's LMTP transport map is defined here:
        # https://www.postfix.org/transport.5.html
        #
        # Sort all existing mailing list names first by domain, then by
        # local part.  For Postfix we need a dummy entry for the domain.
        list_manager = getUtility(IListManager)
        fake_list_manager = _FakeListManager()
        utility = getUtility(IMailTransportAgentAliases)
        by_domain = {}
        sort_key = attrgetter('list_name')
        for list_name, mail_host in list_manager.name_components:
            mlist = fake_list_manager.get(list_name, mail_host)
            by_domain.setdefault(mlist.mail_host, []).append(mlist)
        print("""\
# AUTOMATICALLY GENERATED BY MAILMAN ON {}
#
# This file is generated by Mailman, and is kept in sync with the binary hash
# file.  YOU SHOULD NOT MANUALLY EDIT THIS FILE unless you know what you're
# doing, and can keep the two files properly in sync.  If you screw it up,
# you're on your own.
    """.format(now().replace(microsecond=0)), file=fp)
        for domain in sorted(by_domain):
            print("""\
# Aliases which are visible only in the @{} domain.""".format(domain),
                  file=fp)
            for mlist in sorted(by_domain[domain], key=sort_key):
                aliases = list(utility.aliases(mlist))
                width = max(len(alias) for alias in aliases) + \
                    aliases[0].count('.') + 10
                print(ALIASTMPL.format(self._decorate(aliases.pop(0)),
                                       config, width), file=fp)
                for alias in aliases:
                    print(ALIASTMPL.format(self._decorate(alias),
                                           config, width), file=fp)
                print(file=fp)

    def _decorate(self, name):
        # Postfix regexp tables need regexp matching listname or domains. This
        # method just decorates the name to be printed in the transport map
        # file or relay domains file.
        # We have to do a bit more with the -bounces and -confirm names as
        # they can have + extra information and that results in no match in
        # regexp tables.
        # 'regex' for backward compatibility with Mailman Core 3.3.10.
        if self.transport_file_type in ('regexp', 'regex'):
            local, at, domain = name.partition('@')
            if local.endswith('-bounces') or local.endswith('-confirm'):
                local = local.replace('.', '\\.')
                domain = domain.replace('.', '\\.')
                return '/^{}(\\+.*)?@{}$/'.format(local, domain)
            else:
                return '/^{}$/'.format(name).replace('.', '\\.')
        return name

    def _generate_domains_file(self, fp):
        # Uniquify the domains, then sort them alphabetically.
        domains = set()
        for list_name, mail_host in getUtility(IListManager).name_components:
            domains.add(mail_host)
        print("""\
# AUTOMATICALLY GENERATED BY MAILMAN ON {}
#
# This file is generated by Mailman, and is kept in sync with the binary hash
# file.  YOU SHOULD NOT MANUALLY EDIT THIS FILE unless you know what you're
# doing, and can keep the two files properly in sync.  If you screw it up,
# you're on your own.
""".format(now().replace(microsecond=0)), file=fp)
        for domain in sorted(domains):
            print('{} {}'.format(
                self._decorate(_get_alias_domain(domain)), domain), file=fp)
        print(file=fp)

    def _generate_vmap_file(self, fp):
        # This generates a virtual alias mapping for lists in domains with an
        # alias_domain to map the email_domain addresses to the alias_domain.
        #
        # Sort all existing mailing list names first by domain, then by
        # local part.  For Postfix we need a dummy entry for the domain.
        list_manager = getUtility(IListManager)
        fake_list_manager = _FakeListManager()
        utility = getUtility(IMailTransportAgentAliases)
        by_domain = defaultdict(list)
        sort_key = attrgetter('list_name')
        for list_name, mail_host in list_manager.name_components:
            mlist = fake_list_manager.get(list_name, mail_host)
            if mail_host == mlist.mail_host:
                continue
            by_domain[mail_host].append(mlist)
        if len(by_domain) == 0:
            return False
        print("""\
# AUTOMATICALLY GENERATED BY MAILMAN ON {}
#
# This file is generated by Mailman, and is kept in sync with the binary hash
# file.  YOU SHOULD NOT MANUALLY EDIT THIS FILE unless you know what you're
# doing, and can keep the two files properly in sync.  If you screw it up,
# you're on your own.
    """.format(now().replace(microsecond=0)), file=fp)
        for domain in sorted(by_domain):
            print("""\
# Virtual mappings for the @{} domain.""".format(domain), file=fp)
            for mlist in sorted(by_domain[domain], key=sort_key):
                aliases = list(utility.destinations(mlist))
                width = (max(len(alias) for alias in aliases) +
                         len(mlist.true_mail_host) + 14)
                for alias in aliases:
                    addr = '{}@{}'.format(alias, mlist.mail_host)
                    true_addr = self._decorate(
                        '{}@{}'.format(alias, mlist.true_mail_host))
                    line = VMAPTMPL.format(true_addr, width, addr)
                    print(line, file=fp)
                print(file=fp)
        return True
