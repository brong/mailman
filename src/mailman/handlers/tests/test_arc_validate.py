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

import logging
import tempfile
import unittest
import mailman.handlers.validate_authenticity

from dns.resolver import Timeout
from mailman.app.lifecycle import create_list
from mailman.config import config
from mailman.handlers.validate_authenticity import ValidateAuthenticity
from mailman.testing.helpers import (
    specialized_message_from_string as message_from_string,
)
from mailman.testing.layers import ConfigLayer
from unittest.mock import patch


def get_mock_dnsfunc(mapping):
    """Mock dnsfunc to pass to load_pk_from_dns."""

    def mock_dnsfunc(dom, **kw):
        return mapping.get(dom)

    return mock_dnsfunc


class TestValidateAuthenticity(unittest.TestCase):
    """Test Authentication-Results generation."""
    layer = ConfigLayer

    def setUp(self):
        # stub dns
        dkim0 = ["v=DKIM1; k=rsa; p=MIIBIjANBgkqhkiG9w0BAQEFAAO"
                 "CAQ8AMIIBCgKCAQEAg1i2lO83x/r58cbo/JSBwfZrrct6S/"
                 "yi4L6GsG3wNgFE9lO3orzBwnAEJJM33WrvJfOWia1fAx64V"
                 "s1QEpYtLFCzyeIhDDMaHv/G8NgKPgnWK4gI8/x2Q2SYCmiq"
                 "il66oHaSOC2phMDRI+c/Q35MlZbc2FqlgevpKzdCg+YE6mY"
                 "A0XN7/tdQplbx4meLVsVPIL9QCP4yu8oBsNqcwyxkQafJuc"
                 "VyoZI+VEO+dySw3QXNdmJhr7y1hD1tCNqoAG0iphKQVXPXm"
                 "GnGhaxaVU92Kq5UKL6/LiTZ1piqyJfJyZ/zCgH+mtY8MNk9"
                 "f7LHpwFljI7TbYmr7MmV3d6xj3sghwIDAQAB"]
        dkim1 = ["v=DKIM1; k=rsa; p=MIGfMA0GCSqGSIb3DQEBAQUAA4GNA"
                 "DCBiQKBgQDkHlOQoBTzWRiGs5V6NpP3idY6Wk08a5qhdR6w"
                 "y5bdOKb2jLQiY/J16JYi0Qvx/byYzCNb3W91y3FutACDfzw"
                 "Q/BC/e/8uBsCR+yz1Lxj+PL6lHvqMKrM3rG4hstT5QjvHO9"
                 "PzoxZyVYLzBfO2EeC3Ip3G+2kryOTIKT+l/K4w3QIDAQAB"]
        dmarc = ["v=DMARC1; p=reject; rua=mailto:dmarc.reports@"
                 "valimail.com,mailto:dmarc_agg@vali.email; ruf="
                 "mailto:dmarc.reports@valimail.com,mailto:dmarc_"
                 "c0cb7153_afrf@vali.email"]

        records = {b"google2048._domainkey.valimail.com.": ''.join(dkim0),
                   b"_dmarc.valimail.com.": ''.join(dmarc),
                   b"dummy._domainkey.example.org.": ''.join(dkim1)}
        mailman.handlers.validate_authenticity.dnsfunc = get_mock_dnsfunc(
            records)
        self.keyfile = tempfile.NamedTemporaryFile(delete=True)

    def tearDown(self):
        self.keyfile.close()

    def test_chain_validation_pass(self):
        config.push('dkim_and_cv', """
        [ARC]
        enabled: yes
        authserv_id: lists.example.org
        dkim: no
        dmarc: no
        privkey: {}
        """.format(self.keyfile.name))
        self.addCleanup(config.pop, 'dkim_and_cv')

        lst = create_list('test@example.com')
        msgdata = {}

        msg = message_from_string("""MIME-Version: 1.0
Return-Path: <jqd@d1.example.org>
ARC-Seal: a=rsa-sha256;
    b=dOdFEyhrk/tw5wl3vMIogoxhaVsKJkrkEhnAcq2XqOLSQhPpGzhGBJzR7k1sWGokon3TmQ
    7TX9zQLO6ikRpwd/pUswiRW5DBupy58fefuclXJAhErsrebfvfiueGyhHXV7C1LyJTztywzn
    QGG4SCciU/FTlsJ0QANrnLRoadfps=; cv=none; d=example.org; i=1; s=dummy;
    t=12345
ARC-Message-Signature: a=rsa-sha256;
    b=QsRzR/UqwRfVLBc1TnoQomlVw5qi6jp08q8lHpBSl4RehWyHQtY3uOIAGdghDk/mO+/Xpm
    9JA5UVrPyDV0f+2q/YAHuwvP11iCkBQkocmFvgTSxN8H+DwFFPrVVUudQYZV7UDDycXoM6UE
    cdfzLLzVNPOAHEDIi/uzoV4sUqZ18=;
    bh=KWSe46TZKCcDbH4klJPo+tjk5LWJnVRlP5pvjXFZYLQ=; c=relaxed/relaxed;
    d=example.org;
    h=from:to:date:subject:mime-version:arc-authentication-results;
    i=1; s=dummy; t=12345
ARC-Authentication-Results: i=1; lists.example.org;
    spf=pass smtp.mfrom=jqd@d1.example;
    dkim=pass (1024-bit key) header.i=@d1.example;
    dmarc=pass
Received: from segv.d1.example (segv.d1.example [72.52.75.15])
    by lists.example.org (8.14.5/8.14.5) with ESMTP id t0EKaNU9010123
    for <arc@example.org>; Thu, 14 Jan 2015 15:01:30 -0800 (PST)
    (envelope-from jqd@d1.example)
Authentication-Results: lists.example.org;
    spf=pass smtp.mfrom=jqd@d1.example;
    dkim=pass (1024-bit key) header.i=@d1.example;
    dmarc=pass
Received: by 10.157.14.6 with HTTP; Tue, 3 Jan 2017 12:22:54 -0800 (PST)
Message-ID: <54B84785.1060301@d1.example.org>
Date: Thu, 14 Jan 2015 15:00:01 -0800
From: John Q Doe <jqd@d1.example.org>
To: arc@dmarc.org
Subject: Example 1

Hey gang,
This is a test message.
--J.
""")
        ValidateAuthenticity().process(lst, msg, msgdata)

        res = ["lists.example.org; spf=pass smtp.mfrom=jqd@d1.example"
               "; dkim=pass header.i=@d1.example; dmarc=pass; arc=pass"]
        self.assertEqual(msg["Authentication-Results"], ''.join(res))

    def test_chain_validation_fail(self):
        # Set the log-level for dkimpy to CRITICAL To avoid error output from
        # logger.
        logging.getLogger('dkimpy').setLevel(logging.CRITICAL)
        config.push('dkim_and_cv', """
        [ARC]
        enabled: yes
        authserv_id: lists.example.org
        dkim: yes
        dmarc: no
        privkey: {}
        """.format(self.keyfile.name))
        self.addCleanup(config.pop, 'dkim_and_cv')

        lst = create_list('test@example.com')
        msgdata = {}

        msg = message_from_string("""MIME-Version: 1.0
Return-Path: <jqd@d1.example.org>
ARC-Seal: a=rsa-sha256;
    b=dOdFEyhrk/tw5wl3vMIogoxhaVsKJkrkEhnAcq2XqOLSQhPpGzhGBJzR7k1sWGokon3TmQ
    7TX9zQLO6ikRpwd/pUswiRW5DBupy58fefuclXJAhErsrebfvfiueGyhHXV7C1LyJTztywzn
    QGG4SCciU/FTlsJ0QANrnLRoadfps=; cv=none; d=example.org; i=1; s=dummy;
    t=12345
ARC-Message-Signature: a=rsa-sha256;
    b=QsRzR/UqwRfVLBc1TnoQomlVw5qi6jp08q8lHpBSl4RehWyHQtY3uOIAGdghDk/mO+/Xpm
    9JA5UVrPyDV0f+2q/YAHuwvP11iCkBQkocmFvgTSxN8H+DwFFPrVVUudQYZV7UDDycXoM6UE
    cdfzLLzVNPOAHEDIi/uzoV4sUqZ18=;
    bh=KWSe46TZKCcDbH4klJPo+tjk5LWJnVRlP5pvjXFZYLQ=; c=relaxed/relaxed;
    d=example.org; h=from:to:date:subject:mime-version:arc-authentication-
    results; i=1; s=dummy; t=12345
ARC-Authentication-Results: i=1; lists.example.org;
    spf=pass smtp.mfrom=jqd@d1.example;
    dkim=pass (1024-bit key) header.i=@d1.example;
    dmarc=pass
Received: from segv.d1.example (segv.d1.example [72.52.75.15])
    by lists.example.org (8.14.5/8.14.5) with ESMTP id t0EKaNU9010123
    for <arc@example.org>; Thu, 14 Jan 2015 15:01:30 -0800 (PST)
    (envelope-from jqd@d1.example)
Authentication-Results: lists.example.org;
    spf=pass smtp.mfrom=jqd@d1.example;
    dkim=pass (1024-bit key) header.i=@d1.example;
    dmarc=pass
Received: by 10.157.14.6 with HTTP; Tue, 3 Jan 2017 12:22:54 -0800 (PST)
Message-ID: <54B84785.1060301@d1.example.org>
Date: Thu, 14 Jan 2015 15:00:01 -0800
From: John Q Doe <jqd@d1.example.org>
To: arc@dmarc.org
Subject: Example 1

Hey gang(modified),
This is a test message.
--J.
""")

        ValidateAuthenticity().process(lst, msg, msgdata)
        res = ['lists.example.org; spf=pass smtp.mfrom=jqd@d1.example',
               '; dkim=pass header.i=@d1.example; dmarc=pass; arc=fail ',
               '(Most recent ARC-Message-Signature did not validate)']
        self.assertEqual(msg["Authentication-Results"], ''.join(res))

    def test_authentication_whitelist_hit(self):
        config.push('just_dkim', """
        [ARC]
        enabled: yes
        authserv_id: example.com
        dkim: yes
        dmarc: no
        privkey: {}
        """.format(self.keyfile.name))
        self.addCleanup(config.pop, 'just_dkim')

        lst = create_list('test@example.com')
        msgdata = {}

        msg = message_from_string(
            """DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed;
        d=valimail.com; s=google2048;
        h=mime-version:from:date:message-id:subject:to;
        bh=3VWGQGY+cSNYd1MGM+X6hRXU0stl8JCaQtl4mbX/j2I=;
        b=gntRk4rCVYIGkpO09ROkbs3n4YSIcp/Pi7tUnSIgs8uS+uZ2a77dG+/qlSvnk+mWET
         IBrkt1YpDzev/0ITTDy/zgTHjPiQIFcg9Q+3hn3sTz8ExCyM8/YYgoPqSs3oUXn3jwXk
         N/wpMuF29LTVp1gpkYzaoCDNPGd1Wag6Vh2lw65S7ruECCAdBm5XeSnvTOzIC0E/jmEt
         3hvaPiKAohCAsC5JAN89EATPOjnYJL4Q6X6p2qUsusz/8tkHuYvReHmxQkjQ0/N3fPP0
         6VfkIrPOHympq6qDUizbjiBmgiMWKnarrptblJvyt66/aIHx+QamP6LUA+/RUFY1q7TG
         MSDg==
Authentication-Results: example.com; spf=pass smtp.mailfrom=gmail.com
MIME-Version: 1.0
From: Gene Shuman <gene@valimail.com>
Date: Wed, 25 Jan 2017 16:13:31 -0800
Message-ID:
  <CANtLugNVcUMfjVH22FN=+A6Y_Ss+QX_=GnJ3xGfDY1iuEbbuRA@mail.gmail.com>
Subject: Test
To: geneshuman@gmail.com
Content-Type: text/plain; charset=UTF-8

This is a test!
""")

        ValidateAuthenticity().process(lst, msg, msgdata)

        res = ['example.com; spf=pass smtp.mailfrom=gmail.com',
               '; dkim=pass header.d=valimail.com; arc=none ',
               '(Message is not ARC signed)']
        self.assertEqual(msg["Authentication-Results"], ''.join(res))

    def test_authentication_whitelist_miss(self):
        config.push('just_dkim', """
        [ARC]
        enabled: yes
        authserv_id: test.com
        trusted_authserv_ids: example.com
        dkim: yes
        dmarc: no
        privkey: {}
        """.format(self.keyfile.name))
        self.addCleanup(config.pop, 'just_dkim')

        lst = create_list('test@example.com')
        msgdata = {}

        msg = message_from_string(
            """DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed;
        d=valimail.com; s=google2048;
        h=mime-version:from:date:message-id:subject:to;
        bh=3VWGQGY+cSNYd1MGM+X6hRXU0stl8JCaQtl4mbX/j2I=;
        b=gntRk4rCVYIGkpO09ROkbs3n4YSIcp/Pi7tUnSIgs8uS+uZ2a77dG+/qlSvnk+mWET
         IBrkt1YpDzev/0ITTDy/zgTHjPiQIFcg9Q+3hn3sTz8ExCyM8/YYgoPqSs3oUXn3jwXk
         N/wpMuF29LTVp1gpkYzaoCDNPGd1Wag6Vh2lw65S7ruECCAdBm5XeSnvTOzIC0E/jmEt
         3hvaPiKAohCAsC5JAN89EATPOjnYJL4Q6X6p2qUsusz/8tkHuYvReHmxQkjQ0/N3fPP0
         6VfkIrPOHympq6qDUizbjiBmgiMWKnarrptblJvyt66/aIHx+QamP6LUA+/RUFY1q7TG
         MSDg==
Authentication-Results: example_no.com; spf=pass smtp.mailfrom=gmail.com
MIME-Version: 1.0
From: Gene Shuman <gene@valimail.com>
Date: Wed, 25 Jan 2017 16:13:31 -0800
Message-ID:
  <CANtLugNVcUMfjVH22FN=+A6Y_Ss+QX_=GnJ3xGfDY1iuEbbuRA@mail.gmail.com>
Subject: Test
To: geneshuman@gmail.com
Content-Type: text/plain; charset=UTF-8

This is a test!
""")

        ValidateAuthenticity().process(lst, msg, msgdata)
        self.assertEqual(msg["Authentication-Results"],
                         'test.com; dkim=pass header.d=valimail.com; arc=none '
                         '(Message is not ARC signed)')

    def test_authentication_bad_outlook_header(self):
        config.push('just_dkim', """
        [ARC]
        enabled: yes
        authserv_id: test.com
        trusted_authserv_ids: example.com
        dkim: yes
        dmarc: no
        privkey: {}
        """.format(self.keyfile.name))
        self.addCleanup(config.pop, 'just_dkim')

        lst = create_list('test@example.com')
        msgdata = {}

        msg = message_from_string(
            """DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed;
        d=valimail.com; s=google2048;
        h=mime-version:from:date:message-id:subject:to;
        bh=3VWGQGY+cSNYd1MGM+X6hRXU0stl8JCaQtl4mbX/j2I=;
        b=gntRk4rCVYIGkpO09ROkbs3n4YSIcp/Pi7tUnSIgs8uS+uZ2a77dG+/qlSvnk+mWET
         IBrkt1YpDzev/0ITTDy/zgTHjPiQIFcg9Q+3hn3sTz8ExCyM8/YYgoPqSs3oUXn3jwXk
         N/wpMuF29LTVp1gpkYzaoCDNPGd1Wag6Vh2lw65S7ruECCAdBm5XeSnvTOzIC0E/jmEt
         3hvaPiKAohCAsC5JAN89EATPOjnYJL4Q6X6p2qUsusz/8tkHuYvReHmxQkjQ0/N3fPP0
         6VfkIrPOHympq6qDUizbjiBmgiMWKnarrptblJvyt66/aIHx+QamP6LUA+/RUFY1q7TG
         MSDg==
Authentication-Results: groups.example.com; dkim=none (message not signed)
 header.d=none;groups.groups.example.com; dmarc=none action=none
 header.from=AList.onmicrosoft.com;
MIME-Version: 1.0
From: A List <alist@example.com>
Date: Wed, 25 Jan 2017 16:13:31 -0800
Message-ID:
  <CANtLugNVcUMfjVH22FN=+A6Y_Ss+QX_=GnJ3xGfDY1iuEbbuRA@mail.gmail.com>
Subject: Test
To: geneshuman@gmail.com
Content-Type: text/plain; charset=UTF-8

This is a test!
""")

        ValidateAuthenticity().process(lst, msg, msgdata)
        self.assertEqual(msg["Authentication-Results"],
                         'test.com; dkim=fail header.d=valimail.com; arc=none '
                         '(Message is not ARC signed)')


