"""Security review regression probes. Synthetic data, fake HOME only."""
from _helpers import addon
sienna_desktop, desktop_build = addon.desktop, addon.desktop_build

from _helpers import addon
import unittest
if addon is None:
    raise unittest.SkipTest('sienna add-on absent; dependent desktop/engine tests')
from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import cp, HOME  # noqa: E402  (sets the fake HOME before bin/codexpool loads)
import datetime as dt, json, os, time, unittest
import argparse, contextlib, fcntl, plistlib, subprocess
from _helpers import ROOT, run
from unittest.mock import patch
import test_desktop

mod = test_desktop.mod
APP_DISCOVER = mod.Desktop.app
assert 'codexpool-test-home' in str(cp.HOME), cp.HOME


class Boom(Exception):
    pass


def crash_at(name, nth=1):
    seen = {'n': 0}

    def checkpoint(self, point):
        if point == name:
            seen['n'] += 1
            if seen['n'] == nth:
                raise Boom(point)
    return patch.object(mod.Desktop, 'checkpoint', checkpoint)


class Probes(test_desktop.DesktopTests):
    # ---------------------------------------------------------------- P1

    # ---------------------------------------------------------------- P2
    def test_P2_rollback_creates_configuration_that_never_existed(self):
        with crash_at('prepared'):                           # before any profile write
            with self.assertRaises(Boom):
                self.command('pooled')
        journal = json.loads((cp.STATE / 'desktop-txn.json').read_text())
        self.put(self.mode_path(), {'deploymentMode': '1p', 'userKey': 1})  # any later edit -> foreign
        self.command('rollback')
        entry = sienna_desktop.DESKTOP_P3 / 'configLibrary' / (journal['entry_id'] + '.json')
        self.assertFalse(entry.exists(), 'rollback created an entry file that no write ever produced')
        self.assertFalse(self.meta_path().exists(), 'rollback created _meta.json')

    # ---------------------------------------------------------------- P3
    def test_P3_restart_required_after_completed_relaunch_same_second(self):
        self.command('pooled')
        base = int(time.time()) - 5
        for f in (self.mode_path(), self.meta_path()):
            os.utime(f, (base + 0.3, base + 0.3))          # last write at T+0.3 s
        st = self.state(); st['written_at'] = dt.datetime.fromtimestamp(base + 0.2, dt.timezone.utc).isoformat()
        self.put(cp.STATE / 'desktop.json', st)
        # the app starts at T+0.8 s; ps -o lstart= prints whole seconds, so app_started_at reads T
        started = dt.datetime.fromtimestamp(base).astimezone()
        running = {'app_running': True, 'app_started_at': started.isoformat(), 'app_bundle_ok': True}
        with patch.object(mod.Desktop, 'process', return_value=running):
            with sienna_desktop.desktop_backend() as d:
                s = d.status(probe=False)
        self.assertFalse(s['restart_required'], 'restart_required true for a process started after the write')

    def test_P3b_app_preference_write_flags_restart(self):
        self.command('pooled')
        started = dt.datetime.now().astimezone().replace(microsecond=0) + dt.timedelta(seconds=1)
        time.sleep(1.2)
        # the running 3p app writes its own preferences (mcpServers, globalShortcut) into this same file
        mode = self.read(self.mode_path()); mode['globalShortcut'] = 'Alt+Space'
        self.put(self.mode_path(), mode)
        running = {'app_running': True, 'app_started_at': started.isoformat(), 'app_bundle_ok': True}
        with patch.object(mod.Desktop, 'process', return_value=running):
            with sienna_desktop.desktop_backend() as d:
                s = d.status(probe=False)
        self.assertFalse(s['restart_required'])

    # ---------------------------------------------------------------- P4
    def test_P4_relaunch_quits_then_refuses_and_leaves_app_closed(self):
        self.command('pooled')
        entry = self.read(self.entry_path())
        entry['inferenceGatewayBaseUrl'] = 'http://127.0.0.1:9999'  # the user edited Pool in the app
        self.put(self.entry_path(), entry)
        calls = []
        state = {'running': True}
        running = {'app_running': True, 'app_started_at': dt.datetime.now().astimezone().isoformat(), 'app_bundle_ok': True}

        def process(self, bundle):
            return running if state['running'] else self_stopped

        self_stopped = self.stopped

        def quit_app(self, bundle):
            calls.append('quit'); state['running'] = False

        def open_app(self, bundle):
            calls.append('open')
        with patch.object(mod.Desktop, 'process', process), patch.object(mod.Desktop, 'quit_app', quit_app), \
                patch.object(mod.Desktop, 'open_app', open_app):
            with self.assertRaises(mod.DesktopError) as e:
                self.command('pooled', relaunch=True, yes=True)   # what the GUI switch runs
        self.assertNotEqual(calls, ['quit'], 'Claude was quit, the command refused, and Claude was never reopened')


    # ---------------------------------------------------------------- P5
    def test_P5_remove_unapplies_entry_the_user_applied_later(self):
        self.command('pooled')
        later = '99999999-2222-4333-8444-555555555555'
        self.put(sienna_desktop.DESKTOP_P3 / 'configLibrary' / (later + '.json'),
                 {'inferenceProvider': 'gateway', 'inferenceGatewayBaseUrl': 'https://gw.example.com',
                  'inferenceGatewayApiKey': 'k', 'inferenceModels': ['claude-opus-5-5']})
        meta = self.read(self.meta_path())
        meta['entries'].append({'id': later, 'name': 'Work'}); meta['appliedId'] = later  # Apply in the app window
        self.put(self.meta_path(), meta)
        self.command('remove', yes=True)
        meta = self.read(self.meta_path())
        self.assertEqual(meta['appliedId'], later, "remove un-applied the user's own later choice")

    # ---------------------------------------------------------------- P6
    def test_P6_backups_copy_mcp_secrets_from_the_preferences_file(self):
        secret = 'ghp_SECRETTOKEN0123456789'
        self.put(self.mode_path(), {'deploymentMode': '1p', 'mcpServers': {'gh': {'command': 'x',
                                                                                  'env': {'GITHUB_TOKEN': secret}}}})
        self.command('pooled'); self.command('claudeai'); self.command('pooled'); self.command('claudeai')
        hits = [p for p in cp.STATE.rglob('*') if p.is_file() and secret.encode() in p.read_bytes()]
        self.assertEqual(hits, [])

    # ---------------------------------------------------------------- P7

    # ---------------------------------------------------------------- P8
    def test_P8_app_preference_write_after_complete_writes_tears_down_working_pool(self):
        with crash_at('mode'):                 # every file already at "after"; only bookkeeping missing
            with self.assertRaises(Boom):
                self.command('pooled')
        journal = json.loads((cp.STATE / 'desktop-txn.json').read_text())
        # the user opens Claude (3p on Pool) and adds an MCP server: the app writes this same file
        mode = self.read(self.mode_path()); mode['mcpServers'] = {'fs': {'command': 'npx'}}
        self.put(self.mode_path(), mode)
        try:
            self.command('pooled')
        except mod.DesktopError as e:
            self.fail('preference edits must not block recovery: ' + str(e))
        self.command('rollback')
        entry = sienna_desktop.DESKTOP_P3 / 'configLibrary' / (journal['entry_id'] + '.json')
        self.assertEqual(self.read(entry)['inferenceGatewayApiKey'], 'codexpool')
        self.assertEqual(self.read(self.meta_path())['appliedId'], journal['entry_id'])
        self.assertEqual(self.read(self.mode_path())['mcpServers'], {'fs': {'command': 'npx'}})

    # ---------------------------------------------------------------- P9
    def test_P9_launchservices_selects_among_multiple_copies(self):
        first = HOME / 'Applications/Claude.app'
        chosen = HOME / 'Applications/Other/Claude.app'
        for path in (first, chosen):
            (path / 'Contents').mkdir(parents=True, exist_ok=True)
            (path / 'Contents/Info.plist').write_bytes(plistlib.dumps({
                'CFBundleIdentifier': 'com.anthropic.claudefordesktop',
                'CFBundleShortVersionString': mod.VERIFIED['app']}))
        def discover(command, **kwargs):
            if command[0] == 'mdfind':
                return argparse.Namespace(stdout=str(chosen), returncode=0)
            self.assertEqual(command[0], 'osascript')
            self.assertIn('path to application id', command[-1])
            return argparse.Namespace(stdout=str(chosen) + '/', returncode=0)
        with patch.object(mod.subprocess, 'run', side_effect=discover), sienna_desktop.desktop_backend() as desktop:
            self.assertEqual(APP_DISCOVER(desktop)['bundle_path'], str(chosen))

    # ---------------------------------------------------------------- P10
    def test_P10_long_running_pooled_app_reads_unknown(self):
        self.command('pooled')
        started = dt.datetime.now().astimezone().replace(microsecond=0) - dt.timedelta(hours=6)
        stamp = lambda t: t.strftime('%Y-%m-%d %H:%M:%S')
        lines = [stamp(started + dt.timedelta(seconds=5)) + " [info] [custom-3p] 3P mode active { provider: 'gateway' }"]
        filler = stamp(started + dt.timedelta(hours=1)) + ' [info] [cowork] ' + 'x' * 200
        lines += [filler] * 1400                                   # ~300 KB of ordinary logging
        lines.append(stamp(started + dt.timedelta(hours=5)) + ' [info] [custom-3p] inference apiHost=' + 'http://127.0.0.1:' + str(sienna_pool.CLAUDE_PORT))
        sienna_desktop.DESKTOP_LOGS_3P.mkdir(parents=True, exist_ok=True)
        (sienna_desktop.DESKTOP_LOGS_3P / 'main.log').write_text('\n'.join(lines) + '\n')
        running = {'app_running': True, 'app_started_at': started.isoformat(), 'app_bundle_ok': True}
        with patch.object(mod.Desktop, 'process', return_value=running):
            with sienna_desktop.desktop_backend() as d:
                s = d.status(probe=False)
        self.assertEqual(s['running_mode'], 'pooled')

    # ---------------------------------------------------------------- P11
    def test_P11_remove_then_pooled_drops_users_pool_restrictions(self):
        self.command('pooled')
        entry = self.read(self.entry_path()); entry['builtinToolPolicy'] = {'Bash': 'ask'}
        entry['disableBypassPermissionsMode'] = True                    # tightened in the app's window
        self.put(self.entry_path(), entry)
        self.command('remove', yes=True)                                # edited -> kept, not applied
        self.command('pooled')
        meta = self.read(self.meta_path())
        applied = self.read(sienna_desktop.DESKTOP_P3 / 'configLibrary' / (meta['appliedId'] + '.json'))
        self.assertEqual(applied.get('builtinToolPolicy'), {'Bash': 'ask'})


    def test_P12_cas_rollback_leaves_later_owned_value(self):
        self.command()
        with crash_at('entry'), self.assertRaises(Boom):
            self.command(effort='medium')
        entry = self.read(self.entry_path())
        entry['defaultModelEffort'] = 'low'
        self.put(self.entry_path(), entry)
        out = self.command('rollback')
        self.assertEqual(self.read(self.entry_path())['defaultModelEffort'], 'low')
        self.assertIn('kept changed key: entry.defaultModelEffort', out)
        self.assertFalse((cp.STATE / 'desktop-txn.json').exists())

    def test_P13_guard_lock_released_for_quit_and_open(self):
        self.command()
        running = dict(self.stopped, app_running=True, app_started_at=cp.now_utc().isoformat())
        calls = []
        def check_unlocked(action):
            with (cp.STATE / 'claude-guard.lock').open('rb') as handle:
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
                fcntl.flock(handle, fcntl.LOCK_UN)
            calls.append(action)
            running['app_running'] = action == 'open'
        with patch.object(mod.Desktop, 'process', side_effect=lambda *a: dict(running)), \
             patch.object(mod.Desktop, 'quit_app', side_effect=lambda *a: check_unlocked('quit')), \
             patch.object(mod.Desktop, 'open_app', side_effect=lambda *a: check_unlocked('open')):
            self.command('claudeai', relaunch=True, yes=True)
        self.assertEqual(calls, ['quit', 'open'])

    def test_P14_failure_after_quit_restores_previous_mode_and_reopens(self):
        self.command()
        running = dict(self.stopped, app_running=True, app_started_at=cp.now_utc().isoformat())
        calls = []
        def quit_app(*args):
            calls.append('quit')
            running['app_running'] = False
        def open_app(*args):
            calls.append('open')
            self.assertEqual(self.read(self.mode_path())['deploymentMode'], '3p')
        with patch.object(mod.Desktop, 'process', side_effect=lambda *a: dict(running)), \
             patch.object(mod.Desktop, 'quit_app', side_effect=quit_app), \
             patch.object(mod.Desktop, 'open_app', side_effect=open_app), \
             crash_at('mode'), self.assertRaises(Boom):
            self.command('claudeai', relaunch=True, yes=True)
        self.assertEqual(calls, ['quit', 'open'])
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '3p')

    def test_P15_subprocess_failures_report_without_traceback(self):
        for failure in (subprocess.TimeoutExpired('osascript', 20), subprocess.CalledProcessError(1, 'open')):
            with patch.object(mod.Desktop, 'command', side_effect=failure):
                code, out, err = run(sienna_desktop.cmd_desktop, desktop_cmd='relaunch', yes=True)
            self.assertNotEqual(code, 0)
            self.assertNotIn('Traceback', out + err)

    def test_P16_symlinked_install_root_is_allowed(self):
        target = HOME / 'pool-real'
        ROOT.rename(target)
        ROOT.symlink_to(target, target_is_directory=True)
        try:
            self.command()
            with sienna_desktop.desktop_backend() as desktop:
                self.assertEqual(desktop.status()['configured_mode'], 'pooled')
        finally:
            ROOT.unlink()
            target.rename(ROOT)

    def test_P17_writable_directories_refused(self):
        sienna_desktop.DESKTOP_P3.mkdir(mode=0o700)
        for mode in (0o720, 0o702):
            sienna_desktop.DESKTOP_P3.chmod(mode)
            try:
                with self.assertRaisesRegex(mod.DesktopError, 'group/other writable'):
                    self.command()
            finally:
                sienna_desktop.DESKTOP_P3.chmod(0o700)

    def test_P18_missing_legacy_backup_does_not_block_recovery(self):
        self.put(cp.STATE / 'desktop-txn.json', {'backup_dir': 'state/desktop-backup-missing'})
        self.command('rollback')
        self.assertFalse((cp.STATE / 'desktop-txn.json').exists())
        self.command()
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '3p')

    def test_P19_uninstall_switches_before_stopping_pool_or_refuses(self):
        self.command()
        with patch.object(cp, 'launchd_loaded', return_value=(True, 123)), \
             patch.object(cp, 'script_is_ours', return_value=False):
            plan = sienna_pool.claude_uninstall_plan()
        self.assertIn('desktop', plan[0][0])
        self.assertIn('stop the launchd', plan[1][0])
        plan[0][1]()
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')
        self.command()
        with patch.object(mod.Desktop, 'process', return_value=dict(self.stopped, app_running=True)):
            plan[0][1]()
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')

    def test_P20_precise_start_orders_subsecond_changes(self):
        self.command()
        moment = cp.now_utc().replace(microsecond=500000)
        state = self.state()
        state['written_at'] = moment.isoformat()
        self.put(cp.STATE / 'desktop.json', state)
        for offset, expected in ((-0.1, True), (0.1, False)):
            process = dict(self.stopped, app_running=True, app_start_resolution=0.000001,
                           app_started_at=(moment + dt.timedelta(seconds=offset)).isoformat())
            with patch.object(mod.Desktop, 'process', return_value=process), sienna_desktop.desktop_backend() as desktop:
                self.assertEqual(desktop.status()['restart_required'], expected)

    def test_P21_missing_state_directory_is_no_pending_recovery(self):
        target = ROOT / 'state-away'
        cp.STATE.rename(target)
        try:
            with sienna_desktop.desktop_backend() as desktop:
                desktop.recover()
                self.assertFalse(cp.STATE.exists())
        finally:
            target.rename(cp.STATE)

    def test_P22_journal_rejects_unowned_keys(self):
        with crash_at('mode'), self.assertRaises(Boom):
            self.command()
        journal = self.read(cp.STATE / 'desktop-txn.json')
        journal['steps'][0]['key'] = 'mcpServers'
        self.put(cp.STATE / 'desktop-txn.json', journal)
        with self.assertRaisesRegex(mod.DesktopError, 'does not manage'):
            self.command('rollback')

    def test_P23_preferences_written_during_quit_are_preserved(self):
        self.command()
        running = dict(self.stopped, app_running=True, app_started_at=cp.now_utc().isoformat())
        def quit_app(*args):
            self.put(self.mode_path(), dict(self.read(self.mode_path()), globalShortcut='Alt+Space'))
            running['app_running'] = False
        with patch.object(mod.Desktop, 'process', side_effect=lambda *a: dict(running)), \
             patch.object(mod.Desktop, 'quit_app', side_effect=quit_app), \
             patch.object(mod.Desktop, 'open_app'):
            self.command('claudeai', relaunch=True, yes=True)
        self.assertEqual(self.read(self.mode_path()), {'deploymentMode': '1p', 'globalShortcut': 'Alt+Space'})

    def test_P24_production_home_check_cannot_be_bypassed_by_environment(self):
        with patch.object(mod, 'DESKTOP_TEST_HOME', None), \
             patch.dict(os.environ, CODEXPOOL_HOME=str(ROOT)), \
             self.assertRaisesRegex(mod.DesktopError, 'HOME differs'):
            self.command()


    def test_P25_recovery_never_copies_a_later_credential(self):
        with crash_at('entry'), self.assertRaises(Boom):
            self.command()
        journal = self.read(cp.STATE / 'desktop-txn.json')
        path = sienna_desktop.DESKTOP_P3 / 'configLibrary' / (journal['entry_id'] + '.json')
        secret = 'synthetic-later-private-credential'
        self.put(path, dict(self.read(path), inferenceGatewayApiKey=secret))
        self.command('rollback')
        self.assertEqual(self.read(path)['inferenceGatewayApiKey'], secret)
        for candidate in cp.STATE.glob('desktop*'):
            if candidate.is_file():
                self.assertNotIn(secret, candidate.read_text())

    def test_P26_failed_open_after_commit_reverts_and_reopens(self):
        self.command()
        running = dict(self.stopped, app_running=True, app_started_at=cp.now_utc().isoformat())
        def quit_app(*args):
            running['app_running'] = False
        calls = []
        def open_app(*args):
            calls.append('open')
            if len(calls) == 1:
                self.assertEqual(self.read(self.mode_path())['deploymentMode'], '1p')
                raise subprocess.CalledProcessError(1, 'open')
            self.assertEqual(self.read(self.mode_path())['deploymentMode'], '3p')
        with patch.object(mod.Desktop, 'process', side_effect=lambda *a: dict(running)), \
             patch.object(mod.Desktop, 'quit_app', side_effect=quit_app), \
             patch.object(mod.Desktop, 'open_app', side_effect=open_app), \
             self.assertRaisesRegex(mod.DesktopError, 'failed or timed out'):
            self.command('claudeai', relaunch=True, yes=True)
        self.assertEqual(calls, ['open', 'open'])

    def test_P27_quit_timeout_reopens_without_undoing_unwritten_keys(self):
        self.command()
        running = dict(self.stopped, app_running=True, app_started_at=cp.now_utc().isoformat())
        def quit_app(*args):
            running['app_running'] = False
            raise subprocess.TimeoutExpired('osascript', 20)
        with patch.object(mod.Desktop, 'process', side_effect=lambda *a: dict(running)), \
             patch.object(mod.Desktop, 'quit_app', side_effect=quit_app), \
             patch.object(mod.Desktop, 'open_app') as opened, \
             patch.object(mod.Desktop, 'apply_keys') as write, \
             self.assertRaisesRegex(mod.DesktopError, 'failed or timed out'):
            self.command('claudeai', relaunch=True, yes=True)
        opened.assert_called_once()
        write.assert_not_called()
        self.assertEqual(self.read(self.mode_path())['deploymentMode'], '3p')


    def test_P29_native_process_start_uses_this_test_process_only(self):
        # Read only the test runner PID; never enumerate or inspect an app.
        fallback = dt.datetime(2000, 1, 1, tzinfo=dt.timezone.utc)
        with sienna_desktop.desktop_backend() as desktop:
            started, resolution = desktop.process_started(os.getpid(), fallback)
        self.assertIn(resolution, (1.0, 0.000001))
        if resolution == 0.000001:
            self.assertGreater(started, fallback)
            self.assertLessEqual(started, cp.now_utc())


    def test_P30_desktop_ownership_survives_pool_uninstall_for_doctor(self):
        self.assertFalse(sienna_desktop.desktop_recorded())
        self.command()
        with patch.object(sienna_pool, 'claude_installed', return_value=False):
            self.assertTrue(sienna_desktop.desktop_recorded())
        record = cp.STATE / 'desktop.json'
        record.rename(cp.STATE / ('desktop.json.removed-' + '00000000-0000-4000-8000-000000000000'))
        self.assertTrue(sienna_desktop.desktop_recorded())
        with sienna_desktop.desktop_backend() as desktop:
            self.assertEqual(desktop.status()['configured_mode'], 'pooled')


    def test_P31_cas_distinguishes_boolean_from_number(self):
        self.command(tool_search=False)
        with crash_at('entry'), self.assertRaises(Boom):
            self.command(tool_search=True)
        self.put(self.entry_path(), dict(self.read(self.entry_path()), toolSearchEnabled=1))
        out = self.command('rollback')
        self.assertIs(type(self.read(self.entry_path())['toolSearchEnabled']), int)
        self.assertIn('kept changed key: entry.toolSearchEnabled', out)


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(Probes(name) for name in loader.getTestCaseNames(Probes) if name.startswith('test_P'))
