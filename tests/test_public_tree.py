"""The public tree describes the Codex pool only. An add-on's product, company and code names never appear in a core
file's text or path: everything about an add-on lives under addons/<id>/, which the public repository ignores.
The names are assembled at runtime so this file does not trip its own scan."""
import pathlib
import re
import unittest

from _helpers import REPO

# Each name reversed, so no core file (this one included) spells one out.
NAMES = tuple(word[::-1] for word in ('edualc', 'ciporhtna', 'anneis', 'krowoc'))
PATTERN = re.compile('|'.join(map(re.escape, NAMES)).encode(), re.I)
SKIP_DIRS = {'addons', '.git', '__pycache__', '.ruff_cache', '.venv', 'node_modules'}
SKIP_FILES = {'LOCAL.md'}   # the owner's private notes, never committed (.gitignore)
SKIP_SUFFIXES = {'.pyc'}
# The generic add-on interface may name the add-on mechanism (addons/, add-ons), never a product: no exemptions.
EXEMPT = frozenset()


def core_files():
    for path in sorted(REPO.rglob('*')):
        rel = path.relative_to(REPO)
        if any(part in SKIP_DIRS for part in rel.parts) or not path.is_file():
            continue
        if path.name in SKIP_FILES or path.suffix in SKIP_SUFFIXES:
            continue
        yield rel.as_posix(), path


class PublicTree(unittest.TestCase):
    def test_no_addon_names_in_core_paths_or_text(self):
        hits = []
        for rel, path in core_files():
            if rel in EXEMPT:
                continue
            if PATTERN.search(rel.encode()):
                hits.append(f'{rel}: (path)')
                continue
            data = path.read_bytes()
            for number, line in enumerate(data.splitlines(), 1):
                if PATTERN.search(line):
                    hits.append(f'{rel}:{number}: {line[:120].decode("utf-8", "replace")!r}')
        self.assertEqual(hits, [], 'an add-on name in the public tree:\n' + '\n'.join(hits))

    def test_the_scan_sees_the_tree(self):
        files = dict(core_files())
        self.assertIn('bin/codexpool', files)
        self.assertIn('README.md', files)
        self.assertIn('docs/ADDONS.md', files)
        self.assertFalse(any(rel.startswith('addons/') for rel in files))
        for name in NAMES:
            self.assertTrue(PATTERN.search(('x' + name.upper() + 'y').encode()), name)

    def test_gitignore_keeps_addons_out(self):
        self.assertIn('/addons/', (REPO / '.gitignore').read_text().splitlines())


if __name__ == '__main__':
    unittest.main()
