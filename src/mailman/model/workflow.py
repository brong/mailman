# Copyright (C) 2015-2025 by the Free Software Foundation, Inc.
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

"""Model for workflow states."""

from mailman.database.model import Model
from mailman.database.transaction import dbconnection
from mailman.database.types import SAUnicode, SAUnicodeLarge
from mailman.interfaces.workflow import IWorkflowState, IWorkflowStateManager
from public import public
from sqlalchemy import Column
from zope.interface import implementer


@public
@implementer(IWorkflowState)
class WorkflowState(Model):
    """Workflow states."""

    __tablename__ = 'workflowstate'

    token = Column(SAUnicode, primary_key=True)
    step = Column(SAUnicode)
    data = Column(SAUnicodeLarge)


@public
@implementer(IWorkflowStateManager)
class WorkflowStateManager:
    """See `IWorkflowStateManager`."""

    @dbconnection
    def save(self, store, token, step=None, data=None):
        """See `IWorkflowStateManager`."""
        state = WorkflowState(token=token, step=step, data=data)
        store.add(state)

    @dbconnection
    def restore(self, store, token):
        """See `IWorkflowStateManager`."""
        state = store.query(WorkflowState).get(token)
        if state is not None:
            store.delete(state)
        return state

    @dbconnection
    def discard(self, store, token):
        """See `IWorkflowStateManager`."""
        state = store.query(WorkflowState).get(token)
        if state is not None:
            store.delete(state)

    @dbconnection
    def get_all_tokens(self, store):
        yield from [x[0] for x in store.query(WorkflowState.token).all()]

    @property
    @dbconnection
    def count(self, store):
        """See `IWorkflowStateManager`."""
        return store.query(WorkflowState).count()
