# Copyright (C) 2016-2025 by the Free Software Foundation, Inc.
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

"""Tests and mocks for gatenews subcommand."""

import os
import nntplib

from click.testing import CliRunner
from collections import namedtuple
from email.errors import MessageError
from mailman.app.lifecycle import create_list
from mailman.commands.cli_gatenews import gatenews
from mailman.config import config
from mailman.testing.helpers import get_queue_messages, LogFileMark
from mailman.testing.layers import ConfigLayer
from unittest import TestCase
from unittest.mock import patch


def get_nntplib_nntp(fail=0):
    """Create a nntplib.NNTP mock.

    This is used to return predictable responses to a nntplib.NNTP method
    calls.  It can also be called with non-zero values of 'fail' to raise
    specific exceptions.

    It only implements those classes and attributes used by the gatenews
    command.
    """

    info = namedtuple('info', ('number', 'message_id', 'lines'))

    def make_header(art_num):
        lines = [b'From: ann@example.com',
                 b'Newsgroups: my.group',
                 b'Subject: A Message',
                 b'To: my.group@news.example.com',
                 ]
        if art_num == 1:
            lines.extend([b'Message-ID: <msg1@example.com>',
                          b'List-Id: This is my list on two lines',
                          b' <mylist.example.com>'
                          ])
        elif art_num == 2:
            lines.append(b'Message-ID: <msg2@example.com>')
        elif art_num == 3:
            lines.extend([b'Message-ID: <msg3@example.com>',
                         b'List-Id: My list <mylist.example.com>'])
        return lines

    class NNTP:
        # The NNTP connection class
        def __init__(self, host, port=119, user=None, password=None,
                     readermode=None):
            if fail == 1:
                raise nntplib.NNTPError('Bad call to NNTP')
            self.host = host
            self.port = port
            self.user = user
            self.password = password
            self.readermode = readermode

        def group(self, group_name):
            if group_name == 'my.group':
                return ('', 3, 1, 3, group_name)
            else:
                raise nntplib.NNTPTemporaryError(
                    'No such group: {}'.format(group_name))

        def head(self, art_num):
            if art_num not in (1, 2, 3):
                raise nntplib.NNTPTemporaryError('Bad call to head')
            lines = make_header(art_num)
            info.number = art_num
            info.message_id = '<msg{}@example.com'.format(art_num),
            info.lines = lines
            return ('', info)

        def article(self, art_num):
            if art_num not in (1, 2, 3):
                raise nntplib.NNTPTemporaryError('Bad call to article')
            if art_num == 2 and fail == 2:
                raise nntplib.NNTPTemporaryError('Bad call to article')
            lines = make_header(art_num)
            lines.extend([b'', b'This is the message body'])
            info.number = art_num
            info.message_id = '<msg{}@example.com'.format(art_num),
            info.lines = lines
            return ('', info)

        def quit(self):
            pass

    patcher = patch('nntplib.NNTP', NNTP)
    return patcher


