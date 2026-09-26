"""The command line: version, every --help, gui, doctor --json, lane list --json, and finding the codex CLI."""
import argparse
import ast
import contextlib
import io
import json
import os
import pathlib
import re
import shutil
import unittest
from unittest import mock

from _helpers import HOME, REPO, ROOT, TEST_KEY, FakePool, cp, fake_seat_file, preserved, run, run_script


def subcommands(parser, path=()):
    """(path, parser) for every subcommand, nested ones (lane list, ...) included."""
    for action in parser._actions:
        if isinstance(action, argparse._SubParsersAction):
            for name, sub in action.choices.items():
                yield path + (name,), sub
                yield from subcommands(sub, path + (name,))


class Version(unittest.TestCase):
    def test_constant_is_the_latest_release(self):
        """A release bumps VERSION in bin/codexpool and adds its CHANGELOG.md section together."""
        released = re.findall(r'^## \[(\d+\.\d+\.\d+)\] - \d{4}-\d\d-\d\d$', (REPO / 'CHANGELOG.md').read_text(), re.M)
        self.assertTrue(released, 'CHANGELOG.md has no "## [x.y.z] - YYYY-MM-DD" section')
        self.assertEqual(cp.VERSION, released[0], 'VERSION in bin/codexpool and the newest CHANGELOG.md release differ')

    def test_command(self):
        self.assertEqual(run(cp.cmd_version), (0, f'codexpool {cp.VERSION}\n', ''))

    def test_script(self):
        for args in (['version'], ['--version']):
            r = run_script(*args)
            self.assertEqual((r.returncode, r.stdout, r.stderr), (0, f'codexpool {cp.VERSION}\n', ''), args)


class Help(unittest.TestCase):
    EXPECTED = {('status',), ('doctor',), ('setup',), ('gui',), ('set',), ('version',), ('login',), ('enable',),
                ('disable',), ('priority',), ('label',), ('weight',), ('reset',), ('reserve',), ('remove',),
                ('refresh',), ('restart',), ('logs',), ('selftest',), ('build',), ('upgrade',), ('install',),
                ('uninstall',), ('lane',), ('lane', 'list'), ('lane', 'apply'), ('lane', 'add'), ('lane', 'remove'),
                ('lane', 'key'), ('lane', 'login'), ('lane', 'test'), ('guard',), ('menubar',)}

    def test_every_subcommand_has_help(self):
        found = dict(subcommands(cp.build_parser()))
        self.assertEqual(set(found), self.EXPECTED)
        for path in sorted(found):
            out = io.StringIO()
            with self.subTest(path=' '.join(path)), contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    cp.build_parser().parse_args(list(path) + ['--help'])
                self.assertEqual(ctx.exception.code, 0)
                self.assertIn(f'usage: codexpool {" ".join(path)}', out.getvalue())

    def test_every_subcommand_has_a_summary(self):
        def summaries(parser):
            for action in parser._actions:
                if isinstance(action, argparse._SubParsersAction):
                    for choice in action._choices_actions:
                        yield choice
                    for sub in action.choices.values():
                        yield from summaries(sub)
        choices = list(summaries(cp.build_parser()))
        self.assertEqual(len(choices), len(self.EXPECTED))
        for choice in choices:
            self.assertTrue((choice.help or '').strip(), choice.dest)

    def test_top_level_help(self):
        r = run_script('--help')
        self.assertEqual(r.returncode, 0)
        for word in ('setup', 'gui', 'set', 'version', '--version', 'codexpool setup walks you through'):
            self.assertIn(word, r.stdout)

    def test_new_flags_parse(self):
        p = cp.build_parser()
        self.assertTrue(p.parse_args(['doctor', '--json']).json)
        self.assertFalse(p.parse_args(['doctor']).json)
        self.assertTrue(p.parse_args(['lane', 'list', '--json']).json)
        self.assertEqual(p.parse_args(['gui']).pane, 'overview')
        self.assertEqual(p.parse_args(['gui', 'setup-welcome']).pane, 'setup-welcome')
        args = p.parse_args(['set', 'display', 'used'])
        self.assertEqual((args.key, args.value, args.fn), ('display', 'used', cp.cmd_set))
        self.assertEqual((p.parse_args(['set']).key, p.parse_args(['set']).value), (None, None))
        self.assertIs(p.parse_args(['setup']).fn, cp.cmd_setup)
        with contextlib.redirect_stderr(io.StringIO()):
            with self.assertRaises(SystemExit) as ctx:
                p.parse_args(['gui', 'bogus'])
        self.assertEqual(ctx.exception.code, 2)


