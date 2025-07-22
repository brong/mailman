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

"""Application support for pipeline processing."""

import logging

from mailman.app.bounces import bounce_message
from mailman.config import config
from mailman.interfaces.handler import IHandler
from mailman.interfaces.pipeline import (
    DiscardMessage,
    IPipeline,
    RejectMessage,
)
from mailman.utilities.modules import add_components
from public import public


dlog = logging.getLogger('mailman.debug')
vlog = logging.getLogger('mailman.vette')


@public
def process(mlist, msg, msgdata, pipeline_name='built-in'):
    """Process the message through the given pipeline.

    :param mlist: the IMailingList for this message.
    :param msg: The Message object.
    :param msgdata: The message metadata dictionary.
    :param pipeline_name: The name of the pipeline to process through.
    """
    message_id = msg.get('message-id', 'n/a')
    pipeline = config.pipelines[pipeline_name]
    for handler in pipeline:
        dlog.debug('{} pipeline {} processing: {}'.format(
            message_id, pipeline_name, handler.name))
        try:
            handler.process(mlist, msg, msgdata)
        except DiscardMessage as error:
            vlog.info(
                '{} discarded by "{}" pipeline handler "{}": {}'.format(
                    message_id, pipeline_name, handler.name, error.message))
            # Stop processing the pipeline.
            break
        except RejectMessage as error:
            vlog.info(
                '{} rejected by "{}" pipeline handler "{}": {}'.format(
                    message_id, pipeline_name, handler.name, str(error)))
            bounce_message(mlist, msg, error)
            # Stop processing the pipeline.
            break


@public
def initialize():
    """Initialize the pipelines."""
    # Find all handlers in the registered plugins.
    add_components('handlers', IHandler, config.handlers)
    # Set up some pipelines.
    add_components('pipelines', IPipeline, config.pipelines)