class Test_gatenews(TestCase):
    """Test gating messages from usenet."""

    layer = ConfigLayer

    def setUp(self):
        self._command = CliRunner()
        config.push('gatenews tests', """\
        [nntp]
        host: news.example.com
        """)
        self.addCleanup(config.pop, 'gatenews tests')
        self.mlist = create_list('mylist@example.com')
        self.mlist.linked_newsgroup = 'my.group'
        self.mlist.usenet_watermark = 0
        self.mlist.gateway_to_mail = True
        # Create a second list without gateway for test coverage purposes.
        create_list('otherlist@example.com')
        os.environ['_MAILMAN_GATENEWS_NNTP'] = 'yes'
        # Also get the subject_prefix process.
        self._process = config.handlers['subject-prefix'].process

    def test_bad_nntp_connect(self):
        mark = LogFileMark('mailman.fromusenet')
        with get_nntplib_nntp(fail=1):
            self._command.invoke(gatenews)
        lines = mark.read().splitlines()
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].endswith('error opening connection '
                                          'to nntp_host: news.example.com'))
        self.assertEqual(lines[1], 'Bad call to NNTP')
        self.assertTrue(lines[2].endswith('NNTP error for list '
                                          'mylist@example.com:'))
        self.assertEqual(lines[3], 'Bad call to NNTP')

    def test_bad_group(self):
        self.mlist.linked_newsgroup = 'other.group'
        mark = LogFileMark('mailman.fromusenet')
        with get_nntplib_nntp():
            self._command.invoke(gatenews)
        lines = mark.read().splitlines()
        self.assertEqual(len(lines), 2)
        self.assertTrue(lines[0].endswith('NNTP error for list '
                                          'mylist@example.com:'))
        self.assertEqual(lines[1], 'No such group: other.group')

    def test_catchup_only(self):
        self.mlist.usenet_watermark = None
        mark = LogFileMark('mailman.fromusenet')
        with get_nntplib_nntp():
            self._command.invoke(gatenews)
        lines = mark.read().splitlines()
        self.assertEqual(self.mlist.usenet_watermark, 3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].endswith('mylist@example.com: [1..3]'))
        self.assertTrue(lines[1].endswith('mylist@example.com '
                                          'caught up to article 3'))
        self.assertTrue(lines[2].endswith('mylist@example.com watermark: 3'))

    def test_up_to_date(self):
        self.mlist.usenet_watermark = 3
        mark = LogFileMark('mailman.fromusenet')
        with get_nntplib_nntp():
            self._command.invoke(gatenews)
        lines = mark.read().splitlines()
        self.assertEqual(self.mlist.usenet_watermark, 3)
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].endswith('mylist@example.com: [1..3]'))
        self.assertTrue(lines[1].endswith('nothing new for list '
                                          'mylist@example.com'))
        self.assertTrue(lines[2].endswith('mylist@example.com watermark: 3'))

    def test_post_only_one_of_three(self):
        mark = LogFileMark('mailman.fromusenet')
        with get_nntplib_nntp():
            self._command.invoke(gatenews)
        lines = mark.read().splitlines()
        self.assertEqual(self.mlist.usenet_watermark, 3)
        self.assertEqual(len(lines), 4)
        self.assertTrue(lines[0].endswith('mylist@example.com: [1..3]'))
        self.assertTrue(lines[1].endswith('gating mylist@example.com '
                                          'articles [1..3]'))
        self.assertTrue(lines[2].endswith('posted to list mylist@example.com:'
                                          '       2'))
        self.assertTrue(lines[3].endswith('mylist@example.com watermark: 3'))
        items = get_queue_messages('in', expected_count=1)
        msg = items[0].msg
        msgdata = items[0].msgdata
        self.assertTrue(msgdata.get('fromusenet', False))
        self.assertEqual(msg.get('message-id', ''), '<msg2@example.com>')

    def test_article_exception(self):
        mark = LogFileMark('mailman.fromusenet')
        with get_nntplib_nntp(fail=2):
            self._command.invoke(gatenews)
        lines = mark.read().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[0].endswith('mylist@example.com: [1..3]'))
        self.assertTrue(lines[1].endswith('gating mylist@example.com '
                                          'articles [1..3]'))
        self.assertTrue(lines[2].endswith('NNTP error for list '
                                          'mylist@example.com:       2'))
        self.assertEqual(lines[3], 'Bad call to article')
        self.assertTrue(lines[4].endswith('mylist@example.com watermark: 3'))

    def test_email_parser_exception(self):
        mark = LogFileMark('mailman.fromusenet')
        with patch('mailman.commands.cli_gatenews.message_from_bytes',
                   side_effect=MessageError('Bad message')):
            with get_nntplib_nntp():
                self._command.invoke(gatenews)
        lines = mark.read().splitlines()
        self.assertEqual(len(lines), 5)
        self.assertTrue(lines[0].endswith('mylist@example.com: [1..3]'))
        self.assertTrue(lines[1].endswith('gating mylist@example.com '
                                          'articles [1..3]'))
        self.assertTrue(lines[2].endswith('email package exception for '
                                          'my.group:2'))
        self.assertEqual(lines[3], 'Bad message')
        self.assertTrue(lines[4].endswith('mylist@example.com watermark: 3'))

    def test_original_size_in_msgdata_and_message(self):
        with get_nntplib_nntp():
            self._command.invoke(gatenews)
        items = get_queue_messages('in', expected_count=1)
        msgdata = items[0].msgdata
        msg = items[0].msg
        self.assertTrue(msgdata.get('original_size', False))
        self.assertEqual(msgdata['original_size'], 184)
        self.assertTrue(hasattr(msg, 'original_size'))
        self.assertEqual(msg.original_size, 184)

    def test_wont_run_without_environment(self):
        del os.environ['_MAILMAN_GATENEWS_NNTP']
        result = self._command.invoke(gatenews)
        self.assertNotEqual(result.exit_code, 0)
        self.assertIn('Error: The gatenews command is run periodically',
                      result.output)

    def test_subject_prefixing(self):
        with get_nntplib_nntp():
            self._command.invoke(gatenews)
        items = get_queue_messages('in', expected_count=1)
        msgdata = items[0].msgdata
        msg = items[0].msg
        self._process(self.mlist, msg, msgdata)
        self.assertEqual(msg['subject'], '[Mylist] A Message')
