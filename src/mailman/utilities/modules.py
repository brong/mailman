# Copyright (C) 2009-2025 by the Free Software Foundation, Inc.
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

"""Package and module utilities."""

import os
import sys

from contextlib import contextmanager
from importlib import import_module
from importlib.resources import contents, is_resource, path
from public import public


@public
def abstract_component(cls):
    """Decorator preventing `find_components()` from instantiating the class.

    Normally, `find_components()` instantiates any component class that
    it finds matching the given interface.  Some component classes must not be
    instantiated though, because they act as base classes.  Put this decorator
    on the class definition to prevent instantiation.
    """
    cls.__abstract_component__ = True
    return cls


@public
def find_name(dotted_name):
    """Import and return the named object in package space.

    :param dotted_name: The dotted module path name to the object.
    :type dotted_name: string
    :return: The object.
    :rtype: object
    """
    module_path, dot, object_name = dotted_name.rpartition('.')
    module = import_module(module_path)
    return getattr(module, object_name)


@public
def call_name(dotted_name, *args, **kws):
    """Imports and calls the named object in package space.

    :param dotted_name: The dotted module path name to the object.
    :type dotted_name: string
    :param args: The positional arguments.
    :type args: tuple
    :param kws: The keyword arguments.
    :type kws: dict
    :return: The object.
    :rtype: object
    """
    named_callable = find_name(dotted_name)
    return named_callable(*args, **kws)


@public
def expand_path(resources, url):
    """Expand a python: path, returning the absolute file system path."""
    # Is the context coming from a file system or Python path?
    if url.startswith('python:'):
        resource_path = url[7:]
        package, dot, resource = resource_path.rpartition('.')
        cfg_path = resources.enter_context(path(package, resource + '.cfg'))
        return str(cfg_path)
    else:
        return url


@public
@contextmanager
def hacked_sys_modules(name, module):
    old_module = sys.modules.get(name)
    sys.modules[name] = module
    try:
        yield
    finally:
        if old_module is None:
            del sys.modules[name]
        else:
            sys.modules[name] = old_module


def scan_module(module, interface):
    """Return all the items in a module that conform to an interface.

    Scan every item named in the module's `__all__`.  If that item conforms to
    the given interface, *and* the item is not declared as an
    `@abstract_component`, then return the item.

    :param module: A module object.
    :type module: module
    :param interface: The interface that returned objects must conform to.
    :type interface: `Interface`
    :return: The sequence of matching components.
    :rtype: items implementing `interface`
    """
    missing = object()
    for name in module.__all__:
        component = getattr(module, name, missing)
        assert component is not missing, (
            '%s has bad __all__: %s' % (module, name))   # pragma: nocover
        if (interface.implementedBy(component)
                # We cannot use getattr() here because that will return True
                # for all subclasses.  __abstract_component__ should *not* be
                # inherited, meaning subclasses must declare themselves to be
                # abstract if they also don't want to be instantiated.  Only
                # by looking at the component's __dict__ can we know for sure
                # where the marker has been placed.  The value of
                # __abstract_component__ doesn't matter, only its presence.
                and '__abstract_component__' not in component.__dict__):
            yield component


def find_components(package, interface):
    """Find components which conform to a given interface.

    Search all the modules in a given package, returning an iterator over all
    items found that conform to the given interface, unless that object is
    decorated with `@abstract_component`.

    :param package: The package path to search.
    :type package: string
    :param interface: The interface that returned objects must conform to.
    :type interface: `Interface`
    :return: The sequence of matching components.
    :rtype: items implementing `interface`
    """
    for filename in contents(package):
        basename, extension = os.path.splitext(filename)
        if extension != '.py' or basename.startswith('.'):
            continue
        module_name = '{}.{}'.format(package, basename)
        module = import_module(module_name)
        if not hasattr(module, '__all__'):
            continue
        yield from scan_module(module, interface)


def find_pluggable_components(subpackage, interface):
    """Find components which conform to a given interface.

    This finds components which can be implemented in a plugin.  It will
    search for the interface in the named subpackage, where the Python import
    path of the subpackage will be prepended by `mailman` for system
    components, and the various plugin names for any external components.

    :param subpackage: The subpackage to search.  This is prepended by
        'mailman' to search for system components, and each enabled plugin for
        external components.
    :type subpackage: str
    :param interface: The interface that returned objects must conform to.
    :type interface: `Interface`
    :return: The sequence of matching components.
    :rtype: Objects implementing `interface`
    """
    # This can't be imported at module level because of circular imports.
    from mailman.config import config

    # Return the system components first.
    yield from find_components('mailman.' + subpackage, interface)
    # Return all the matching components in all the subpackages of all enabled
    # plugins.  Only enabled and existing plugins will appear in this
    # dictionary.
    for name, plugin_config in config.plugin_configs:
        # If the plugin's configuration defines a components package, use
        # that, falling back to the plugin's name.
        package = plugin_config['component_package'].strip()
        if len(package) == 0:
            package = name
        # It's possible that the plugin doesn't include the directory for this
        # subpackage.  That's fine.
        if (subpackage in contents(package) and
                not is_resource(package, subpackage)):
            plugin_package = '{}.{}'.format(package, subpackage)
            yield from find_components(plugin_package, interface)


@public
def add_components(subpackage, interface, mapping):
    """Add components to a given mapping.

    Similarly to `find_pluggable_components()` this inspects all modules
    in the given subpackage, relative to the 'mailman' parent package,
    and all the plugin names, that match the given interface.  All such
    found objects (unless decorated with `@abstract_component`) are
    instantiated and added to the given mapping, keyed by the object's `.name`
    attribute, which is required.  It is a fatal error if that key already
    exists in the mapping.

    :param subpackage: The subpackage path to search.
    :type subpackage: str
    :param interface: The interface that returned objects must conform to.
        Objects found must have a `.name` attribute containing a unique
        string.
    :type interface: `Interface`
    :param mapping: The mapping to add the found components to.
    :type mapping: A dict-like mapping.  This only needs to support
        containment tests (e.g. `in` and `not in`) and `__setitem__()`.
    :raises RuntimeError: when a duplicate key is found.
    """
    for component_class in find_pluggable_components(subpackage, interface):
        component = component_class()
        if component.name in mapping:
            raise RuntimeError(     # pragma: nocover
                'Duplicate key "{}" found in {}; previously {}'.format(
                    component.name, component, mapping[component.name]))
        mapping[component.name] = component
