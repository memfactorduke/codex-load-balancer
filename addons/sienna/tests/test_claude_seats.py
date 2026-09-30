"""codexpool claude login and the Claude account commands (enable, disable, label, weight, priority, reserve, remove,
order, credits), which run the Codex seat code with the Claude pool's port, claude-seats.json and claude-guard.json;
the plan tier from the profile call; and the selftest's pure parts (the selftest itself never runs here). A stand-in
Claude pool answers on the fake home's Claude port and a stand-in CLIProxyAPI does -claude-login. No real sign-in,
no real clipboard, nothing outside the fake HOME of tests/_helpers.py."""
import contextlib
import hashlib
import http.server
import io
import json
import os
import shutil
import sys
import threading
import unittest
import urllib.parse
from unittest import mock

from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import HOME, ROOT, TEST_KEY, cp, preserved, run, settings_restored

assert sienna_pool.CLAUDE_PORT != 8321 and str(sienna_pool.CLAUDE_AUTH).startswith(str(HOME)), 'live settings'

CLIPBOARD = HOME / 'clipboard.txt'
CPA_CALLS = HOME / 'claude-cpa-calls.jsonl'
MAX_5X = {'account': {'uuid': 'acct-1', 'email': 'one@test', 'has_claude_max': True, 'has_claude_pro': False},
          'organization': {'uuid': 'org-1', 'organization_type': 'claude_max',
                           'rate_limit_tier': 'default_claude_max_5x'}}

# A stand-in for CLIProxyAPI's -claude-login: prints what the real one prints, then saves a made-up account file
# (claude-<8 hex>-<email>.json in the config's auth-dir, no real token material). FAKE_CLAUDE_MODE=fail logs the
# failure to logs/main.log in its working directory, as the real one does with logging-to-file on.
FAKE_CLAUDE_CPA = '''#!{python}
import datetime, hashlib, json, os, pathlib, sys
args = sys.argv[1:]
config = pathlib.Path(args[args.index('-config') + 1])
with open(os.environ['HOME'] + '/claude-cpa-calls.jsonl', 'a') as f:
    f.write(json.dumps({{'args': args, 'cwd': os.getcwd()}}) + '\\n')
auth = config.parent / 'auth-claude'
email = os.environ.get('FAKE_CLAUDE_EMAIL', 'one@test')
if '-no-browser' not in args:
    print('Opening browser for Claude authentication', flush=True)
else:
    print('Visit the following URL to continue authentication:', flush=True)
    print('https://claude.example.invalid/oauth/authorize?code=true&state=s1', flush=True)
print('Waiting for Claude authentication callback...', flush=True)
if os.environ.get('FAKE_CLAUDE_MODE') == 'fail':
    pathlib.Path('logs').mkdir(exist_ok=True)
    with open('logs/main.log', 'a') as f:
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        f.write('[' + stamp + '] [--------] [error] [anthropic_login.go:44] Authentication failed: state mismatch\\n')
    sys.exit(0)
org = 'org-' + email.split('@')[0]
path = auth / ('claude-' + hashlib.sha256(org.encode()).hexdigest()[:8] + '-' + email + '.json')
path.write_text(json.dumps({{'type': 'claude', 'email': email, 'organization_uuid': org, 'access_token': 'test'}}))
path.chmod(0o600)
print('Authentication saved to ' + str(path), flush=True)
print('Claude authentication successful!', flush=True)
'''


def account_file(email, priority=None):
    """An account file in the fake auth-claude/, named the way CLIProxyAPI names it. Returns its name."""
    org = 'org-' + email.split('@')[0]
    path = sienna_pool.CLAUDE_AUTH / f'claude-{hashlib.sha256(org.encode()).hexdigest()[:8]}-{email}.json'
    data = {'type': 'claude', 'email': email, 'organization_uuid': org, 'access_token': 'test'}
    if priority is not None:
        data['priority'] = priority
    sienna_pool.CLAUDE_AUTH.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data))
    return path.name


