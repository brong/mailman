# Copyright (C) 2018-2025 by the Free Software Foundation, Inc.
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

"""Command line fixes for some bugs."""

from mailman.config import config
from mailman.model.address import Address
from mailman.model.member import Member
from public import public
from sqlalchemy import func


@public
def remove_duplicate_addresses():
    """
    Remove duplicate Address records from the database and fix their
    subscriptions so that a User doesn't loose their subscriptions.

    This iterates over all the Address records, creates a list of Email
    addresses that are duplicate. It then creates a list of memberships
    for all those emails and subscribes a single address on all memberships.

    It then deletes all but the first email address.
    """
    # Get all the duplicated entries.
    dup_addresses = config.db.store.query(
        Address.email).group_by(
        Address.email).having(
        func.count(Address.email) > 1).all()
    # Iterate over all the duplicate entries and check which one has
    # subscriptions attached with it, then delete the other one.
    for email in dup_addresses:
        all_objs = config.db.store.query(
            Address).filter(
            Address.email == email.email).all()
        # Due to #476, these duplicate records are created when a User tries to
        # link an already existing Address. So, we get all the memberships
        # linked to all duplicate addresses and subscribe one of the addresses
        # to all memberships and just delete rest of the addresses
        all_memberships = []
        for address in all_objs:
            results = config.db.store.query(
                Member).filter(Member._address == address).all()
            if len(results):
                all_memberships.extend(results)
                # Delete all except first email object.
        right_address = all_objs[0]
        for address in all_objs[1:]:
            config.db.store.delete(address)
            # Update memberships.
        for membership in all_memberships:
            membership.address_id = right_address.id
            config.db.store.commit()
