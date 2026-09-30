"""The Claude pool's groundwork: its launchd template, config-claude.yaml, the gate's claude profile (the Go source,
the gate self-test harness and doctor's probes, against a fake gated CLIProxyAPI), and its own build link
(bin/claude-current) next to the Codex pool's bin/current."""
import contextlib
import json
import os
import pathlib
import plistlib
import re
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest import mock

from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import HOME, REPO, ROOT, TEST_KEY, cp, settings_restored

# A stand-in for a CLIProxyAPI build with the gate: it serves /v1/models, /v1/responses (WebSocket upgrade: 101) and
# the management auth-files call (with its key), and in front of them applies the rules of build/codexpool_gate.go
# for the profile in CODEXPOOL_GATE_PROFILE. FAKE_GATE_IGNORE_PROFILE=1 plays a build from before the claude
# profile existed (every profile acts as the Codex pool's); FAKE_CPA_VERSION is the X-CPA-VERSION it reports.
FAKE_GATED_CPA = '''#!{python}
import http.server, os, re, sys
args = sys.argv[1:]
config = open(args[args.index('-config') + 1]).read()
port = int(re.search(r'(?m)^port: (\\d+)', config).group(1))
key = re.search(r'secret-key: "([^"]*)"', config).group(1)
profile = os.environ.get('CODEXPOOL_GATE_PROFILE', '').strip()
if os.environ.get('FAKE_GATE_IGNORE_PROFILE'):
    profile = ''
version = os.environ.get('FAKE_CPA_VERSION', '7.3.18+gate.fake')

def clean(path):
    parts = []
    for part in path.split('?')[0].split('/'):
        if part == '..':
            parts = parts[:-1]
        elif part and part != '.':
            parts.append(part)
    return '/' + '/'.join(parts)

class Handler(http.server.BaseHTTPRequestHandler):
    def allowed(self):
        origin, site = self.headers.get('Origin', ''), self.headers.get('Sec-Fetch-Site', '')
        from_app = origin == 'app://-' and profile == ''
        browser = (origin and not from_app) or (site and site != 'none' and not from_app)
        host = (self.headers.get('Host') or '').lower()
        host = host.rsplit(':', 1)[0] if host.count(':') == 1 else host
        if host.strip('[]') not in ('127.0.0.1', 'localhost', '::1') or browser:
            return False
        if profile == '':
            return True
        if profile == 'claude':
            p = self.path.split('?')[0]   # as gin routes it: management only when the path is that, and clean
            if p == '/v1/messages/count_tokens':
                return False  # self-test sends only unrecognised token-count clients
            return ((p == '/v0/management' or p.startswith('/v0/management/')) and clean(p) == p or
                    (self.headers.get('User-Agent', '').startswith('claude-cli/') and self.headers.get('X-App') == 'cli'))
        return False

    def answer(self, code, extra=()):
        self.send_response(code)
        for k, v in extra:
            self.send_header(k, v)
        self.send_header('Content-Length', '2')
        self.end_headers()
        self.wfile.write(b'{{}}')

    def do_GET(self):
        if not self.allowed():
            return self.answer(403)
        path = self.path.split('?')[0]
        if path == '/v1/responses' and self.headers.get('Upgrade', '').lower() == 'websocket':
            self.send_response(101)
            self.send_header('Upgrade', 'websocket')
            self.send_header('Connection', 'Upgrade')
            self.end_headers()
            return
        if path == '/v1/models':
            return self.answer(200)
        if path == '/v0/management/auth-files':
            if self.headers.get('Authorization') != 'Bearer ' + key:
                return self.answer(401)
            return self.answer(200, [('X-CPA-VERSION', version)])
        return self.answer(404)

    def do_OPTIONS(self):
        return self.answer(403 if not self.allowed() else 204)

    def do_POST(self):
        return self.answer(403 if not self.allowed() else 404)

    def log_message(self, *a):
        pass

http.server.ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
'''


def fake_gated_cpa():
    path = HOME / 'fake-gated-cpa'
    if not path.exists():
        path.write_text(FAKE_GATED_CPA.format(python=sys.executable))
        path.chmod(0o755)
    return path