class FakeClaudePool:
    """A stand-in for the Claude pool on sienna_pool.CLAUDE_PORT: auth-files from the fake auth-claude/, fields, status,
    delete, and api-call (the profile endpoint answers with profile, or profile_code). It checks the management
    key. Use it as a context manager."""

    def __init__(self, profile=MAX_5X, profile_code=200):
        self.profile, self.profile_code = profile, profile_code
        self.priorities, self.disabled, self.patches, self.calls, self.deleted = {}, set(), [], [], []

    def files(self):
        out = []
        for f in sorted(sienna_pool.CLAUDE_AUTH.glob('claude-*.json')):
            data = json.loads(f.read_text())
            out.append({'name': f.name, 'id': f.name, 'provider': 'claude', 'path': str(f), 'auth_index': f.name,
                        'email': data.get('email'), 'priority': self.priorities.get(f.name, data.get('priority', 0)),
                        'disabled': f.name in self.disabled, 'status': 'active'})
        return out

    def __enter__(self):
        pool = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def reply(self, code, body):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def body(self):
                return json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')

            def managed(self):
                if self.headers.get('Authorization') != f'Bearer {TEST_KEY}':
                    self.reply(401, {'error': 'bad management key'})
                    return False
                return True

            def do_GET(self):
                if self.path == '/v0/management/auth-files' and self.managed():
                    self.reply(200, {'files': pool.files()})
                elif self.path != '/v0/management/auth-files':
                    self.reply(404, {'error': 'not here'})

            def do_POST(self):
                body = self.body()
                if self.path != '/v0/management/api-call' or not self.managed():
                    return self.reply(404, {'error': 'not here'}) if self.path != '/v0/management/api-call' else None
                pool.calls.append(body)
                if body.get('url') == sienna_pool.CLAUDE_PROFILE_URL:
                    self.reply(200, {'status_code': pool.profile_code, 'body': json.dumps(pool.profile)})
                else:
                    self.reply(200, {'status_code': 404, 'body': '{}'})

            def do_PATCH(self):
                body = self.body()
                if not self.managed():
                    return
                pool.patches.append((self.path, body))
                if self.path.endswith('/fields') and 'priority' in body:
                    pool.priorities[body['name']] = body['priority']
                if self.path.endswith('/status'):
                    (pool.disabled.add if body['disabled'] else pool.disabled.discard)(body['name'])
                self.reply(200, {'status': 'ok'})

            def do_DELETE(self):
                if not self.managed():
                    return
                name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('name', [''])[0]
                path = sienna_pool.CLAUDE_AUTH / name
                if not name or not path.is_file():
                    return self.reply(404, {'error': 'no such auth file'})
                path.unlink()
                pool.deleted.append(name)
                self.reply(200, {'status': 'ok'})

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', sienna_pool.CLAUDE_PORT), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()

    def priority(self, name):
        return next(f['priority'] for f in self.files() if f['name'] == name)


def cli(*argv):
    """(exit code, stdout, stderr) of `codexpool ARGV...`, parsed by the real parser and run in-process."""
    args = cp.build_parser().parse_args(list(argv))
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            args.fn(args)
        except SystemExit as e:
            if isinstance(e.code, str):
                err.write(e.code + '\n')
                code = 1
            else:
                code = e.code or 0
    return code, out.getvalue(), err.getvalue()


