========
Commands
========

Many of the following docs begin with a Python snippet that imports the ``cli``
function from ``mailman.testing.documentation`` and sets ``command`` to an
invocation of that function.  Then they invoke example commands by calling
``command`` with an argument of the command line.

This is done to facilitate the testing framework for doc tests which actually
runs all the Python snippets in the docs to verify they work as expected.  In
practice, one just runs the command line directly.

For email commands, one sends a plain text message to the list's -request
address. The Subject: and first few body lines of the message are parsed for
email commands which are then executed and results are reported back by email
to the sender.

.. toctree::
   :glob:

   ./*