class Gui(unittest.TestCase):
    def test_argv(self):
        self.assertEqual(cp.gui_argv(), [str(cp.menubar_python_bin()), str(ROOT / 'menubar' / 'codexpool_settings.py'),
                                         '--pane', 'overview'])
        self.assertEqual(cp.gui_argv('lanes')[-2:], ['--pane', 'lanes'])
        self.assertEqual(cp.gui_argv()[0], str(ROOT / '.venv' / 'bin' / 'python'))
        with self.assertRaises(ValueError):
            cp.gui_argv('bogus')

    def test_argv_uses_menubar_python(self):
        with mock.patch.dict(cp.SETTINGS, {'menubar_python': '~/py/bin/python3'}):
            self.assertEqual(cp.gui_argv('about')[0], str(pathlib.Path.home() / 'py' / 'bin' / 'python3'))
        with mock.patch.dict(cp.SETTINGS, {'python': '/opt/py/bin/python3', 'menubar_python': None}):
            self.assertEqual(cp.gui_argv('about')[0], '/opt/py/bin/python3')

    def test_panes_are_ones_the_settings_window_takes(self):
        source = REPO / 'menubar' / 'codexpool_settings.py'
        if not source.exists():
            self.skipTest('menubar/codexpool_settings.py is not in this checkout')
        panes = {}
        for node in ast.parse(source.read_text()).body:
            if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name) and \
                    node.targets[0].id in ('PANES', 'SETUP_PANES'):
                panes[node.targets[0].id] = ast.literal_eval(node.value)
        self.assertLessEqual(set(cp.GUI_PANES), set(panes.get('PANES', ())) | set(panes.get('SETUP_PANES', ())))

    def test_settings_window_is_installed_code(self):
        self.assertIn('menubar/codexpool_settings.py', cp.CODE_FILES)

    def test_license_files_are_installed(self):
        # The installed README links LICENSE and NOTICE (the license's required notice and the credits).
        for name in ('LICENSE', 'NOTICE'):
            self.assertIn(name, cp.CODE_FILES)
            self.assertTrue((REPO / name).is_file(), name)

    def test_missing_settings_window(self):
        code, out, err = run(cp.cmd_gui, pane='overview')
        self.assertEqual((code, out), (1, ''))
        self.assertIn('not installed', err)
        self.assertIn('codexpool_settings.py', err)

    def launchable(self):
        """A dummy Settings window and interpreter in the fake install, removed afterwards."""
        script, python = cp.settings_app(), ROOT / '.venv' / 'bin' / 'python'
        self.addCleanup(shutil.rmtree, str(ROOT / 'menubar'), True)
        self.addCleanup(shutil.rmtree, str(ROOT / '.venv'), True)
        script.parent.mkdir(parents=True, exist_ok=True)
        script.write_text('raise SystemExit("the tests never run this")\n')
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text('#!/bin/sh\nexit 1\n')
        python.chmod(0o755)
        return script, python

    def test_starts_detached_without_waiting(self):
        script, python = self.launchable()
        with mock.patch.object(cp.subprocess, 'Popen') as popen:
            code, out, _ = run(cp.cmd_gui, pane='health')
        self.assertEqual(code, 0)
        self.assertIn('Settings window (health)', out)
        popen.assert_called_once()
        argv, kwargs = popen.call_args[0][0], popen.call_args[1]
        self.assertEqual(argv, [str(python), str(script), '--pane', 'health'])
        self.assertTrue(kwargs['start_new_session'])
        self.assertEqual(kwargs['stdin'], cp.subprocess.DEVNULL)
        self.assertEqual(kwargs['cwd'], ROOT)
        popen.return_value.wait.assert_not_called()

    def test_missing_interpreter(self):
        _, python = self.launchable()
        python.unlink()
        with mock.patch.object(cp.subprocess, 'Popen') as popen:
            code, _, err = run(cp.cmd_gui, pane='overview')
        self.assertEqual(code, 1)
        self.assertIn('the interpreter with PyObjC', err)
        popen.assert_not_called()