class ClaudeAccounts(unittest.TestCase):
    """The Claude pool installed (its plist and a build with the stand-in CLIProxyAPI), a stand-in Claude pool, and
    every Claude file gone again afterwards. seats.json and guard.json (the Codex pool's) must not change."""

    profile = MAX_5X

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(settings_restored())
        stack.enter_context(preserved(cp.SEATS_META, cp.GUARD_FILE, CPA_CALLS))
        stack.enter_context(mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY))
        stack.enter_context(mock.patch.object(cp, 'notify'))
        self.addCleanup(self.wipe)
        self.wipe()
        cp.SEATS_META.write_text('{"codex-a.json": {"label": "Codex A"}}\n')
        self.codex_meta = cp.SEATS_META.read_text()
        plist = cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist'
        plist.parent.mkdir(parents=True, exist_ok=True)
        plist.write_text('<plist/>')
        build = cp.VERSIONS / 'v7.3.20-gate-test'
        build.mkdir(parents=True)
        os.symlink(build, sienna_pool.CLAUDE_CURRENT)
        binary = cp.cpa_binary(sienna_pool.CLAUDE_CURRENT)
        binary.write_text(FAKE_CLAUDE_CPA.format(python=sys.executable))
        binary.chmod(0o755)
        sienna_pool.CLAUDE_AUTH.mkdir(mode=0o700, parents=True)
        (sienna_pool.CLAUDE_CONFIG).write_text('port: 0\n')
        self.pool = stack.enter_context(FakeClaudePool(self.profile))

    def tearDown(self):
        self.assertEqual(cp.SEATS_META.read_text(), self.codex_meta, 'the Codex pool\'s seats.json changed')

    def wipe(self):
        for p in (sienna_pool.CLAUDE_CONFIG, sienna_pool.CLAUDE_STATUS_FILE, sienna_pool.CLAUDE_GUARD_FILE, sienna_pool.CLAUDE_SEATS_META,
                  sienna_pool.CLAUDE_SELFTEST_JOURNAL, cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist', CLIPBOARD):
            with contextlib.suppress(FileNotFoundError):
                p.unlink()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(sienna_pool.CLAUDE_CURRENT)
        for d in (sienna_pool.CLAUDE_AUTH, sienna_pool.CLAUDE_WORK, cp.VERSIONS):
            shutil.rmtree(d, ignore_errors=True)

    def meta(self):
        return cp.read_json(sienna_pool.CLAUDE_SEATS_META, {})

    def guard(self):
        return cp.read_json(sienna_pool.CLAUDE_GUARD_FILE, {})


class Plan(unittest.TestCase):
    def test_tiers_and_weights(self):
        def org(kind, rate=None, seat=None, **account):
            return {'organization': {'organization_type': kind, 'rate_limit_tier': rate, 'seat_tier': seat},
                    'account': account}
        for profile, want in ((MAX_5X, ('max_5x', 5.0)),
                              (org('claude_max', 'default_claude_max_20x'), ('max_20x', 20.0)),
                              (org('claude_max'), ('max_5x', 5.0)),
                              (org('claude_pro', 'default_claude_pro'), ('pro', 1.0)),
                              (org('claude_team', 'default_raven', 'team_standard'), ('team', 1.25)),
                              (org('claude_team', 'default_raven', 'team_premium'), ('team_premium', 6.25)),
                              (org('claude_team', 'default_raven', 'team_tier_1'), ('team_premium', 6.25)),
                              (org('claude_team', 'default_claude_max_5x'), ('team_premium', 6.25)),
                              (org('claude_enterprise'), ('enterprise', 1.0)),
                              (org('claude_enterprise', 'default_claude_max_5x', 'team_tier_1'), ('enterprise', 5.0)),
                              (org(None, has_claude_max=True), ('max_5x', 5.0)),
                              (org(None, has_claude_pro=True), ('pro', 1.0)),
                              (org(None), (None, None)), ({}, (None, None)), ('odd', (None, None))):
            self.assertEqual(sienna_pool.claude_plan(profile), want, profile)

    def test_refreshed_team_defaults_keep_explicit_weights(self):
        for tier, old, new in (('team', 1.0, 1.25), ('team_premium', 5.0, 6.25)):
            seats = [{'name': name, 'plan': tier, 'weight': weight}
                     for name, weight in (('default', old), ('explicit-old', old), ('custom', 9.0))]
            meta = {'explicit-old': {'weight': old}, 'custom': {'weight': 9.0}}
            guard = {'plans': {s['name']: {'plan': tier, 'weight': new} for s in seats}}
            sienna_guard.claude_apply_plans(guard, seats, meta)
            self.assertEqual([s['weight'] for s in seats], [new, old, 9.0])
            self.assertEqual(meta, {'explicit-old': {'weight': old}, 'custom': {'weight': 9.0}})

    def test_seat_pools(self):
        codex, claude = cp.seat_pool('codex'), cp.seat_pool('claude')
        self.assertEqual((codex.port, codex.meta, codex.guard, codex.provider), (cp.PORT, cp.SEATS_META,
                                                                                  cp.GUARD_FILE, 'codex'))
        self.assertEqual((claude.port, claude.meta, claude.guard, claude.provider, claude.work, claude.log),
                         (sienna_pool.CLAUDE_PORT, sienna_pool.CLAUDE_SEATS_META, sienna_pool.CLAUDE_GUARD_FILE, 'claude', sienna_pool.CLAUDE_WORK,
                          sienna_pool.CLAUDE_MAIN_LOG))
        self.assertEqual(cp.seat_pool(cp.argparse.Namespace()).name, 'codex')
        self.assertEqual(cp.seat_pool(cp.argparse.Namespace(pool='claude')).name, 'claude')

    def test_credit_policy(self):
        meta = {'a': {'credits': {'policy': 'last-resort', 'cap': 200}}, 'b': {'credits': {'policy': 'last-resort'}},
                'c': {'label': 'C'}, 'd': {'credits': {'policy': 'weird', 'cap': 5}}, 'e': 'odd'}
        self.assertEqual(sienna_pool.credit_policy(meta, 'a'), {'policy': 'last-resort', 'cap': 200.0})
        self.assertEqual(sienna_pool.credit_policy(meta, 'b'), {'policy': 'last-resort', 'cap': None})
        for name in 'cdex':
            self.assertEqual(sienna_pool.credit_policy(meta, name), {'policy': 'off', 'cap': None}, name)


class Login(ClaudeAccounts):
    def login(self, label='Work', no_open=True, priority=None, **env):
        with mock.patch.dict(os.environ, env):
            return run(sienna_pool.cmd_claude_login, label=label, no_open=no_open, no_copy=False, priority=priority)

    def test_a_new_account(self):
        other = account_file('two@test', priority=1000)
        added_at = cp.now_utc()
        with mock.patch.object(cp, 'now_utc', return_value=added_at):
            code, out, err = self.login('Work')
        self.assertEqual(code, 0, err)
        name = next(p.name for p in sienna_pool.CLAUDE_AUTH.glob('claude-*one@test.json'))
        line = next(ln for ln in out.splitlines() if ln.startswith('seat '))
        self.assertTrue(line.startswith(f'seat {name}: one@test plan=max_5x '), line)
        self.assertIn(' label=Work priority=900', line)
        self.assertIn('https://claude.example.invalid/oauth/authorize', out)
        self.assertIn('Claude Code keeps its own login', out)
        call = json.loads(CPA_CALLS.read_text().splitlines()[-1])
        self.assertEqual(call['args'], ['-config', str(sienna_pool.CLAUDE_CONFIG), '-claude-login', '-no-browser'])
        self.assertEqual(call['cwd'], str(sienna_pool.CLAUDE_WORK))
        self.assertEqual(self.meta(), {name: {'label': 'Work', 'added_at': added_at.isoformat()}})
        self.assertEqual(self.pool.priority(name), 900)
        self.assertEqual(self.pool.priority(other), 1000)
        plan = self.guard()['plans'][name]
        self.assertEqual((plan['plan'], plan['weight']), ('max_5x', 5.0))
        call = next(c for c in self.pool.calls if c['url'] == sienna_pool.CLAUDE_PROFILE_URL)
        self.assertEqual((call['auth_index'], call['method'], call['header']['Authorization']),
                         (name, 'GET', 'Bearer $TOKEN$'))
        self.assertFalse(CLIPBOARD.exists(), 'CODEXPOOL_NO_CLIPBOARD=1 keeps the link off the clipboard')
        seats = cp.load_seats(cp.seat_pool('claude'))
        me = next(s for s in seats if s['name'] == name)
        self.assertEqual((me['label'], me['plan'], me['weight'], me['account_id']), ('Work', 'max_5x', 5.0, 'org-one'))

    def test_the_link_goes_on_the_clipboard(self):
        with preserved(CLIPBOARD), mock.patch.object(cp, 'clipboard_wanted', lambda no_copy=False: not no_copy):
            code, out, err = self.login('Work')
            self.assertEqual(code, 0, err)
            self.assertIn(cp.LINK_COPIED, out)
            self.assertEqual(CLIPBOARD.read_text(), 'https://claude.example.invalid/oauth/authorize?code=true&state=s1')

    def test_the_browser_flow(self):
        code, out, err = self.login('Work', no_open=False, priority=42)
        self.assertEqual(code, 0, err)
        self.assertEqual(json.loads(CPA_CALLS.read_text().splitlines()[-1])['args'],
                         ['-config', str(sienna_pool.CLAUDE_CONFIG), '-claude-login'])
        self.assertIn('priority=42', out)

    def test_a_failed_sign_in_changes_nothing(self):
        code, out, err = self.login('Work', FAKE_CLAUDE_MODE='fail')
        self.assertEqual(code, 1)
        self.assertIn('login did not complete; nothing changed (the pool said: Authentication failed: state mismatch)',
                      err)
        self.assertEqual(list(sienna_pool.CLAUDE_AUTH.glob('*.json')), [])
        self.assertFalse(sienna_pool.CLAUDE_SEATS_META.exists())

    def test_the_same_account_again_keeps_its_name(self):
        added_at = cp.now_utc()
        with mock.patch.object(cp, 'now_utc', return_value=added_at):
            self.assertEqual(self.login('Work')[0], 0)
        with mock.patch.object(cp, 'now_utc', return_value=added_at + cp.dt.timedelta(seconds=60)):
            code, out, err = self.login('Other')
        self.assertEqual(code, 0, err)
        self.assertIn('label=Work', out)
        self.assertIn('its login is refreshed, nothing was added and it keeps its name', out)
        self.assertEqual(list(self.meta().values()), [{'label': 'Work', 'added_at': added_at.isoformat()}])

    def test_removed_account_added_again_refreshes_added_at(self):
        added_at = cp.now_utc()
        with mock.patch.object(cp, 'now_utc', return_value=added_at):
            self.assertEqual(self.login('Work')[0], 0)
        name = next(iter(self.meta()))
        (sienna_pool.CLAUDE_AUTH / name).unlink()
        # Stale metadata must not make a newly added account's old poll fresh.
        later = added_at + cp.dt.timedelta(seconds=60)
        with mock.patch.object(cp, 'now_utc', return_value=later):
            code, _, err = self.login('Work again')
        self.assertEqual(code, 0, err)
        self.assertEqual(self.meta()[name], {'label': 'Work again', 'added_at': later.isoformat()})

    def test_a_plan_the_profile_call_cannot_tell(self):
        self.pool.profile_code = 403
        code, out, err = self.login('Work')
        self.assertEqual(code, 0, err)
        self.assertIn(' plan=unknown ', out)
        self.assertIn('Its plan is not known yet (HTTP 403', out)
        self.assertNotIn('plans', self.guard())

    def test_a_bad_label_or_no_pool(self):
        code, _, err = self.login('-x')
        self.assertEqual((code, err.strip()), (1, 'codexpool claude login: a label cannot start with - (it would read '
                                                  'as an option)'))
        (cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist').unlink()
        code, _, err = self.login('Work')
        self.assertEqual(code, 1)
        self.assertIn('the Claude pool is not installed', err)
        self.assertFalse(CPA_CALLS.exists() and CPA_CALLS.read_text())

    def test_the_parser(self):
        args = cp.build_parser().parse_args(['claude', 'login', 'Work', '--no-open', '--priority', '5'])
        self.assertEqual((args.claude_fn, args.label, args.no_open, args.priority), (sienna_pool.cmd_claude_login, 'Work',
                                                                                     True, 5))


class Commands(ClaudeAccounts):
    def setUp(self):
        super().setUp()
        self.a = account_file('a@test', priority=1000)
        self.b = account_file('b@test', priority=990)
        self.r = account_file('r@test', priority=900)
        cp.update_meta(self.a, cp.seat_pool('claude'), label='Alpha')
        cp.update_meta(self.b, cp.seat_pool('claude'), label='Beta')
        cp.update_meta(self.r, cp.seat_pool('claude'), label='Reserve', reserve=True)
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, {'generated_at': cp.now_utc().isoformat(), 'pool': {'installed': True},
                                              'seats': [{'name': n, 'label': n, 'state': 'ready', 'plan': 'max_5x',
                                                         'credits': {'enabled': True, 'used': 3.5, 'limit': 50.0}}
                                                        for n in (self.a, self.b, self.r)]})

    def row(self, name):
        return next(r for r in cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})['seats'] if r['name'] == name)

    def test_disable_and_enable(self):
        code, out, err = cli('claude', 'disable', 'Beta')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, 'Beta disabled (sessions on it move to the next account)\n')
        self.assertIn(self.b, self.pool.disabled)
        self.assertEqual(self.row(self.b)['state'], 'disabled')
        # a seat the guard parked for credits: enable overrides the park, in claude-guard.json (not guard.json)
        guard_before = cp.GUARD_FILE.read_text() if cp.GUARD_FILE.exists() else None
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, {'seats': {self.b: {'parked_until': '2099-01-01T00:00:00+00:00',
                                                                'parked_reason': 'credits'}}})
        code, out, err = cli('claude', 'enable', 'Beta')
        self.assertEqual(code, 0, err)
        self.assertIn('Credit guard overridden for Beta', out)
        self.assertNotIn(self.b, self.pool.disabled)
        self.assertEqual(self.guard()['seats'][self.b], {'override_until': '2099-01-01T00:00:00+00:00'})
        self.assertEqual(self.row(self.b)['state'], 'ready')
        self.assertEqual(cp.GUARD_FILE.read_text() if cp.GUARD_FILE.exists() else None, guard_before)

    def test_label_and_weight(self):
        code, out, err = cli('claude', 'label', 'Beta', 'Work Max')
        self.assertEqual((code, out), (0, f'{self.b} is now labelled Work Max\n'), err)
        self.assertEqual(self.meta()[self.b]['label'], 'Work Max')
        self.assertEqual(self.row(self.b)['label'], 'Work Max')
        code, out, err = cli('claude', 'weight', 'Work Max', '20')
        self.assertEqual(code, 0, err)
        self.assertEqual(out, 'Work Max weight 1 → 20 (its share of the pool total; Pro = 1)\n')
        self.assertEqual((self.meta()[self.b]['weight'], self.row(self.b)['weight']), (20.0, 20.0))
        code, _, err = cli('claude', 'weight', 'Work Max', '0')
        self.assertEqual(code, 1)
        self.assertIn('Pro = 1, Max 5× = 5, Max 20× = 20', err)

    def test_priority_and_order(self):
        code, out, err = cli('claude', 'priority', 'Beta', '2000')
        self.assertEqual((code, out), (0, 'Beta priority 990 → 2000 (higher is used first)\n'), err)
        self.assertEqual((self.pool.priority(self.b), self.meta()[self.b]['manual_priority']), (2000, 2000))
        self.assertTrue(self.guard()['you_reordered'])
        code, out, err = cli('claude', 'order', 'Reserve', 'Alpha')
        self.assertEqual(code, 0, err)
        self.assertIn('Fill order: Alpha > Beta > Reserve (reserve)', out)
        self.assertIn('Reserve is a reserve account: a reserve account always fills after every regular account '
                      '(codexpool claude reserve <account> --off makes it a regular one).', out)
        self.assertEqual([self.pool.priority(n) for n in (self.a, self.b, self.r)], [1000, 990, 980])
        self.assertEqual([r['name'] for r in cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})['seats']],
                         [self.a, self.b, self.r])

    def test_order_on_reset_balancing_is_kept_for_later(self):
        cp.SETTINGS['claude_balancing'] = 'reset'
        code, out, err = cli('claude', 'order', 'Beta')
        self.assertEqual(code, 0, err)
        self.assertIn('takes effect with: codexpool set claude_balancing priority', out)
        self.assertEqual([p for p, _ in self.pool.patches], [])
        self.assertEqual(self.meta()[self.b]['manual_priority'], 1000)

    def test_reserve(self):
        code, out, err = cli('claude', 'reserve', 'Alpha')
        self.assertEqual(code, 0, err)
        self.assertIn('Alpha is a reserve account. It now fills after every regular account (priority 1000 → 890).',
                      out)
        self.assertTrue(self.meta()[self.a]['reserve'])
        self.assertTrue(self.row(self.a)['reserve'])
        code, out, err = cli('claude', 'reserve', 'Reserve', '--off')
        self.assertEqual(code, 0, err)
        self.assertTrue(out.startswith('Reserve is no longer a reserve account. The menu bar'), out)  # 900: in place
        self.assertNotIn('reserve', self.meta()[self.r])
        self.assertEqual([self.pool.priority(n) for n in (self.a, self.b, self.r)], [890, 990, 900])

    def test_remove(self):
        cp.update_meta(self.b, cp.seat_pool('claude'), credits={'policy': 'last-resort', 'cap': 200.0})
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, {'seats': {self.b: {'parked_until': 'x'}}, 'plans': {self.b: {}}})
        code, _, err = cli('claude', 'remove', 'Beta')
        self.assertEqual(code, 1)
        self.assertIn('Re-run with --yes', err)
        code, out, err = cli('claude', 'remove', 'Beta', '--yes')
        self.assertEqual((code, out), (0, f'removed {self.b}\n'), err)
        self.assertEqual(self.pool.deleted, [self.b])
        self.assertNotIn(self.b, self.meta())
        self.assertEqual(self.guard(), {'seats': {}, 'plans': {}})
        self.assertEqual([r['name'] for r in cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})['seats']], [self.a, self.r])

    def test_credits(self):
        code, _, err = cli('claude', 'credits', 'Beta', 'last-resort')
        self.assertEqual(code, 1)
        self.assertIn('last-resort needs a cap, the most Beta may spend: codexpool claude credits Beta last-resort '
                      '--cap <USD>', err)
        code, out, err = cli('claude', 'credits', 'Beta', 'last-resort', '--cap', '200')
        self.assertEqual(code, 0, err)
        self.assertIn('Beta: credits as the very last resort, up to $200 this month.', out)
        self.assertEqual(self.meta()[self.b]['credits'], {'policy': 'last-resort', 'cap': 200.0})
        self.assertEqual(self.row(self.b)['credits'], {'enabled': True, 'used': 3.5, 'limit': 50.0,
                                                       'policy': 'last-resort', 'cap': 200.0, 'mismatch': False})
        code, out, err = cli('claude', 'credits', 'Beta', 'last-resort')  # keeps the cap it has
        self.assertEqual((code, self.meta()[self.b]['credits']['cap']), (0, 200.0), err)
        code, out, err = cli('claude', 'credits', 'Beta', 'off')
        self.assertEqual(code, 0, err)
        self.assertIn('Beta: policy off. Turn usage credits off at claude.ai', out)
        self.assertEqual(self.meta()[self.b], {'label': 'Beta'})
        self.assertEqual(self.row(self.b)['credits']['policy'], 'off')
        for argv, want in ((('off', '--cap', '5'), '--cap goes with last-resort'),
                           (('last-resort', '--cap', '-1'), 'the cap is a positive number of US dollars, not -1'),
                           (('last-resort', '--cap', 'nan'), 'the cap is a positive number')):
            code, _, err = cli('claude', 'credits', 'Beta', *argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(want, err)
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            cp.build_parser().parse_args(['claude', 'credits', 'Beta', 'always'])

    def test_a_new_credit_policy_ends_an_override(self):
        """After codexpool claude enable over a credit park, credits off applies at once: the override ends and the
        account, at its limit with credits on, is parked again."""
        now = cp.now_utc()
        later = (now + cp.dt.timedelta(hours=2)).isoformat()
        usage = {'five_hour': {'used': 100.0, 'reset_at': later}, 'week': {'used': 40.0, 'reset_at': later},
                 'scoped': [], 'credits': {'enabled': True, 'used': 3.5, 'limit': 50.0, 'currency': 'USD'},
                 'overage': None, 'refused': False, 'at': now.isoformat(), 'source': 'poll',
                 'polled_at': now.isoformat()}
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, {'seats': {self.b: {'override_until': later}}, 'usage': {self.b: usage}})
        code, out, err = cli('claude', 'credits', 'Beta', 'off')
        self.assertEqual(code, 0, err)
        self.assertIn('Beta: the credit guard\'s override (codexpool claude enable) ends; the new policy applies.', out)
        g = self.guard()['seats'][self.b]
        self.assertNotIn('override_until', g)
        self.assertEqual((g['parked_reason'], g['parked_until']), ('credits', later))
        self.assertIn(self.b, self.pool.disabled)
        self.assertEqual(self.row(self.b)['state'], 'parked')

    def test_a_new_account_gets_a_row(self):
        new = account_file('n@test', priority=500)
        with cp.guard_lock():
            self.assertTrue(sienna_pool.refresh_claude_status_file())
        row = self.row(new)
        self.assertEqual((row['state'], row['label'], row['credits']['policy'], row['five_hour']),
                         ('ready', 'test-n', 'off', None))

    def test_no_status_file_yet(self):
        sienna_pool.CLAUDE_STATUS_FILE.unlink()
        code, _, err = cli('claude', 'disable', 'Alpha')
        self.assertEqual(code, 0, err)
        self.assertFalse(sienna_pool.CLAUDE_STATUS_FILE.exists(), 'only the guard writes the first claude-status.json')

    def test_unknown_account(self):
        code, _, err = cli('claude', 'enable', 'nobody')
        self.assertEqual(code, 1)
        self.assertIn('"nobody" matches no seats (Alpha, Beta, Reserve)', err)
        self.assertEqual(self.pool.patches, [])


