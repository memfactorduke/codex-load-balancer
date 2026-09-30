"""Desktop backend: fake HOME only, no apps, real credentials, or launchd actions."""
from _helpers import addon
sienna_desktop, desktop_build = addon.desktop, addon.desktop_build

from _helpers import addon
import unittest
if addon is None:
    raise unittest.SkipTest('sienna add-on absent; dependent desktop/engine tests')
from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import cp, HOME, ROOT, REPO, run, run_script, preserved
import argparse
import contextlib
import datetime as dt
import io
import json
import os
import pathlib
import shutil
import stat
import sys
import unittest
from unittest.mock import patch

mod = sienna_desktop


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.assertEqual(sienna_desktop.DESKTOP_AS, HOME / 'Library/Application Support')
        self.assertEqual(sienna_desktop.DESKTOP_P3, HOME / 'Library/Application Support/Claude-3p')
        self.clean()
        sienna_desktop.DESKTOP_AS.mkdir(parents=True, exist_ok=True)
        self.app = {'bundle_path': str(HOME / 'Applications/Claude.app'), 'app_version': mod.VERIFIED['app']}
        self.stopped = {'app_running': False, 'app_started_at': None, 'app_bundle_ok': True}
        self.now = cp.now_utc()
        self.seats = [dict(name='test-seat', label='Test seat', provider='claude', disabled=False, status='active',
                           status_message='', cooldowns=[], quota={}, unavailable=False, path=None)]
        self.meta = {'test-seat': {'added_at': (self.now - dt.timedelta(hours=1)).isoformat()}}
        self.guard = {'seats': {}, 'usage': {'test-seat': {'at': self.now.isoformat(), 'source': 'poll',
                                                        'credits': {'enabled': False}}}}
        self.stack = contextlib.ExitStack()
        self.stack.enter_context(preserved(sienna_pool.CLAUDE_STATUS_FILE))
        self.stack.enter_context(patch.object(mod.Desktop, 'app', return_value=self.app))
        self.stack.enter_context(patch.object(mod.Desktop, 'process', return_value=self.stopped))
        self.stack.enter_context(patch.object(mod.Desktop, 'pool_ok', return_value=True))
        self.stack.enter_context(patch.object(cp, 'load_seats', return_value=self.seats))
        self.stack.enter_context(patch.object(cp, 'read_guard', return_value=self.guard))
        self.stack.enter_context(patch.object(cp, 'read_meta', return_value=self.meta))
        self.stack.enter_context(patch.object(cp, 'launchd_getenv', return_value=None))
        self.stack.enter_context(patch.object(cp, 'cpa_version', return_value=mod.VERIFIED['cpa']))
        self.stack.enter_context(patch.object(cp, 'running_version', return_value=mod.VERIFIED['cpa'].replace('-gate-', '+gate.')))
        self.wire_record = desktop_build.desktop_wire_record_path('v' + mod.VERIFIED['cpa'])
        self.stack.enter_context(preserved(self.wire_record))
        self.put(self.wire_record, {'build_id': 'v' + mod.VERIFIED['cpa'],
                                  'version': mod.VERIFIED['cpa'].split('-gate-')[0],
                                  'result': 'PASS', 'failing_paths': []})
        self.addCleanup(self.stack.close)
        self.addCleanup(self.clean)

    def clean(self):
        for path in (sienna_desktop.DESKTOP_P3, sienna_desktop.DESKTOP_LOGS_3P, sienna_desktop.DESKTOP_LOGS_1P):
            if path.is_symlink():
                path.unlink()
            elif path.exists():
                shutil.rmtree(path)
        for p in cp.STATE.glob('desktop*'):
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()

    def command(self, name='pooled', **flags):
        args = dict(desktop_cmd=name, dry_run=False, relaunch=False, yes=False, json=False,
                    models=None, effort=None, one_m=None, import_history=None, tool_search=None,
                    reclaim=False, delete_edited=False)
        args.update(flags)
        with sienna_desktop.desktop_backend() as d:
            with contextlib.redirect_stdout(io.StringIO()):
                d.command(argparse.Namespace(**args))
            return '\n'.join(d.events)

    def state(self):
        return json.loads((cp.STATE / 'desktop.json').read_text())

    def entry_path(self):
        return sienna_desktop.DESKTOP_P3 / 'configLibrary' / (self.state()['entry_id'] + '.json')

    def put(self, path, obj):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(mod.encoded(obj))

    def meta_path(self):
        return sienna_desktop.DESKTOP_P3 / 'configLibrary/_meta.json'

    def mode_path(self):
        return sienna_desktop.DESKTOP_P3 / 'claude_desktop_config.json'

    def read(self, path):
        return json.loads(path.read_text())

    def original(self, chooser=False):
        ident = '11111111-2222-4333-8444-555555555555'
        path = sienna_desktop.DESKTOP_P3 / 'configLibrary' / (ident + '.json')
        obj = {'builtinToolPolicy': {'Bash': 'ask'}, 'chatSessionRetentionDays': 7,
               'inferenceGatewayApiKey': 'sk-real-private', 'unknownField': 'private-value'}
        if chooser:
            obj['disableDeploymentModeChooser'] = True
        self.put(path, obj)
        self.put(self.meta_path(), {'appliedId': ident, 'entries': [{'id': ident, 'name': 'Original'}]})
        return ident, path

    def test_defaults_idempotence_and_cli_alias(self):
        self.command()
        first = self.state()
        entry = self.read(self.entry_path())
        self.assertNotIn('claudeAiImport', entry)
        self.assertEqual(len(entry['inferenceModels']), 5)
        self.assertTrue(all(m['maxEffort'] == 'high' and 'supports1m' not in m for m in entry['inferenceModels']))
        before = {p: p.read_bytes() for p in cp.STATE.glob('desktop*') if p.is_file()}
        self.assertIn('nothing to change', self.command())
        self.assertEqual(first, self.state())
        self.assertEqual(before, {p: p.read_bytes() for p in cp.STATE.glob('desktop*') if p.is_file()})
        for alias in ('sienna', 'claude'):
            args = cp.build_parser().parse_args([alias, 'desktop', 'pooled', '--import'])
            self.assertTrue(args.import_history)
            self.assertEqual(args.claude_fn, sienna_desktop.cmd_desktop)
        self.assertEqual(cp.build_parser().parse_args(['claude', 'status']).claude_fn, sienna_pool.cmd_claude_status)
        self.assertEqual(cp.build_parser().parse_args(['sienna', 'desktop', 'pooled']).import_history, None)

    def test_merge_and_options_adoption(self):
        self.command(import_history=True, one_m=True, effort='medium')
        path = self.entry_path()
        entry = self.read(path)
        extra = {'builtinToolPolicy': {'Bash': 'ask'}, 'disabledBuiltinTools': ['Delete'],
                 'disableBypassPermissionsMode': True, 'unknown': {'value': ['kept']}}
        entry.update(extra, toolSearchEnabled=False, claudeAiImport={'enabled': False}, defaultModelEffort='low')
        self.put(path, entry)
        out = self.command()
        current = self.read(path)
        for key, value in extra.items():
            self.assertEqual(current[key], value)
        self.assertEqual(self.state()['options']['effort'], 'low')
        self.assertFalse(self.state()['options']['import'])
        self.assertFalse(self.state()['options']['tool_search'])
        self.assertIn('adopted from the app', out)
        self.command(effort='high', import_history=True)
        self.assertEqual(self.state()['options']['effort'], 'high')
        self.assertTrue(self.state()['options']['import'])
        self.assertFalse(self.state()['options']['tool_search'])


    def test_refused_all_spellings(self):
        self.command()
        path = self.entry_path()
        base = self.read(path)
        for key in list(mod.REFUSED) + ['inferenceGatewayHeaders', 'INFERENCEGATEWAYHEADERS', 'INFERENCECUSTOMHEADERS']:
            with self.subTest(key=key):
                self.put(path, dict(base, **{key: False}))
                before = path.read_bytes()
                with self.assertRaisesRegex(mod.DesktopError, 'carries'):
                    self.command(reclaim=True)
                self.assertEqual(path.read_bytes(), before)

    def test_owned_drift_hash_only_and_reclaim(self):
        self.command()
        path = self.entry_path()
        entry = self.read(path)
        entry['inferenceGatewayApiKey'] = 'sk-real-secret-value'
        self.put(path, entry)
        with self.assertRaises(mod.DesktopError) as caught:
            self.command()
        self.assertIn('inferenceGatewayApiKey:', str(caught.exception))
        self.assertNotIn('sk-real', str(caught.exception))
        with sienna_desktop.desktop_backend() as d:
            status = d.status()
        self.assertTrue(status['owned_drift'])
        self.assertFalse(status['ours'])
        self.assertNotIn('sk-real', json.dumps(status))
        with self.assertRaisesRegex(mod.DesktopError, 'no credential is backed up'):
            self.command(reclaim=True)
        self.assertFalse(list(cp.STATE.glob('desktop-backup-*')))
        entry['inferenceGatewayApiKey'] = 'codexpool'
        self.put(path, entry)
        self.command(reclaim=True)

    def test_port_change_is_not_user_drift(self):
        self.command()
        with patch.object(sienna_pool, 'CLAUDE_PORT', sienna_pool.CLAUDE_PORT + 1):
            self.command()
            self.assertEqual(self.read(self.entry_path())['inferenceGatewayBaseUrl'], 'http://127.0.0.1:' + str(sienna_pool.CLAUDE_PORT))

    def test_write_order_and_journal_durable_before_profile(self):
        original_write = mod.SafeFiles.write
        real_fsync = os.fsync
        synced = []
        def record_sync(fd):
            info = os.fstat(fd)
            synced.append((info.st_dev, info.st_ino))
            real_fsync(fd)
        writes = []
        def observed(fs, path, data):
            if sienna_desktop.DESKTOP_P3 in pathlib.Path(path).parents:
                journal = cp.STATE / 'desktop-txn.json'
                self.assertTrue(journal.is_file())
                self.assertEqual(self.read(journal)['phase'], 'prepared')
                js, ds = journal.stat(), cp.STATE.stat()
                journal_sync = synced.index((js.st_dev, js.st_ino))
                self.assertIn((ds.st_dev, ds.st_ino), synced[journal_sync + 1:])
            original_write(fs, path, data)
            writes.append(str(path))

        with patch.object(mod.SafeFiles, 'write', observed), patch.object(mod.os, 'fsync', side_effect=record_sync) as sync:
            self.command()
        self.assertGreater(sync.call_count, 10)
        self.assertIsNone(self.state()['last_backup'])
        self.assertFalse(list(cp.STATE.glob('desktop-backup-*')))
        self.assertFalse((cp.STATE / 'desktop-txn.json').exists())
        self.assertEqual(stat.S_IMODE((cp.STATE / 'desktop.json').stat().st_mode), 0o600)

    def test_interrupt_every_boundary(self):
        for boundary, occurrence in [('prepared', 1), ('mode', 1), ('entry', 1), ('meta', 1),
                                     ('published-state', 1), ('published-status', 1), ('committed', 1)]:
            with self.subTest(boundary=boundary, occurrence=occurrence):
                self.clean()
                count = [0]
                def crash(d, name):
                    if name == boundary:
                        count[0] += 1
                        if count[0] == occurrence:
                            raise RuntimeError('crash')
                with patch.object(mod.Desktop, 'checkpoint', crash), self.assertRaisesRegex(RuntimeError, 'crash'):
                    self.command()
                journal = self.read(cp.STATE / 'desktop-txn.json')
                original_id = journal['entry_id']
                self.command('rollback')
                self.assertFalse((cp.STATE / 'desktop-txn.json').exists())
                if (cp.STATE / 'desktop.json').exists():
                    self.assertEqual(self.state()['entry_id'], original_id)
                    self.assertEqual(self.read(self.mode_path())['deploymentMode'], '3p')
                self.command()
                self.assertEqual(len(self.read(self.meta_path())['entries']), 1)

    def test_foreign_rollback_keeps_permission_and_remove_keeps_entry(self):
        def crash(d, name):
            if name == 'entry':
                raise RuntimeError('crash')
        with patch.object(mod.Desktop, 'checkpoint', crash), self.assertRaises(RuntimeError):
            self.command()
        journal = self.read(cp.STATE / 'desktop-txn.json')
        path = sienna_desktop.DESKTOP_P3 / 'configLibrary' / (journal['entry_id'] + '.json')
        entry = self.read(path)
        entry['builtinToolPolicy'] = {'Bash': 'ask'}
        self.put(path, entry)
        self.command('rollback')
        self.assertEqual(self.read(path), {'builtinToolPolicy': {'Bash': 'ask'}})
        self.assertFalse(self.meta_path().exists())
        self.command('remove', yes=True)
        self.assertTrue(path.exists())
        self.assertEqual(self.read(path), {'builtinToolPolicy': {'Bash': 'ask'}})

    def test_foreign_meta_and_mode_preserved(self):
        self.command()
        def crash(d, name):
            if name == 'published-state':
                raise RuntimeError('crash')
        with patch.object(mod.Desktop, 'checkpoint', crash), self.assertRaises(RuntimeError):
            self.command('claudeai')
        mode = self.read(self.mode_path())
        mode['newPreference'] = 'keep'
        self.put(self.mode_path(), mode)
        self.command('rollback')
        self.assertEqual(self.read(self.mode_path())['newPreference'], 'keep')
        # All owned keys reached their final values: finish the interrupted switch.
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')

    def test_remove_later_entry_unapplied(self):
        self.command()
        meta = self.read(self.meta_path())
        later = '22222222-2222-4333-8444-555555555555'
        self.put(sienna_desktop.DESKTOP_P3 / 'configLibrary' / (later + '.json'), {'inferenceProvider': 'gateway'})
        meta['entries'].append({'id': later, 'name': 'Later'})
        self.put(self.meta_path(), meta)
        self.command('remove', yes=True)
        self.assertEqual(self.read(self.meta_path())['appliedId'], '')
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')



    def test_remove_edited_and_delete_override(self):
        self.command()
        path = self.entry_path()
        self.put(path, dict(self.read(path), builtinToolPolicy={'Bash': 'ask'}))
        self.command('remove', yes=True, delete_edited=True)
        self.assertEqual(self.read(path)['builtinToolPolicy'], {'Bash': 'ask'})
        self.assertEqual(self.read(self.meta_path())['appliedId'], '')
        self.command()
        self.assertEqual(self.entry_path(), path)
        self.assertEqual(self.read(path)['builtinToolPolicy'], {'Bash': 'ask'})

    def test_filesystem_refusals_no_write(self):
        for target in ('profile', 'library', 'meta', 'fifo'):
            with self.subTest(target=target):
                self.clean()
                if target == 'profile':
                    sienna_desktop.DESKTOP_P3.symlink_to(sienna_desktop.DESKTOP_P1)
                else:
                    (sienna_desktop.DESKTOP_P3 / 'configLibrary').mkdir(parents=True)
                    if target == 'library':
                        (sienna_desktop.DESKTOP_P3 / 'configLibrary').rmdir()
                        (sienna_desktop.DESKTOP_P3 / 'configLibrary').symlink_to(HOME)
                    elif target == 'meta':
                        self.meta_path().symlink_to(HOME / 'missing')
                    else:
                        os.mkfifo(self.mode_path())
                with patch.object(mod.SafeFiles, 'write', side_effect=AssertionError('must not write')):
                    with self.assertRaises(mod.DesktopError):
                        self.command()

    def test_hard_link_refused_before_open(self):
        self.command()
        path = self.entry_path()
        sienna_desktop.DESKTOP_P1.mkdir(parents=True, exist_ok=True)
        extra = sienna_desktop.DESKTOP_P1 / 'desktop-test-hardlink.json'
        self.addCleanup(lambda: extra.unlink(missing_ok=True))
        os.link(path, extra)
        real_open = os.open
        def guarded(name, *args, **kwargs):
            self.assertNotEqual(name, path.name, 'hard-linked inode opened')
            return real_open(name, *args, **kwargs)
        with patch.object(os, 'open', guarded), self.assertRaisesRegex(mod.DesktopError, '2 links'):
            self.command()

    def test_legacy_journal_paths_ignored(self):
        self.command()
        self.put(cp.STATE / 'desktop-txn.json', {'entry_id': self.state()['entry_id'], 'plan': [
            {'role': 'entry', 'path': str(HOME / 'unmanaged'), 'before': None, 'after': None}]})
        before = self.entry_path().read_bytes()
        self.assertIn('ignored legacy', self.command('rollback'))
        self.assertEqual(self.entry_path().read_bytes(), before)
        self.assertFalse((cp.STATE / 'desktop-txn.json').exists())

    def test_redirect_and_managed_and_malformed(self):
        with patch.object(cp, 'launchd_getenv', return_value='/redirect'), self.assertRaisesRegex(mod.DesktopError, 'redirected'):
            self.command()
        self.put(self.meta_path(), {'entries': [], 'appliedId': '', 'hybridPointer': 'private'})
        with self.assertRaisesRegex(mod.DesktopError, 'hybridPointer'):
            self.command()
        self.meta_path().write_text('{broken')
        with self.assertRaisesRegex(mod.DesktopError, 'not JSON'):
            self.command()

    def test_dry_run_and_running_refusal(self):
        with patch.object(mod.Desktop, 'process', return_value=dict(self.stopped, app_running=True)):
            with self.assertRaisesRegex(mod.DesktopError, 'Claude is running'):
                self.command()
            self.command(dry_run=True)
        self.assertFalse(sienna_desktop.DESKTOP_P3.exists())
        self.assertFalse(list(cp.STATE.glob('desktop*')))

    def test_credits_branches(self):
        with sienna_desktop.desktop_backend() as d:
            self.assertTrue(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
            for enabled in (None, True):
                self.guard['usage']['test-seat']['credits']['enabled'] = enabled
                self.assertFalse(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
            self.meta['test-seat']['credits'] = {'policy': 'last-resort', 'cap': 5}
            self.assertTrue(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
            self.meta['test-seat']['credits']['cap'] = None
            self.assertFalse(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
            self.guard['usage']['test-seat']['credits']['enabled'] = False
            self.guard['usage']['test-seat']['at'] = (self.now - dt.timedelta(minutes=16)).isoformat()
            self.assertFalse(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
            self.guard['usage']['test-seat']['at'] = self.now.isoformat()
            self.meta['test-seat']['added_at'] = (self.now + dt.timedelta(seconds=1)).isoformat()
            self.assertFalse(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
            self.assertFalse(d.credits([], self.guard, self.meta, self.now)['ok'])
            self.assertFalse(d.credits(self.seats, {'usage': {}}, self.meta, self.now)['ok'])

    def test_running_evidence_requires_current_process_and_correct_host(self):
        self.command()
        started = cp.now_utc() + dt.timedelta(seconds=2)
        process = dict(app_running=True, app_bundle_ok=True, app_started_at=started.isoformat())
        prefix = started.astimezone().strftime('%Y-%m-%d %H:%M:%S') + ' [info] [custom-3p] '
        log = sienna_desktop.DESKTOP_LOGS_3P / 'main.log'
        log.parent.mkdir(parents=True)
        with patch.object(mod.Desktop, 'process', return_value=process):
            for lines, expected in [('', 'unknown'), ('3P mode active', '3p'),
                                    ("3P mode active { provider: 'vertex' }", 'other'),
                                    ('3P mode active\n' + prefix + 'inference apiHost=http://127.0.0.1:' + str(sienna_pool.CLAUDE_PORT), 'pooled'),
                                    ('3P mode active\n' + prefix + 'inference apiHost=https://other.invalid', 'other'),
                                    ('configError: a private value', 'fallback')]:
                log.write_text(prefix + lines)
                with sienna_desktop.desktop_backend() as d:
                    s = d.status()
                self.assertEqual(s['running_mode'], expected)
                self.assertFalse(s['restart_required'])
                self.assertNotIn('private value', json.dumps(s))
            with patch.object(mod.Desktop, 'process', return_value=dict(process, app_bundle_ok=False)), sienna_desktop.desktop_backend() as d:
                self.assertEqual(d.status()['running_mode'], 'unknown')

    def test_mode_mtime_restart_and_claudeai_log_stat_only(self):
        self.command()
        self.command('claudeai')
        started = cp.now_utc() + dt.timedelta(seconds=2)
        process = dict(app_running=True, app_bundle_ok=True, app_started_at=started.isoformat())
        log = sienna_desktop.DESKTOP_LOGS_1P / 'main.log'
        log.parent.mkdir(parents=True)
        log.write_text('never read')
        os.utime(log, (started.timestamp() + 1, started.timestamp() + 1))
        with patch.object(mod.Desktop, 'process', return_value=process), sienna_desktop.desktop_backend() as d:
            self.assertEqual(d.status()['running_mode'], 'claudeai')
        os.utime(self.mode_path(), (started.timestamp() + 2, started.timestamp() + 2))
        with patch.object(mod.Desktop, 'process', return_value=process), sienna_desktop.desktop_backend() as d:
            s = d.status()
            self.assertFalse(s['restart_required'])
            self.assertEqual(s['running_mode'], 'claudeai')
        state = self.state()
        state['written_at'] = (started + dt.timedelta(seconds=3)).isoformat()
        self.put(cp.STATE / 'desktop.json', state)
        with patch.object(mod.Desktop, 'process', return_value=process), sienna_desktop.desktop_backend() as d:
            self.assertTrue(d.status()['restart_required'])

    def test_confirmation_required_before_quit(self):
        with patch.object(mod.os, 'isatty', return_value=False), patch.object(mod.Desktop, 'quit_app') as quit_app:
            with self.assertRaisesRegex(mod.DesktopError, 'confirmation'):
                self.command(relaunch=True)
            quit_app.assert_not_called()
        self.assertFalse(sienna_desktop.DESKTOP_P3.exists())

    def test_open_absolute_validated_bundle(self):
        self.command()
        process = dict(app_running=True, app_bundle_ok=True, app_started_at=cp.now_utc().isoformat())
        with sienna_desktop.desktop_backend() as d, patch.object(mod.subprocess, 'run') as launch, patch.object(d, 'process', return_value=process), patch.object(d, 'status', return_value={'running_mode': 'pooled'}):
            d.open_app(self.app['bundle_path'])
            self.assertEqual(launch.call_args[0][0], ['open', self.app['bundle_path']])

    def test_doctor_redaction_and_status_contract(self):
        self.command()
        entry = self.read(self.entry_path())
        entry['inferenceGatewayApiKey'] = 'sk-real-must-not-print'
        self.put(self.entry_path(), entry)
        rep = cp.DoctorReport(echo=False)
        mod.doctor(cp, rep)
        self.assertEqual(rep.sections[0]['title'], 'Desktop')
        self.assertNotIn('sk-real', json.dumps(rep.as_json()))
        with sienna_desktop.desktop_backend() as d:
            s = d.status(self.seats, self.guard, self.meta)
        self.assertTrue(s['credits_ok'])
        self.assertTrue(set('configured_mode chooser_disabled running_mode running_host app_running app_started_at app_bundle_ok restart_required ours current owned_drift app_version applied_name base_url port_ok credits_ok txn_pending txn_classes pool_seen_desktop_at old_3p_logs checked_at'.split()) <= set(s))

    def test_audit_hook_no_normal_profile_syscall(self):
        events = []
        active = [True]
        def hook(event, args):
            if active[0] and event in ('open', 'os.rename', 'os.remove', 'os.mkdir', 'os.rmdir', 'os.chmod', 'os.link'):
                events.append((event, args))
                for arg in args:
                    if isinstance(arg, (str, bytes)):
                        value = os.fsdecode(arg)
                        self.assertFalse(value == str(sienna_desktop.DESKTOP_P1) or value.startswith(str(sienna_desktop.DESKTOP_P1) + '/'), (event, value))
        sys.addaudithook(hook)
        real_stat, real_lstat, real_open = os.stat, os.lstat, os.open
        directories = {}
        def checked_open(path, flags, *args, **kwargs):
            absolute = pathlib.Path(path) if os.path.isabs(path) else directories[kwargs['dir_fd']] / path
            if not flags & os.O_DIRECTORY:
                self.assertTrue(sienna_desktop.DESKTOP_P3 in absolute.parents or cp.STATE in absolute.parents, absolute)
            fd = real_open(path, flags, *args, **kwargs)
            if flags & os.O_DIRECTORY:
                directories[fd] = absolute
            return fd
        def checked(fn):
            def call(path, *a, **kw):
                if isinstance(path, (str, bytes, os.PathLike)):
                    value = os.fsdecode(path)
                    self.assertFalse(value == str(sienna_desktop.DESKTOP_P1) or value.startswith(str(sienna_desktop.DESKTOP_P1) + '/'))
                return fn(path, *a, **kw)
            return call
        try:
            with patch.object(os, 'stat', checked(real_stat)), patch.object(os, 'lstat', checked(real_lstat)), patch.object(os, 'open', checked_open):
                self.command()
                self.command('claudeai')
                self.command('remove', yes=True)
                self.command('rollback')
        finally:
            active[0] = False
        self.assertTrue(events)
        # All mutations use relative names with pinned directory fds; no absolute mutation paths.
        for event, args in events:
            if event in ('os.rename', 'os.remove', 'os.mkdir', 'os.rmdir'):
                self.assertFalse(os.path.isabs(args[0]), (event, args))

    def test_credits_stale_poll_cannot_be_refreshed_by_live_signal(self):
        self.guard['usage']['test-seat']['at'] = (self.now - dt.timedelta(minutes=16)).isoformat()
        live = {'at': self.now.isoformat(), 'source': 'headers', 'overage_in_use': False}
        with patch.object(sienna_guard, 'claude_usage_from_signals', return_value=live), sienna_desktop.desktop_backend() as d:
            result = d.credits(self.seats, self.guard, self.meta, self.now)
        self.assertFalse(result['ok'])
        self.assertIn('no fresh credits reading', result['problems'][0])

    def test_credits_overage_and_birthtime_and_new_account(self):
        live = {'at': self.now.isoformat(), 'source': 'headers', 'overage_in_use': True}
        with patch.object(sienna_guard, 'claude_usage_from_signals', return_value=live), sienna_desktop.desktop_backend() as d:
            self.assertFalse(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
        del self.meta['test-seat']['added_at']
        self.seats[0]['path'] = str(ROOT / 'auth-claude' / 'synthetic.json')
        with patch.object(mod.os, 'stat', return_value=argparse.Namespace(st_birthtime=self.now.timestamp() + 1)), sienna_desktop.desktop_backend() as d:
            self.assertFalse(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
        with patch.object(mod.os, 'stat', return_value=argparse.Namespace(st_birthtime=self.now.timestamp() - 100)), sienna_desktop.desktop_backend() as d:
            self.assertTrue(d.credits(self.seats, self.guard, self.meta, self.now)['ok'])
        other = dict(self.seats[0], name='new-seat', label='New seat')
        meta = dict(self.meta, **{'new-seat': {'added_at': self.now.isoformat()}})
        with patch.object(mod.os, 'stat', return_value=argparse.Namespace(st_birthtime=self.now.timestamp() - 100)), sienna_desktop.desktop_backend() as d:
            result = d.credits(self.seats + [other], self.guard, meta, self.now)
        self.assertFalse(result['ok'])
        self.assertTrue(any('New seat' in p for p in result['problems']))

    def test_owner_and_state_symlink_refused(self):
        self.command()
        real_check = mod.SafeFiles.check_stat
        def owner_check(fs, info, path, directory=False):
            if path == sienna_desktop.DESKTOP_P3:
                values = {key: getattr(info, key) for key in ('st_mode', 'st_uid', 'st_nlink')}
                values['st_uid'] += 1
                return real_check(fs, argparse.Namespace(**values), path, directory)
            return real_check(fs, info, path, directory)
        with patch.object(mod.SafeFiles, 'check_stat', owner_check), self.assertRaisesRegex(mod.DesktopError, 'not yours'):
            self.command()
        alternate = ROOT / 'desktop-symlink-state'
        alternate.symlink_to(cp.STATE)
        try:
            with patch.object(cp, 'STATE', alternate), self.assertRaises(mod.DesktopError):
                self.command()
        finally:
            alternate.unlink()

    def test_recovery_has_no_backup_dependency(self):
        self.command()
        original = self.mode_path().read_bytes()
        def crash(d, name):
            if name == 'entry':
                raise RuntimeError('crash')
        with patch.object(mod.Desktop, 'checkpoint', crash), self.assertRaises(RuntimeError):
            self.command(effort='medium')
        journal = self.read(cp.STATE / 'desktop-txn.json')
        self.assertEqual(journal['version'], 2)
        self.assertNotIn('backup_dir', journal)
        self.command('rollback')
        self.assertEqual(self.mode_path().read_bytes(), original)

    def test_foreign_meta_rename_kept_after_crash(self):
        self.command()
        def crash(d, name):
            if name == 'published-state':
                raise RuntimeError('crash')
        with patch.object(mod.Desktop, 'checkpoint', crash), self.assertRaises(RuntimeError):
            self.command(effort='medium')
        # meta wasn't touched by this plan; user changes must remain regardless.
        meta = self.read(self.meta_path())
        meta['entries'][0]['name'] = 'Renamed'
        self.put(self.meta_path(), meta)
        self.command('rollback')
        self.assertEqual(self.read(self.meta_path())['entries'][0]['name'], 'Renamed')

    def test_recovery_after_publish_reuses_uuid(self):
        def crash(d, name):
            if name == 'published-state':
                raise RuntimeError('crash')
        with patch.object(mod.Desktop, 'checkpoint', crash), self.assertRaises(RuntimeError):
            self.command()
        wanted = self.read(cp.STATE / 'desktop-txn.json')['state_after']
        self.command()
        for key in ('entry_id', 'owned', 'options'):
            self.assertEqual(self.state()[key], wanted[key])
        self.assertEqual(len(self.read(self.meta_path())['entries']), 1)
        self.assertTrue(self.read(sienna_pool.CLAUDE_STATUS_FILE)['pool']['desktop']['credits_ok'])

    def test_build_cache_and_failure_gate(self):
        files = desktop_build.desktop_wire_files()
        self.assertEqual(len(files), 5)
        with patch.object(desktop_build, 'desktop_wire_files', return_value=files[:-1]), patch.object(cp.subprocess, 'run') as build:
            result = desktop_build.desktop_wire_test(HOME / 'unused-build-tree', 'fake-go', {})
            self.assertEqual(result['result'], 'FAIL')
            self.assertIn('fixtures missing', result['reason'])
            build.assert_not_called()
        first = cp.gate_id(b'gate')
        with patch.object(desktop_build, 'desktop_wire_files', return_value=files[:-1]):
            self.assertNotEqual(first, cp.gate_id(b'gate'))
        tree = HOME / 'desktop-build-test'
        executor = tree / 'internal/runtime/executor'
        executor.mkdir(parents=True, exist_ok=True)
        (tree / 'go.mod').write_text('module github.com/router-for-me/CLIProxyAPI/v7\n')
        try:
            with patch.object(cp.subprocess, 'run', return_value=argparse.Namespace(returncode=1, stdout='$.headers.changed', stderr='')):
                result = desktop_build.desktop_wire_test(tree, 'fake-go', {})
                self.assertEqual(result['result'], 'FAIL')
                self.assertEqual(result['failing_paths'], ['$.headers.changed'])
            # Switching major module versions must rewrite imports and remove old fixtures.
            (tree / 'go.mod').write_text('module github.com/router-for-me/CLIProxyAPI/v8\n')
            (executor / 'testdata/codexpool-desktop/stale.json').write_text('{}')
            with patch.object(cp.subprocess, 'run', return_value=argparse.Namespace(returncode=0, stdout='', stderr='')):
                self.assertEqual(desktop_build.desktop_wire_test(tree, 'fake-go', {})['result'], 'PASS')
            self.assertIn('CLIProxyAPI/v8/', (executor / 'codexpool_desktop_wire_test.go').read_text())
            self.assertNotIn('CLIProxyAPI/v7/', (executor / 'codexpool_desktop_wire_test.go').read_text())
            self.assertFalse((executor / 'testdata/codexpool-desktop/stale.json').exists())
            self.assertTrue((executor / 'codexpool_desktop_wire_test.go').exists())
            self.assertEqual(len(list((executor / 'testdata/codexpool-desktop').glob('*.json'))), 4)
        finally:
            shutil.rmtree(tree)

    def test_recorded_wire_failure_refuses_pooled_and_doctor_reports_it(self):
        self.command()
        record = self.read(self.wire_record)
        record.update(result='FAIL', failing_paths=['$.headers.User-Agent', '$.headers.Anthropic-Beta'])
        self.put(self.wire_record, record)
        with patch.object(mod.Desktop, 'quit_app') as quit_app:
            with self.assertRaisesRegex(mod.DesktopError, 'identity headers; pooled desktop mode is off'):
                self.command(relaunch=True, yes=True)
            quit_app.assert_not_called()
        with sienna_desktop.desktop_backend() as d:
            status = d.status()
        self.assertFalse(status['cpa_compatible'])
        self.assertIn('v7.3.18', status['cpa_compatibility_reason'])
        self.assertIn('build/cpa-native-desktop-3p.patch', status['cpa_compatibility_reason'])
        from unittest.mock import Mock
        rep = Mock()
        mod.doctor(cp, rep)
        checks = [c for c in rep.check.call_args_list if 'identity headers' in str(c)]
        self.assertEqual(len(checks), 1)
        self.assertFalse(checks[0].args[0])
        self.assertFalse(checks[0].kwargs['warn'])
        code, out, _ = run(sienna_desktop.cmd_desktop, desktop_cmd='status', json=False)
        self.assertEqual(code, 0)
        self.assertIn('pooled desktop mode is off', out)
        self.command('claudeai')
        rep.reset_mock()
        mod.doctor(cp, rep)
        checks = [c for c in rep.check.call_args_list if 'identity headers' in str(c)]
        self.assertTrue(checks[0].kwargs['warn'])

    def test_wire_pass_must_match_running_build(self):
        self.assertTrue(desktop_build.desktop_cpa_compatibility()['cpa_compatible'])
        for live in (None, '7.3.19+gate.b3efb6adf8', '7.3.18+gate.abcdef0123'):
            with patch.object(cp, 'running_version', return_value=live):
                self.assertFalse(desktop_build.desktop_cpa_compatibility()['cpa_compatible'])
                with self.assertRaisesRegex(mod.DesktopError, 'pooled desktop mode is off'):
                    self.command()
        self.wire_record.unlink()
        self.assertEqual(desktop_build.desktop_cpa_compatibility()['cpa_wire_result'], 'UNKNOWN')
        with self.assertRaisesRegex(mod.DesktopError, 'no recorded desktop wire PASS'):
            self.command()

    def test_standalone_compatibility_probes_only_the_claude_port(self):
        live = mod.VERIFIED['cpa'].replace('-gate-', '+gate.')
        with patch.object(cp, 'running_version', return_value=live) as probe:
            self.assertTrue(desktop_build.desktop_cpa_compatibility()['cpa_compatible'])
            probe.assert_called_once_with(sienna_pool.CLAUDE_PORT)
        with patch.object(cp, 'running_version', side_effect=AssertionError('unexpected probe')):
            self.assertFalse(sienna_desktop.desktop_status(live_version='')['cpa_compatible'])
            self.assertTrue(sienna_desktop.desktop_status(live_version=live)['cpa_compatible'])

    def test_status_always_zero_on_unreadable_json(self):
        self.put(self.mode_path(), {})
        self.mode_path().write_text('bad-json')
        code, out, err = run(sienna_desktop.cmd_desktop, desktop_cmd='status', json=True)
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out)['configured_mode'], 'unknown')
        self.assertFalse(err)

    def test_cli_help_in_fake_home(self):
        result = run_script('--help')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('sienna', result.stdout)


    def test_automatic_recovery_precedes_new_credits_refusal(self):
        def crash(d, name):
            if name == 'published-state':
                raise RuntimeError('crash')
        with patch.object(mod.Desktop, 'checkpoint', crash), self.assertRaises(RuntimeError):
            self.command()
        self.guard['usage']['test-seat']['at'] = (self.now - dt.timedelta(hours=1)).isoformat()
        with self.assertRaisesRegex(mod.DesktopError, 'no fresh credits'):
            self.command()
        self.assertFalse((cp.STATE / 'desktop-txn.json').exists())
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '3p')



if __name__ == '__main__':
    unittest.main()
