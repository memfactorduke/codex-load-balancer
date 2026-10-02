"""The extracted pool is reached only through the published add-on hooks."""
from _helpers import addon, cp, HOME, REPO, run, sienna_guard, sienna_pool
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


class Registration(unittest.TestCase):
    def test_manifest_owns_settings_and_core_has_no_pool_implementation(self):
        manifest = cp.read_addon_manifest(REPO / 'addons/sienna')
        self.assertEqual(set(manifest['settings']),
                         {'claude_label', 'claude_port', 'claude_balancing', 'claude_cpa'})
        self.assertTrue(set(manifest['settings']).isdisjoint(cp.CORE_SETTINGS_DEFAULTS))
        self.assertEqual(set(cp.POOL_INSTANCES), {'codex'})
        self.assertEqual(set(cp.SEAT_POOLS), {'codex'})
        for name in ('claude_guard_pass', 'doctor_claude_checks', 'cmd_claude_selftest', 'CLAUDE_AUTH'):
            self.assertFalse(hasattr(cp, name), name)
        self.assertEqual(cp.seat_pool('claude').auth, cp.ROOT / 'auth-claude')
        self.assertEqual(cp.pool_instance('claude').link, cp.BIN / 'claude-current')

    def test_guard_runs_core_then_addon_with_separate_locks(self):
        order, locks = [], []
        @contextlib.contextmanager
        def lock(**kwargs):
            locks.append(kwargs['pool'].lock)
            yield True
        with mock.patch.object(cp, 'guard_lock', side_effect=lock), \
                mock.patch.object(cp, 'lock_down_logins'), \
                mock.patch.object(cp, 'guard_pass', side_effect=lambda: order.append('codex')), \
                mock.patch.object(sienna_pool, 'claude_installed', return_value=True), \
                mock.patch.object(sienna_guard, 'claude_guard_pass', side_effect=lambda: order.append('claude')):
            cp.cmd_guard(None)
        self.assertEqual(order, ['codex', 'claude'])
        self.assertEqual(locks, [cp.LOCK_FILE, sienna_pool.CLAUDE_LOCK_FILE])
        with mock.patch.object(sienna_pool, 'claude_installed', return_value=False):
            self.assertEqual(addon.guard_passes(), [])

    def test_status_keeps_legacy_json_key_and_print_order(self):
        core, private = {'pool': {'running': False}}, {'pool': {'installed': True}}
        with mock.patch.object(cp, 'require_installed'), mock.patch.object(cp, 'status_now', return_value=core), \
                mock.patch.object(sienna_pool, 'claude_installed', return_value=True), \
                mock.patch.object(sienna_pool, 'claude_status_view', return_value=(private, 'fixture')), \
                mock.patch.object(cp, 'print_status', side_effect=lambda *_: print('core')), \
                mock.patch.object(sienna_pool, 'print_claude_status', side_effect=lambda *_: print('private')):
            code, text, err = run(cp.cmd_status, live=True, json=True)
            self.assertEqual((code, json.loads(text), err), (0, dict(core, claude=private), ''))
            self.assertEqual(run(cp.cmd_status, live=True, json=False), (0, 'core\n\nprivate\n', ''))

    def test_doctor_and_uninstall_delegate_to_the_pool(self):
        report = cp.DoctorReport(echo=False)
        with mock.patch.object(sienna_pool, 'claude_installed', return_value=True), \
                mock.patch.object(sienna_pool, 'doctor_claude_checks') as doctor, \
                mock.patch.object(sienna_pool, 'claude_uninstall_plan', return_value=[('first', lambda: None)]) as plan:
            addon.doctor(report)
            doctor.assert_called_once_with(report)
            steps = cp.addon_uninstall_steps()
            self.assertEqual([(a.id, title) for a, title, _ in steps], [('sienna', 'first')])
            plan.assert_called_once_with()

    def test_partial_payload_never_executes(self):
        with tempfile.TemporaryDirectory(dir=HOME) as work:
            target = Path(work) / 'sienna'
            shutil.copytree(REPO / 'addons/sienna', target)
            (target / 'guard.py').unlink()
            manifest = cp.read_addon_manifest(target)
            with self.assertRaisesRegex(ValueError, 'missing'):
                cp.load_addon(target, manifest)

    def test_core_only_checkout_starts_without_private_commands_or_settings(self):
        with tempfile.TemporaryDirectory(dir=HOME) as work:
            root = Path(work)
            (root / 'bin').mkdir()
            shutil.copy(REPO / 'bin/subpool', root / 'bin/subpool')
            fake_home = root / 'home'
            fake_home.mkdir()
            env = dict(os.environ, HOME=str(fake_home), CODEXPOOL_SETTINGS=str(root / 'settings.json'))
            result = subprocess.run([sys.executable, str(root / 'bin/subpool'), '--help'],
                                    env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertNotIn('sienna', result.stdout)
            self.assertNotIn('claude_balancing', result.stdout)
            self.assertNotIn('claude', result.stderr)
            result = subprocess.run([sys.executable, str(root / 'bin/subpool'), '--version'],
                                    env=env, text=True, capture_output=True, timeout=15)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, f'subpool {cp.VERSION}\n')
            self.assertFalse((fake_home / '.subpool').exists(), 'read-only bootstrap wrote runtime state')
