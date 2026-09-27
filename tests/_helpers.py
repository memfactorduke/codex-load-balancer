"""Shared set-up for the codexpool tests: a throwaway HOME, stubbed macOS tools, and bin/codexpool loaded as the
module codexpool_cli.

Every test module imports this first. bin/codexpool computes its paths, ports and launchd labels from HOME and
settings.json when it loads, so the fake home must exist before it does. Nothing here touches the real
~/.codexpool, ~/.codex, ~/Library/LaunchAgents, the Keychain, the clipboard or the network: launchctl, security,
osascript and mdfind are stubs that log their arguments and fail, pbcopy is a stub that writes to a file in the fake
home (all of them first on PATH, for bin/codexpool run as a command too; CODEXPOOL_NO_CLIPBOARD=1 keeps even the
pbcopy stub off unless a test turns it on), the pool and bridge ports are free ports picked at random, and the
launchd labels are test labels. Standard library only; Python 3.9+.
"""
import argparse
import atexit
import base64
import contextlib
import http.server
import importlib.machinery
import importlib.util
import io
import json
import os
import pathlib
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.parse

sys.dont_write_bytecode = True  # no __pycache__ next to bin/codexpool

REPO = pathlib.Path(__file__).resolve().parent.parent
SCRIPT = REPO / 'bin' / 'codexpool'
BRIDGE_SCRIPT = REPO / 'lanes' / 'bridge.py'
STUBBED = ('launchctl', 'security', 'osascript', 'mdfind')
TEST_LABELS = {'pool_label': 'com.codexpool-test.pool', 'guard_label': 'com.codexpool-test.guard',
               'menubar_label': 'com.codexpool-test.menubar', 'bridge_label': 'com.codexpool-test.bridge'}
TEST_KEY = 'test-management-key'


def free_ports(n):
    """n distinct loopback ports that nothing listens on right now."""
    socks = []
    try:
        for _ in range(n):
            s = socket.socket()
            s.bind(('127.0.0.1', 0))
            socks.append(s)
        return [s.getsockname()[1] for s in socks]
    finally:
        for s in socks:
            s.close()


def write_private(path, text):
    path.write_text(text)
    path.chmod(0o600)


def build_home():
    home = pathlib.Path(tempfile.mkdtemp(prefix='codexpool-test-home-')).resolve()
    stubs = home / 'stubs'
    stubs.mkdir()
    for name in STUBBED:
        stub = stubs / name
        stub.write_text(f'#!/bin/sh\necho "{name} $*" >> "$HOME/stub-calls.log"\nexit 1\n')
        stub.chmod(0o755)
    # the clipboard: what pbcopy gets on stdin lands in $HOME/clipboard.txt; FAKE_PBCOPY_EXIT=1 makes it fail
    pbcopy = stubs / 'pbcopy'
    pbcopy.write_text('#!/bin/sh\necho "pbcopy $*" >> "$HOME/stub-calls.log"\ncat > "$HOME/clipboard.txt"\n'
                      'exit "${FAKE_PBCOPY_EXIT:-0}"\n')
    pbcopy.chmod(0o755)
    root = home / '.codexpool'
    for d in ('state', 'logs', 'auth', 'bin/current', 'lanes/secrets'):
        (root / d).mkdir(parents=True)
    (root / 'auth').chmod(0o700)
    (root / 'lanes' / 'secrets').chmod(0o700)
    cpa = root / 'bin' / 'current' / 'cli-proxy-api'
    cpa.write_text('#!/bin/sh\nexit 1\n')
    cpa.chmod(0o755)
    port, bridge_port = free_ports(2)
    settings = dict(TEST_LABELS, port=port, bridge_port=bridge_port)
    (root / 'settings.json').write_text(json.dumps(settings, indent=2) + '\n')
    config = (REPO / 'examples' / 'config.yaml').read_text()
    config = config.replace('port: 8319', f'port: {port}').replace('127.0.0.1:8319', f'127.0.0.1:{port}')
    write_private(root / 'config.yaml', config)
    write_private(root / 'lanes' / 'secrets' / 'opencode-go.key', secrets.token_urlsafe(24) + '\n')
    write_private(root / 'lanes' / 'secrets' / 'bridge.key', secrets.token_urlsafe(32) + '\n')
    shutil.copy(REPO / 'examples' / 'lanes.json', root / 'lanes.json')
    return home


