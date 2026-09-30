"""codexpool claude ...: install (dry run and a stubbed real run), uninstall, the claude-pool launcher and the shim
(run for real with sh, against a stand-in claude and a stand-in pool), route, status and the Claude sections of
codexpool status and doctor. Everything runs in the fake HOME of tests/_helpers.py; the launcher runs as a subprocess
with an explicit PATH that holds no real claude."""
from _helpers import addon
sienna_desktop, desktop_build = addon.desktop, addon.desktop_build

import contextlib
import http.server
import io
import json
import os
import pathlib
import socket
import subprocess
import threading
import time
import unittest
from unittest import mock

from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import HOME, ROOT, TEST_KEY, cp, preserved, run, settings_restored

assert sienna_pool.CLAUDE_LAUNCHER == HOME / '.local' / 'bin' / 'claude-pool' and sienna_pool.CLAUDE_REAL == HOME / '.local' / 'bin' / 'claude'
assert sienna_pool.CLAUDE_PORT != 8321, 'live settings'

# A stand-in for Claude Code: prints what the launcher handed it.
FAKE_CLAUDE = '''#!/bin/sh
printf '{"argv0": "%s", "args": "%s", "ANTHROPIC_BASE_URL": "%s", "ENABLE_TOOL_SEARCH": "%s", "TTL": "%s", "HELPER": "%s", "LEGACY_HELPER": "%s"}\\n' \\
  "$0" "$*" "${ANTHROPIC_BASE_URL-<unset>}" "${ENABLE_TOOL_SEARCH-<unset>}" "${CLAUDE_CODE_PROMPT_CACHE_TTL-<unset>}" \\
  "${ANTHROPIC_DEFAULT_HAIKU_MODEL-<unset>}" "${ANTHROPIC_SMALL_FAST_MODEL-<unset>}"
'''
SYSTEM_PATH = '/usr/bin:/bin:/usr/sbin:/sbin'  # curl, head, grep, sed; no claude
FORBIDDEN = ('ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'apiKeyHelper', 'CLAUDE_CONFIG_DIR',
             'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC')


