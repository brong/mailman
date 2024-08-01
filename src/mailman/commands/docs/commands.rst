========
Commands
========

All of the following docs begin with a Python snippet that imports the ``cli``
function from ``mailman.testing.documentation`` and sets ``command`` to an
invocation of that function.  Then they invoke example commands by calling
``command`` with an argument of the command line.

This is done to facilitate the testing framework for doc tests which actually
runs all the Python snippets in the docs to verify they work as expected.  In
practice, one just runs the command line directly.

.. toctree::
   :glob:

   ./*