class DoctorJson(unittest.TestCase):
    """doctor --json in the fake home: the pool is down, the Keychain says no."""

    def doctor(self, as_json=True):
        return run(cp.cmd_doctor, json=as_json)

    def assertShape(self, data):
        self.assertEqual(set(data), {'ok', 'problems', 'warnings', 'sections'})
        self.assertIsInstance(data['ok'], bool)
        checks = [c for s in data['sections'] for c in s['checks']]
        for s in data['sections']:
            self.assertEqual(set(s), {'title', 'checks'})
            self.assertIsInstance(s['title'], str)
            self.assertTrue(s['checks'], s['title'])
        for c in checks:
            self.assertEqual(set(c), {'status', 'text', 'fix'})
            self.assertIn(c['status'], ('ok', 'warn', 'fail'))
            self.assertIsInstance(c['text'], str)
            self.assertTrue(c['fix'] is None or (isinstance(c['fix'], str) and c['fix']))
            if c['status'] == 'ok':
                self.assertIsNone(c['fix'])
        self.assertEqual(data['problems'], sum(c['status'] == 'fail' for c in checks))
        self.assertEqual(data['warnings'], sum(c['status'] == 'warn' for c in checks))
        self.assertEqual(data['ok'], data['problems'] == 0)
        return checks

    def test_pool_down(self):
        code, out, err = self.doctor()
        data = json.loads(out)  # stdout is the JSON and nothing else
        self.assertEqual(err, '')
        self.assertEqual(code, 1)
        checks = self.assertShape(data)
        self.assertFalse(data['ok'])
        self.assertEqual([s['title'] for s in data['sections']],
                         ['Pool process', 'Codex app', 'Seats', 'Guard and visibility', 'Lanes',
                          'Recent pool errors (last 24h of logs)'])
        by_text = {c['text']: c for c in checks}
        listening = by_text[f'listening on 127.0.0.1:{cp.PORT}']
        self.assertEqual((listening['status'], listening['fix']), ('fail', 'codexpool restart ; codexpool logs'))
        self.assertEqual(by_text['bound to loopback only'], {'status': 'ok', 'text': 'bound to loopback only',
                                                            'fix': None})
        self.assertEqual(by_text['new threads land on: nothing available']['status'], 'warn')

    def test_same_checks_as_the_text_report(self):
        code_json, out_json, _ = self.doctor()
        code_text, out_text, _ = self.doctor(as_json=False)
        data = json.loads(out_json)
        self.assertEqual(code_json, code_text)
        expected = []
        for i, s in enumerate(data['sections']):
            expected.append(('\n' if i else '') + s['title'])
            for c in s['checks']:
                icon = {'ok': '✓', 'warn': '!', 'fail': '✗'}[c['status']]
                expected.append(f' {icon} {c["text"]}' + (f'\n     → {c["fix"]}' if c['fix'] else ''))
        expected.append(f'\n{data["problems"]} problem(s)' if data['problems'] else '\nOK')
        self.assertEqual(out_text, '\n'.join(expected) + '\n')

    def test_without_lanes(self):
        with preserved(cp.LANES_FILE):
            cp.LANES_FILE.unlink()
            _, out, _ = self.doctor()
        self.assertNotIn('Lanes', [s['title'] for s in json.loads(out)['sections']])

    def test_not_installed(self):
        with mock.patch.object(cp, 'missing_install', return_value=['~/.codexpool/config.yaml']):
            code, out, _ = self.doctor()
            code_text, out_text, err_text = self.doctor(as_json=False)
        data = json.loads(out)
        self.assertShape(data)
        self.assertEqual(code, 1)
        self.assertEqual([s['title'] for s in data['sections']], ['Install'])
        self.assertIn('not installed yet', data['sections'][0]['checks'][0]['text'])
        self.assertEqual((code_text, out_text), (1, ''))
        self.assertIn('not installed yet', err_text)

    def test_pool_up(self):
        with preserved(cp.LANES_FILE), mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY):
            cp.LANES_FILE.unlink()
            seat = fake_seat_file('work@test')
            self.addCleanup(seat.unlink)
            with FakePool(models=['gpt-test']):
                code, out, _ = self.doctor()
        checks = {c['text']: c['status'] for c in self.assertShape(json.loads(out))}
        self.assertEqual(code, 1)  # launchd, the guard and the Codex config are still missing
        self.assertEqual(checks[f'listening on 127.0.0.1:{cp.PORT}'], 'ok')
        self.assertEqual(checks['origin gate: Codex-like request 200, browser request 403'], 'ok')
        self.assertEqual(checks['management API reachable (Keychain key ok)'], 'ok')
        self.assertEqual(checks['1 Codex seats in the pool'], 'ok')
        self.assertEqual(checks['new threads land on: test-work-plus'], 'ok')


