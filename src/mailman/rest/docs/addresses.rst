=========
Addresses
=========

The REST API can be used to manage addresses.

There are no addresses yet.

    >>> from mailman.testing.documentation import dump_json
    >>> dump_json('http://localhost:9001/3.0/addresses')
    http_etag: "..."
    start: 0
    total_size: 0

When an address is created via the internal API, it is available in the REST
API.
::

    >>> from zope.component import getUtility
    >>> from mailman.interfaces.usermanager import IUserManager
    >>> user_manager = getUtility(IUserManager)
    >>> anne = user_manager.create_address('anne@example.com')
    >>> from mailman.config import config
    >>> transaction = config.db
    >>> transaction.commit()

    >>> dump_json('http://localhost:9001/3.0/addresses')
    entry 0:
        email: anne@example.com
        http_etag: "..."
        original_email: anne@example.com
        registered_on: 2005-08-01T07:49:23
        self_link: http://localhost:9001/3.0/addresses/anne@example.com
    http_etag: "..."
    start: 0
    total_size: 1

Anne's address can also be accessed directly.

    >>> dump_json('http://localhost:9001/3.0/addresses/anne@example.com')
    email: anne@example.com
    http_etag: "..."
    original_email: anne@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/anne@example.com

Bart registers with a mixed-case address.  The canonical URL always includes
the lower-case version.

    >>> bart = user_manager.create_address('Bart.Person@example.com')
    >>> transaction.commit()
    >>> dump_json(
    ...     'http://localhost:9001/3.0/addresses/bart.person@example.com')
    email: bart.person@example.com
    http_etag: "..."
    original_email: Bart.Person@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/bart.person@example.com

But his address record can be accessed with the case-preserved version too.

    >>> dump_json(
    ...     'http://localhost:9001/3.0/addresses/Bart.Person@example.com')
    email: bart.person@example.com
    http_etag: "..."
    original_email: Bart.Person@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/bart.person@example.com

When an address has a real name associated with it, this is also available in
the REST API.

    >>> cris = user_manager.create_address('cris@example.com', 'Cris Person')
    >>> transaction.commit()
    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com')
    display_name: Cris Person
    email: cris@example.com
    http_etag: "..."
    original_email: cris@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/cris@example.com


Verifying
=========

When the address gets verified, this attribute is available in the REST
representation.

    >>> from mailman.utilities.datetime import now
    >>> anne.verified_on = now()
    >>> transaction.commit()
    >>> dump_json('http://localhost:9001/3.0/addresses/anne@example.com')
    email: anne@example.com
    http_etag: "..."
    original_email: anne@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/anne@example.com
    verified_on: 2005-08-01T07:49:23

Addresses can also be verified through the REST API, by POSTing to the
'verify' sub-resource.  The POST data is ignored.

    >>> dump_json('http://localhost:9001/3.0/addresses/'
    ...           'cris@example.com/verify', {})
    date: ...
    server: ...
    status: 204

Now Cris's address is verified.

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com')
    display_name: Cris Person
    email: cris@example.com
    http_etag: "..."
    original_email: cris@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/cris@example.com
    verified_on: 2005-08-01T07:49:23

If you should ever need to 'unverify' an address, POST to the 'unverify'
sub-resource.  Again, the POST data is ignored.

    >>> dump_json('http://localhost:9001/3.0/addresses/'
    ...           'cris@example.com/unverify', {})
    date: ...
    server: ...
    status: 204

Now Cris's address is unverified.

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com')
    display_name: Cris Person
    email: cris@example.com
    http_etag: "..."
    original_email: cris@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/cris@example.com


Updating
========

Each address has a ``display_name`` associated with it. This name can be
updated by PATCH'ing the address resource::

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com',
    ...           method='PATCH', data={'display_name': 'Cris P. Person'})
    date: ...
    server: ...
    status: 204

This should update the display_name associated::

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com')
    display_name: Cris P. Person
    email: cris@example.com
    http_etag: "..."
    original_email: cris@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/cris@example.com


The user
========

To link an address to a user, a POST request can be sent to the ``/user``
sub-resource of the address.  If the user does not exist, it will be created.

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com/user',
    ...           {'display_name': 'Cris X. Person'})
    content-length: 0
    content-type: application/json
    date: ...
    location: http://localhost:9001/3.0/users/1
    server: ...
    status: 201

The user is now created and the address is linked to it:

    >>> cris.user
    <User "Cris X. Person" (1) at 0x...>
    >>> cris_user = user_manager.get_user('cris@example.com')
    >>> cris_user
    <User "Cris X. Person" (1) at 0x...>
    >>> cris.user == cris_user
    True
    >>> [a.email for a in cris_user.addresses]
    ['cris@example.com']