class TestTimeout(unittest.TestCase):
    """Test socket.error occurring in the delivery function."""

    layer = ConfigLayer

    def setUp(self):

        class timeout():
            def __init__(self):
                self.counter = 0

            def __call__(self, *args, **kwargs):
                self.counter += 1
                raise Timeout

        self.mock_timeout = timeout()
        self.patcher = patch(
            'mailman.handlers.validate_authenticity.authenticate_message',
            side_effect=self.mock_timeout)
        self.patcher.start()
        self.keyfile = tempfile.NamedTemporaryFile(delete=True)

    def tearDown(self):
        self.patcher.stop()
        self.keyfile.close()

    def test_timeout(self):
        config.push('just_dkim', """
        [ARC]
        enabled: yes
        dkim: yes
        dmarc: no
        privkey: {}
        """.format(self.keyfile.name))
        self.addCleanup(config.pop, 'just_dkim')

        mlist = create_list('test@example.com')
        msg = """DKIM-Signature: v=1; a=rsa-sha256; c=relaxed/relaxed;
        d=valimail.com; s=google2048;
        h=mime-version:from:date:message-id:subject:to;
        bh=3VWGQGY+cSNYd1MGM+X6hRXU0stl8JCaQtl4mbX/j2I=;
        b=gntRk4rCVYIGkpO09ROkbs3n4YSIcp/Pi7tUnSIgs8uS+uZ2a77dG+/qlSvnk+mWET
         IBrkt1YpDzev/0ITTDy/zgTHjPiQIFcg9Q+3hn3sTz8ExCyM8/YYgoPqSs3oUXn3jwXk
         N/wpMuF29LTVp1gpkYzaoCDNPGd1Wag6Vh2lw65S7ruECCAdBm5XeSnvTOzIC0E/jmEt
         3hvaPiKAohCAsC5JAN89EATPOjnYJL4Q6X6p2qUsusz/8tkHuYvReHmxQkjQ0/N3fPP0
         6VfkIrPOHympq6qDUizbjiBmgiMWKnarrptblJvyt66/aIHx+QamP6LUA+/RUFY1q7TG
         MSDg==
MIME-Version: 1.0
From: Gene Shuman <gene@valimail.com>
Date: Wed, 25 Jan 2017 16:13:31 -0800
Message-ID: <CANtLugNVcUMfjVH22FN=+A6Y_Ss+QX_=GnJ3xGfDY1iuEbbuRA@gmail.com>
Subject: Test
To: geneshuman@gmail.com
Content-Type: text/plain; charset=UTF-8

This is a test!
"""
        msg = message_from_string(msg)

        with self.assertRaises(Timeout):
            ValidateAuthenticity().process(mlist, msg, {})

        # Make sure that timeout was called 3 times.
        self.assertEqual(self.mock_timeout.counter, 3)