@contextlib.contextmanager
def running_fake(port, profile=None, **env):
    """The fake gated CLIProxyAPI on port with the given gate profile (and FAKE_* settings), as a pool would run it."""
    work = pathlib.Path(tempfile.mkdtemp(prefix='fake-gated-', dir=HOME))
    (work / 'config.yaml').write_text(f'port: {port}\nremote-management: {{secret-key: "{TEST_KEY}"}}\n')
    full = {**cp.gate_env(profile), **env}
    proc = subprocess.Popen([str(fake_gated_cpa()), '-config', str(work / 'config.yaml')], env=full,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        deadline = time.time() + 10
        while time.time() < deadline and not cp.port_open(port):
            time.sleep(0.05)
        yield proc
    finally:
        proc.terminate()
        proc.wait(10)


def installed_gate():
    """The installed gate source (ROOT/build/codexpool_gate.go) as the repo has it, for gate_id()."""
    cp.GATE_SOURCE.parent.mkdir(parents=True, exist_ok=True)
    cp.GATE_SOURCE.write_bytes((REPO / 'build' / 'codexpool_gate.go').read_bytes())
    return cp.gate_id(profile="claude")


class GateSource(unittest.TestCase):
    """build/codexpool_gate.go: the claude profile is an extra rule on top of today's, and the gate still only
    accepts or rejects (AGENTS.md invariant 1)."""

    src = ((REPO / 'build' / 'codexpool_gate.go').read_text() +
           (addon.path / 'gate/codexpool_gate_sienna.go').read_text())

    def test_profile_comes_from_the_environment(self):
        self.assertIn('os.Getenv("CODEXPOOL_GATE_PROFILE")', self.src)
        self.assertIn('codexpoolRegisterProfile("claude",', self.src)
        self.assertIn('strings.HasPrefix(r.Header.Get("User-Agent"), "claude-cli/")', self.src)
        self.assertIn('r.Header.Get("X-App") == "cli"', self.src)
        self.assertNotIn('r.Header.Get("X-App") == "cli-bg"', self.src)

    def test_management_is_exempt_only_on_its_own_clean_path(self):
        # gin routes on the path as sent: a cleaned path would let /v1beta/models/../../v0/management/… through
        self.assertIn('return codexpoolManagementPath(r.URL.Path) ||', self.src)
        self.assertIn('&& path.Clean(p) == p', self.src)
        self.assertNotIn('path.Clean("/" + r.URL.Path)', self.src)

    def test_app_origin_is_the_codex_pools_only(self):
        self.assertIn('fromApp := origin == codexpoolAppOrigin && codexpoolProfile == ""', self.src)

    def test_unknown_profile_fails_closed(self):
        self.assertIn('if !registered || !profile.allowed(r) {', self.src)

    def test_only_accepts_or_rejects(self):
        for forbidden in ('Header.Set(', 'Header.Del(', 'Header.Add(', '.Body', 'c.Set(', 'c.Redirect'):
            self.assertNotIn(forbidden, self.src)
        self.assertNotRegex(self.src, r'URL\.Path\s*=(?!=)')
        self.assertEqual(self.src.count('c.Next()'), 1)
        self.assertEqual(self.src.count('c.AbortWithStatusJSON(http.StatusForbidden'), 3)

    def test_default_profile_rules_unchanged(self):
        # the 1.2.0 browser/Host rule and its log line are still there, word for word
        self.assertIn('browser := (origin != "" && !fromApp) || (site != "" && site != "none" && !fromApp)', self.src)
        self.assertIn('if !codexpoolLoopbackHost(r.Host) || browser {', self.src)
        self.assertIn('"codexpool gate: rejected %q %q host=%q origin=%q sec-fetch-site=%q"', self.src)
        self.assertIn('"": {allowed: func(*http.Request) bool { return true }}', self.src)


class GateSelftest(unittest.TestCase):
    """gate_selftest runs a build once per profile; each run gets the profile's environment and its own cases."""

    def test_a_gate_with_the_claude_profile_passes_both(self):
        with mock.patch.dict(os.environ, {'CODEXPOOL_GATE_PROFILE': 'claude'}):  # must not leak into the codex run
            ok, detail = cp.gate_selftest(fake_gated_cpa())
        self.assertTrue(ok, detail)
        self.assertRegex(detail, r'^11 codex gate checks passed \+ 18 claude gate checks passed$')

    def test_a_gate_without_the_profile_fails_the_claude_run(self):
        with mock.patch.dict(os.environ, {'FAKE_GATE_IGNORE_PROFILE': '1'}):
            ok, detail = cp.gate_selftest(fake_gated_cpa())
        self.assertFalse(ok)
        self.assertTrue(detail.startswith('claude profile: '), detail)
        self.assertIn('other client (no Claude Code User-Agent): got 200, want 403', detail)
        self.assertIn('Codex renderer app://-: got 200, want 403', detail)
        self.assertIn('other client outside /v1 (/v1beta): got 404, want 403', detail)
        self.assertNotIn('Claude Code GET /v1/models', detail)

    def test_a_binary_that_does_not_start(self):
        dud = HOME / 'dud-cpa'
        dud.write_text('#!/bin/sh\nexit 1\n')
        dud.chmod(0o755)
        with mock.patch.object(cp.time, 'time', side_effect=[0, 0, 100, 100]):
            ok, detail = cp.gate_selftest(dud)
        self.assertEqual((ok, detail), (False, 'codex profile: built binary did not start'))

    def test_gate_env(self):
        with mock.patch.dict(os.environ, {'CODEXPOOL_GATE_PROFILE': 'x', 'KEEP': '1'}):
            self.assertNotIn('CODEXPOOL_GATE_PROFILE', cp.gate_env('codex'))
            self.assertEqual(cp.gate_env('claude')['CODEXPOOL_GATE_PROFILE'], 'claude')
            self.assertEqual(cp.gate_env('claude')['KEEP'], '1')


class ClaudePlist(unittest.TestCase):
    def render(self, template, label):
        return plistlib.loads(cp.render_plist(label, template).encode())

    def test_claude_template(self):
        pool = cp.pool_instance('claude')
        p = self.render(pool.template, pool.job)
        self.assertEqual(p['Label'], 'com.codexpool-test.claude')
        self.assertEqual(p['ProgramArguments'], [str(ROOT / 'bin/claude-current/cli-proxy-api'), '-config',
                                                 str(ROOT / 'config-claude.yaml')])
        self.assertEqual(p['EnvironmentVariables'], {'CODEXPOOL_GATE_PROFILE': 'claude'})
        self.assertEqual(p['WorkingDirectory'], str(sienna_pool.CLAUDE_WORK))
        self.assertEqual((p['StandardOutPath'], p['StandardErrorPath']), (str(sienna_pool.CLAUDE_LOGS / 'launchd.log'),) * 2)
        self.assertTrue(p['KeepAlive'] and p['RunAtLoad'])
        # CLIProxyAPI writes logs/main.log under its working directory: the Claude pool's log is its own
        self.assertEqual(sienna_pool.CLAUDE_MAIN_LOG, pathlib.Path(p['WorkingDirectory']) / 'logs' / 'main.log')
        self.assertNotEqual(sienna_pool.CLAUDE_MAIN_LOG, cp.MAIN_LOG)

    def test_codex_template_has_no_profile(self):
        p = self.render('pool.plist.template', cp.POOL_JOB)
        self.assertNotIn('EnvironmentVariables', p)
        self.assertEqual(p['ProgramArguments'][0], str(ROOT / 'bin/current/cli-proxy-api'))
        self.assertEqual(p['WorkingDirectory'], str(ROOT))

    def test_no_anthropic_variables_anywhere_in_launchd(self):
        for template in (REPO / 'launchd').glob('*.template'):
            self.assertNotRegex(template.read_text(), r'ANTHROPIC_|CLAUDE_CODE|apiKeyHelper', template.name)


class ClaudeConfig(unittest.TestCase):
    """addons/sienna/examples/config-claude.yaml, rendered as codexpool claude install writes config-claude.yaml."""

    def setUp(self):
        self.text = cp.render_config('k' * 48, cp.pool_instance('claude'))

    def top(self, key):
        m = re.search(rf'(?m)^{re.escape(key)}:\s*(.*?)\s*(?:#.*)?$', self.text)
        return m and m.group(1)

    def block(self, name, text=None):
        m = re.search(rf'(?m)^{name}:\n((?:  .*\n)+)', text or self.text)
        return {k: v for k, v in re.findall(r'(?m)^  ([a-z-]+):\s*(.*?)\s*(?:#.*)?$', m.group(1))} if m else None

    def test_port_key_and_loopback(self):
        self.assertEqual(self.top('port'), str(sienna_pool.CLAUDE_PORT))
        self.assertEqual(self.top('host'), '"127.0.0.1"')
        self.assertEqual(self.top('api-keys'), '[]')
        self.assertIn(f'secret-key: "{"k" * 48}"', self.text)
        self.assertEqual(self.block('remote-management')['allow-remote'], 'false')
        self.assertEqual(self.block('remote-management')['disable-control-panel'], 'true')
        self.assertNotIn('8321', self.text.replace(f'port: {sienna_pool.CLAUDE_PORT}', ''))
        self.assertIn(f'127.0.0.1:{sienna_pool.CLAUDE_PORT}', self.text)

    def test_auth_dir_is_its_own(self):
        self.assertEqual(self.top('auth-dir'), '"~/.codexpool/auth-claude"')
        self.assertEqual(pathlib.Path(os.path.expanduser('~/.codexpool/auth-claude')), sienna_pool.CLAUDE_AUTH)

    def test_failover_and_routing_match_the_codex_pool(self):
        codex = cp.render_config('k' * 48)
        self.assertEqual(self.block('routing'), self.block('routing', codex))
        self.assertEqual(self.block('routing'), {'strategy': '"fill-first"', 'session-affinity': 'true',
                                                 'session-affinity-ttl': '"24h"', 'session-affinity-subagents': 'true'})
        for key, want in (('request-retry', '3'), ('max-retry-interval', '30'), ('passthrough-headers', 'true'),
                          ('disable-cooling', 'false'), ('save-cooldown-status', 'false'), ('debug', 'false')):
            self.assertEqual(self.top(key), want, key)

    def test_claude_settings_and_no_cloak(self):
        self.assertEqual(self.block('claude'), {'model-level-cooling': 'false'})
        self.assertEqual(self.block('claude-code'), {'disable-cloaking-model-list': 'true'})
        self.assertEqual(self.top('disable-claude-cloak-mode'), 'true')
        live = [line for line in self.text.splitlines() if not line.lstrip().startswith('#')]
        self.assertFalse([line for line in live if re.match(r'\s*cloak[a-z_-]*:', line)], 'a cloak block')
        self.assertNotIn('codex:', live)

    def test_cloak_disable_is_claude_only(self):
        self.assertNotIn('disable-claude-cloak-mode:', cp.render_config('k' * 48))

    def test_no_client_credentials(self):
        self.assertNotRegex(self.text, r'ANTHROPIC_(AUTH_TOKEN|API_KEY)\s*=|claude-api-key:\s*\n\s+-')


class BuildLinks(unittest.TestCase):
    """Each pool runs its own build: bin/current (Codex) and bin/claude-current (Claude), both into bin/versions."""

    def setUp(self):
        # the fake home's bin/current is a plain directory; make it a link to a build, as install leaves it
        self.assertFalse(cp.CURRENT.is_symlink())
        self.original = cp.VERSIONS / 'v0.0.0-original'
        self.original.parent.mkdir(parents=True, exist_ok=True)
        os.rename(cp.CURRENT, self.original)
        cp.CURRENT.symlink_to(self.original)
        self.addCleanup(self.restore)

    def restore(self):
        for link in (sienna_pool.CLAUDE_CURRENT, cp.CURRENT):
            with contextlib.suppress(FileNotFoundError):
                link.unlink()
        os.rename(self.original, cp.CURRENT)
        for d in cp.VERSIONS.glob('v7.*'):
            (d / 'cli-proxy-api').unlink()
            d.rmdir()

    def version_dir(self, name):
        d = cp.VERSIONS / name
        d.mkdir(parents=True, exist_ok=True)
        (d / 'cli-proxy-api').write_text('#!/bin/sh\n')
        return d

    def test_claude_build_leaves_bin_current_alone(self):
        before = cp.CURRENT.resolve()
        dest = self.version_dir('v7.3.20-gate-abc1234567')
        with mock.patch.object(cp, 'build', return_value=dest) as build:
            self.assertEqual(cp.build_for(cp.pool_instance('claude'), '7.3.20'), dest)
        build.assert_called_once_with('7.3.20', profile='claude')
        self.assertEqual(os.readlink(sienna_pool.CLAUDE_CURRENT), str(dest))
        self.assertEqual(cp.CURRENT.resolve(), before)
        self.assertEqual(cp.cpa_version(sienna_pool.CLAUDE_CURRENT), '7.3.20-gate-abc1234567')
        self.assertEqual(cp.cpa_binary(sienna_pool.CLAUDE_CURRENT), sienna_pool.CLAUDE_CURRENT / 'cli-proxy-api')
        self.assertEqual(cp.cpa_binary(), cp.CURRENT / 'cli-proxy-api')
        self.assertEqual([p.name for p in cp.BIN.iterdir() if p.name.endswith('.tmp')], [])

    def test_codex_build_leaves_claude_current_alone(self):
        claude = self.version_dir('v7.3.20-gate-abc1234567')
        cp._point_current(claude, sienna_pool.CLAUDE_CURRENT)
        codex = self.version_dir('v7.3.18-gate-abc1234567')
        with mock.patch.object(cp, 'build', return_value=codex):
            cp.build_for(cp.pool_instance('codex'), '7.3.18')
        self.assertEqual((os.readlink(cp.CURRENT), os.readlink(sienna_pool.CLAUDE_CURRENT)), (str(codex), str(claude)))
        self.assertEqual(cp.cpa_version(), '7.3.18-gate-abc1234567')

    def test_claude_cpa_version(self):
        with settings_restored():
            cp.SETTINGS['claude_cpa'] = 'v7.3.19'
            with mock.patch.object(cp, '_github_json') as gh:
                self.assertEqual(sienna_pool.claude_cpa_version(), '7.3.19')
            gh.assert_not_called()
            cp.SETTINGS['claude_cpa'] = None
            with mock.patch.object(cp, '_github_json', return_value={'tag_name': 'v8.0.2'}):
                self.assertEqual(sienna_pool.claude_cpa_version(), '8.0.2')
            with mock.patch.object(cp, '_github_json', return_value={'tag_name': 'v7.3.17'}):
                with self.assertRaisesRegex(RuntimeError, r'7\.3\.17, is older than the Claude pool supports \(7\.3\.18\)'):
                    sienna_pool.claude_cpa_version()

    def test_pool_instances(self):
        codex, claude = cp.pool_instance('codex'), cp.pool_instance('claude')
        self.assertEqual((codex.link, codex.config, codex.port, codex.job, codex.profile, codex.log),
                         (cp.CURRENT, cp.CONFIG, cp.PORT, cp.POOL_JOB, 'codex', cp.MAIN_LOG))
        self.assertEqual((claude.link, claude.config, claude.port, claude.job, claude.profile, claude.log),
                         (ROOT / 'bin' / 'claude-current', ROOT / 'config-claude.yaml', sienna_pool.CLAUDE_PORT,
                          'com.codexpool-test.claude', 'claude', ROOT / 'claude' / 'logs' / 'main.log'))
        self.assertNotIn(sienna_pool.CLAUDE_PORT, (cp.PORT, cp.BRIDGE_PORT))

    def test_gate_hash_of(self):
        self.assertEqual(cp.gate_hash_of('7.3.18+gate.d8de5c7f8f'), 'd8de5c7f8f')
        self.assertEqual(cp.gate_hash_of('7.3.18-gate-2bd3518caf'), '2bd3518caf')
        for none in ('7.3.17+gate', '7.3.17-gate', '7.3.17', '', None):
            self.assertIsNone(cp.gate_hash_of(none))


class DoctorPerInstance(unittest.TestCase):
    """doctor checks each pool's running build and live gate against that pool's own profile."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY))
        self.gid = installed_gate()
        self.addCleanup(cp.GATE_SOURCE.unlink)

    def checks(self, pool):
        rep = cp.DoctorReport(echo=False)
        rep.section('test')
        cp.doctor_build_checks(rep, pool, None)
        return rep.sections[0]['checks']

    def test_claude_pool_with_the_claude_profile(self):
        pool = cp.pool_instance('claude')
        with running_fake(pool.port, 'claude', FAKE_CPA_VERSION=f'7.3.20+gate.{self.gid}'):
            build, gate = self.checks(pool)
        self.assertEqual(build['status'], 'ok')
        self.assertTrue(build['text'].startswith(f'running build: 7.3.20+gate.{self.gid} (installed '), build)
        self.assertNotIn('gate source is now', build['text'])
        self.assertEqual(gate, {'status': 'ok', 'fix': None, 'text': 'origin gate (claude profile): Claude Code '
                                'request 200, other client 403, browser request 403, app://- origin 403'})

    def test_claude_pool_running_without_the_profile(self):
        """A build from before the profile (or a plist without CODEXPOOL_GATE_PROFILE) lets every client in."""
        pool = cp.pool_instance('claude')
        with running_fake(pool.port, 'claude', FAKE_GATE_IGNORE_PROFILE='1', FAKE_CPA_VERSION='7.3.17+gate.2bd3518caf'):
            build, gate = self.checks(pool)
        self.assertEqual(build['status'], 'ok')  # a +gate build: the probe below is what fails
        self.assertIn(f'; the gate source is now {self.gid}: the next codexpool claude install picks it up', build['text'])
        self.assertEqual(gate['status'], 'fail')
        self.assertEqual(gate['text'], 'origin gate (claude profile): Claude Code request 200, other client 200, '
                                       'browser request 403, app://- origin 200')
        self.assertIn('expected 200, 403, 403 and 403', gate['fix'])
        self.assertIn('CODEXPOOL_GATE_PROFILE=claude', gate['fix'])

    def test_codex_pool_with_an_older_gate_is_fine(self):
        """The gate source changed (1.3.0): the Codex pool keeps its build, doctor only says what picks it up."""
        pool = cp.pool_instance('codex')
        with running_fake(pool.port, 'codex', FAKE_CPA_VERSION='7.3.17+gate.2bd3518caf'):
            build, gate = self.checks(pool)
        self.assertEqual(build['status'], 'ok')
        # The Codex pool's gate id hashes the core source only: an add-on's gate never makes it look out of date
        self.assertIn(f'the gate source is now {cp.gate_id(profile="codex")}: the next codexpool upgrade picks it up',
                      build['text'])
        self.assertEqual(gate, {'status': 'ok', 'fix': None,
                                'text': 'origin gate: Codex-like request 200, browser request 403'})

    def test_codex_pool_down(self):
        build, gate = self.checks(cp.pool_instance('codex'))
        self.assertEqual((build['status'], gate['status']), ('fail', 'fail'))
        self.assertEqual(gate['text'], 'origin gate: Codex-like request None, browser request None')
        self.assertEqual(gate['fix'], 'expected 200 and 403; run: codexpool upgrade <version> --force')
        self.assertEqual(build['fix'], 'the pool must run a +gate build: codexpool upgrade <version>')

    def test_the_other_client_probe_is_marked(self):
        seen = []
        with mock.patch.object(cp, '_probe_status', side_effect=lambda port, h: seen.append(h) or 403):
            cp.gate_probe_cases(cp.pool_instance('claude'))
        self.assertIn({'User-Agent': sienna_pool.PROBE_UA}, seen)
        self.assertTrue(all(h.get('Origin') in (None, cp.PROBE_ORIGIN, 'app://-') for h in seen))

    def test_claude_section_only_once_installed(self):
        plist = cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist'
        self.assertFalse(sienna_pool.claude_installed())

        def titles():
            rep = cp.DoctorReport(echo=False)
            with mock.patch.object(cp, 'api', side_effect=cp.PoolDown('not in this test')):
                cp.doctor_checks(rep)
            return rep
        self.assertNotIn('Claude pool', [s['title'] for s in titles().sections])
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text('<plist/>')
        self.addCleanup(plist.unlink)
        rep = titles()
        (claude,) = [s['checks'] for s in rep.sections if s['title'] == 'Claude pool']
        texts = [c['text'] for c in claude]
        self.assertTrue(texts[0].startswith('launchd job com.codexpool-test.claude'), texts)
        self.assertIn(f'listening on 127.0.0.1:{sienna_pool.CLAUDE_PORT}', texts)
        self.assertIn('bin/claude-current → (missing)', texts)
        self.assertTrue(any(t.startswith('origin gate (claude profile): ') for t in texts), texts)


if __name__ == '__main__':
    unittest.main()