class Selftest(ClaudeAccounts):
    def test_turn_facts(self):
        lines = [{'type': 'system', 'subtype': 'init', 'session_id': 's-1'},
                 {'type': 'assistant', 'session_id': 's-1', 'message': {'content': [
                     {'type': 'thinking', 'thinking': '...', 'signature': 'x'}, {'type': 'text', 'text': 'N=269'}]}},
                 {'type': 'result', 'subtype': 'success', 'is_error': False, 'result': 'N=269', 'session_id': 's-1'}]
        facts = sienna_selftest.claude_turn_facts('\n'.join(json.dumps(x) for x in lines) + '\nnot json\n')
        self.assertEqual(facts, {'session_id': 's-1', 'answer': 'N=269', 'errors': [], 'thinking': 1})
        bad = sienna_selftest.claude_turn_facts(json.dumps({'type': 'result', 'is_error': True, 'result': 'API Error: 400'}))
        self.assertEqual(bad['errors'], ['API Error: 400'])
        self.assertEqual(sienna_selftest.claude_turn_facts('')['errors'], ['no result from claude -p'])

    def test_verdict(self):
        def turn(answer, thinking=0, errors=()):
            return {'session_id': 's', 'answer': answer, 'errors': list(errors), 'thinking': thinking}
        ok = [('1', turn('N=269', 2)), ('2', turn('ANSWER=278'))]
        self.assertEqual(sienna_selftest.claude_selftest_verdict(ok, [], False)[0], 'PASS')
        self.assertEqual(sienna_selftest.claude_selftest_verdict(ok, ['Invalid `signature` in `thinking` block'], False)[0], 'FAIL')
        self.assertEqual(sienna_selftest.claude_selftest_verdict(ok, [], True)[0], 'FAIL')  # the /compact turn is missing
        self.assertEqual(sienna_selftest.claude_selftest_verdict([ok[0], ('2', turn('ANSWER=9'))], [], False)[0], 'FAIL')
        self.assertEqual(sienna_selftest.claude_selftest_verdict([('1', turn('N=269')), ok[1]], [], False)[0], 'INCONCLUSIVE')
        self.assertEqual(sienna_selftest.claude_selftest_verdict([ok[0], ('c', turn('')), ok[1]], [], True),
                         ('PASS', 'thinking and a /compact survived the account move'))
        self.assertEqual(sienna_selftest.claude_selftest_verdict([ok[0], ('2', turn('', errors=['x']))], [], False)[0], 'FAIL')

    def test_env_is_the_launchers(self):
        with mock.patch.dict(os.environ, {'ENABLE_TOOL_SEARCH': 'false'}, clear=True):
            env = sienna_selftest.claude_selftest_env()
        self.assertEqual(env['ANTHROPIC_BASE_URL'], f'http://127.0.0.1:{sienna_pool.CLAUDE_PORT}')
        self.assertEqual((env['ENABLE_TOOL_SEARCH'], env['CLAUDE_CODE_PROMPT_CACHE_TTL']), ('false', '1h'))
        self.assertEqual(env['ANTHROPIC_DEFAULT_HAIKU_MODEL'], 'claude-sonnet-5')
        self.assertNotIn('ANTHROPIC_BASE_URL', os.environ)

    def test_env_defaults_empty_values_and_keeps_overrides(self):
        for value, expected in (('', 'claude-sonnet-5'), ('custom-helper', 'custom-helper')):
            with self.subTest(value=value), mock.patch.dict(os.environ, {
                    'ANTHROPIC_DEFAULT_HAIKU_MODEL': value, 'ENABLE_TOOL_SEARCH': '',
                    'CLAUDE_CODE_PROMPT_CACHE_TTL': '', 'ANTHROPIC_SMALL_FAST_MODEL': 'legacy-helper',
                    'ANTHROPIC_DEFAULT_OPUS_MODEL': 'custom-opus', 'CLAUDEPOOL': 'off'}, clear=True):
                env = sienna_selftest.claude_selftest_env()
            self.assertEqual(env['ANTHROPIC_DEFAULT_HAIKU_MODEL'], expected)
            self.assertEqual((env['ENABLE_TOOL_SEARCH'], env['CLAUDE_CODE_PROMPT_CACHE_TTL']), ('true', '1h'))
            self.assertEqual(env['ANTHROPIC_SMALL_FAST_MODEL'], 'legacy-helper')
            self.assertEqual(env['ANTHROPIC_DEFAULT_OPUS_MODEL'], 'custom-opus')
            self.assertNotIn('ANTHROPIC_DEFAULT_FABLE_MODEL', env)
            self.assertNotIn('CLAUDEPOOL', env)

    def test_it_asks_first_and_changes_nothing_on_no(self):
        a, b = account_file('a@test', 1000), account_file('b@test', 990)
        marker = HOME / 'claude-ran'
        claude = HOME / '.local' / 'bin' / 'claude'
        with preserved(claude, marker):
            claude.parent.mkdir(parents=True, exist_ok=True)
            claude.write_text(f'#!/bin/sh\ntouch {marker}\n')
            claude.chmod(0o755)
            with mock.patch('builtins.input', return_value='n'):
                code, out, err = cli('claude', 'selftest', a, b, '--compact')
            self.assertEqual((code, err.strip()), (1, 'cancelled'))
            self.assertIn('spends a few requests from both accounts', out)
            self.assertFalse(marker.exists())
        self.assertEqual(self.pool.patches, [])
        self.assertFalse(sienna_pool.CLAUDE_SELFTEST_JOURNAL.exists())

    def test_recovery_restores_what_a_dead_selftest_toggled(self):
        a = account_file('a@test', 1000)
        self.pool.disabled.add(a)
        cp.write_json(sienna_pool.CLAUDE_SELFTEST_JOURNAL, {'pid': 2 ** 22 + 12345, 'toggled': {a: False}})
        cp.recover_selftest({}, cp.seat_pool('claude'))
        self.assertNotIn(a, self.pool.disabled)
        self.assertFalse(sienna_pool.CLAUDE_SELFTEST_JOURNAL.exists())


if __name__ == '__main__':
    unittest.main()
