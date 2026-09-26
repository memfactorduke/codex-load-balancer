"""install.sh: how it finds the Codex app and the codex CLI inside it, through a dry run (which downloads and changes
nothing) in the fake home, with macOS's /bin/bash 3.2."""
import os
import plistlib
import shutil
import subprocess
import unittest

from _helpers import HOME, REPO

NEW = 'Contents/Resources/codex-cli/bin/codex'  # the Codex app 26.924 and later
OLD = 'Contents/Resources/codex'                # older builds


class CodexAppCheck(unittest.TestCase):
    def setUp(self):
        self.apps = HOME / 'Applications'
        self.apps.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, str(self.apps), True)
        self.bin = HOME / 'install-sh-test'
        self.bin.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, str(self.bin), True)

    def bundle(self, name, *clis, bundle_id='com.openai.codex'):
        """A fake app bundle in ~/Applications: an Info.plist with bundle_id and a codex script at each of clis."""
        app = self.apps / name
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents' / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': bundle_id}))
        for rel in clis:
            (app / rel).parent.mkdir(parents=True, exist_ok=True)
            (app / rel).write_text('#!/bin/sh\n')
            (app / rel).chmod(0o755)
        return app

    def app_line(self, *found):
        """install.sh --dry-run's line about the Codex app, with Spotlight (mdfind) finding the bundles in found."""
        mdfind = self.bin / 'mdfind'
        mdfind.write_text('#!/bin/sh\n' + ''.join(f'echo "{app}"\n' for app in found))
        mdfind.chmod(0o755)
        r = subprocess.run(['/bin/bash', str(REPO / 'install.sh'), '--dry-run', '--no-gui'], capture_output=True,
                           text=True, stdin=subprocess.DEVNULL, start_new_session=True, timeout=60,
                           env=dict(os.environ, PATH=f'{self.bin}{os.pathsep}{os.environ["PATH"]}'))
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return next(line.strip() for line in r.stdout.splitlines() if 'Codex app' in line)

    def test_new_layout(self):
        app = self.bundle('ChatGPT.app', NEW, OLD)
        self.assertEqual(self.app_line(app), f'✓ Codex app: ~/Applications/ChatGPT.app (its codex CLI: {NEW})')

    def test_older_layout(self):
        app = self.bundle('Codex.app', OLD)
        self.assertEqual(self.app_line(app), f'✓ Codex app: ~/Applications/Codex.app (its codex CLI: {OLD})')

    def test_by_bundle_id_not_by_name(self):
        other = self.bundle('Codex.app', NEW, bundle_id='com.example.other')
        app = self.bundle('Renamed.app', NEW)
        self.assertEqual(self.app_line(other, app), f'✓ Codex app: ~/Applications/Renamed.app (its codex CLI: {NEW})')


if __name__ == '__main__':
    unittest.main()
