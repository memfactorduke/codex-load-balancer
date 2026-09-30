"""Second verification regressions: isolated fixtures, never a real app or pool."""
from _helpers import addon
sienna_desktop, desktop_build = addon.desktop, addon.desktop_build

from _helpers import addon
import unittest
if addon is None:
    raise unittest.SkipTest('sienna add-on absent; dependent desktop/engine tests')
from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import cp, HOME, ROOT
import contextlib
import datetime as dt
import fcntl
import json
import pathlib
import unittest
from unittest.mock import patch
import test_desktop
from test_desktop import mod
from test_desktop_security import crash_at, Boom


class ReworkTests(unittest.TestCase):
    # Reuse only fixture helpers, not the original tests.
    for _name in ('setUp', 'clean', 'command', 'state', 'entry_path', 'put', 'meta_path', 'mode_path', 'read', 'original'):
        locals()[_name] = getattr(test_desktop.DesktopTests, _name)

    def test_other_configuration_refuses_before_reads_or_quit(self):
        ident, path = self.original()
        before = path.read_bytes()
        read = mod.SafeFiles.read
        def checked(fs, p, *args, **kwargs):
            self.assertNotEqual(p, path, 'another provider must not be read')
            return read(fs, p, *args, **kwargs)
        with patch.object(mod.SafeFiles, 'read', checked), patch.object(mod.Desktop, 'quit_app') as quit_app:
            with self.assertRaisesRegex(mod.DesktopError, 'another third-party configuration exists') as err:
                self.command(relaunch=True, yes=True)
            quit_app.assert_not_called()
            with sienna_desktop.desktop_backend() as d:
                status = d.status(probe=False)
        self.assertIn('Configure Third-Party Inference', str(err.exception))
        self.assertIn('codexpool leaves the desktop to you', ' '.join(status['errors']))
        self.assertEqual(status['configured_mode'], 'other')
        self.assertIsNone(status['policy_inherited'])
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse((cp.STATE / 'desktop.json').exists())

    def test_N1_undo_groups_files_and_writes_1p_first(self):
        with crash_at('published-state'), self.assertRaises(Boom):
            self.command()
        written = []
        write = mod.SafeFiles.write
        def record(fs, path, data):
            if sienna_desktop.DESKTOP_P3 in path.parents:
                written.append(path)
            return write(fs, path, data)
        with sienna_desktop.desktop_backend() as d, patch.object(mod.SafeFiles, 'write', record), d.fs.lock():
            d.recover(explicit=True, force_undo=True)
        self.assertEqual(written[0], self.mode_path())
        self.assertEqual(len(written), len(set(written)))
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')

    def test_N2_recovery_preserves_renamed_or_edited_membership(self):
        for rename, edit in ((True, False), (False, True), (True, True)):
            with self.subTest(rename=rename, edit=edit):
                self.clean()
                with crash_at('meta'), self.assertRaises(Boom):
                    self.command()
                journal = self.read(cp.STATE / 'desktop-txn.json')
                entry = sienna_desktop.DESKTOP_P3 / 'configLibrary' / (journal['entry_id'] + '.json')
                if rename:
                    meta = self.read(self.meta_path()); meta['entries'][0]['name'] = 'My pool'
                    self.put(self.meta_path(), meta)
                if edit:
                    self.put(entry, dict(self.read(entry), builtinToolPolicy={'Bash': 'ask'}))
                self.command('claudeai' if rename and edit else 'rollback')
                self.assertTrue(entry.exists())
                self.assertEqual(self.read(self.meta_path())['entries'],
                                 [{'id': journal['entry_id'], 'name': 'My pool' if rename else 'Pool'}])
                if edit:
                    self.assertEqual(self.read(entry)['builtinToolPolicy'], {'Bash': 'ask'})
                self.command()
                self.assertEqual(self.state()['entry_id'], journal['entry_id'])

    def test_N4_second_lock_detects_state_and_journal_changes(self):
        for rival in ('commit', 'journal'):
            with self.subTest(rival=rival):
                self.clean()
                running = dict(self.stopped, app_running=True, app_started_at=cp.now_utc().isoformat())
                saved = {}
                def quit_app(*args):
                    running['app_running'] = False
                    if rival == 'commit':
                        self.command()
                        saved['state'] = (cp.STATE / 'desktop.json').read_bytes()
                    else:
                        self.put(cp.STATE / 'desktop-txn.json', {'rival': True})
                        saved['journal'] = (cp.STATE / 'desktop-txn.json').read_bytes()
                with patch.object(mod.Desktop, 'process', side_effect=lambda *a: dict(running)), \
                     patch.object(mod.Desktop, 'quit_app', side_effect=quit_app), patch.object(mod.Desktop, 'open_app'):
                    with self.assertRaisesRegex(mod.DesktopError, 'changed since planning'):
                        self.command(relaunch=True, yes=True)
                path = cp.STATE / ('desktop.json' if rival == 'commit' else 'desktop-txn.json')
                self.assertEqual(path.read_bytes(), saved['state' if rival == 'commit' else 'journal'])

    def test_N7_uninstall_does_not_require_an_app_or_visible_chooser(self):
        for problem in ('missing', 'old', 'chooser'):
            with self.subTest(problem=problem):
                self.clean(); self.command()
                entry = self.entry_path()
                if problem == 'chooser':
                    self.put(entry, dict(self.read(entry), disableDeploymentModeChooser=True))
                with patch.object(mod.Desktop, 'app', side_effect=mod.DesktopError(problem)), \
                     patch.object(mod.Desktop, 'quit_app') as quit_app, patch.object(mod.Desktop, 'open_app') as open_app:
                    step = next(fn for title, fn in sienna_pool.claude_uninstall_plan() if 'desktop' in title)
                    step()
                quit_app.assert_not_called(); open_app.assert_not_called()
                self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')
                self.assertNotEqual(self.read(self.meta_path()).get('appliedId') if self.meta_path().exists() else '', self.state()['entry_id'])
                self.assertTrue(self.state()['removed'])

    def test_declined_quit_is_asked_once(self):
        self.command()
        running = dict(self.stopped, app_running=True, app_started_at=cp.now_utc().isoformat())
        with patch.object(mod.Desktop, 'process', return_value=running), \
             patch.object(mod.Desktop, 'quit_app', side_effect=mod.DesktopError('did not quit')) as quit_app, \
             patch.object(mod.Desktop, 'open_app') as open_app:
            with self.assertRaises(mod.DesktopError):
                self.command('claudeai', relaunch=True, yes=True)
        quit_app.assert_called_once(); open_app.assert_not_called()

    def test_gateway_credentials_never_enter_journal(self):
        self.command()
        for url in ('https://gw.invalid/path-secret/v1', 'https://gw.invalid?secret=x', 'https://user:secret@gw.invalid',
                    'https://gw.invalid/#secret'):
            with self.subTest(url=url):
                self.put(self.entry_path(), dict(self.read(self.entry_path()), inferenceGatewayBaseUrl=url))
                with self.assertRaisesRegex(mod.DesktopError, 'gateway URL'):
                    self.command(reclaim=True)
                self.assertFalse((cp.STATE / 'desktop-txn.json').exists())

    def test_open_patches_status_under_guard_lock(self):
        self.command()
        running = dict(self.stopped, app_running=True, app_started_at=cp.now_utc().isoformat())
        write = mod.SafeFiles.write
        def checked(fs, path, data):
            if path == sienna_pool.CLAUDE_STATUS_FILE:
                with (cp.STATE / 'claude-guard.lock').open('rb') as f:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return write(fs, path, data)
        with sienna_desktop.desktop_backend() as d, patch.object(mod.subprocess, 'run'), \
             patch.object(d, 'process', return_value=running), patch.object(d, 'status', return_value={'running_mode': 'pooled'}), \
             patch.object(mod.SafeFiles, 'write', checked):
            d.open_app(self.app['bundle_path'])

    def test_N1_crash_before_each_undo_write_is_safe_and_repeatable(self):
        for fail_at in range(1, 4):
            with self.subTest(fail_at=fail_at):
                self.clean()
                with crash_at('published-state'), self.assertRaises(Boom):
                    self.command()
                writes = []
                write = mod.SafeFiles.write
                def crash(fs, path, data):
                    if sienna_desktop.DESKTOP_P3 in path.parents:
                        writes.append(path)
                        if len(writes) == fail_at:
                            raise Boom('undo')
                    return write(fs, path, data)
                with sienna_desktop.desktop_backend() as d, d.fs.lock(), patch.object(mod.SafeFiles, 'write', crash):
                    with self.assertRaises(Boom):
                        d.recover(explicit=True, force_undo=True)
                if fail_at > 1:
                    self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')
                with sienna_desktop.desktop_backend() as d, d.fs.lock():
                    d.recover(explicit=True, force_undo=True)
                self.assertFalse((cp.STATE / 'desktop-txn.json').exists())
                self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')

    def test_N1_undo_to_3p_writes_mode_last(self):
        self.command()
        with crash_at('entry'), self.assertRaises(Boom):
            self.command('remove', yes=True)
        written = []
        write = mod.SafeFiles.write
        def record(fs, path, data):
            if sienna_desktop.DESKTOP_P3 in path.parents:
                written.append(path)
            return write(fs, path, data)
        with sienna_desktop.desktop_backend() as d, d.fs.lock(), patch.object(mod.SafeFiles, 'write', record):
            d.recover(explicit=True, force_undo=True)
        self.assertEqual(written[-1], self.mode_path())
        self.assertEqual(len(written), len(set(written)))
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '3p')
        self.assertEqual(self.read(self.meta_path())['appliedId'], self.state()['entry_id'])

    def test_N3_N5_N10_foreign_entries_never_inherited_or_restored(self):
        self.command()
        state = self.state()
        self.assertNotIn('original_applied_id', state)
        self.assertNotIn('policy_inherited', state)
        self.entry_path().unlink()
        ident, other = self.original(chooser=True)
        for command in ('pooled', 'claudeai'):
            with self.assertRaisesRegex(mod.DesktopError, 'another third-party configuration exists'):
                self.command(command)
        before = other.read_bytes()
        self.command('remove', yes=True)
        self.assertEqual(other.read_bytes(), before)
        self.assertEqual(self.read(self.meta_path())['appliedId'], ident)
        # Even an unlisted file is another configuration; its content is not read.
        self.put(self.meta_path(), {'appliedId': '', 'entries': []})
        with self.assertRaisesRegex(mod.DesktopError, 'another third-party configuration exists'):
            self.command()

    def test_log_rotation_and_offsets_survive_status_instances(self):
        self.command()
        started = cp.now_utc() - dt.timedelta(hours=6)
        running = dict(self.stopped, app_running=True, app_started_at=started.isoformat())
        log = sienna_desktop.DESKTOP_LOGS_3P / 'main.log'
        log.parent.mkdir(parents=True, exist_ok=True)
        prefix = (started + dt.timedelta(seconds=1)).astimezone().strftime('%Y-%m-%d %H:%M:%S')
        activation = prefix + " [custom-3p] 3P mode active { provider: 'gateway' }\n"
        host = prefix + ' [custom-3p] inference apiHost=http://127.0.0.1:' + str(sienna_pool.CLAUDE_PORT) + '\n'
        log.write_text(activation + host)
        def status():
            with sienna_desktop.desktop_backend() as d:
                return d.status(probe=False)
        with patch.object(mod.Desktop, 'process', return_value=running):
            self.assertEqual(status()['running_mode'], 'pooled')
            with patch.object(mod.SafeFiles, 'lines', side_effect=AssertionError('unchanged log rescanned')):
                self.assertEqual(status()['running_mode'], 'pooled')
            log.rename(log.with_name('main.old.log'))
            log.write_text(prefix + ' unrelated output\n')
            self.assertEqual(status()['running_mode'], 'pooled')
            (cp.STATE / 'desktop-log-cache.json').unlink()  # first status after a rotation
            self.assertEqual(status()['running_mode'], 'pooled')
            log.with_name('main.old.log').rename(log.with_name('main.log.1'))
            self.assertEqual(status()['running_mode'], 'pooled')
            with patch.object(mod.SafeFiles, 'lines', side_effect=AssertionError('rotated log rescanned')):
                self.assertEqual(status()['running_mode'], 'pooled')
            with patch.object(mod.Desktop, 'process', return_value=dict(running, app_started_at=cp.now_utc().isoformat())):
                self.assertEqual(status()['running_mode'], 'unknown')

    def test_first_request_evidence_is_for_pool_lifetime(self):
        self.command()
        pool_start = cp.now_utc() - dt.timedelta(hours=2)
        app_start = cp.now_utc() - dt.timedelta(minutes=20)
        seen = pool_start + dt.timedelta(seconds=3)
        sienna_pool.CLAUDE_MAIN_LOG.parent.mkdir(parents=True, exist_ok=True)
        from _helpers import preserved
        with preserved(sienna_pool.CLAUDE_MAIN_LOG):
            sienna_pool.CLAUDE_MAIN_LOG.write_text(seen.astimezone().strftime('%Y-%m-%d %H:%M:%S') +
                ' codexpool gate: first request from client user-agent=claude-desktop-3p\n')
            running = dict(self.stopped, app_running=True, app_started_at=app_start.isoformat())
            with patch.object(mod.Desktop, 'process', return_value=running), \
                 patch.object(mod.Desktop, 'pool_started', return_value=pool_start), sienna_desktop.desktop_backend() as d:
                self.assertIsNotNone(d.status(probe=False)['pool_seen_desktop_at'])
            with patch.object(mod.Desktop, 'pool_started', return_value=cp.now_utc()), sienna_desktop.desktop_backend() as d:
                self.assertIsNone(d.status(probe=False)['pool_seen_desktop_at'])

    def test_legacy_backup_warning_without_reading_or_deleting(self):
        backup = cp.STATE / 'desktop-backup-old'
        backup.mkdir()
        secret = backup / 'entry.json'
        secret.write_text('synthetic-secret')
        read = mod.SafeFiles.read
        def checked(fs, path, *args, **kw):
            self.assertNotIn(backup, path.parents)
            return read(fs, path, *args, **kw)
        rep = cp.DoctorReport(echo=False)
        with patch.object(mod.SafeFiles, 'read', checked):
            mod.doctor(cp, rep)
        output = json.dumps(rep.as_json())
        self.assertIn('may hold secrets', output)
        self.assertIn('delete that folder yourself', output)
        self.assertNotIn('synthetic-secret', output)
        self.assertTrue(secret.exists())
        self.assertTrue(sienna_desktop.desktop_recorded())

    def test_uninstall_safety_failure_reports_leftovers_and_continues(self):
        self.command()
        path = self.entry_path()
        saved = path.read_bytes()
        path.unlink()
        target = HOME / 'outside-entry'
        target.write_bytes(saved)
        path.symlink_to(target)
        with sienna_desktop.desktop_backend() as d:
            d.uninstall()
            self.assertIn('uninstall continues', ' '.join(d.events))
        self.assertTrue(path.is_symlink())
        self.assertEqual(target.read_bytes(), saved)

    def test_old_provider_records_are_retired_without_touching_the_provider(self):
        self.command()
        state = self.state()
        state.update(original_applied_id='11111111-2222-4333-8444-555555555555', original_mode='3p',
                     policy_inherited={'keys': ['builtinToolPolicy']}, undo=[])
        self.put(cp.STATE / 'desktop.json', state)
        self.command()
        for key in ('original_applied_id', 'original_mode', 'policy_inherited', 'undo'):
            self.assertNotIn(key, self.state())
        with sienna_desktop.desktop_backend() as d:
            self.assertIsNone(d.status(probe=False)['policy_inherited'])

    def test_cli_status_patch_uses_same_lock_and_preserves_desktop(self):
        self.command()
        write = cp.write_json
        def checked(path, data):
            if path == sienna_pool.CLAUDE_STATUS_FILE:
                with (cp.STATE / 'claude-guard.lock').open('rb') as f:
                    with self.assertRaises(BlockingIOError):
                        fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return write(path, data)
        before = self.read(sienna_pool.CLAUDE_STATUS_FILE)['pool']['desktop']
        with patch.object(cp, 'write_json', checked):
            sienna_pool.patch_claude_status(installed=False)
        after = self.read(sienna_pool.CLAUDE_STATUS_FILE)
        self.assertFalse(after['pool']['installed'])
        self.assertEqual(after['pool']['desktop'], before)

    def test_old_inherited_keys_are_user_keys_on_remove(self):
        self.command()
        entry = dict(self.read(self.entry_path()), builtinToolPolicy={'Bash': 'ask'})
        self.put(self.entry_path(), entry)
        state = self.state()
        state['created_entry'] = entry
        self.put(cp.STATE / 'desktop.json', state)
        self.command('remove', yes=True)
        self.assertEqual(self.read(self.entry_path())['builtinToolPolicy'], {'Bash': 'ask'})
        self.assertEqual(self.read(self.meta_path())['entries'][0]['id'], state['entry_id'])

    def test_each_forward_transaction_writes_profile_files_once(self):
        write = mod.SafeFiles.write
        for command in ('pooled', 'claudeai', 'pooled', 'remove'):
            with self.subTest(command=command):
                paths = []
                def record(fs, path, data):
                    if sienna_desktop.DESKTOP_P3 in path.parents:
                        paths.append(path)
                    return write(fs, path, data)
                with patch.object(mod.SafeFiles, 'write', record):
                    self.command(command, yes=True)
                self.assertEqual(len(paths), len(set(paths)))
                self.assertEqual(paths[-1] if command == 'pooled' else paths[0], self.mode_path())