A link to the user resource is now available as a sub-resource.

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com')
    display_name: Cris P. Person
    email: cris@example.com
    http_etag: "..."
    original_email: cris@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/cris@example.com
    user: http://localhost:9001/3.0/users/1

To prevent automatic user creation from taking place, add the `auto_create`
parameter to the POST request and set it to False.

    >>> dump_json('http://localhost:9001/3.0/addresses/anne@example.com/user',
    ...           {'display_name': 'Anne User', 'auto_create': False})
    HTTP Error 403: 403 Forbidden

A request to the `/user` sub-resource will return the linked user's
representation:

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com/user')
    created_on: 2005-08-01T07:49:23
    display_name: Cris X. Person
    http_etag: "..."
    is_server_owner: False
    password: ...
    self_link: http://localhost:9001/3.0/users/1
    user_id: 1

The address and the user can be unlinked by sending a DELETE request on the
`/user` resource.  The user itself is not deleted, only the link.

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com/user',
    ...           method='DELETE')
    date: ...
    server: ...
    status: 204
    >>> transaction.abort()
    >>> cris.user == None
    True
    >>> from uuid import UUID
    >>> user_manager.get_user_by_id(UUID(int=1))
    <User "Cris X. Person" (1) at 0x...>
    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com/user')
    HTTP Error 404: 404 Not Found

You can link an existing user to an address by passing the user's ID in the
POST request.
::

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com/user',
    ...           {'user_id': 1})
    content-length: 0
    content-type: application/json
    date: ...
    server: ...
    status: 200

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com/user')
    created_on: ...
    display_name: Cris X. Person
    http_etag: ...
    password: ...
    self_link: http://localhost:9001/3.0/users/1
    user_id: 1

To link an address to a different user, you can either send a DELETE request
followed by a POST request, or you can send a PUT request.
::

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com/user',
    ...           {'display_name': 'Cris Q Person'}, method="PUT")
    content-length: 0
    content-type: application/json
    date: ...
    location: http://localhost:9001/3.0/users/2
    server: ...
    status: 201

    >>> dump_json('http://localhost:9001/3.0/addresses/cris@example.com/user')
    created_on: ...
    display_name: Cris Q Person
    http_etag: ...
    password: ...
    self_link: http://localhost:9001/3.0/users/2
    user_id: 2


User addresses
==============

Users control addresses.  The canonical URLs for these user-controlled
addresses live in the ``/addresses`` namespace.
::

    >>> dave = user_manager.create_user('dave@example.com', 'Dave Person')
    >>> transaction.commit()
    >>> dump_json('http://localhost:9001/3.0/users/dave@example.com/addresses')
    entry 0:
        display_name: Dave Person
        email: dave@example.com
        http_etag: "..."
        original_email: dave@example.com
        registered_on: 2005-08-01T07:49:23
        self_link: http://localhost:9001/3.0/addresses/dave@example.com
        user: http://localhost:9001/3.0/users/3
    http_etag: "..."
    start: 0
    total_size: 1

    >>> dump_json('http://localhost:9001/3.0/addresses/dave@example.com')
    display_name: Dave Person
    email: dave@example.com
    http_etag: "..."
    original_email: dave@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/dave@example.com
    user: http://localhost:9001/3.0/users/3

A user can be associated with multiple email addresses.  You can add new
addresses to an existing user.

    >>> dump_json(
    ...     'http://localhost:9001/3.0/users/dave@example.com/addresses', {
    ...           'email': 'dave.person@example.org'
    ...           })
    content-length: 0
    content-type: application/json
    date: ...
    location: http://localhost:9001/3.0/addresses/dave.person@example.org
    server: ...
    status: 201

When you add the new address, you can give it an optional display name.

    >>> dump_json(
    ...     'http://localhost:9001/3.0/users/dave@example.com/addresses', {
    ...           'email': 'dp@example.org',
    ...           'display_name': 'Davie P',
    ...           })
    content-length: 0
    content-type: application/json
    date: ...
    location: http://localhost:9001/3.0/addresses/dp@example.org
    server: ...
    status: 201