HOME = build_home()
atexit.register(shutil.rmtree, str(HOME), True)
os.environ['HOME'] = str(HOME)
os.environ['PATH'] = f'{HOME / "stubs"}{os.pathsep}{os.environ.get("PATH", "")}'
os.environ['CODEXPOOL_HOME'] = str(HOME / '.codexpool')  # what lanes/bridge.py reads
for _name in ('CODEXPOOL_SETTINGS', 'CODEX_HOME', 'CODEXPOOL_REEXEC', 'CODEXPOOL_HEADLINE_NOTE',
              'CODEXPOOL_VIA_INSTALLER', 'FAKE_PBCOPY_EXIT'):
    os.environ.pop(_name, None)
os.environ['CODEXPOOL_NO_CLIPBOARD'] = '1'  # subprocesses too; tests.test_setup.clipboard() turns copies on in-process


def load_cli():
    """bin/codexpool as the module codexpool_cli, loaded once per test run."""
    if 'codexpool_cli' in sys.modules:
        return sys.modules['codexpool_cli']
    loader = importlib.machinery.SourceFileLoader('codexpool_cli', str(SCRIPT))
    spec = importlib.util.spec_from_loader('codexpool_cli', loader)
    module = importlib.util.module_from_spec(spec)
    sys.modules['codexpool_cli'] = module
    loader.exec_module(module)
    return module


def load_bridge():
    if 'codexpool_bridge' in sys.modules:
        return sys.modules['codexpool_bridge']
    spec = importlib.util.spec_from_file_location('codexpool_bridge', str(BRIDGE_SCRIPT))
    module = importlib.util.module_from_spec(spec)
    sys.modules['codexpool_bridge'] = module
    spec.loader.exec_module(module)
    return module


cp = load_cli()
ROOT = HOME / '.codexpool'
PBCOPY_STUB = HOME / 'stubs' / 'pbcopy'
CLIPBOARD = HOME / 'clipboard.txt'
cp.PBCOPY = str(PBCOPY_STUB)  # the fallback for a PATH without pbcopy too, in-process: never the real clipboard
# Refuse to run anything unless the module really points at the fake home.
assert cp.HOME == HOME and cp.ROOT == ROOT and cp.SETTINGS_FILE == ROOT / 'settings.json', 'fake HOME not in effect'
assert cp.POOL_JOB == TEST_LABELS['pool_label'] and cp.PORT != 8319 and cp.BRIDGE_PORT != 8320, 'live settings'
assert cp.pbcopy_tool() == str(PBCOPY_STUB) and cp.PBCOPY == str(PBCOPY_STUB) and not cp.clipboard_wanted(), \
    'the real clipboard is in reach'
PRISTINE_SETTINGS = dict(cp.SETTINGS)
EXAMPLE_LANES = json.loads((REPO / 'examples' / 'lanes.json').read_text())


@contextlib.contextmanager
def preserved(*paths):
    """Put each file back as it was (content and mode, or absent) when the block ends."""
    saved = []
    for p in map(pathlib.Path, paths):
        saved.append((p, p.read_bytes() if p.exists() else None, p.stat().st_mode & 0o777 if p.exists() else None))
    try:
        yield
    finally:
        for p, data, mode in saved:
            if data is None:
                with contextlib.suppress(FileNotFoundError):
                    p.unlink()
            else:
                p.write_bytes(data)
                p.chmod(mode)


@contextlib.contextmanager
def settings_restored():
    """cp.SETTINGS and settings.json as they were, after a test that changes them."""
    with preserved(cp.SETTINGS_FILE):
        try:
            yield
        finally:
            cp.SETTINGS.clear()
            cp.SETTINGS.update(PRISTINE_SETTINGS)


def run(fn, **args):
    """(exit code, stdout, stderr) of a cmd_* function called in-process with an argparse Namespace. A sys.exit()
    with a message counts as exit code 1 with the message on stderr, as it does for the real command."""
    out, err = io.StringIO(), io.StringIO()
    code = 0
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        try:
            fn(argparse.Namespace(**args))
        except SystemExit as e:
            if isinstance(e.code, str):
                err.write(e.code + '\n')
                code = 1
            else:
                code = e.code or 0
    return code, out.getvalue(), err.getvalue()


