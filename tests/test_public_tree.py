"""Public source includes Claude; logins and runtime state stay local."""
import pathlib
import importlib.util
import tempfile
import unittest
from _helpers import REPO

RUNTIME_PATHS = ('auth', 'auth-claude', 'state', 'logs', 'config.yaml', 'config-claude.yaml',
                 'seats.json', 'claude-seats.json', 'settings.json', 'rollback')

class PublicTree(unittest.TestCase):
    def test_gitignore_allows_addon_source(self):
        rules = (REPO / '.gitignore').read_text().splitlines()
        self.assertNotIn('/addons/', rules)
        self.assertNotIn('/addons/sienna/', rules)

    def test_runtime_state_is_ignored(self):
        rules = (REPO / '.gitignore').read_text().splitlines()
        for path in RUNTIME_PATHS:
            self.assertTrue('/' + path in rules or '/' + path + '/' in rules, path)
        self.assertIn('LOCAL.md', rules)

    def test_claude_publication_is_explicit(self):
        policy = (REPO / 'AGENTS.md').read_text()
        self.assertIn('Claude integration in `addons/sienna/` is expressly allowed', policy)
        self.assertIn('Claude, Anthropic, sienna and Cowork may be named', policy)

class PublicationScan(unittest.TestCase):
    def scanner(self):
        spec = importlib.util.spec_from_file_location('publication_scan', REPO / 'scripts/publish_tree.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def test_claude_source_and_synthetic_fixtures_can_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / 'addons/sienna').mkdir(parents=True)
            (root / 'addons/sienna/README.md').write_text('Claude Anthropic sienna Cowork; sample@example.com')
            self.assertEqual(self.scanner().scan(root)[1], [])

    def test_credentials_and_runtime_state_cannot_publish(self):
        with tempfile.TemporaryDirectory() as temp:
            root = pathlib.Path(temp)
            (root / 'auth-claude').mkdir()
            (root / 'auth-claude/account.json').write_text('{}')
            (root / 'README.md').write_text('account' + '@' + 'company.tld; ' + 'sk-' + 'a' * 32)
            hits = self.scanner().scan(root)[1]
            for kind in ('runtime:', 'secret:', 'personal:'):
                self.assertTrue(any(hit.startswith(kind) for hit in hits), kind)

if __name__ == '__main__':
    unittest.main()
