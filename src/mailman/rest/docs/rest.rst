.. _rest-api:

========================
 Mailman 3 Core REST API
========================

Here is extensive documentation on the Mailman Core administrative REST API.


The REST server
===============

Mailman exposes a REST HTTP server for administrative control.

The server listens for connections on a configurable host name and port.

It is always protected by HTTP basic authentication using a single global
user name and password. The credentials are set in the `[webservice]` section
of the configuration using the `admin_user` and `admin_pass` properties.

Because the REST server has full administrative access, it should never be
exposed to the public internet.  By default it only listens to connections on
``localhost``.  Don't change this unless you really know what you're doing.
In addition you should set the user name and password to secure values and
distribute them to any REST clients with reasonable precautions.

The Mailman major and minor version numbers are in the URL.

You can write your own HTTP clients to speak this API, or you can use the
`official Python bindings`_.


Root URL
========

In this documentation, we mainly use ``http://localhost:9001/3.0/``
as the REST root url. Port ``9001`` is used for unit tests, but
for a running system, the port is ``8001`` unless changed in config.

In the documentation we use ``3.0`` as the primary API version, but
the latest version of the API might be different. You may check the
difference of versions in `Basic Operation`_.

The ``hostname`` and ``port`` where Mailman's REST API will be
listening can be found by running `mailman info`_ command.
You can configure that in ``mailman.cfg`` configuration file.::


    >>> from mailman.testing.documentation import cli
    >>> command = cli('mailman.commands.cli_info.info')

    >>> command('mailman info')
    GNU Mailman 3...
    Python ...
    ...
    config file: .../test.cfg
    db url: ...
    REST root url: http://localhost:9001/3.1/
    REST credentials: restadmin:restpass

Helpers
=======

There are several :ref:`doc-helpers` which are used throughout the Mailman
documentation. These include the utilities like :py:func:`.dump_json`,
:py:func:`.dump_msgdata` and :py:func:`.call_http`.

These helpers methods are simply meant to simplify the documentation and are
hence included in the namespaces without imports. If you are trying out these
commands on your local machine, you can replace them with ``curl`` commands
instead.

.. note:: While the documentation below refers only to ``dump_json`` calls,
          other utilities mentioned above will also have similar curl
          equivalents, albeit without the ``| python -m json.tool`` part, which
          is only meant only to pretty print the json response.

For example, call like::

    >>> from mailman.testing.documentation import dump_json
    >>> dump_json('http://localhost:9001/3.1/domains')
    entry 0:
        alias_domain: None
        base_url: None
        description: An example domain.
        http_etag: "..."
        mail_host: example.com
        self_link: http://localhost:9001/3.1/domains/example.com
    http_etag: "..."
    start: 0
    total_size: 1

is a ``GET`` request to the URL specified as the first parameter. An equivalent
``curl`` command for this would be::

   $ curl --user restadmin:restpass http://localhost:8001/3.1/domains | python -m json.tool
   {
   "entries": [
        {
            "alias_domain": null,
            "base_url": null,
            "description": null,
            "http_etag": "\"75a9858de80b96f525d71157558fff523cb940c3\"",
            "mail_host": "example.com",
            "self_link": "http://localhost:8001/3.1/domains/example.com"
        }
    ],
    "http_etag": "\"33480b0f1e9249f6bbcc2c55a1ffaa33c13d424f\"",
    "start": 0,
    "total_size": 1
    }


.. warning:: Note that the port used in the above two commands are intentionally
             different. Documentation uses 9001 to make sure that the doctests
             do not run against a running instance of Mailman. By Default the
             REST API is available at 8001 port on the host where Mailman Core
             is listening.

.. note:: For authentication, the username & password specified with ``--user``
          is only the default values. Please change them to the appropriate
          values.

Similarly, when some data is provided, the requests are actually post requests::


   >>> dump_json('http://localhost:9001/3.1/domains', {
   ...           'mail_host': 'lists.example.com',
   ...           })
   content-length: 0
   content-type: application/json
   date: ...
   location: http://localhost:9001/3.1/domains/lists.example.com
    ...

This is equivalent to::

   $ curl --user restadmin:restpass -X POST http://localhost:8001/3.1/domains \
       -d mail_host=lists.example.com
   $ curl --user restadmin:restpass http://localhost:8001/3.1/domains | python -m json.tool
   {
    "entries": [
        {
            "alias_domain": null,
            "base_url": null,
            "description": null,
            "http_etag": "\"75a9858de80b96f525d71157558fff523cb940c3\"",
            "mail_host": "example.com",
            "self_link": "http://localhost:8001/3.1/domains/example.com"
        },
        {
            "alias_domain": null,
            "base_url": null,
            "description": null,
            "http_etag": "\"a13efb90674956b3ed26363705bf966a954f1121\"",
            "mail_host": "lists.example.com",
            "self_link": "http://localhost:8001/3.1/domains/lists.example.com"
        }
    ],
    "http_etag": "\"8c1a1d2664b41673bc61126b99359772ce93cfdb\"",
    "start": 0,
    "total_size": 2
    }



.. note:: Note that by default, Mailman's REST API accepts both
          ``application/json`` and ``application/x-www-form-urlencoded`` inputs
          with ``PATCH`` and ``POST`` requests. We are using the latter in the
          call above, but you can also use JSON inputs if you prefer that.

Pay careful attention to which request type you are using. As a rule of thumb,
when you are creating new resources, like a ``Domain`` resource in the above
call you have to use ``POST``. However, when updating an existing resource,
you'd want to use ``PATCH`` request. Mailman also support ``PUT`` requests for
updating a resource, but you need to specify **all** the attributes when
updating via a ``PUT`` request.


REST API Documentation
======================

.. toctree::
   :glob:
   :maxdepth: 1

   ./basic
   ./collections
   ./helpers
   ./systemconf
   ./domains
   ./lists
   ./listconf
   ./addresses
   ./users
   ./membership
   ./queues
   ./*


.. _`official Python bindings`: https://mailmanclient.readthedocs.io/en/latest/
.. _`mailman info`: https://mailman.readthedocs.io/en/latest/src/mailman/config/docs/config.html#which-configuration-file-is-in-use
.. _`Basic Operation`: https://mailman.readthedocs.io/en/latest/src/mailman/rest/docs/basic.html#api-versions
