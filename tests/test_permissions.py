"""Login files hold refresh tokens: CLIProxyAPI writes new ones readable by other users, codexpool makes them private."""
import os
import unittest
from unittest import mock

from _helpers import cp


class LoginFilesPrivate(unittest.TestCase):
    def setUp(self):
        cp.AUTH.mkdir(parents=True, exist_ok=True)
        self.files = [cp.AUTH / ('codex-test-open' + '@' + 'test.json')]  # built: CI's email scan
        for f in self.files:
            f.write_text('{}')
            os.chmod(f, 0o644)
            self.addCleanup(lambda f=f: f.exists() and f.unlink())

    def test_make_private(self):
        f = self.files[0]
        self.assertTrue(cp.make_private(f))
        self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertFalse(cp.make_private(f))   # already private: nothing to do

    def test_the_guard_locks_down_every_login_file(self):
        with mock.patch.object(cp, 'log_line') as logged:
            cp.lock_down_logins()
        for f in self.files:
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(logged.call_count, len(self.files))

    def test_a_missing_file_is_not_an_error(self):
        self.assertFalse(cp.make_private(cp.AUTH / 'not-there.json'))


if __name__ == '__main__':
    unittest.main()
