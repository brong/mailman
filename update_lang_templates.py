#! /usr/bin/env python
# Author: Abhilash Raj
# Date: Aug 21, 2021
#
# The purpose of this script is to generate various templates supported by
# Mailman from the .po files that are translated by translators. The workflow
# for the entire translation looks like this:
# english template -> en mailman.po -> weblate -> language.po -> language templates.
#
# English templates -> mailman.pot is handled by update_po.py script, which
# will copy the contents of the english templates into the .po file for en
# language so it can serve as the reference for translators in weblate. After
# we get translated .po files back from weblate, we copy the template text
# back into individual files of each language using *this script*.

from pathlib import Path


try:
    from babel.messages.pofile import read_po, write_po
except ImportError:
    print('Please install `babel` to run this script.')
    exit(1)

PO_BASE_PATH = Path('src/mailman/messages')
PO_PATH_TEMPLATE = 'src/mailman/messages/{}/LC_MESSAGES/mailman.po'
TEMPLATE_PATH_TEMPLATE = 'src/mailman/templates/{lang}/{name}'

IGNORE_PATH_NAMES = [
    '__init__.py',
    '__pycache__',
    'en',
    'mailman.pot',
]


def get_po(lang):
    "Read the po file path and return a Catalog object."
    po_path = Path(PO_PATH_TEMPLATE.format(lang))
    if not po_path.exists():
        print(f'{po_path} does not exist.')
        return None

    with po_path.open() as fd:
        catalog = read_po(fd)
    return catalog


def write_template(lang, name, content):
    "Get the template text with the name if it exists."
    template_path = Path(TEMPLATE_PATH_TEMPLATE.format(lang=lang, name=name))
    template_path.parent.mkdir(exist_ok=True, parents=True)
    if content and not content.endswith('\n'):
        content += '\n'
    template_path.write_text(content)


def main():
    for subdir in PO_BASE_PATH.iterdir():
        if subdir.name in IGNORE_PATH_NAMES:
            continue

        lang = subdir.name
        catalog = get_po(lang)

        if catalog is None:
            print(f'Failed to get catalog for {lang}')
            continue

        for each in catalog:
            if each.id.endswith('.txt'):
                write_template(lang, each.id, each.string)

        print(f'Finished writing templates for {lang}')

main()