def run_script(*args, env=None, python=None):
    """bin/codexpool as a subprocess, in the fake home."""
    return subprocess.run([python or sys.executable, str(SCRIPT)] + list(args), capture_output=True, text=True,
                          env=dict(os.environ, **(env or {})), timeout=60)


def seat_name(email, plan='plus'):
    """The file name CLIProxyAPI gives a seat (and FAKE_CPA copies)."""
    return f'codex-{email}-{plan}.json'


def fake_seat_file(email, plan='plus', priority=None):
    """A seat file in the fake auth dir, with an unsigned made-up id_token that only carries the claims codexpool
    reads (no real token material). Returns its path."""
    def b64(d):
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip('=')
    claims = {'email': email, 'https://api.openai.com/auth': {'chatgpt_plan_type': plan,
                                                              'chatgpt_account_id': 'acct-' + email.split('@')[0]}}
    path = ROOT / 'auth' / seat_name(email, plan)
    data = {'id_token': '.'.join([b64({'alg': 'none'}), b64(claims), 'unsigned']), 'email': email}
    if priority is not None:
        data['priority'] = priority
    write_private(path, json.dumps(data))
    return path


class FakePool:
    """A stand-in for the pool on cp.PORT: /v1/models (403 for a browser-like request, as the gate answers), and
    the management API calls codexpool makes for seats (auth-files from the fake auth dir, fields, status, delete).
    It checks the management key. models: the /v1/models ids, or a function that returns them (to follow config.yaml
    the way the real pool reloads it). Use it as a context manager."""

    def __init__(self, models=()):
        self.models = models if callable(models) else list(models)
        self.priorities = {}
        self.patches = []
        self.deleted = []
        self.server = None

    def seat_files(self):
        out = []
        for f in sorted((ROOT / 'auth').glob('codex-*.json')):
            prio = self.priorities.get(f.name, json.loads(f.read_text()).get('priority', 0))
            out.append({'name': f.name, 'id': f.name, 'provider': 'codex', 'path': str(f), 'priority': prio,
                        'disabled': False, 'status': 'active'})
        return out

    def __enter__(self):
        pool = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def reply(self, code, body, headers=()):
                data = json.dumps(body).encode()
                self.send_response(code)
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(data)))
                for k, v in headers:
                    self.send_header(k, v)
                self.end_headers()
                self.wfile.write(data)

            def managed(self):
                if self.headers.get('Authorization') != f'Bearer {TEST_KEY}':
                    self.reply(401, {'error': 'bad management key'})
                    return False
                return True

            def do_GET(self):
                if self.path == '/v1/models':
                    if self.headers.get('Origin'):
                        self.reply(403, {'error': 'blocked by the gate'})
                    else:
                        ids = pool.models() if callable(pool.models) else pool.models
                        self.reply(200, {'data': [{'id': m} for m in ids]})
                elif self.path == '/v0/management/auth-files':
                    if self.managed():
                        self.reply(200, {'files': pool.seat_files()}, [('X-CPA-VERSION', '7.0.0+gate.test')])
                else:
                    self.reply(404, {'error': 'not here'})

            def do_PATCH(self):
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length') or 0)) or b'{}')
                if not self.managed():
                    return
                pool.patches.append((self.path, body))
                if self.path.endswith('/fields') and 'priority' in body:
                    pool.priorities[body['name']] = body['priority']
                self.reply(200, {'status': 'ok'})

            def do_DELETE(self):
                if not self.managed():
                    return
                name = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get('name', [''])[0]
                path = ROOT / 'auth' / name
                if not name or path.parent != ROOT / 'auth' or not path.is_file():
                    self.reply(404, {'error': 'no such auth file'})
                    return
                path.unlink()
                pool.deleted.append(name)
                self.reply(200, {'status': 'ok'})

            def log_message(self, *a):
                pass

        self.server = http.server.ThreadingHTTPServer(('127.0.0.1', cp.PORT), Handler)
        self.server.daemon_threads = True
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()


