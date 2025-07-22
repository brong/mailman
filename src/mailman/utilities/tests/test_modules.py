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

"""Tests for mailman.utilities.modules."""

import os
import sys
import unittest

from contextlib import contextmanager, ExitStack
from importlib.resources import files
from mailman.interfaces.rules import IRule
from mailman.interfaces.styles import IStyle
from mailman.testing.helpers import configuration
from mailman.testing.layers import ConfigLayer
from mailman.utilities.modules import (
    find_components,
    find_pluggable_components,
    hacked_sys_modules,
)
from pathlib import Path
from tempfile import TemporaryDirectory


@contextmanager
def hack_syspath(index, path):
    old_path = sys.path[:]
    try:
        sys.path.insert(index, path)
        yield
    finally:
        sys.path = old_path


def clean_mypackage():
    # Make a copy since we'll mutate it as we go.
    for module in sys.modules.copy():
        package, dot, rest = module.partition('.')
        if package == 'mypackage':
            del sys.modules[module]


class TestModuleImports(unittest.TestCase):
    layer = ConfigLayer

    def test_find_modules_with_dotfiles(self):
        # Emacs creates lock files when a single file is opened by more than
        # one user. These files look like .#<filename>.py because of which
        # find_components() tries to import them but fails. All such files
        # should be ignored by default.
        with ExitStack() as resources:
            # Creating a temporary directory and adding it to sys.path.
            temp_package = resources.enter_context(TemporaryDirectory())
            resources.enter_context(hack_syspath(0, temp_package))
            resources.callback(clean_mypackage)
            # Create a module inside the above package along with a good, bad
            # and __init__.py file so that we can import from it.
            module_path = os.path.join(temp_package, 'mypackage')
            os.mkdir(module_path)
            init_file = os.path.join(module_path, '__init__.py')
            Path(init_file).touch()
            good_file = os.path.join(module_path, 'goodfile.py')
            bad_file = os.path.join(module_path, '.#badfile.py')
            with open(good_file, 'w', encoding='utf-8') as fp:
                print("""\
from public import public
from mailman.interfaces.styles import IStyle
from zope.interface import implementer

@public
@implementer(IStyle)
class GoodStyle:
    name = 'good-style'
    def apply(self):
        pass
""", file=fp)
            with open(bad_file, 'w', encoding='utf-8') as fp:
                print("""\
from public import public
from mailman.interfaces.styles import IStyle
from zope.interface import implementer

@public
@implementer(IStyle)
class BadStyle:
    name = 'bad-style'
    def apply(self):
        pass
""", file=fp)
            # Find all the IStyle components in the dummy package.  This
            # should find GoodStyle but not BadStyle.
            names = [component.name
                     for component
                     in find_components('mypackage', IStyle)]
            self.assertEqual(names, ['good-style'])

    def test_find_components_abstract_component(self):
        # find_components() finds the class unless it's been
        # decorated with the @abstract_component decorator.
        with ExitStack() as resources:
            # Creating a temporary directory and adding it to sys.path.
            temp_package = resources.enter_context(TemporaryDirectory())
            resources.enter_context(hack_syspath(0, temp_package))
            resources.callback(clean_mypackage)
            # Create a module inside the above package along with an
            # __init__.py file so that we can import from it.
            module_path = os.path.join(temp_package, 'mypackage')
            os.mkdir(module_path)
            init_file = os.path.join(module_path, '__init__.py')
            Path(init_file).touch()
            component_file = os.path.join(module_path, 'components.py')
            with open(component_file, 'w', encoding='utf-8') as fp:
                print("""\
from mailman.interfaces.styles import IStyle
from mailman.utilities.modules import abstract_component
from public import public
from zope.interface import implementer

@public
@implementer(IStyle)
class ConcreteStyle:
    name = 'concrete-style'
    def apply(self):
        pass

@public
@implementer(IStyle)
@abstract_component
class AbstractStyle:
    name = 'abstract-style'
    def apply(self):
        pass
""", file=fp)
            names = [component.name
                     for component
                     in find_components('mypackage', IStyle)]
            self.assertEqual(names, ['concrete-style'])

    def test_hacked_sys_modules(self):
        self.assertIsNone(sys.modules.get('mailman.not_a_module'))
        with hacked_sys_modules('mailman.not_a_module', object()):
            self.assertIsNotNone(sys.modules.get('mailman.not_a_module'))

    def test_hacked_sys_modules_restore(self):
        email_package = sys.modules['email']
        sentinel = object()
        with hacked_sys_modules('email', sentinel):
            self.assertEqual(sys.modules.get('email'), sentinel)
        self.assertEqual(sys.modules.get('email'), email_package)

    def test_find_pluggable_components_by_plugin_name(self):
        with ExitStack() as resources:
            testing_path = files('mailman.plugins.testing')
            resources.enter_context(hack_syspath(0, str(testing_path)))
            resources.enter_context(configuration('plugin.example', **{
                'class': 'example.hooks.ExamplePlugin',
                'enabled': 'yes',
                }))
            components = list(find_pluggable_components('rules', IRule))
        self.assertIn('example-rule', {rule.name for rule in components})

    def test_find_pluggable_components_by_component_package(self):
        with ExitStack() as resources:
            testing_path = files('mailman.plugins.testing')
            resources.enter_context(hack_syspath(0, str(testing_path)))
            resources.enter_context(configuration('plugin.example', **{
                'class': 'example.hooks.ExamplePlugin',
                'enabled': 'yes',
                'component_package': 'alternate',
                }))
            components = list(find_pluggable_components('rules', IRule))
        self.assertNotIn('example-rule', {rule.name for rule in components})
        self.assertIn('alternate-rule', {rule.name for rule in components})