def write_exe(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    path.chmod(0o755)


class PoolStandIn(http.server.BaseHTTPRequestHandler):
    """/healthz as the Claude pool's gate lets it through: only for Claude Code's User-Agent and X-App: cli."""
    def do_GET(self):
        ok = self.headers.get('User-Agent', '').startswith('claude-cli/') and self.headers.get('X-App') == 'cli'
        self.send_response(200 if ok and self.path == '/healthz' else 403)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def log_message(self, *a):
        pass


@contextlib.contextmanager
def claude_pool_up():
    server = http.server.ThreadingHTTPServer(('127.0.0.1', sienna_pool.CLAUDE_PORT), PoolStandIn)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield
    finally:
        server.shutdown()
        server.server_close()


@contextlib.contextmanager
def claude_pool_hangs():
    """Something that accepts connections on the port and never answers."""
    s = socket.socket()
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    s.bind(('127.0.0.1', sienna_pool.CLAUDE_PORT))
    s.listen(8)
    try:
        yield
    finally:
        s.close()


class Launcher(unittest.TestCase):
    def setUp(self):
        self.addCleanup(self.cleanup)
        cp.write_script(sienna_pool.CLAUDE_LAUNCHER, sienna_pool.launcher_text())
        write_exe(sienna_pool.CLAUDE_REAL, FAKE_CLAUDE)

    def cleanup(self):
        for p in (sienna_pool.CLAUDE_LAUNCHER, sienna_pool.CLAUDE_REAL, sienna_pool.CLAUDE_SHIM, sienna_pool.CLAUDE_ROUTE_FILE):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()

    def launch(self, script=None, path=SYSTEM_PATH, **env):
        clean = {k: v for k, v in os.environ.items()
                 if not k.startswith(('ANTHROPIC_', 'CLAUDE')) and k not in ('ENABLE_TOOL_SEARCH',)}
        clean.update(PATH=path, HOME=str(HOME), **env)
        r = subprocess.run([str(script or sienna_pool.CLAUDE_LAUNCHER), 'one', 'two'], capture_output=True, text=True,
                           env=clean, timeout=20)
        out = json.loads(r.stdout) if r.stdout.strip().startswith('{') else None
        return r, out

    def test_text(self):
        text = sienna_pool.launcher_text()
        self.assertTrue(text.startswith(f'#!/bin/sh\n{sienna_pool.CLAUDE_LAUNCHER_MARK}\n'))
        self.assertIn(f"\npool_url=http://127.0.0.1:{sienna_pool.CLAUDE_PORT}\n", text)
        self.assertIn('-m 0.3 ', text)
        for name in FORBIDDEN:
            self.assertNotIn(name, text)
        exported = sorted(line.split('export ')[1].split('=')[0] for line in text.splitlines() if 'export ' in line)
        self.assertEqual(exported, ['ANTHROPIC_BASE_URL', 'ANTHROPIC_DEFAULT_HAIKU_MODEL',
                                    'CLAUDE_CODE_PROMPT_CACHE_TTL', 'ENABLE_TOOL_SEARCH'])
        for name in ('ANTHROPIC_DEFAULT_OPUS_MODEL', 'ANTHROPIC_DEFAULT_FABLE_MODEL', 'ANTHROPIC_SMALL_FAST_MODEL'):
            self.assertNotIn(name, text)
        self.assertEqual(subprocess.run(['sh', '-n', str(sienna_pool.CLAUDE_LAUNCHER)]).returncode, 0)
        self.assertEqual(oct(sienna_pool.CLAUDE_LAUNCHER.stat().st_mode & 0o777), '0o755')

    def test_pool_up(self):
        with claude_pool_up():
            r, out = self.launch()
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertEqual(out, {'argv0': str(sienna_pool.CLAUDE_REAL), 'args': 'one two',
                               'ANTHROPIC_BASE_URL': f'http://127.0.0.1:{sienna_pool.CLAUDE_PORT}',
                               'ENABLE_TOOL_SEARCH': 'true', 'TTL': '1h', 'HELPER': 'claude-sonnet-5',
                               'LEGACY_HELPER': '<unset>'})

    def test_your_own_values_win(self):
        with claude_pool_up():
            _, out = self.launch(ENABLE_TOOL_SEARCH='auto:5', CLAUDE_CODE_PROMPT_CACHE_TTL='5m',
                                 ANTHROPIC_DEFAULT_HAIKU_MODEL='custom-helper',
                                 ANTHROPIC_SMALL_FAST_MODEL='legacy-helper')
        self.assertEqual((out['ENABLE_TOOL_SEARCH'], out['TTL']), ('auto:5', '5m'))
        self.assertEqual((out['HELPER'], out['LEGACY_HELPER']), ('custom-helper', 'legacy-helper'))

    def test_empty_helper_uses_default_in_launcher_and_shim(self):
        cp.write_script(sienna_pool.CLAUDE_SHIM, sienna_pool.shim_text())
        with claude_pool_up():
            for script in (sienna_pool.CLAUDE_LAUNCHER, sienna_pool.CLAUDE_SHIM):
                with self.subTest(script=script):
                    r, out = self.launch(script, ANTHROPIC_DEFAULT_HAIKU_MODEL='')
                    self.assertEqual((r.returncode, out['HELPER']), (0, 'claude-sonnet-5'))

    def test_pool_down_runs_direct_with_one_warning(self):
        r, out = self.launch()
        self.assertEqual(r.returncode, 0)
        self.assertEqual(out['ANTHROPIC_BASE_URL'], '<unset>')
        self.assertEqual(out['ENABLE_TOOL_SEARCH'], '<unset>')
        self.assertEqual(out['HELPER'], '<unset>')
        self.assertEqual(r.stderr, f'claude-pool: the Claude pool does not answer on 127.0.0.1:{sienna_pool.CLAUDE_PORT} '
                                   '(codexpool claude status); starting Claude Code direct, on its own account\n')

    def test_a_pool_that_hangs_costs_300_ms(self):
        with claude_pool_hangs():
            t = time.monotonic()
            r, out = self.launch()
            took = time.monotonic() - t
        self.assertEqual(out['ANTHROPIC_BASE_URL'], '<unset>')
        self.assertIn('does not answer', r.stderr)
        self.assertLess(took, 2.0)

    def test_required_pool_down_never_execs(self):
        sienna_pool.write_claude_route('direct')
        r, out = self.launch(CLAUDEPOOL='required')
        self.assertEqual((r.returncode, r.stdout, out), (75, '', None))
        self.assertEqual(len(r.stderr.splitlines()), 1)
        self.assertIn('pool required, Claude Code was not started', r.stderr)
        self.assertNotIn('starting Claude Code direct', r.stderr)

    def test_required_ignores_direct_route(self):
        sienna_pool.write_claude_route('direct')
        with claude_pool_up():
            r, out = self.launch(CLAUDEPOOL='required')
        self.assertEqual((r.returncode, r.stderr), (0, ''))
        self.assertEqual(out['ANTHROPIC_BASE_URL'], f'http://127.0.0.1:{sienna_pool.CLAUDE_PORT}')

    def test_required_hanging_pool_never_execs(self):
        with claude_pool_hangs():
            r, out = self.launch(CLAUDEPOOL='required')
        self.assertEqual((r.returncode, r.stdout, out), (75, '', None))

    def test_required_probe_results_without_sockets(self):
        """Run the real shell script, stubbing only curl; useful even where loopback binds are denied."""
        curl = HOME / 'probe-bin' / 'curl'
        self.addCleanup(lambda: curl.unlink(missing_ok=True))
        sienna_pool.write_claude_route('direct')
        for code in ('000', '302', '403', '404', '500', '200'):
            with self.subTest(code=code):
                write_exe(curl, f'#!/bin/sh\nprintf "{code}"\n')
                r, out = self.launch(path=f'{curl.parent}:{SYSTEM_PATH}', CLAUDEPOOL='required')
                self.assertEqual(r.returncode, 0 if code == '200' else 75)
                if code == '200':
                    self.assertEqual(out['ANTHROPIC_BASE_URL'], f'http://127.0.0.1:{sienna_pool.CLAUDE_PORT}')
                else:
                    self.assertEqual((r.stdout, out), ('', None))
                    self.assertEqual(len(r.stderr.splitlines()), 1)

    def test_an_inherited_pool_url_goes_when_direct(self):
        """claude -p inside a pooled session inherits ANTHROPIC_BASE_URL; direct must really be direct."""
        r, out = self.launch(ANTHROPIC_BASE_URL=f'http://127.0.0.1:{sienna_pool.CLAUDE_PORT}')
        self.assertEqual(out['ANTHROPIC_BASE_URL'], '<unset>')
        _, out = self.launch(ANTHROPIC_BASE_URL='https://gateway.example', CLAUDEPOOL='off')
        self.assertEqual(out['ANTHROPIC_BASE_URL'], 'https://gateway.example')  # not ours: left alone

    def test_claudepool_off(self):
        with claude_pool_up():
            r, out = self.launch(CLAUDEPOOL='off')
        self.assertEqual((r.returncode, r.stderr, out['ANTHROPIC_BASE_URL']), (0, '', '<unset>'))
        self.assertEqual(out['HELPER'], '<unset>')

    def test_route_direct(self):
        sienna_pool.write_claude_route('direct')
        with claude_pool_up():
            r, out = self.launch()
        self.assertEqual(out['ANTHROPIC_BASE_URL'], '<unset>')
        self.assertEqual(out['HELPER'], '<unset>')
        self.assertEqual(r.stderr, 'claude-pool: the route is direct (codexpool claude route pool sends new sessions '
                                   'through the pool); starting Claude Code direct, on its own account\n')
        sienna_pool.write_claude_route('pool')
        with claude_pool_up():
            _, out = self.launch()
        self.assertEqual(out['ANTHROPIC_BASE_URL'], f'http://127.0.0.1:{sienna_pool.CLAUDE_PORT}')

    def test_never_itself(self):
        """Without ~/.local/bin/claude it takes the first claude on PATH, skipping the shim, the shims directory and
        any copy of a codexpool launcher; with none left it stops instead of looping."""
        sienna_pool.CLAUDE_REAL.unlink()
        cp.write_script(sienna_pool.CLAUDE_SHIM, sienna_pool.shim_text())
        copy = HOME / 'elsewhere' / 'claude'
        write_exe(copy, sienna_pool.launcher_text())
        other = HOME / 'other-bin' / 'claude'
        write_exe(other, FAKE_CLAUDE)
        self.addCleanup(copy.unlink)
        self.addCleanup(other.unlink)
        path = f'{sienna_pool.SHIMS}/:{copy.parent}:{sienna_pool.CLAUDE_LAUNCHER.parent}:relative/dir:{other.parent}:{SYSTEM_PATH}'
        with claude_pool_up():
            r, out = self.launch(sienna_pool.CLAUDE_SHIM, path=path)
        self.assertEqual(out['argv0'], str(other), r.stderr)
        self.assertEqual(out['ANTHROPIC_BASE_URL'], f'http://127.0.0.1:{sienna_pool.CLAUDE_PORT}')
        r, out = self.launch(sienna_pool.CLAUDE_SHIM, path=f'{sienna_pool.SHIMS}:{copy.parent}:{SYSTEM_PATH}')
        self.assertEqual((r.returncode, out), (127, None))
        self.assertIn('claude: Claude Code (claude) is not in ~/.local/bin or on PATH', r.stderr)

    def test_a_real_claude_symlink(self):
        """Claude Code's installer makes ~/.local/bin/claude a symlink into its versions directory."""
        target = HOME / '.local' / 'share' / 'claude' / 'versions' / '9.9.9'
        write_exe(target, FAKE_CLAUDE)
        self.addCleanup(target.unlink)
        sienna_pool.CLAUDE_REAL.unlink()
        sienna_pool.CLAUDE_REAL.symlink_to(target)
        with claude_pool_up():
            _, out = self.launch()
        self.assertEqual(out['ANTHROPIC_BASE_URL'], f'http://127.0.0.1:{sienna_pool.CLAUDE_PORT}')



def now_iso(offset=0):
    return (cp.now_utc() + cp.dt.timedelta(seconds=offset)).isoformat(timespec='seconds')


CLAUDE_FILES = (sienna_pool.CLAUDE_CONFIG, sienna_pool.CLAUDE_LAUNCHER, sienna_pool.CLAUDE_SHIM, sienna_pool.CLAUDE_ROUTE_FILE, sienna_pool.CLAUDE_STATUS_FILE,
                cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist')


class ClaudeHome(unittest.TestCase):
    """A test that may create the Claude pool's files: all of them are gone again afterwards, install.json and
    settings.json are as they were."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(preserved(cp.INSTALL_FILE))
        stack.enter_context(settings_restored())
        stack.enter_context(mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY))
        self.addCleanup(self.wipe)
        self.wipe()

    def wipe(self):
        import shutil
        for p in CLAUDE_FILES:
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(sienna_pool.CLAUDE_CURRENT)
        for d in (sienna_pool.CLAUDE_AUTH, sienna_pool.CLAUDE_WORK, cp.VERSIONS):
            shutil.rmtree(d, ignore_errors=True)

    def install_plist(self):
        cp.LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
        (cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist').write_text(cp.render_plist(sienna_pool.CLAUDE_JOB, sienna_pool.CLAUDE_PLIST_TEMPLATE))


class Route(ClaudeHome):
    def test_route(self):
        self.assertEqual(run(sienna_pool.cmd_claude_route, route=None), (0, 'pool\n', ''))
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, {'generated_at': now_iso(), 'pool': {'route': 'pool'}, 'seats': []})
        code, out, _ = run(sienna_pool.cmd_claude_route, route='direct')
        self.assertEqual(code, 0)
        self.assertIn('route: direct. New Claude Code sessions start direct', out)
        self.assertEqual(sienna_pool.CLAUDE_ROUTE_FILE.read_text(), 'direct\n')
        self.assertEqual(cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})['pool']['route'], 'direct')
        self.assertEqual(run(sienna_pool.cmd_claude_route, route=None), (0, 'direct\n', ''))
        run(sienna_pool.cmd_claude_route, route='pool')
        self.assertEqual(sienna_pool.claude_route(), 'pool')
        sienna_pool.CLAUDE_ROUTE_FILE.write_text('sideways\n')
        self.assertEqual(sienna_pool.claude_route(), 'pool')

    def test_parser(self):
        args = cp.build_parser().parse_args(['claude', 'route', 'direct'])
        self.assertEqual((args.claude_fn, args.route), (sienna_pool.cmd_claude_route, 'direct'))
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            cp.build_parser().parse_args(['claude', 'route', 'sideways'])


class Shim(ClaudeHome):
    def test_install_and_remove(self):
        code, out, _ = run(sienna_pool.cmd_claude_shim, action='install')
        self.assertEqual(code, 0)
        self.assertEqual(sienna_pool.CLAUDE_SHIM.read_text(), sienna_pool.shim_text())
        self.assertEqual(oct(sienna_pool.CLAUDE_SHIM.stat().st_mode & 0o777), '0o755')
        self.assertIn(f'\n  {sienna_pool.CLAUDE_PATH_LINE}', out)
        self.assertIn('never edits shell profiles', out)
        self.assertEqual(cp.read_json(cp.INSTALL_FILE, {})['claude']['shim'], str(sienna_pool.CLAUDE_SHIM))
        self.assertIn(sienna_pool.CLAUDE_SHIM_MARK, sienna_pool.shim_text())
        self.assertIn('echo "claude: $why;', sienna_pool.shim_text())
        code, out, _ = run(sienna_pool.cmd_claude_shim, action='remove')
        self.assertEqual(code, 0)
        self.assertFalse(sienna_pool.CLAUDE_SHIM.exists())
        self.assertIn(f'Remove the line {sienna_pool.CLAUDE_PATH_LINE}', out)
        self.assertNotIn('shim', cp.read_json(cp.INSTALL_FILE, {})['claude'])

    def test_a_shim_that_is_not_ours(self):
        write_exe(sienna_pool.CLAUDE_SHIM, '#!/bin/sh\necho mine\n')
        for action in ('install', 'remove'):
            code, _, err = run(sienna_pool.cmd_claude_shim, action=action)
            self.assertEqual(code, 1)
            self.assertIn('not codexpool\'s; left alone', err)
        self.assertEqual(sienna_pool.CLAUDE_SHIM.read_text(), '#!/bin/sh\necho mine\n')


def fake_build(pool, version):
    dest = cp.VERSIONS / f'v{version}-gate-{cp.gate_id(profile="claude")}'
    write_exe(dest / 'cli-proxy-api', '#!/bin/sh\nexit 1\n')
    cp.write_json(desktop_build.desktop_wire_record_path(dest.name),
                  {'build_id': dest.name, 'version': version, 'result': 'FAIL',
                   'failing_paths': ['$.headers.User-Agent'], 'reason': 'desktop wire test failed'})
    cp._point_current(dest, pool.link)
    return dest


GOOD_GATE = [('Claude Code request', 200, 200), ('other client', 403, 403), ('browser request', 403, 403),
             ('app://- origin', 403, 403)]


class Install(ClaudeHome):
    def setUp(self):
        super().setUp()
        cp.SETTINGS['claude_cpa'] = '7.3.20'
        gate = cp.CODE_DIR / 'build' / 'codexpool_gate.go'
        cp.GATE_SOURCE.parent.mkdir(parents=True, exist_ok=True)
        if not cp.GATE_SOURCE.exists():
            cp.GATE_SOURCE.write_bytes(gate.read_bytes())
            self.addCleanup(cp.GATE_SOURCE.unlink)

    def test_dry_run_changes_nothing(self):
        before = cp.INSTALL_FILE.read_bytes() if cp.INSTALL_FILE.exists() else None
        with mock.patch.object(cp, 'build_for') as build, mock.patch.object(cp, 'load_agent') as load:
            code, out, err = run(sienna_pool.cmd_claude_install, dry_run=True)
        self.assertEqual((code, err), (0, ''), out)
        build.assert_not_called()
        load.assert_not_called()
        for p in CLAUDE_FILES + (sienna_pool.CLAUDE_AUTH, sienna_pool.CLAUDE_LOGS, sienna_pool.CLAUDE_CURRENT):
            self.assertFalse(p.exists(), p)
        self.assertEqual(cp.INSTALL_FILE.read_bytes() if cp.INSTALL_FILE.exists() else None, before)
        self.assertIn('codexpool claude install --dry-run: nothing is changed', out)
        for n in range(1, 7):
            self.assertIn(f'[{n}/6] ', out)
        self.assertIn(f'[dry-run] build (or reuse) CLIProxyAPI v7.3.20 with build/codexpool_gate.go', out)
        self.assertIn('bin/current is left alone', out)
        self.assertIn(f'port {sienna_pool.CLAUDE_PORT}, api-keys: []', out)
        self.assertIn('(mode 700, one sign-in per Claude account)', out)
        self.assertIn('CODEXPOOL_GATE_PROFILE=claude', out)
        self.assertIn('a Claude Code request must get 200', out)
        self.assertIn(f'write {cp.tilde(sienna_pool.CLAUDE_LAUNCHER)}', out)
        self.assertIn('Dry run: nothing was changed.', out)
        for name in FORBIDDEN[:2]:
            self.assertNotIn(name, out)

    def test_a_gate_source_from_before_the_claude_pool(self):
        saved = cp.GATE_SOURCE.read_bytes()
        self.addCleanup(cp.GATE_SOURCE.write_bytes, saved)
        cp.GATE_SOURCE.write_text('package main // 1.2.0: no profiles\n')
        code, out, _ = run(sienna_pool.cmd_claude_install, dry_run=True)
        self.assertEqual(code, 1)
        self.assertIn(f'✗ {cp.tilde(cp.GATE_SOURCE)} has no claude gate profile (it predates the Claude pool)', out)
        self.assertIn('would stop a real install', out)
        with mock.patch.object(cp, 'build_for') as build:
            code, _, err = run(sienna_pool.cmd_claude_install, dry_run=False)
        self.assertEqual(code, 1)
        build.assert_not_called()
        self.assertIn('update codexpool first', err)

    def test_needs_codexpool_installed(self):
        with mock.patch.object(cp, 'missing_install', return_value=['~/.codexpool/config.yaml']):
            code, _, err = run(sienna_pool.cmd_claude_install, dry_run=True)
        self.assertEqual(code, 1)
        self.assertIn('Run codexpool install first', err)

    def real_install(self, gate=GOOD_GATE):
        loaded = {'yes': False}

        def load(label, plist, text, replace):
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text(text)
            loaded['yes'] = True
        with mock.patch.object(cp, 'build_for', side_effect=fake_build) as build, \
                mock.patch.object(cp, 'load_agent', side_effect=load) as agent, \
                mock.patch.object(cp, 'launchd_loaded', side_effect=lambda label: (loaded['yes'], 4242 if loaded['yes'] else None)), \
                mock.patch.object(cp, 'wait_port_healthy', return_value=True), \
                mock.patch.object(cp, 'gate_probe_cases', return_value=gate), \
                mock.patch.object(cp, 'kick_agent') as kick:
            result = run(sienna_pool.cmd_claude_install, dry_run=False)
            second = run(sienna_pool.cmd_claude_install, dry_run=False) if result[0] == 0 else None
        return result, second, build, agent, kick

    def test_install_then_again(self):
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, {'generated_at': now_iso(), 'pool': {'installed': False}, 'seats': []})
        (code, out, err), second, build, agent, kick = self.real_install()
        self.assertEqual((code, err), (0, ''), out)
        self.assertEqual(build.call_count, 1)
        self.assertEqual(cp.read_json(desktop_build.desktop_wire_record_path('v' + cp.cpa_version(sienna_pool.CLAUDE_CURRENT)), {})['result'], 'FAIL')
        self.assertEqual(agent.call_count, 1)
        kick.assert_not_called()
        self.assertEqual(cp.cpa_version(sienna_pool.CLAUDE_CURRENT), f'7.3.20-gate-{cp.gate_id(profile="claude")}')
        self.assertEqual(os.readlink(cp.CURRENT) if cp.CURRENT.is_symlink() else None, None)  # the Codex pool's
        cfg = sienna_pool.CLAUDE_CONFIG.read_text()
        self.assertIn(f'\nport: {sienna_pool.CLAUDE_PORT}\n', '\n' + cfg)
        self.assertIn(TEST_KEY, cfg)
        self.assertEqual(oct(sienna_pool.CLAUDE_CONFIG.stat().st_mode & 0o777), '0o600')
        self.assertEqual(oct(sienna_pool.CLAUDE_AUTH.stat().st_mode & 0o777), '0o700')
        self.assertTrue(sienna_pool.CLAUDE_LOGS.is_dir())
        self.assertEqual(sienna_pool.CLAUDE_ROUTE_FILE.read_text(), 'pool\n')
        self.assertEqual(sienna_pool.CLAUDE_LAUNCHER.read_text(), sienna_pool.launcher_text())
        self.assertTrue(sienna_pool.claude_installed())
        rec = cp.read_json(cp.INSTALL_FILE, {})['claude']
        self.assertEqual((rec['agent'], rec['port'], rec['build'], rec['launcher']),
                         (sienna_pool.CLAUDE_JOB, sienna_pool.CLAUDE_PORT, f'7.3.20-gate-{cp.gate_id(profile="claude")}', str(sienna_pool.CLAUDE_LAUNCHER)))
        self.assertEqual(cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})['pool'], {'installed': True, 'route': 'pool'})
        self.assertIn('codexpool claude login "<Label>" --priority <n>', out)
        self.assertIn('Start Claude Code through the pool:  claude-pool', out)
        # the second run finds everything in place
        code, out, err = second
        self.assertEqual((code, err), (0, ''), out)
        self.assertEqual((build.call_count, agent.call_count), (1, 1))
        kick.assert_not_called()
        changes = [line for line in out.splitlines() if line.startswith('  + ')]
        self.assertEqual(changes, ['  + record in ' + cp.tilde(cp.INSTALL_FILE) + f': the agent {sienna_pool.CLAUDE_JOB}, '
                                   f'port {sienna_pool.CLAUDE_PORT}, the build, the launcher'])
        self.assertIn(f'✓ bin/claude-current → 7.3.20-gate-{cp.gate_id(profile="claude")}', out)
        self.assertIn(f'✓ {sienna_pool.CLAUDE_JOB} loaded (pid 4242)', out)
        self.assertIn('✓ origin gate (claude profile): Claude Code request 200, other client 403', out)

    def test_a_new_version_or_gate_restarts_the_pool(self):
        (code, out, _), _, build, _, kick = self.real_install()
        self.assertEqual(code, 0, out)
        cp.SETTINGS['claude_cpa'] = '7.3.21'
        loaded = (True, 1)
        with mock.patch.object(cp, 'build_for', side_effect=fake_build), \
                mock.patch.object(cp, 'launchd_loaded', return_value=loaded), \
                mock.patch.object(cp, 'wait_port_healthy', return_value=True), \
                mock.patch.object(cp, 'gate_probe_cases', return_value=GOOD_GATE), \
                mock.patch.object(cp, 'kick_agent') as kick:
            code, out, _ = run(sienna_pool.cmd_claude_install, dry_run=False)
        self.assertEqual(code, 0, out)
        kick.assert_called_once_with(sienna_pool.CLAUDE_JOB)
        self.assertEqual(cp.cpa_version(sienna_pool.CLAUDE_CURRENT), f'7.3.21-gate-{cp.gate_id(profile="claude")}')
        self.assertIn('(it runs 7.3.20-gate-', out)

    def test_a_gate_that_lets_others_in_stops_the_pool(self):
        calls = HOME / 'stub-calls.log'
        before = calls.read_text() if calls.exists() else ''
        bad = [('Claude Code request', 200, 200), ('other client', 200, 403), ('browser request', 403, 403),
               ('app://- origin', 200, 403)]
        (code, out, err), _, _, _, _ = self.real_install(bad)
        self.assertEqual(code, 1)
        self.assertIn('claude install stopped: the Claude pool\'s gate does not keep other clients out (Claude Code '
                      'request 200, other client 200, browser request 403, app://- origin 200), so it was stopped', err)
        self.assertIn(f'launchctl bootout gui/{cp.UID}/{sienna_pool.CLAUDE_JOB}', calls.read_text()[len(before):])
        self.assertFalse(sienna_pool.CLAUDE_LAUNCHER.exists())
        # its plist is gone too: launchd does not start it at the next login, and the guard's Claude pass stays off
        self.assertFalse((cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist').exists())
        self.assertFalse(sienna_pool.claude_installed())

    def test_someone_elses_launcher_is_left_alone(self):
        write_exe(sienna_pool.CLAUDE_LAUNCHER, '#!/bin/sh\necho mine\n')
        (code, out, _), _, _, _, _ = self.real_install()
        self.assertEqual(code, 0, out)
        self.assertIn('claude-pool exists and is not codexpool\'s; left alone', out)
        self.assertEqual(sienna_pool.CLAUDE_LAUNCHER.read_text(), '#!/bin/sh\necho mine\n')
        self.assertNotIn('launcher', cp.read_json(cp.INSTALL_FILE, {})['claude'])


class Uninstall(ClaudeHome):
    def setUp(self):
        super().setUp()
        self.install_plist()
        cp.write_script(sienna_pool.CLAUDE_LAUNCHER, sienna_pool.launcher_text())
        cp.write_script(sienna_pool.CLAUDE_SHIM, sienna_pool.shim_text())
        sienna_pool.write_claude_route('direct')
        sienna_pool.claude_make_dirs()
        (sienna_pool.CLAUDE_AUTH / 'claude-a@test.json').write_text('{}')
        sienna_pool.CLAUDE_CONFIG.write_text('port: 1\n')
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, {'generated_at': now_iso(), 'pool': {'installed': True}, 'seats': []})

    def test_plan_then_yes(self):
        code, out, _ = run(sienna_pool.cmd_claude_uninstall, yes=False)
        self.assertEqual(code, 0)
        for text in (f'stop the launchd agent {sienna_pool.CLAUDE_JOB}', f'remove {cp.tilde(sienna_pool.CLAUDE_LAUNCHER)}',
                     f'remove {cp.tilde(sienna_pool.CLAUDE_SHIM)}', f'remove {cp.tilde(sienna_pool.CLAUDE_ROUTE_FILE)}',
                     f'keep {cp.tilde(sienna_pool.CLAUDE_AUTH)} (the Claude account logins)', 'Re-run with --yes.'):
            self.assertIn(text, out)
        self.assertTrue(sienna_pool.claude_installed() and sienna_pool.CLAUDE_LAUNCHER.exists() and sienna_pool.CLAUDE_SHIM.exists())
        code, out, _ = run(sienna_pool.cmd_claude_uninstall, yes=True)
        self.assertEqual(code, 0)
        self.assertNotIn('✗', out)
        self.assertFalse(sienna_pool.claude_installed())
        for p in (sienna_pool.CLAUDE_LAUNCHER, sienna_pool.CLAUDE_SHIM, sienna_pool.CLAUDE_ROUTE_FILE):
            self.assertFalse(p.exists(), p)
        self.assertTrue((sienna_pool.CLAUDE_AUTH / 'claude-a@test.json').exists())
        self.assertTrue(sienna_pool.CLAUDE_CONFIG.exists())
        self.assertFalse(cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})['pool']['installed'])
        self.assertIn('uninstalled_at', cp.read_json(cp.INSTALL_FILE, {})['claude'])
        self.assertIn(f'remove the line {sienna_pool.CLAUDE_PATH_LINE} from your shell profile', out)

    def test_nothing_installed(self):
        self.wipe()
        self.assertEqual(run(sienna_pool.cmd_claude_uninstall, yes=True), (0, 'The Claude pool is not installed; nothing to do.\n', ''))

    def test_leaves_a_launcher_that_is_not_ours(self):
        write_exe(sienna_pool.CLAUDE_LAUNCHER, '#!/bin/sh\necho mine\n')
        run(sienna_pool.cmd_claude_uninstall, yes=True)
        self.assertTrue(sienna_pool.CLAUDE_LAUNCHER.exists())

    def test_codexpool_uninstall_takes_the_claude_pool_along(self):
        code, out, _ = run(cp.cmd_uninstall, yes=False)
        self.assertEqual(code, 0)
        self.assertIn(f'the Claude pool: stop the launchd agent {sienna_pool.CLAUDE_JOB}', out)
        self.assertIn(f'the Claude pool: remove {cp.tilde(sienna_pool.CLAUDE_LAUNCHER)}', out)
        self.assertIn('codexpool claude install bring it all back', out)
        self.assertTrue(sienna_pool.claude_installed())



SEAT_A = {'name': 'claude-a@test.json', 'label': 'Work A', 'state': 'active', 'priority': 10, 'plan': 'max_5x',
          'weight': 5, 'five_hour': {'used': 42, 'reset_at': None}, 'week': {'used': 12.5, 'reset_at': None},
          'scoped': [], 'credits': {'enabled': False, 'used': 0.0, 'limit': None, 'policy': 'off', 'cap': None}}
SEAT_B = {'name': 'claude-b@test.json', 'label': 'Big', 'state': 'ready', 'priority': 1, 'plan': 'max_20x',
          'weight': 20, 'reserve': True, 'five_hour': {'used': 0, 'reset_at': None},
          'week': {'used': 3, 'reset_at': None}, 'scoped': [],
          'credits': {'enabled': True, 'used': 12.0, 'limit': 500.0, 'policy': 'last-resort', 'cap': 200.0}}


def claude_status(age=0, seats=(SEAT_A, SEAT_B), **pool):
    return {'generated_at': now_iso(-age), 'pool': dict({'installed': True, 'running': True, 'route': 'pool'}, **pool),
            'seats': [dict(r) for r in seats]}


class Status(ClaudeHome):
    def test_not_installed(self):
        code, _, err = run(sienna_pool.cmd_claude_status, json=False, live=False)
        self.assertEqual(code, 1)
        self.assertIn('the Claude pool is not installed. codexpool claude install sets it up', err)
        code, out, _ = run(sienna_pool.cmd_claude_status, json=True, live=False)
        self.assertEqual((code, json.loads(out)), (0, {'pool': {'installed': False, 'route': 'pool'}, 'seats': []}))

    def test_from_the_guards_file(self):
        self.install_plist()
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, claude_status(age=30))
        code, out, _ = run(sienna_pool.cmd_claude_status, json=False, live=False)
        self.assertEqual(code, 0)
        self.assertIn('Claude pool  25× total  ·  2 of 2 accounts available  ·  route: pool  ·  new sessions → Work A  ·  (guard, ', out)
        work = next(line for line in out.splitlines() if 'Work A' in line and line.startswith('●'))
        self.assertIn('max_5x 5×', work)
        self.assertIn('42%', work)
        self.assertIn('12.5%', work)
        big = next(line for line in out.splitlines() if ' Big ' in line)
        self.assertIn('last resort, cap $200 ($12 used)', big)
        code, out, _ = run(sienna_pool.cmd_claude_status, json=True, live=False)
        self.assertEqual(json.loads(out)['seats'][1]['credits']['cap'], 200.0)

    def test_stale_file_means_a_live_look(self):
        self.install_plist()
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, claude_status(age=3600))
        code, out, _ = run(sienna_pool.cmd_claude_status, json=False, live=False)
        self.assertEqual(code, 0)
        self.assertIn(f'Claude pool is NOT running on :{sienna_pool.CLAUDE_PORT}', out)
        code, out, _ = run(sienna_pool.cmd_claude_status, json=True, live=False)
        st = json.loads(out)
        self.assertEqual((st['live'], st['pool']['running'], st['pool']['installed']), (True, False, True))

    def test_live_reads_the_claude_pools_accounts(self):
        self.install_plist()
        files = {'files': [{'name': 'codex-x.json', 'provider': 'codex'},
                           {'name': 'claude-b.json', 'type': 'claude', 'email': 'b@example.com', 'priority': 1},
                           {'name': 'claude-a.json', 'provider': 'claude', 'email': 'a@example.com', 'priority': 5,
                            'disabled': True}]}
        with mock.patch.object(cp, 'port_open', return_value=True), \
                mock.patch.object(cp, 'api', return_value=(files, {'X-CPA-VERSION': '7.3.20+gate.x'})) as api:
            st = sienna_pool.claude_status_now()
        self.assertEqual(api.call_count, 1)
        self.assertEqual(api.call_args.kwargs['port'], sienna_pool.CLAUDE_PORT)
        self.assertEqual(st['pool']['version'], '7.3.20+gate.x')
        self.assertEqual([(r['name'], r['state']) for r in st['seats']],
                         [('claude-a.json', 'disabled'), ('claude-b.json', 'active')])
        self.assertEqual((st['live'], st['active'], st['pool']['installed'], st['pool']['available']),
                         (True, st['seats'][1]['label'], True, 1))
        for r in st['seats']:   # the contract's full row, as the guard writes it
            self.assertLessEqual({'label', 'reserve', 'weight', 'plan', 'credits', 'five_hour', 'week', 'scoped'},
                                 set(r))
            self.assertEqual(set(r['credits']) & {'enabled', 'used', 'limit', 'policy', 'cap'},
                             {'enabled', 'used', 'limit', 'policy', 'cap'})
        with mock.patch.object(cp, 'port_open', return_value=True), \
                mock.patch.object(cp, 'api', return_value=(files, {})):
            code, out, _ = run(sienna_pool.cmd_claude_status, json=False, live=True)
        self.assertIn(f'1 of 2 accounts available', out)
        self.assertIn(f'new sessions → {st["seats"][1]["label"]}', out)

    def test_codexpool_status_gains_a_claude_section(self):
        with preserved(cp.STATUS_FILE):
            cp.write_json(cp.STATUS_FILE, {'generated_at': now_iso(), 'pool': {'running': False}, 'seats': [],
                                           'active': None})
            code, out, _ = run(cp.cmd_status, json=True, live=False)
            self.assertNotIn('claude', json.loads(out))
            self.install_plist()
            cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, claude_status())
            code, out, _ = run(cp.cmd_status, json=True, live=False)
            self.assertEqual(json.loads(out)['claude']['seats'][0]['label'], 'Work A')
            self.assertNotIn('claude', cp.read_json(cp.STATUS_FILE, {}))  # status.json itself is untouched
            code, out, _ = run(cp.cmd_status, json=False, live=False)
        self.assertIn('Pool is NOT running', out)
        self.assertIn('\n\nClaude pool  25× total  ·  2 of 2 accounts available', out)


class Doctor(ClaudeHome):
    def section(self, fn):
        rep = cp.DoctorReport(echo=False)
        fn(rep)
        return rep, {c['text']: c for s in rep.sections for c in s['checks']}

    def test_accounts(self):
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, claude_status(seats=(SEAT_A, SEAT_B, dict(
            SEAT_A, name='c', label='Old', state='blocked', detail='sign-in ended', credits={
                'enabled': True, 'used': 0.0, 'limit': 100.0, 'policy': 'last-resort', 'cap': None}))))
        rep, checks = self.section(sienna_pool.doctor_claude_accounts)
        self.assertEqual(rep.sections[0]['title'], 'Claude accounts')
        self.assertTrue(next(t for t in checks if t.startswith('usage polling: the guard last wrote')))
        self.assertEqual(checks['3 Claude account(s) in the pool']['status'], 'ok')
        self.assertEqual(checks['Work A: active']['status'], 'ok')
        self.assertEqual(checks['Old: blocked (sign-in ended)']['status'], 'fail')
        self.assertEqual(checks['Old: blocked (sign-in ended)']['fix'], "codexpool claude login Old --priority 10")
        self.assertEqual(checks['Big: credits as the last resort, cap $200']['status'], 'ok')
        self.assertEqual(checks['Old: credits as the last resort without a cap']['status'], 'fail')

    def test_stale_or_missing_polling(self):
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, claude_status(age=900, seats=()))
        _, checks = self.section(sienna_pool.doctor_claude_accounts)
        polling = next(c for t, c in checks.items() if t.startswith('usage polling'))
        self.assertEqual(polling['status'], 'fail')
        self.assertEqual(checks['0 Claude account(s) in the pool']['status'], 'fail')
        sienna_pool.CLAUDE_STATUS_FILE.unlink()
        _, checks = self.section(sienna_pool.doctor_claude_accounts)
        self.assertEqual(checks[f'{cp.tilde(sienna_pool.CLAUDE_STATUS_FILE)} not written yet']['status'], 'warn')

    def test_launch(self):
        cp.write_script(sienna_pool.CLAUDE_LAUNCHER, sienna_pool.launcher_text())
        write_exe(sienna_pool.CLAUDE_REAL, FAKE_CLAUDE)
        self.addCleanup(sienna_pool.CLAUDE_REAL.unlink)
        with mock.patch.object(cp, 'launchd_getenv', return_value=None):
            rep, checks = self.section(sienna_pool.doctor_claude_launch)
        self.assertEqual(rep.sections[0]['title'], 'Claude Code')
        self.assertEqual(rep.problems + rep.warnings, 0, checks)
        self.assertIn(f'{cp.tilde(sienna_pool.CLAUDE_LAUNCHER)} present', checks)
        self.assertIn(f'Claude Code: {cp.tilde(sienna_pool.CLAUDE_REAL)}', checks)
        self.assertIn('no ANTHROPIC_* variable in launchd\'s environment', checks)
        # an out-of-date launcher, and a token in launchd's environment (named, never shown)
        sienna_pool.CLAUDE_LAUNCHER.write_text(sienna_pool.launcher_text().replace(str(sienna_pool.CLAUDE_PORT), '1'))
        with mock.patch.object(cp, 'launchd_getenv', side_effect=lambda n: 'sk-secret' if n == 'ANTHROPIC_API_KEY' else None):
            rep, checks = self.section(sienna_pool.doctor_claude_launch)
        self.assertEqual(checks[f'{cp.tilde(sienna_pool.CLAUDE_LAUNCHER)} present but out of date (another port or home)']
                         ['status'], 'fail')
        bad = checks['ANTHROPIC_API_KEY set in launchd\'s environment (every app started from the Dock gets it)']
        self.assertEqual((bad['status'], bad['fix'].split(';')[0]), ('warn', 'launchctl unsetenv ANTHROPIC_API_KEY'))
        self.assertNotIn('sk-secret', json.dumps(rep.as_json()))

    def test_helper_model_in_launchd_is_reported_without_its_value(self):
        name = 'ANTHROPIC_DEFAULT_HAIKU_MODEL'
        with mock.patch.object(cp, 'launchd_getenv', side_effect=lambda n: 'private-model' if n == name else None):
            rep, checks = self.section(sienna_pool.doctor_claude_launch)
        bad = checks[f'{name} set in launchd\'s environment (every app started from the Dock gets it)']
        self.assertEqual((bad['status'], bad['fix'].split(';')[0]), ('warn', f'launchctl unsetenv {name}'))
        self.assertNotIn('private-model', json.dumps(rep.as_json()))

    def test_launchd_getenv_asks_launchctl(self):
        calls = HOME / 'stub-calls.log'
        before = calls.read_text() if calls.exists() else ''
        self.assertIsNone(cp.launchd_getenv('ANTHROPIC_BASE_URL'))  # the stub fails: nothing set
        self.assertIn('launchctl getenv ANTHROPIC_BASE_URL', calls.read_text()[len(before):])

    def test_log(self):
        sienna_pool.CLAUDE_LOGS.mkdir(parents=True, exist_ok=True)
        stamp = cp.dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        lines = [
            f'[{stamp}] [abc] [error] [claude_executor.go:12] upstream 400: Invalid `signature` in `thinking` block',
            f'[{stamp}] [abc] [warning] [codexpool_gate.go:60] codexpool gate: rejected client "GET" "/v1/models" '
            f'profile="claude" user-agent="{sienna_pool.PROBE_UA}" x-app=""',
            f'[{stamp}] [abc] [warning] [codexpool_gate.go:52] codexpool gate: rejected "GET" "/v1/models" '
            f'host="127.0.0.1" origin="app://-" sec-fetch-site=""',
            f'[{stamp}] [abc] [warning] [codexpool_gate.go:52] codexpool gate: rejected "GET" "/v1/messages" '
            f'host="127.0.0.1" origin="https://evil.example" sec-fetch-site="cross-site"',
            f'[{stamp}] [abc] [info] [gin_logger.go:1] GET /bound to a different conversation',
        ]
        sienna_pool.CLAUDE_MAIN_LOG.write_text('\n'.join(lines) + '\n')
        _, checks = self.section(sienna_pool.doctor_claude_log)
        sig = next(c for t, c in checks.items() if 'thinking-signature' in t)
        self.assertEqual((sig['status'], sig['text'].split(' (')[0]), ('warn', '1 thinking-signature rejection(s)'))
        self.assertEqual(checks['1 requests from other clients or browsers blocked by the gate']['status'], 'warn')
        sienna_pool.CLAUDE_MAIN_LOG.write_text(lines[1] + '\n')
        rep, checks = self.section(sienna_pool.doctor_claude_log)
        self.assertEqual((rep.problems, rep.warnings), (0, 0), checks)

    def test_unrecognised_token_counter_has_an_update_hint(self):
        sienna_pool.CLAUDE_LOGS.mkdir(parents=True, exist_ok=True)
        stamp = cp.dt.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        for source in ('codexpool_gate.go', 'codexpool_gate_sienna.go'):
            sienna_pool.CLAUDE_MAIN_LOG.write_text(f'[{stamp}] [abc] [warning] [{source}:100] '
                'codexpool gate: rejected unrecognised token-count client user-agent="claude-cli/99.0.0" x-app="cli"\n')
            _, checks = self.section(sienna_pool.doctor_claude_log)
            check = checks['Claude Code token-count client is not recognised by this pool']
            self.assertEqual(check['status'], 'warn')
            self.assertIn('update codexpool or run direct', check['fix'])
            self.assertFalse(any('other clients or browsers' in text for text in checks))

    def test_doctor_json_has_the_claude_sections(self):
        self.install_plist()
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, claude_status())
        rep = cp.DoctorReport(echo=False)
        with mock.patch.object(cp, 'api', side_effect=cp.PoolDown('not in this test')), \
                mock.patch.object(cp, 'launchd_getenv', return_value=None):
            cp.doctor_checks(rep)
        titles = [s['title'] for s in rep.as_json()['sections']]
        self.assertEqual([title for title in titles if title != 'Add-ons'][-5:], ['Claude pool', 'Claude accounts', 'Claude Code', 'Desktop',
                                       'Recent Claude pool errors (last 24h of logs)'])


if __name__ == '__main__':
    unittest.main()


class Settings(unittest.TestCase):
    def assertRefused(self, raw, *texts):
        with mock.patch.object(cp, 'ADDONS', []):
            settings = cp.parse_settings(raw)
        with self.assertRaises(ValueError) as ctx:
            cp.check_addon_settings(addon, settings)
        for text in texts:
            self.assertIn(text, str(ctx.exception))

    def test_claude_defaults(self):
        s = cp.parse_settings({})
        self.assertEqual((s['claude_label'], s['claude_port'], s['claude_balancing'], s['claude_cpa']),
                         ('com.codexpool.claude', 8321, 'priority', None))


    def test_claude_port_must_differ_from_port_and_bridge_port(self):
        for raw in ({'claude_port': 8319}, {'claude_port': 8320}, {'port': 9000, 'claude_port': 9000},
                    {'bridge_port': 9001, 'claude_port': 9001}):
            self.assertRefused(raw, 'claude_port must differ')
        s = cp.parse_settings({'port': 9000, 'bridge_port': 9001, 'claude_port': 9002})
        self.assertEqual((s['port'], s['bridge_port'], s['claude_port']), (9000, 9001, 9002))
        for bad in (0, 65536, '8321', 8321.0, True, None):
            self.assertRefused({'claude_port': bad}, 'invalid claude_port')


    def test_claude_port_left_out_moves_past_the_other_two(self):
        # a pool on 8320 has its bridge on 8321 (above), so the Claude pool takes 8322; set explicitly, it is refused
        self.assertEqual(cp.parse_settings({'port': 8320})['claude_port'], 8322)
        self.assertEqual(cp.parse_settings({'bridge_port': 8321})['claude_port'], 8322)
        self.assertEqual(cp.parse_settings({'port': 8321, 'bridge_port': 8322})['claude_port'], 8323)
        self.assertEqual(cp.parse_settings({'port': 9000})['claude_port'], 8321)
        self.assertRefused({'port': 8320, 'claude_port': 8321}, 'claude_port must differ')


    def test_claude_label(self):
        self.assertEqual(cp.parse_settings({'claude_label': 'com.example.claude'})['claude_label'], 'com.example.claude')
        for bad in ('', 'has space', '-lead', None, 5):
            self.assertRefused({'claude_label': bad}, 'invalid claude_label')
        for key in ('pool_label', 'guard_label', 'menubar_label', 'bridge_label'):
            self.assertRefused({key: 'com.codexpool.claude'}, 'claude_label must differ')


    def test_claude_balancing(self):
        self.assertEqual(cp.parse_settings({'claude_balancing': 'reset'})['claude_balancing'], 'reset')
        self.assertEqual(cp.parse_settings({'claude_balancing': 'reset'})['balancing'], 'priority')  # the two pools are separate
        for bad in ('soonest', 'Reset', None, 1):
            self.assertRefused({'claude_balancing': bad}, 'invalid claude_balancing')


    def test_claude_cpa(self):
        for good in ('7.3.18', '7.3.20', 'v7.3.19', '7.4.0', '8.0.2', '10.0.0'):
            self.assertEqual(cp.parse_settings({'claude_cpa': good})['claude_cpa'], good)
        self.assertIsNone(cp.parse_settings({'claude_cpa': None})['claude_cpa'])
        for bad in ('7.3.17', '7.2.99', '6.9.30', 'latest', '7.3', '7.3.18-rc1', '', 7318, True):
            self.assertRefused({'claude_cpa': bad}, 'claude_cpa (null, or a CLIProxyAPI version 7.3.18 or later)')
        self.assertEqual((sienna_pool.parse_cpa_version('v7.3.20'), sienna_pool.parse_cpa_version('7.3.17')), ((7, 3, 20), None))
