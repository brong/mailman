# Copyright (C) 2012-2023 by the Free Software Foundation, Inc.
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

"""REST moderation tests."""

import unittest

from mailman.app.lifecycle import create_list
from mailman.app.moderator import hold_message
from mailman.config import config
from mailman.database.transaction import transaction
from mailman.interfaces.bans import IBanManager
from mailman.interfaces.mailinglist import SubscriptionPolicy
from mailman.interfaces.messages import IMessageStore
from mailman.interfaces.requests import IListRequests, RequestType
from mailman.interfaces.subscriptions import ISubscriptionManager
from mailman.interfaces.usermanager import IUserManager
from mailman.testing.helpers import (
    call_api,
    get_queue_messages,
    set_preferred,
    specialized_message_from_string as mfs,
)
from mailman.testing.layers import RESTLayer
from urllib.error import HTTPError
from zope.component import getUtility


class TestPostModeration(unittest.TestCase):
    layer = RESTLayer

    def setUp(self):
        with transaction():
            self._mlist = create_list('ant@example.com')
        self._msg = mfs("""\
From: anne@example.com
To: ant@example.com
Subject: Something
Message-ID: <alpha>

Something else.
""")

    def test_list_not_found(self):
        # When a bogus mailing list is given, 404 should result.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/bee@example.com/held')
        self.assertEqual(cm.exception.code, 404)

    def test_bad_held_message_request_id(self):
        # Bad request when request_id is not an integer.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/ant@example.com/held/bogus')
        self.assertEqual(cm.exception.code, 404)

    def test_bad_held_message_request_id_post(self):
        # Bad request when request_id is not an integer.
        with self.assertRaises(HTTPError) as cm:
            call_api(
                'http://localhost:9001/3.0/lists/ant@example.com/held/bogus',
                dict(action='defer'))
        self.assertEqual(cm.exception.code, 404)

    def test_missing_held_message_request_id(self):
        # Not found when the request_id is not in the database.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant@example.com/held/99')
        self.assertEqual(cm.exception.code, 404)

    def test_held_message_is_missing(self):
        # Missing message returns appropriately.
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
        url = 'http://localhost:9001/3.0/lists/ant@example.com/held'
        json, response = call_api(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['total_size'], 1)
        self.assertEqual(json['entries'][0]['request_id'], held_id)
        self.assertEqual(json['entries'][0]['msg'], """\
From: anne@example.com
To: ant@example.com
Subject: Something
Message-ID: <alpha>
Message-ID-Hash: XZ3DGG4V37BZTTLXNUX4NABB4DNQHTCP
X-Message-ID-Hash: XZ3DGG4V37BZTTLXNUX4NABB4DNQHTCP

Something else.
""")
        # Now delete the message from the message store and try again.
        getUtility(IMessageStore).delete_message('<alpha>')
        config.db.commit()
        json, response = call_api(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['total_size'], 1)
        self.assertEqual(json['entries'][0]['request_id'], held_id)
        self.assertEqual(json['entries'][0]['msg'], """\
Subject: Message content lost
Message-ID: <alpha>

This held message has been lost.
""")

    def test_delete_missing_message(self):
        # Ensure we can delete a held message request with a missing message.
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
        # Now delete the message from the message store.
        getUtility(IMessageStore).delete_message('<alpha>')
        config.db.commit()
        # There is still a request.
        requests = IListRequests(self._mlist)
        key, data = requests.get_request(held_id)
        self.assertEqual(key, '<alpha>')
        # Now delete the request.
        requests.delete_request(held_id)
        self.assertIsNone(requests.get_request(held_id))

    def test_request_is_not_held_message(self):
        requests = IListRequests(self._mlist)
        with transaction():
            request_id = requests.hold_request(RequestType.subscription, 'foo')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant.example.com'
                     '/held/{}'.format(request_id))
        self.assertEqual(cm.exception.code, 404)

    def test_bad_held_message_action(self):
        # POSTing to a held message with a bad action.
        held_id = hold_message(self._mlist, self._msg)
        url = 'http://localhost:9001/3.0/lists/ant@example.com/held/{}'
        with self.assertRaises(HTTPError) as cm:
            call_api(url.format(held_id), {'action': 'bogus'})
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.msg,
                         'Invalid Parameter "action": Accepted Values are:'
                         ' hold, reject, discard, accept, defer.')

    def test_held_message_count(self):
        # Initially, the count should be zero.
        url = 'http://localhost:9001/3.0/lists/ant@example.com/held/count'
        json, resp = call_api(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json['count'], 0)
        # Now, verify that we get the number when a held message is added.
        with transaction():
            hold_message(self._mlist, self._msg)
        json, resp = call_api(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json['count'], 1)
        # Hold some more to see if we get the right numbers.
        with transaction():
            hold_message(self._mlist, self._msg)
            hold_message(self._mlist, self._msg)
        json, resp = call_api(url)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(json['count'], 3)

    def test_discard(self):
        # Discarding a message removes it from the moderation queue.
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
        url = 'http://localhost:9001/3.0/lists/ant@example.com/held/{}'.format(
            held_id)
        json, response = call_api(url, dict(action='discard'))
        self.assertEqual(response.status_code, 204)
        # Now it's gone.
        with self.assertRaises(HTTPError) as cm:
            call_api(url, dict(action='discard'))
        self.assertEqual(cm.exception.code, 404)

    def test_list_held_messages(self):
        # We can view all the held requests.
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/held')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['total_size'], 1)
        self.assertEqual(json['entries'][0]['request_id'], held_id)

    def test_cant_get_other_lists_holds(self):
        # Issue #161: It was possible to moderate a held message for another
        # list via the REST API.
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
            create_list('bee@example.com')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/bee.example.com'
                     '/held/{}'.format(held_id))
        self.assertEqual(cm.exception.code, 404)

    def test_cant_moderate_other_lists_holds(self):
        # Issue #161: It was possible to moderate a held message for another
        # list via the REST API.
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
            create_list('bee@example.com')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/bee.example.com'
                     '/held/{}'.format(held_id),
                     dict(action='discard'))
        self.assertEqual(cm.exception.code, 404)

    def test_subject_encoding_error(self):
        # GL#383: messages with badly encoded Subject headers crash the REST
        # server.
        self._msg = mfs("""\
From: anne@example.com
To: ant@example.com
Subject: =?GB2312?B?saa9o7fmtNPEpbVaQ2h1o6zDt7uoz+PX1L/guq7AtKGj?=
Message-ID: <alpha>

Something else.
""")
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/held')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['total_size'], 1)
        self.assertEqual(json['entries'][0]['request_id'], held_id)

    def test_handle_message_with_comment(self):
        self._msg = mfs("""\
From: anne@example.com
To: ant@example.com
Subject: Hello
Message-ID: <alpha>

Something else.
""")
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com'
            '/held/{}'.format(held_id),
            dict(action='reject', comment='Because I want to.'))
        self.assertEqual(response.status_code, 204)

    def test_forward_held_message(self):
        self._msg = mfs("""\
From: aperson@example.com
To: ant@example.com
Subject: Hold my post
Message-ID: <AwesomeID>

Hello
""")
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com'
            '/held/{}'.format(held_id),)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['request_id'], held_id)
        # Now, let's reject the post and forward it to another
        # address.
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com'
            '/held/{}'.format(held_id),
            dict(action='reject', comment='Wrong list for you',
                 forward=['bee@example.com', 'me@example.com']))
        self.assertEqual(response.status_code, 204)
        items = get_queue_messages('virgin')
        self.assertEqual(len(items), 2)
        message = items[0].msg
        # First message is reject message to the sender.
        self.assertEqual(message['From'], 'ant-bounces@example.com')
        self.assertEqual(message['To'], 'aperson@example.com')
        self.assertEqual(str(message['Subject']),
                         'Request to mailing list "Ant" rejected')
        fwd_msg = items[1].msg
        # Second message is the forwarded message to multiple recipients.
        self.assertEqual(fwd_msg['From'], 'ant-bounces@example.com')
        self.assertEqual(fwd_msg['To'], 'bee@example.com, me@example.com')
        self.assertEqual(str(fwd_msg['Subject']),
                         'Forward of moderated message')

    def test_forward_held_message_single_address(self):
        self._msg = mfs("""\
From: aperson@example.com
To: ant@example.com
Subject: Hold my post
Message-ID: <AwesomeID>

Hello
""")
        with transaction():
            held_id = hold_message(self._mlist, self._msg)
        # Now, let's discard the post and forward it to another
        # address.
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com'
            '/held/{}'.format(held_id),
            dict(action='discard', forward='bee@example.com',))
        self.assertEqual(response.status_code, 204)
        items = get_queue_messages('virgin')
        self.assertEqual(len(items), 1)
        fwd_msg = items[0].msg
        # Second message is the forwarded message.
        self.assertEqual(fwd_msg['From'], 'ant-bounces@example.com')
        self.assertEqual(fwd_msg['To'], 'bee@example.com')
        self.assertEqual(str(fwd_msg['Subject']),
                         'Forward of moderated message')