The user controls these new addresses.

    >>> dump_json('http://localhost:9001/3.0/users/dave@example.com/addresses')
    entry 0:
        email: dave.person@example.org
        http_etag: "..."
        original_email: dave.person@example.org
        registered_on: 2005-08-01T07:49:23
        self_link: http://localhost:9001/3.0/addresses/dave.person@example.org
        user: http://localhost:9001/3.0/users/3
    entry 1:
        display_name: Dave Person
        email: dave@example.com
        http_etag: "..."
        original_email: dave@example.com
        registered_on: 2005-08-01T07:49:23
        self_link: http://localhost:9001/3.0/addresses/dave@example.com
        user: http://localhost:9001/3.0/users/3
    entry 2:
        display_name: Davie P
        email: dp@example.org
        http_etag: "..."
        original_email: dp@example.org
        registered_on: 2005-08-01T07:49:23
        self_link: http://localhost:9001/3.0/addresses/dp@example.org
        user: http://localhost:9001/3.0/users/3
    http_etag: "..."
    start: 0
    total_size: 3


Preferred address
=================

Each user can have a preferred address. Initially, every user's preferred
address is unset.
::

    >>> ram = user_manager.create_user('ram@example.com', 'Ram Person')
    >>> transaction.commit()
    >>> dump_json(
    ...    'http://localhost:9001/3.1/users/ram@example.com/addresses')
    entry 0:
        display_name: Ram Person
        email: ram@example.com
        http_etag: "..."
        original_email: ram@example.com
        registered_on: 2005-08-01T07:49:23
        self_link: http://localhost:9001/3.1/addresses/ram@example.com
        user: http://localhost:9001/3.1/users/00000000000000000000000000000004
    http_etag: "..."
    start: 0
    total_size: 1
    >>> import pdb
    >>> dump_json('http://localhost:9001/3.1/users/ram@example.com/preferred_address')
    HTTP Error 404: 404 Not Found

Setting Ram's preferred addresses requires that it first be verified:
::

    >>> dump_json('http://localhost:9001/3.1/users/ram@example.com/preferred_address',
    ...     {'email': 'ram@example.com'})
    HTTP Error 400: Ram Person <ram@example.com> must be verified before setting as primary

Verify Ram's address first:
::

    >>> addr = ram.addresses[0]
    >>> addr.verified_on = now()
    >>> transaction.commit()

Now, Ram can set his preferred address:
::

    >>> dump_json('http://localhost:9001/3.1/users/ram@example.com/preferred_address',
    ...     {'email': 'ram@example.com'})
    content-length: 0
    content-type: application/json
    date: ...
    location: http://localhost:9001/3.1/addresses/ram@example.com
    server: ...
    status: 201
    >>> dump_json('http://localhost:9001/3.1/users/ram@example.com/preferred_address')
    display_name: Ram Person
    email: ram@example.com
    http_etag: "..."
    original_email: ram@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.1/addresses/ram@example.com
    user: http://localhost:9001/3.1/users/00000000000000000000000000000004
    verified_on: 2005-08-01T07:49:23


To unset Ram's preferred address, call a ``DELETE`` on preferred address resource:
::

   >>> dump_json('http://localhost:9001/3.1/users/ram@example.com/preferred_address',
   ...     method='DELETE')
   date: ...
   server: ...
   status: 204

Memberships
===========

Addresses can be subscribed to mailing lists.  When they are, all the
membership records for that address are easily accessible via the REST API.

Elle registers several email addresses.

    >>> elle = user_manager.create_user('elle@example.com', 'Elle Person')
    >>> subscriber = list(elle.addresses)[0]
    >>> elle.register('eperson@example.com')
    <Address: eperson@example.com [not verified] at ...>
    >>> elle.register('elle.person@example.com')
    <Address: elle.person@example.com [not verified] at ...>

Elle subscribes to two mailing lists with one of her addresses.
::

    >>> from mailman.app.lifecycle import create_list
    >>> ant = create_list('ant@example.com')
    >>> bee = create_list('bee@example.com')
    >>> ant.subscribe(subscriber)
    <Member: Elle Person <elle@example.com> on ant@example.com
             as MemberRole.member>
    >>> bee.subscribe(subscriber)
    <Member: Elle Person <elle@example.com> on bee@example.com
             as MemberRole.member>
    >>> transaction.commit()

Elle can get her memberships for each of her email addresses.
::

    >>> dump_json('http://localhost:9001/3.0/addresses/'
    ...           'elle@example.com/memberships')
    entry 0:
        address: http://localhost:9001/3.0/addresses/elle@example.com
        bounce_score: 0
        delivery_mode: regular
        delivery_status: enabled
        display_name: Elle Person
        email: elle@example.com
        http_etag: "..."
        last_warning_sent: 0001-01-01T00:00:00
        list_id: ant.example.com
        member_id: 1
        role: member
        self_link: http://localhost:9001/3.0/members/1
        subscription_mode: as_address
        total_warnings_sent: 0
        user: http://localhost:9001/3.0/users/5
    entry 1:
        address: http://localhost:9001/3.0/addresses/elle@example.com
        bounce_score: 0
        delivery_mode: regular
        delivery_status: enabled
        display_name: Elle Person
        email: elle@example.com
        http_etag: "..."
        last_warning_sent: 0001-01-01T00:00:00
        list_id: bee.example.com
        member_id: 2
        role: member
        self_link: http://localhost:9001/3.0/members/2
        subscription_mode: as_address
        total_warnings_sent: 0
        user: http://localhost:9001/3.0/users/5
    http_etag: "..."
    start: 0
    total_size: 2

    >>> dump_json('http://localhost:9001/3.0/addresses/'
    ...           'eperson@example.com/memberships')
    http_etag: "..."
    start: 0
    total_size: 0