class LockedKeychain(unittest.TestCase):
    """The Keychain refuses the management key before any connection is made, so whether the pool runs comes from
    its port: a locked Keychain with the pool down still reads as "not running" (status, the guard, the menu bar)."""
    LOCKED = cp.KeyUnavailable('the Keychain would not release the key; it is probably locked', 'unlock it')

    def status(self):
        with mock.patch.object(cp, 'load_seats', side_effect=self.LOCKED):
            return cp.status_now()['pool']

    def test_pool_down(self):
        pool = self.status()
        self.assertFalse(pool['running'])
        self.assertIn('Keychain', pool['error'])
        with mock.patch.object(cp, 'load_seats', side_effect=self.LOCKED):
            code, out, _ = run(cp.cmd_status, live=True, json=False)
        self.assertEqual(code, 0)
        self.assertIn('Pool is NOT running', out)

    def test_pool_up(self):
        with FakePool():
            pool = self.status()
        self.assertTrue(pool['running'])
        self.assertIn('Keychain', pool['error'])

    def test_management_api_refusal_still_means_running(self):
        with mock.patch.object(cp, 'load_seats', side_effect=cp.ApiError(401, 'bad management key')):
            pool = cp.status_now()['pool']
        self.assertEqual((pool['running'], pool['error']), (True, 'HTTP 401: bad management key'))

    def test_guard_writes_the_same(self):
        for listening in (False, True):
            with self.subTest(listening=listening), preserved(cp.GUARD_FILE, cp.STATUS_FILE), \
                    mock.patch.object(cp, 'load_seats', side_effect=self.LOCKED), \
                    (FakePool() if listening else contextlib.nullcontext()):
                cp.guard_pass()
                self.assertIs(json.loads(cp.STATUS_FILE.read_text())['pool']['running'], listening)


class InstallNextSteps(unittest.TestCase):
    """The end of a first install (no seats yet): its own next steps, or none under install.sh, which prints them
    after it (and after opening the Setup assistant), so the one-liner shows a single list."""
    STEPS = ('install_preflight', 'install_toolchain', 'install_build', 'install_key_and_config', 'install_agents',
             'install_codex_config', 'install_command')

    def install(self, **env):
        with contextlib.ExitStack() as stack:
            for name in self.STEPS:
                stack.enter_context(mock.patch.object(cp, name))
            stack.enter_context(mock.patch.object(cp, 'headline_upgrade_note', return_value=None))
            stack.enter_context(mock.patch.dict(os.environ, env))
            return run(cp.cmd_install, dry_run=False, fix_config=False)

    def test_on_its_own(self):
        code, out, _ = self.install()
        self.assertEqual(code, 0)
        self.assertIn('codexpool setup walks you through', out)
        self.assertIn('codexpool login "<Label>" --priority <n>', out)

    def test_under_the_one_line_installer(self):
        code, out, _ = self.install(CODEXPOOL_VIA_INSTALLER='1')
        self.assertEqual(code, 0)
        self.assertIn('The installer shows the next steps below.', out)
        self.assertNotIn('codexpool login', out)
        self.assertNotIn('codexpool setup', out)
        self.assertIn('CODEXPOOL_VIA_INSTALLER=1 "${PY_CMD[@]}" bin/codexpool install',
                      (REPO / 'install.sh').read_text())