class TestSubscriptionModeration(unittest.TestCase):
    layer = RESTLayer
    maxDiff = None

    def setUp(self):
        with transaction():
            self._mlist = create_list('ant@example.com')
            self._mlist.unsubscription_policy = SubscriptionPolicy.moderate
            self._registrar = ISubscriptionManager(self._mlist)
            manager = getUtility(IUserManager)
            self._anne = manager.create_address(
                'anne@example.com', 'Anne Person')
            self._bart = manager.make_user(
                'bart@example.com', 'Bart Person')
            set_preferred(self._bart)

    def test_no_such_list(self):
        # Try to get the requests of a nonexistent list.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/bee@example.com/'
                     'requests')
        self.assertEqual(cm.exception.code, 404)

    def test_no_such_subscription_token(self):
        # Bad request when the token is not in the database.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant@example.com/'
                     'requests/missing')
        self.assertEqual(cm.exception.code, 404)

    def test_bad_subscription_action(self):
        # POSTing to a held message with a bad action.
        token, token_owner, member = self._registrar.register(self._anne)
        # Anne's subscription request got held.
        self.assertIsNone(member)
        # Let's try to handle her request, but with a bogus action.
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        with self.assertRaises(HTTPError) as cm:
            call_api(url.format(token), dict(
                action='bogus',
                ))
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.msg,
                         'Invalid Parameter "action": Accepted Values are:'
                         ' hold, reject, discard, accept, defer.')

    def test_list_held_requests(self):
        # We can view all the held requests.
        with transaction():
            token_1, token_owner, member = self._registrar.register(self._anne)
            # Anne's subscription request got held.
            self.assertIsNotNone(token_1)
            self.assertIsNone(member)
            token_2, token_owner, member = self._registrar.register(self._bart)
            self.assertIsNotNone(token_2)
            self.assertIsNone(member)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['total_size'], 2)
        tokens = set(entry['token'] for entry in json['entries'])
        self.assertEqual(tokens, {token_1, token_2})
        emails = set(entry['email'] for entry in json['entries'])
        self.assertEqual(emails, {'anne@example.com', 'bart@example.com'})

    def test_list_held_requests_with_owner(self):
        with transaction():
            token_1, token_owner, member = self._registrar.register(self._anne)
            # Anne's subscription request got held.
            self.assertIsNotNone(token_1)
            self.assertIsNone(member)
            token_2, token_owner, member = self._registrar.register(self._bart)
            self.assertIsNotNone(token_2)
            self.assertIsNone(member)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests'
            '?token_owner=moderator')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['total_size'], 0)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests'
            '?token_owner=subscriber')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['total_size'], 2)

    def test_list_held_requests_count(self):
        with transaction():
            token_1, token_owner, member = self._registrar.register(self._anne)
            # Anne's subscription request got held.
            self.assertIsNotNone(token_1)
            self.assertIsNone(member)
            token_2, token_owner, member = self._registrar.register(self._bart)
            self.assertIsNotNone(token_2)
            self.assertIsNone(member)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests'
            '/count')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['count'], 2)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests'
            '/count?token_owner=moderator')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['count'], 0)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests'
            '/count?token_owner=subscriber')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['count'], 2)

    def test_list_held_unsubscription_request(self):
        with transaction():
            # First, subscribe Anne and then trigger an un-subscription.
            self._mlist.subscribe(self._bart)
            token, token_owner, member = self._registrar.unregister(self._bart)
            # Anne's un-subscription request got held.
            self.assertIsNotNone(token)
            self.assertIsNotNone(member)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests'
            '?request_type=unsubscription')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(json['entries']), 1)
        # Individual request can then be fetched.
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        json, response = call_api(url.format(token))
        self.assertEqual(json['token'], token)
        self.assertEqual(json['token_owner'], token_owner.name)
        self.assertEqual(json['email'], 'bart@example.com')
        self.assertEqual(json['type'], 'unsubscription')
        # Bart should still be a Member.
        self.assertIsNotNone(
            self._mlist.members.get_member('bart@example.com'))
        # Now, accept the request.
        json, response, call_api(url.format(token), dict(
            action='accept',
            ))
        self.assertEqual(response.status_code, 200)
        # Now, the Member should be un-subscribed.
        self.assertIsNone(
            self._mlist.members.get_member('bart@example.com'))

    def test_unsubscription_request_count(self):
        with transaction():
            # First, subscribe Anne and then trigger an un-subscription.
            self._mlist.subscribe(self._bart)
            token, token_owner, member = self._registrar.unregister(self._bart)
            # Anne's un-subscription request got held.
            self.assertIsNotNone(token)
            self.assertIsNotNone(member)
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests/count'
            '?request_type=unsubscription')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['count'], 1)

    def test_individual_request(self):
        # We can view an individual request.
        with transaction():
            token, token_owner, member = self._registrar.register(self._anne)
            # Anne's subscription request got held.
            self.assertIsNotNone(token)
            self.assertIsNone(member)
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        json, response = call_api(url.format(token))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json['token'], token)
        self.assertEqual(json['token_owner'], token_owner.name)
        self.assertEqual(json['email'], 'anne@example.com')

    def test_accept(self):
        # POST to the request to accept it.
        with transaction():
            token, token_owner, member = self._registrar.register(self._anne)
        # Anne's subscription request got held.
        self.assertIsNone(member)
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        json, response = call_api(url.format(token), dict(
            action='accept',
            ))
        self.assertEqual(response.status_code, 204)
        # Anne is a member.
        self.assertEqual(
            self._mlist.members.get_member('anne@example.com').address,
            self._anne)
        # The request URL no longer exists.
        with self.assertRaises(HTTPError) as cm:
            call_api(url.format(token), dict(
                action='accept',
                ))
        self.assertEqual(cm.exception.code, 404)

    def test_accept_already_subscribed(self):
        # POST to a subscription request, but the user is already subscribed.
        with transaction():
            token, token_owner, member = self._registrar.register(self._anne)
            # Make Anne already a member.
            self._mlist.subscribe(self._anne)
        # Accept the pending subscription, which raises an error.
        url = 'http://localhost:9001/3.0/lists/ant.example.com/requests/{}'
        with self.assertRaises(HTTPError) as cm:
            call_api(url.format(token), dict(
                action='accept',
                ))
        self.assertEqual(cm.exception.code, 409)
        self.assertEqual(cm.exception.reason, 'Already subscribed')

    def test_accept_bad_token(self):
        # Try to accept a request with a bogus token.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant@example.com'
                     '/requests/bogus',
                     dict(action='accept'))
        self.assertEqual(cm.exception.code, 404)

    def test_accept_by_moderator_clears_request_queue(self):
        # After accepting a message held for moderator approval, there are no
        # more requests to handle.
        #
        # We start with nothing in the queue.
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests')
        self.assertEqual(json['total_size'], 0)
        # Anne tries to subscribe to a list that only requests moderator
        # approval.
        with transaction():
            self._mlist.subscription_policy = SubscriptionPolicy.moderate
            token, token_owner, member = self._registrar.register(
                self._anne,
                pre_verified=True, pre_confirmed=True)
        # There's now one request in the queue, and it's waiting on moderator
        # approval.
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests')
        self.assertEqual(json['total_size'], 1)
        entry = json['entries'][0]
        self.assertEqual(entry['token_owner'], 'moderator')
        self.assertEqual(entry['email'], 'anne@example.com')
        # The moderator approves the request.
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        json, response = call_api(url.format(token), {'action': 'accept'})
        self.assertEqual(response.status_code, 204)
        # And now the request queue is empty.
        json, response = call_api(
            'http://localhost:9001/3.0/lists/ant@example.com/requests')
        self.assertEqual(json['total_size'], 0)

    def test_discard(self):
        # POST to the request to discard it.
        with transaction():
            token, token_owner, member = self._registrar.register(self._anne)
        # Anne's subscription request got held.
        self.assertIsNone(member)
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        json, response = call_api(url.format(token), dict(
            action='discard',
            ))
        self.assertEqual(response.status_code, 204)
        # Anne is not a member.
        self.assertIsNone(self._mlist.members.get_member('anne@example.com'))
        # The request URL no longer exists.
        with self.assertRaises(HTTPError) as cm:
            call_api(url.format(token), dict(
                action='discard',
                ))
        self.assertEqual(cm.exception.code, 404)

    def test_defer(self):
        # Defer the decision for some other moderator.
        with transaction():
            token, token_owner, member = self._registrar.register(self._anne)
        # Anne's subscription request got held.
        self.assertIsNone(member)
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        json, response = call_api(url.format(token), dict(
            action='defer',
            ))
        self.assertEqual(response.status_code, 204)
        # Anne is not a member.
        self.assertIsNone(self._mlist.members.get_member('anne@example.com'))
        # The request URL still exists.
        json, response = call_api(url.format(token), dict(
            action='defer',
            ))
        self.assertEqual(response.status_code, 204)
        # And now we can accept it.
        json, response = call_api(url.format(token), dict(
            action='accept',
            ))
        self.assertEqual(response.status_code, 204)
        # Anne is a member.
        self.assertEqual(
            self._mlist.members.get_member('anne@example.com').address,
            self._anne)
        # The request URL no longer exists.
        with self.assertRaises(HTTPError) as cm:
            call_api(url.format(token), dict(
                action='accept',
                ))
        self.assertEqual(cm.exception.code, 404)

    def test_defer_bad_token(self):
        # Try to accept a request with a bogus token.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant@example.com'
                     '/requests/bogus',
                     dict(action='defer'))
        self.assertEqual(cm.exception.code, 404)

    def test_reject(self):
        # POST to the request to reject it.  This leaves a bounce message in
        # the virgin queue.
        with transaction():
            token, token_owner, member = self._registrar.register(self._anne)
        # Anne's subscription request got held.
        self.assertIsNone(member)
        # Clear out the virgin queue, which currently contains the
        # confirmation message sent to Anne.
        get_queue_messages('virgin')
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        json, response = call_api(url.format(token), dict(
            action='reject',
            ))
        self.assertEqual(response.status_code, 204)
        # Anne is not a member.
        self.assertIsNone(self._mlist.members.get_member('anne@example.com'))
        # The request URL no longer exists.
        with self.assertRaises(HTTPError) as cm:
            call_api(url.format(token), dict(
                action='reject',
                ))
        self.assertEqual(cm.exception.code, 404)
        # And the rejection message to Anne is now in the virgin queue.
        items = get_queue_messages('virgin')
        self.assertEqual(len(items), 1)
        message = items[0].msg
        self.assertEqual(message['From'], 'ant-bounces@example.com')
        self.assertEqual(message['To'], 'anne@example.com')
        self.assertEqual(message['Subject'],
                         'Request to mailing list "Ant" rejected')

    def test_reject_bad_token(self):
        # Try to accept a request with a bogus token.
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/lists/ant@example.com'
                     '/requests/bogus',
                     dict(action='reject'))
        self.assertEqual(cm.exception.code, 404)

    def test_reject_with_reason(self):
        # Try to reject a request with an additional comment/reason.
        # POST to the request to reject it.  This leaves a bounce message in
        # the virgin queue.
        with transaction():
            token, token_owner, member = self._registrar.register(self._anne)
        # Anne's subscription request got held.
        self.assertIsNone(member)
        # Clear out the virgin queue, which currently contains the
        # confirmation message sent to Anne.
        get_queue_messages('virgin')
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        reason = 'You are not authorized!'
        json, response = call_api(url.format(token), dict(
            action='reject',
            reason=reason))
        self.assertEqual(response.status_code, 204)
        # And the rejection message to Anne is now in the virgin queue.
        items = get_queue_messages('virgin')
        self.assertEqual(len(items), 1)
        message = items[0].msg
        self.assertEqual(message['From'], 'ant-bounces@example.com')
        self.assertEqual(message['To'], 'anne@example.com')
        self.assertEqual(message['Subject'],
                         'Request to mailing list "Ant" rejected')
        self.assertTrue(reason in message.as_string())

    def test_hold_keeps_holding(self):
        # POST to the request to continue holding it.
        with transaction():
            token, token_owner, member = self._registrar.register(self._anne)
        # Anne's subscription request got held.
        self.assertIsNone(member)
        # Clear out the virgin queue, which currently contains the
        # confirmation message sent to Anne.
        get_queue_messages('virgin')
        url = 'http://localhost:9001/3.0/lists/ant@example.com/requests/{}'
        json, response = call_api(url.format(token), dict(
            action='hold',
            ))
        self.assertEqual(response.status_code, 204)
        # Anne is not a member.
        self.assertIsNone(self._mlist.members.get_member('anne@example.com'))
        # The request URL still exists.
        json, response = call_api(url.format(token), dict(
                action='defer',
                ))
        self.assertEqual(response.status_code, 204)

    def test_subscribe_other_role_with_no_preferred_address(self):
        with transaction():
            cate = getUtility(IUserManager).create_user('cate@example.com')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'ant.example.com',
                'subscriber': cate.id,
                'role': 'moderator',
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'User without preferred address')

    def test_subscribe_other_role_banned_email_address(self):
        bans = IBanManager(self._mlist)
        with transaction():
            bans.ban('anne@example.com')
        with self.assertRaises(HTTPError) as cm:
            call_api('http://localhost:9001/3.0/members', {
                'list_id': 'ant.example.com',
                'subscriber': 'anne@example.com',
                'role': 'moderator',
                })
        self.assertEqual(cm.exception.code, 400)
        self.assertEqual(cm.exception.reason, 'Membership is banned')