When Elle subscribes to the `bee` list again with a different address, this
does not show up in the list of memberships for his other address.
::

    >>> subscriber = user_manager.get_address('eperson@example.com')
    >>> bee.subscribe(subscriber)
    <Member: eperson@example.com on bee@example.com as MemberRole.member>
    >>> transaction.commit()

    >>> dump_json('http://localhost:9001/3.0/addresses/'
    ...           'elle@example.com/memberships')
    entry 0:
        address: http://localhost:9001/3.0/addresses/elle@example.com
        bounce_score: 0
        delivery_mode: regular
        delivery_status: enabled
        display_name: Elle Person
        email: elle@example.com
        http_etag: "..."
        last_warning_sent: 0001-01-01T00:00:00
        list_id: ant.example.com
        member_id: 1
        role: member
        self_link: http://localhost:9001/3.0/members/1
        subscription_mode: as_address
        total_warnings_sent: 0
        user: http://localhost:9001/3.0/users/5
    entry 1:
        address: http://localhost:9001/3.0/addresses/elle@example.com
        bounce_score: 0
        delivery_mode: regular
        delivery_status: enabled
        display_name: Elle Person
        email: elle@example.com
        http_etag: "..."
        last_warning_sent: 0001-01-01T00:00:00
        list_id: bee.example.com
        member_id: 2
        role: member
        self_link: http://localhost:9001/3.0/members/2
        subscription_mode: as_address
        total_warnings_sent: 0
        user: http://localhost:9001/3.0/users/5
    http_etag: "..."
    start: 0
    total_size: 2

    >>> dump_json('http://localhost:9001/3.0/addresses/'
    ...           'eperson@example.com/memberships')
    entry 0:
        address: http://localhost:9001/3.0/addresses/eperson@example.com
        bounce_score: 0
        delivery_mode: regular
        delivery_status: enabled
        display_name: Elle Person
        email: eperson@example.com
        http_etag: "..."
        last_warning_sent: 0001-01-01T00:00:00
        list_id: bee.example.com
        member_id: 3
        role: member
        self_link: http://localhost:9001/3.0/members/3
        subscription_mode: as_address
        total_warnings_sent: 0
        user: http://localhost:9001/3.0/users/5
    http_etag: "..."
    start: 0
    total_size: 1




Deleting
========

Addresses can be deleted via the REST API.
::

    >>> fred = user_manager.create_address('fred@example.com', 'Fred Person')
    >>> transaction.commit()
    >>> dump_json('http://localhost:9001/3.0/addresses/fred@example.com')
    display_name: Fred Person
    email: fred@example.com
    http_etag: "..."
    original_email: fred@example.com
    registered_on: 2005-08-01T07:49:23
    self_link: http://localhost:9001/3.0/addresses/fred@example.com

    >>> dump_json('http://localhost:9001/3.0/addresses/fred@example.com',
    ...     method='DELETE')
    date: ...
    server: ...
    status: 204
    >>> transaction.abort()

    >>> print(user_manager.get_address('fred@example.com'))
    None

If an address is linked to a user, deleting the address does not delete the
user, it just unlinks it.
::

    >>> gwen = user_manager.create_user('gwen@example.com', 'Gwen Person')
    >>> transaction.commit()
    >>> dump_json('http://localhost:9001/3.0/users/6/addresses')
    entry 0:
        display_name: Gwen Person
        email: gwen@example.com
        http_etag: "..."
        original_email: gwen@example.com
        registered_on: 2005-08-01T07:49:23
        self_link: http://localhost:9001/3.0/addresses/gwen@example.com
        user: http://localhost:9001/3.0/users/6
    http_etag: "..."
    start: 0
    total_size: 1


    >>> dump_json('http://localhost:9001/3.0/addresses/gwen@example.com',
    ...     method='DELETE')
    date: ...
    server: ...
    status: 204

    >>> dump_json('http://localhost:9001/3.0/users/6/addresses')
    http_etag: "..."
    start: 0
    total_size: 0