FAKE_CPA = '''#!{python}
"""A stand-in for CLIProxyAPI's -codex-login: prints what the real one prints (with -no-browser, its SSH-tunnel
block first), then saves a made-up seat file. Without -no-browser it says it opens the browser and, like the real one
when the browser opens, prints no link (FAKE_CPA_BROWSER=fail: the browser did not open, and the link follows). Like the real one with logging-to-file on (as install sets it), it
logs the failures that are AuthenticationErrors to logs/main.log and not to its output: FAKE_CPA_MODE fail (a state
mismatch) and expire (the 5-minute callback wait ran out, after the paste prompt). denied prints its error, as the
real one does for an OAuth error; silent fails without a word anywhere."""
import base64, datetime, json, os, pathlib, sys
args = sys.argv[1:]
root = pathlib.Path(args[args.index('-config') + 1]).parent
auth = root / 'auth'
email, plan = os.environ.get('FAKE_CPA_EMAIL', 'one@test'), os.environ.get('FAKE_CPA_PLAN', 'plus')
mode = os.environ.get('FAKE_CPA_MODE', 'ok')
def log_error(message):
    with open(root / 'logs' / 'main.log', 'a') as f:
        stamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        f.write('[' + stamp + '] [--------] [error] [openai_login.go:58] ' + message + '\\n')
if '-no-browser' not in args:
    print('Opening browser for Codex authentication', flush=True)
if '-no-browser' in args or os.environ.get('FAKE_CPA_BROWSER') == 'fail':
    border = '=' * 80
    for line in ('To authenticate from a remote machine, an SSH tunnel may be required.', border,
                 '  Run one of the following commands on your local machine (NOT the server):', '',
                 '  # Standard SSH command (assumes SSH port 22):', '  ssh -L 1455:127.0.0.1:1455 root@192.0.2.1 -p 22',
                 '', '  # If using an SSH key (assumes SSH port 22):',
                 '  ssh -i <path_to_your_key> -L 1455:127.0.0.1:1455 root@192.0.2.1 -p 22', '',
                 "  NOTE: If your server's SSH port is not 22, please modify the '-p 22' part accordingly.", border):
        print(line, flush=True)
    print('Visit the following URL to continue authentication:', flush=True)
    print('https://auth.example.invalid/oauth/authorize?client_id=app_test&state=s1', flush=True)
print('Waiting for Codex authentication callback...', flush=True)
if mode == 'fail':
    log_error('Authentication failed. Please try again.')
    sys.exit(0)
if mode == 'expire':
    sys.stdout.write('Paste the Codex callback URL (or press Enter to keep waiting): ')
    sys.stdout.flush()
    log_error('Authentication timed out. Please try again.')
    sys.exit(0)
if mode == 'denied':
    print('Codex authentication failed: authentication was cancelled or denied', flush=True)
    sys.exit(0)
if mode == 'silent':
    sys.exit(0)
if mode == 'prompt':
    sys.stdout.write('Paste the Codex callback URL (or press Enter to keep waiting): ')
    sys.stdout.flush()
def b64(d):
    return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip('=')
claims = {{'email': email, 'https://api.openai.com/auth': {{'chatgpt_plan_type': plan,
                                                          'chatgpt_account_id': 'acct-' + email.split('@')[0]}}}}
path = auth / ('codex-' + email + '-' + plan + '.json')
path.write_text(json.dumps({{'id_token': '.'.join([b64({{'alg': 'none'}}), b64(claims), 'unsigned']), 'email': email}}))
path.chmod(0o600)
print('Authentication saved to ' + str(path), flush=True)
print('Codex authentication successful!', flush=True)
'''


@contextlib.contextmanager
def fake_cpa(**env):
    """bin/current/cli-proxy-api replaced by FAKE_CPA for the block (FAKE_CPA_* settings from env), and every seat
    file it wrote removed afterwards."""
    binary = cp.cpa_binary()
    before = {p.name for p in (ROOT / 'auth').glob('*.json')}
    old_env = {k: os.environ.get(k) for k in env}
    with preserved(binary, cp.SEATS_META):
        binary.write_text(FAKE_CPA.format(python=sys.executable))
        binary.chmod(0o755)
        os.environ.update(env)
        try:
            yield
        finally:
            for k, v in old_env.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
            for p in (ROOT / 'auth').glob('*.json'):
                if p.name not in before:
                    p.unlink()
