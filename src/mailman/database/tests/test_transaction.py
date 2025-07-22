# Copyright (C) 2022-2025 by the Free Software Foundation, Inc.
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

"""Tests for database transactions."""

import unittest
import sqlalchemy

from mailman.config import config
from mailman.database.transaction import api_transaction
from mailman.testing.layers import ConfigLayer
from sqlalchemy import Column, inspect, Integer, String
from sqlalchemy.orm import declarative_base


Base = declarative_base()


class AModel(Base):
    __tablename__ = 'atable'

    id = Column(Integer, primary_key=True)
    data = Column(String(50))
    nos = Column(Integer)


class APITransactionTest(unittest.TestCase):
    layer = ConfigLayer

    def setUp(self):
        Base.metadata.create_all(config.db.engine)

    def tearDown(self) -> None:
        Base.metadata.drop_all(config.db.engine)

    def test_api_transaction_closes_session(self):
        session = config.db.store

        @api_transaction
        def example():
            new_obj = AModel(data='some data')
            session.add(new_obj)
            return new_obj

        obj = example()

        self.assertTrue(inspect(obj).detached)
        # Trying to access the .id will try to refresh the object from the
        # session, but since it is closed, it will raise error. ORM objects
        # are not supposed to live longer than their session lifecycle.
        with self.assertRaises(sqlalchemy.orm.exc.DetachedInstanceError):
            obj.nos

        def without_transaction(data, nos=None):
            new_obj = AModel(data=data, nos=nos)
            # Using the same session as above since the sessions can be
            # reused after the close.
            session.add(new_obj)
            return new_obj

        valid_obj = without_transaction('another data')
        # This will try to refresh from db and the id is None since we didn't
        # set it. But it won't raise an exception since the session is still
        # open.
        self.assertIsNone(valid_obj.id)
        session.commit()
        # If we update the obj, sesison will become dirty.
        valid_obj.nos = 100
        self.assertTrue(session.dirty)
        # Committing the transaction will clean up session.
        session.commit()
        self.assertFalse(session.dirty)
        # The object is still attached to the session.
        self.assertFalse(inspect(valid_obj).detached)

        # If we close the session after adding the object, the
        # object doesn't get updated on committing since it is detached.
        another_obj = without_transaction('another valid data', nos=101)
        self.assertTrue(session.new)
        session.commit()
        self.assertFalse(inspect(another_obj).detached)
        session.close()
        # This is because another_obj is in detached state as we closed
        # the session it was attached to.
        self.assertTrue(inspect(another_obj).detached)
