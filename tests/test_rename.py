"""Migration must keep one credential directory and leave unrelated installs alone."""
from _helpers import cp, HOME
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import plistlib
import tempfile
import unittest
from unittest import mock


class Rename(unittest.TestCase):
    def migration(self, dry=False, existing=False):
        temp = tempfile.TemporaryDirectory(dir=HOME)
        self.addCleanup(temp.cleanup)
        home = Path(temp.name)
        legacy, new = home / '.codexpool', home / '.subpool'
        legacy.mkdir()
        (legacy / 'auth').mkdir()
        # An opaque fixture stands in for credential data; migration never opens it.
        (legacy / 'auth' / 'seat').write_bytes(b'opaque fixture')
        if existing:
            new.mkdir()
        with mock.patch.object(cp, 'ROOT', new), mock.patch.object(cp, 'LEGACY_ROOT', legacy), \
                contextlib.redirect_stdout(io.StringIO()):
            cp.migrate_legacy_install(cp.Steps(dry))
        return legacy, new

    def test_one_directory_and_compatibility_link(self):
        old, new = self.migration()
        self.assertTrue(old.is_symlink())
        self.assertEqual(old.resolve(), new)
        self.assertEqual((old / 'auth' / 'seat').stat().st_ino, (new / 'auth' / 'seat').stat().st_ino)

    def test_dry_run_changes_nothing(self):
        old, new = self.migration(dry=True)
        self.assertFalse(old.is_symlink())
        self.assertFalse(new.exists())

    def test_two_installs_are_not_merged(self):
        with self.assertRaisesRegex(RuntimeError, 'separate installs'):
            self.migration(existing=True)

    def test_missing_legacy_settings_keep_existing_agent_labels(self):
        with tempfile.TemporaryDirectory(dir=HOME) as name:
            home = Path(name)
            legacy = home / '.codexpool'
            legacy.mkdir()
            script = Path(cp.__file__)
            code = ('import runpy,json,sys; m=runpy.run_path(sys.argv[1],run_name="test"); '
                    'print(json.dumps(m["SETTINGS"]))')
            env = dict(os.environ, HOME=str(home))
            for key in ('SUBPOOL_SETTINGS', 'CODEXPOOL_SETTINGS'):
                env.pop(key, None)
            def settings():
                result = subprocess.run([sys.executable, '-c', code, str(script)], env=env,
                                        capture_output=True, text=True, check=True)
                return json.loads(result.stdout)
            before = settings()
            self.assertEqual(before['pool_label'], 'com.codexpool.pool')
            self.assertEqual(before['guard_label'], 'com.codexpool.guard')
            new = home / '.subpool'
            legacy.rename(new)
            legacy.symlink_to(new.name)
            self.assertEqual(settings(), before)
            (new / 'settings.json').write_text(json.dumps({'pool_label': 'com.example.custom'}))
            self.assertEqual(settings()['pool_label'], 'com.example.custom')

    def test_bundle_launch_is_preferred(self):
        launcher = cp.ROOT / 'apps' / 'subpool.app' / 'Contents' / 'MacOS' / 'launch'
        launcher.parent.mkdir(parents=True, exist_ok=True)
        launcher.write_text('#!/bin/sh\n')
        self.addCleanup(launcher.unlink)
        self.assertEqual(cp.gui_argv('seats', 'codex'),
                         [str(launcher), '--pane', 'seats', '--pool', 'codex'])


class Bundle(unittest.TestCase):
    def test_metadata_and_quoted_launcher(self):
        spec = importlib.util.spec_from_file_location('subpool_bundle', Path(__file__).resolve().parents[1]
                                                      / 'menubar' / 'bundle.py')
        bundle = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(bundle)
        with tempfile.TemporaryDirectory(dir=HOME, prefix='bundle with spaces ') as name:
            root = Path(name)
            base = root / 'runtime with spaces'
            (base / 'bin').mkdir(parents=True)
            executable = base / 'bin' / 'python'
            executable.write_bytes(b'opaque executable fixture')
            executable.chmod(0o755)
            runtime = {'base': str(base), 'executable': str(executable), 'site': str(root / 'venv site')}
            with mock.patch.object(bundle, 'runtime_description', return_value=runtime), \
                    mock.patch.object(bundle, 'check_bundle') as check:
                app = bundle.build_bundle(root, executable, '1.3.0')
                info = plistlib.loads((app / 'Contents' / 'Info.plist').read_bytes())
                self.assertEqual(info['CFBundleDisplayName'], 'subpool.app')
                self.assertEqual(info['CFBundleIdentifier'], 'com.subpool.settings')
                launch = app / 'Contents' / 'MacOS' / info['CFBundleExecutable']
                self.assertIn("export PYTHONHOME='", launch.read_text())
                self.assertIn('"$@"', launch.read_text())
                inode = (app / 'Contents' / 'MacOS' / 'subpool').stat().st_ino
                bundle.build_bundle(root, executable, '1.3.0')
                self.assertEqual(inode, (app / 'Contents' / 'MacOS' / 'subpool').stat().st_ino)
                self.assertEqual(check.call_count, 2)