class LaneListJson(unittest.TestCase):
    MEMBER_KEYS = {'id', 'provider', 'model', 'name', 'state', 'last_test'}

    def lanes(self):
        code, out, err = run(cp.cmd_lane_list, json=True)
        self.assertEqual((code, err), (0, ''))
        return json.loads(out)

    def test_shape(self):
        data = self.lanes()
        self.assertEqual(list(data), ['lanes'])
        (lane,) = data['lanes']
        self.assertEqual(set(lane), {'name', 'effort', 'role', 'agent_type', 'last_test', 'members'})
        self.assertEqual((lane['name'], lane['effort'], lane['agent_type'], lane['last_test']),
                         ('bulk', 'xhigh', 'bulk', None))
        self.assertTrue(lane['role'].startswith('A capable, fast model'))
        self.assertEqual([set(m) for m in lane['members']], [self.MEMBER_KEYS] * 2)
        self.assertEqual([(m['id'], m['provider'], m['model'], m['name'], m['state'], m['last_test'])
                          for m in lane['members']],
                         [('grok', 'xai', 'grok-4.7-build-fast', 'Grok 4.7 Fast', 'unknown', None),
                          ('muse', 'opencode-go', 'muse-spark-1.3-contributor', 'Muse Spark 1.3 contributor',
                           'bridge down', None)])

    def test_states_match_the_text_list(self):
        code, text, _ = run(cp.cmd_lane_list, json=False)
        self.assertEqual(code, 0)
        for m in self.lanes()['lanes'][0]['members']:
            row = next(line for line in text.splitlines() if f' {m["id"]} ' in line)
            self.assertIn(f' {m["state"]} ', row + ' ')

    def test_no_lanes_file(self):
        with preserved(cp.LANES_FILE):
            cp.LANES_FILE.unlink()
            self.assertEqual(self.lanes(), {'lanes': []})

    def test_no_lanes_defined(self):
        with preserved(cp.LANES_FILE):
            cp.LANES_FILE.write_text('{"lanes": {}}\n')
            self.assertEqual(self.lanes(), {'lanes': []})

    def test_last_tests(self):
        record = {'bulk': {'grok': {'ok': True, 'when': '2026-09-24T14:12:00+00:00', 'seconds': 31.0,
                                    'reason': 'edited both files with apply_patch', 'compaction': None},
                           '*': {'ok': False, 'when': '2026-09-24T14:20:00+00:00', 'seconds': 2.0,
                                 'reason': 'codex exec exit 1', 'compaction': None}}}
        with preserved(cp.LANE_TESTS):
            cp.LANE_TESTS.write_text(json.dumps(record))
            (lane,) = self.lanes()['lanes']
        self.assertEqual(lane['last_test'], {'ok': False, 'when': '2026-09-24T14:20:00+00:00',
                                             'reason': 'codex exec exit 1'})
        self.assertEqual(lane['members'][0]['last_test'], {'ok': True, 'when': '2026-09-24T14:12:00+00:00',
                                                           'reason': 'edited both files with apply_patch'})
        self.assertIsNone(lane['members'][1]['last_test'])

    def test_missing_key(self):
        key = cp.lane_key_path('opencode-go')
        with preserved(key):
            key.unlink()
            self.assertEqual(self.lanes()['lanes'][0]['members'][1]['state'], 'no key')

    def test_broken_lanes_file(self):
        with preserved(cp.LANES_FILE):
            cp.LANES_FILE.write_text('{"lanes": {"Bad Name": {}}}\n')
            with self.assertRaises(cp.LaneError):
                run(cp.cmd_lane_list, json=True)

    def test_lane_without_subcommand_still_lists(self):
        code, out, _ = run(cp.cmd_lane)
        self.assertEqual(code, 0)
        self.assertIn('agent_type "bulk"', out)


