# Copyright (C) 2011-2023 by the Free Software Foundation, Inc.
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
# GNU Mailman.  If not, see <http://www.gnu.org/licenses/>.

"""Test the outgoing runner."""

import tempfile
import unittest
import mailman.handlers.arc_sign

from dkim import DKIMException
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.handlers.arc_sign import ARCSign
from mailman.testing.helpers import (
    LogFileMark,
    specialized_message_from_string as message_from_string,
)
from mailman.testing.layers import ConfigLayer
from unittest.mock import patch


class TestARCSignMessage(unittest.TestCase):
    """Test Authentication-Results generation."""
    layer = ConfigLayer

    def setUp(self):
        privkey = b"""-----BEGIN RSA PRIVATE KEY-----
MIICXQIBAAKBgQDkHlOQoBTzWRiGs5V6NpP3idY6Wk08a5qhdR6wy5bdOKb2jLQi
Y/J16JYi0Qvx/byYzCNb3W91y3FutACDfzwQ/BC/e/8uBsCR+yz1Lxj+PL6lHvqM
KrM3rG4hstT5QjvHO9PzoxZyVYLzBfO2EeC3Ip3G+2kryOTIKT+l/K4w3QIDAQAB
AoGAH0cxOhFZDgzXWhDhnAJDw5s4roOXN4OhjiXa8W7Y3rhX3FJqmJSPuC8N9vQm
6SVbaLAE4SG5mLMueHlh4KXffEpuLEiNp9Ss3O4YfLiQpbRqE7Tm5SxKjvvQoZZe
zHorimOaChRL2it47iuWxzxSiRMv4c+j70GiWdxXnxe4UoECQQDzJB/0U58W7RZy
6enGVj2kWF732CoWFZWzi1FicudrBFoy63QwcowpoCazKtvZGMNlPWnC7x/6o8Gc
uSe0ga2xAkEA8C7PipPm1/1fTRQvj1o/dDmZp243044ZNyxjg+/OPN0oWCbXIGxy
WvmZbXriOWoSALJTjExEgraHEgnXssuk7QJBALl5ICsYMu6hMxO73gnfNayNgPxd
WFV6Z7ULnKyV7HSVYF0hgYOHjeYe9gaMtiJYoo0zGN+L3AAtNP9huqkWlzECQE1a
licIeVlo1e+qJ6Mgqr0Q7Aa7falZ448ccbSFYEPD6oFxiOl9Y9se9iYHZKKfIcst
o7DUw1/hz2Ck4N5JrgUCQQCyKveNvjzkkd8HjYs0SwM0fPjK16//5qDZ2UiDGnOe
uEzxBDAr518Z8VFbR41in3W4Y3yCDgQlLlcETrS+zYcL
-----END RSA PRIVATE KEY-----
"""
        self.keyfile = tempfile.NamedTemporaryFile(delete=True)
        self.keyfile.write(privkey)
        self.keyfile.flush()

        mailman.handlers.arc_sign.timestamp = "12345"

    def tearDown(self):
        self.keyfile.close()

    def test_arc_sign_message(self):
        config.push('arc_sign', """
        [ARC]
        enabled: yes
        authserv_id: lists.example.org
        selector: dummy
        domain: example.org
        sig_headers: mime-version, date, from, to, subject
        privkey: %s
        """ % (self.keyfile.name))

        self.addCleanup(config.pop, 'arc_sign')

        lst = create_list('test@example.com')
        msgdata = {'ARC-Standardize': True}

        msg = """Authentication-Results: lists.example.org; arc=none;
        spf=pass smtp.mfrom=jqd@d1.example;
        dkim=pass header.i=@d1.example; dmarc=pass
MIME-Version: 1.0
Return-Path: <jqd@d1.example.org>
Received: by 10.157.14.6 with HTTP; Tue, 3 Jan 2017 12:22:54 -0800 (PST)
Message-ID: <54B84785.1060301@d1.example.org>
Date: Thu, 14 Jan 2015 15:00:01 -0800
From: John Q Doe <jqd@d1.example.org>
To: arc@dmarc.org
Subject: Example 1

Hey gang,
This is a test message.
--J."""

        msg = message_from_string(msg)

        ARCSign().process(lst, msg, msgdata)

        res = ["i=1;lists.example.org;arc=none;spf=passsmtp.mfrom=jqd@d1"
               ".example;dkim=passheader.i=@d1.example;dmar"
               "c=pass"]
        self.assertEqual("".join(msg["ARC-Authentication-Results"].split()),
                         "".join(res))

        sig = """a=rsa-sha256;
b=XWeK9DxQ8MUm+Me5GLZ5lQ3L49RdoFv7m7VlrAkKb3/C7jjw33TrTY0KYI5lkowvEGnAtm
5lAqLz67FxA/VrJc2JiYFQR/mBoJLLz/hh9y77byYmSO9tLfIDe2A83+6QsXHO3K6PxTz7+v
rCB4wHD9GADeUKVfHzmpZhFuYOa88=;
bh=KWSe46TZKCcDbH4klJPo+tjk5LWJnVRlP5pvjXFZYLQ=; c=relaxed/relaxed;
d=example.org; h=mime-version:date:from:to:subject;
i=1; s=dummy; t=12345"""
        sig = set("".join(sig.split()).split(";"))
        expected = "".join(msg["ARC-Message-Signature"].split()).split(";")
        expected = set(expected)
        self.assertEqual(sig, expected)

        seal = "".join(["a=rsa-sha256;b=OI+GmgigM5/fJ8u+yoYCoT70X"
                        "a433kSYLvhZWtx4HdLnLB2wvKy9zX7KHFVOhfhZ6"
                        "gWtsfMAufWx0rw+xgZNseDnejbz++2pPfj+jKUg+"
                        "JF7VC15IbpPUloISLKjfiK1cxC+u7s9NLu1wI4QV"
                        "sAT/M6RQ42MauUR/dBSdQdSMho=; cv=none; d="
                        "example.org; i=1; s=dummy; t=12345"])
        seal = set("".join(seal.split()).split(";"))
        expected = set("".join(msg["ARC-Seal"].split()).split(";"))
        self.assertEqual(seal, expected)

    # I *believe* that this test from Gene Shuman's PR is incorrect.  As I
    # read the currect draft
    # https://tools.ietf.org/html/draft-ietf-dmarc-arc-protocol-23#section-5.2
    # the ARC Validator SHOULD have added an arc=none clause to the field,
    # but its absence doesn't invalidate the Authentication-Results field.
    # As far as I can see the draft says nothing about a missing arc method
    # in the Authentication-Results field(s) used by the Signer.
    # I suspect that the Signer might want to add a "missing arc" warning,
    # but it's unclear what the syntax should be.
    # Differences from previous test: no sig_headers in config, no arc=none
    # in Authentication-Results.
    @unittest.expectedFailure
    def test_arc_sign_message_no_chain_validation(self):
        config.push('arc_sign', """
        [ARC]
        enabled: yes
        authserv_id: lists.example.org
        selector: dummy
        domain:   example.org
        privkey: %s
        """ % (self.keyfile.name))

        self.addCleanup(config.pop, 'arc_sign')

        lst = create_list('test@example.com')
        msgdata = {}

        msg = """Authentication-Results: lists.example.org;
        spf=pass smtp.mfrom=jqd@d1.example;
        dkim=pass (1024-bit key) header.i=@d1.example; dmarc=pass
MIME-Version: 1.0
Return-Path: <jqd@d1.example.org>
Received: by 10.157.14.6 with HTTP; Tue, 3 Jan 2017 12:22:54 -0800 (PST)
Message-ID: <54B84785.1060301@d1.example.org>
Date: Thu, 14 Jan 2015 15:00:01 -0800
From: John Q Doe <jqd@d1.example.org>
To: arc@dmarc.org
Subject: Example 1

Hey gang,
This is a test message.
--J."""

        msg = message_from_string(msg)
        ARCSign().process(lst, msg, msgdata)

        self.assertEqual("ARC-Authentication-Results" in msg, False)
        self.assertEqual("ARC-Message-Signature" in msg, False)
        self.assertEqual("ARC-Seal" in msg, False)

    # This test is now expected to fail since we now only log the exception
    # and don't re-raise it.
    @unittest.expectedFailure
    @patch('mailman.handlers.arc_sign.sign_message', side_effect=DKIMException)
    def test_arc_sign_raises_exception(self, mocked_sign_message):
        config.push('arc_sign', """
        [ARC]
        enabled: yes
        authserv_id: test.example.org
        selector: dummy
        domain: example.org
        privkey: %s
        """ % (self.keyfile.name))

        self.addCleanup(config.pop, 'arc_sign')

        lst = create_list('test@example.com')
        msgdata = {}

        msg = """\
Message-ID: <54B84785.1060301@d1.example.org>
Date: Thu, 14 Jan 2015 15:00:01 -0800
From: John Q Doe <jqd@d1.example.org>
To: test@example.com
Subject: Example 1

Hey gang,
This is a test message.
--J."""

        msg = message_from_string(msg)
        with self.assertRaises(DKIMException):
            ARCSign().process(lst, msg, msgdata)
        self.assertTrue(mocked_sign_message.called)

    def test_dkim_exception_is_logged(self):

        config.push('arc_sign', """
        [ARC]
        enabled: yes
        dmarc: yes
        dkim: yes
        authserv_id: example.com
        selector: dummy
        domain: example.com
        privkey: %s
        """ % (self.keyfile.name))

        self.addCleanup(config.pop, 'arc_sign')

        lst = create_list('test@example.com')
        msgdata = {}

        msg = """\
Authentication-Results: example.com; arc=none; dkim=fail; arc=none; dmarc=none
Received: from example.com (localhost [127.0.0.1])
        by mail.example.com (Postfix) with ESMTP id B9E401F93BD
        for <test@example.com>; Mon, 26 Apr 2021 16:21:37 -0700 (PDT)
From: User <user@example.org>
To: test@example.com
Date: Mon, 26 Apr 2021 23:21:37 -0000
Message-ID: <161947929775.27402.13795118666345547446@example.com>
Subject: A test...

Message body
"""
        msg = message_from_string(msg)
        mark = LogFileMark('mailman.error')
        ARCSign().process(lst, msg, msgdata)
        self.assertIn(
            'ParameterError: No existing chain found on message, '
            'cv should be none', mark.read())
