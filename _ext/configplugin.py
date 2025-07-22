# Copyright (C) 2020-2025 by the Free Software Foundation, Inc.
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

"""Sphinx plugin to render Mailman Core configuration file schema.cfg."""

import re
import configparser

from docutils import nodes
from docutils.parsers.rst import Directive, directives
from docutils.statemachine import ViewList
from importlib.resources import files
from sphinx.util.nodes import nested_parse_with_titles


def get_config_text():
    """Get Mailman's schema.cfg as str"""
    return files('mailman.config').joinpath('schema.cfg').read_text()


def get_section_text(section, schema_text):
    """Get the text of a give ini section.

    This includes the region between two `[section]` headers in the ini file.

    :param section: The name of the section.
    :param schema_text: The whole config file contents.
    :returns: The str of all the value in the section.
    :raises ValueError: If the name of the section can't be found.
    ."""
     # Split the whole file at the boundary of [sections].
    sections = re.split(r'^\[(?P<header>[^]]+)\]',
                        schema_text, flags=re.MULTILINE)
    if not f'{section}' in sections:
        raise ValueError('Invalid section name {}'.format(section))
    section_index = sections.index(section)
    section_text = sections[section_index + 1]

    return section_text

def is_comment(para):
    """Check if a paragraph is comment without any options.

    :param para: The paragraph text.
    :returns: True if all lines start with '#', False otherwise.
    """
    para = para.strip()
    for line in para.splitlines():
        if not line.startswith('#'):
            return False
    return True


def get_options(section_text):
    """Parse the key:value pairs from it along with comments.

    Given the text of a section, split the whole text with empty lines
    ('\n\n').  For each part get the (key: value) pairs by letting configparser
    parse the text.  The remaining lines of text, which ends up being the
    comments in the file serve as the documentation for those key: value pairs.

    Note: We append a `[dummy]` section name to the section_text since
    configparser will refuse to parse a section text that doesn't include a
    `[section]` header.  There is no real significance of that since we
    immidiately discard the section name.

    If the section starts off with a block of just comment, it is called
    "section_doc".

    The return format looks something like:

    ([{'key': 'value', 'key2': 'value2', 'doc': 'Comments'}], "Section Doc")

    The first item is a list of dictionaries, each of which represents a
    paragraph in the ini text.  All (key: value) pairs are in the dictionary
    and the comments as a part of the 'doc'.  When there are no comments, 'doc'
    option is omitted.

    :param section_text: The whole text of the section, not include the header
        `[section]` itself.
    :returns: Parsed options, docs and section docs.
    """
    options = []
    opts_list = section_text.split('\n\n')
    section_doc = None

    # We check for a section leve doc by looking if the
    # first *two* paragraphs are both comments and *only* comments.
    if is_comment(opts_list[0]) and is_comment(opts_list[1]):
        section_doc = opts_list.pop(0)

    for each in opts_list:
        if each.strip() == '':
            continue
        # Configparser will refuse to parse section without it's name.
        each = '[dummy]\n' + each
        config = configparser.ConfigParser()
        config.read_string(each)
        data = {}
        for key in config['dummy']:
            data[key] = config['dummy'][key]

        doc = '\n'.join(line for line in each.splitlines() if line.startswith('#'))
        data['doc'] = doc.replace('#', '')
        options.append(data)
    return options, section_doc


def get_section_rst(section, section_doc, opts):
    """Convert the section text into formatted ReST.

    A section from ini file that looks like this:

        [section]
        # This is a section level documentation.
        <BLANKLINE>
        # This documentation if for immediately following key:value
        key: value

    Is converted to ReST that looks something like:

        ``[section]``
        =============

        key
        ~~~
        **default**: value

          This documentation is for the immediately following key:value
    """
    rst = '``[{}]``\n{}\n'.format(section, '='*(len(section) + 6))
    if section_doc:
        rst += section_doc.replace('#', '')
    for each in opts:
        doc = '\n'
        if 'doc' in each:
            doc = each.pop('doc')
        for opt, value in each.items():
            rst += '{}\n{}\n'.format(opt, '~'*len(opt))
            if value:
                rst += '**default**:  {}\n\n'.format(value)
        rst += doc.replace('#', '')
        rst += '\n\n'
    return rst


class ConfigSectionDirective(Directive):
    """Sphinx plugin that renders Mailman's ini configuration as ReST."""

    required_arguments = 1
    final_argument_whitespace = True
    option_spec = {}
    has_content = False

    def run(self):
        """Split the arguments as a list of sections and render as ReST."""

        sections = self.arguments[0].split()
        child_nodes = []
        lineno = 1
        for section in sections:
            rst = ViewList()
            config_text = get_config_text()
            section_text = get_section_text(section, config_text)
            section_opts, section_doc = get_options(section_text)
            section_rst = get_section_rst(section, section_doc, section_opts)
            for line in section_rst.splitlines():
                rst.append(line, 'fakefile.rst', lineno)
                lineno += 1

            node = nodes.section()
            node.document = self.state.document
            nested_parse_with_titles(self.state, rst, node)
            child_nodes.extend(node.children)
        return child_nodes


def setup(app):
    app.add_directive('configsection', ConfigSectionDirective)

    return {
        'version': '0.1',
        'parallel_read_safe': True,
        'parallel_write_safe': True,
    }