class CodexBin(unittest.TestCase):
    """codex_bin(): settings.json, then the CLI inside the Codex app (26.924+ layout first), then PATH. The real
    /Applications is left out (APP_FOLDERS is patched to the fake home's ~/Applications) and mdfind is the
    failing stub unless a test says otherwise."""

    NEW = 'Contents/Resources/codex-cli/bin/codex'
    OLD = 'Contents/Resources/codex'

    def setUp(self):
        self.apps = HOME / 'Applications'
        self.apps.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, str(self.apps), True)
        self.bin = HOME / 'codex-bin-test'
        self.bin.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, str(self.bin), True)
        for patch in (mock.patch.object(cp, 'APP_FOLDERS', (self.apps,)),
                      mock.patch.dict(cp.SETTINGS, {'codex_bin': None}),
                      mock.patch.dict(os.environ, {'PATH': f'{HOME / "stubs"}{os.pathsep}/usr/bin:/bin'})):
            patch.start()
            self.addCleanup(patch.stop)

    def app(self, name, *clis, folder=None, mode=0o755):
        """A fake app bundle holding a codex script at each of clis (paths inside the bundle)."""
        bundle = (folder or self.apps) / name
        for rel in clis:
            cli = bundle / rel
            cli.parent.mkdir(parents=True, exist_ok=True)
            cli.write_text('#!/bin/sh\necho codex-cli 0.0.0\n')
            cli.chmod(mode)
        (bundle / 'Contents').mkdir(parents=True, exist_ok=True)
        return bundle

    def test_new_layout_comes_first(self):
        app = self.app('ChatGPT.app', self.NEW, self.OLD)
        self.assertEqual(cp.codex_bin(), str(app / self.NEW))

    def test_older_layout(self):
        app = self.app('Codex.app', self.OLD)
        self.assertEqual(cp.codex_bin(), str(app / self.OLD))

    def test_found_by_bundle_id_anywhere(self):
        elsewhere = HOME / 'codex-bin-test' / 'Tools'
        app = self.app('Renamed.app', self.NEW, folder=elsewhere)
        mdfind = self.bin / 'mdfind'
        mdfind.write_text(f'#!/bin/sh\necho "$*" > "{self.bin}/mdfind-args"\necho "{elsewhere}/Other"\n'
                          f'echo "{app}"\n')
        mdfind.chmod(0o755)
        with mock.patch.dict(os.environ, {'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}'}):
            self.assertEqual(cp.codex_bin(), str(app / self.NEW))
        self.assertEqual((self.bin / 'mdfind-args').read_text().strip(),
                         f'kMDItemCFBundleIdentifier == "{cp.CODEX_BUNDLE_ID}"')

    def test_skips_what_cannot_run(self):
        self.app('ChatGPT.app', self.NEW, mode=0o644)  # not executable
        (self.apps / 'Codex.app' / self.OLD).mkdir(parents=True)  # a folder, not the CLI
        self.assertIsNone(cp.codex_bin())

    def test_falls_back_to_path(self):
        codex = self.bin / 'codex'
        codex.write_text('#!/bin/sh\n')
        codex.chmod(0o755)
        with mock.patch.dict(os.environ, {'PATH': f'{self.bin}{os.pathsep}{os.environ["PATH"]}'}):
            self.assertEqual(cp.codex_bin(), str(codex))

    def test_setting_wins(self):
        self.app('ChatGPT.app', self.NEW)
        with mock.patch.dict(cp.SETTINGS, {'codex_bin': '~/tools/codex'}):
            self.assertEqual(cp.codex_bin(), str(HOME / 'tools' / 'codex'))


if __name__ == '__main__':
    unittest.main()
