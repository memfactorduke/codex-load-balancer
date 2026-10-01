"""CPA compatibility checks. All processes use a throwaway HOME and fabricated credentials.

Set CODEXPOOL_CPA_SOURCE (defaults to sibling scratchpad/cpa-src) for the Go check,
CODEXPOOL_GO if Go is not on PATH, and CODEXPOOL_CPA_BINARY plus CODEXPOOL_CPA_FIXTURES
for captured E4.2 requests. No captured fixture is claimed until E4 runs; absence skips.
"""
import socket
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request


def changed_paths(a, b, path='$'):
    if type(a) is not type(b):
        return [path]
    if isinstance(a, dict):
        return [p for key in sorted(set(a) | set(b)) for p in
                ([path + '.' + key] if key not in a or key not in b else changed_paths(a[key], b[key], path + '.' + key))]
    if isinstance(a, list):
        if len(a) != len(b):
            return [path]
        return [p for n, (x, y) in enumerate(zip(a, b)) for p in changed_paths(x, y, '%s[%d]' % (path, n))]
    return [] if a == b else [path]


def body_view(body):
    result = copy.deepcopy(body)
    user = result.get('metadata', {}).get('user_id')
    if isinstance(user, str):
        try:
            result['metadata']['user_id'] = json.loads(user)
        except ValueError:
            pass
    return result


def allowed_diff(sent, received):
    """Only §12 step 1; never ignore whole system, metadata or header objects."""
    a, z = body_view(sent['body']), body_view(received['body'])
    paths = changed_paths(a, z)
    allowed = {'$.metadata.user_id.account_uuid', '$.metadata.user_id.device_id'}
    unexpected = []
    for path in paths:
        if path in allowed:
            # Missing/extra fields are not credential replacement.
            old = a.get('metadata', {}).get('user_id', {})
            new = z.get('metadata', {}).get('user_id', {})
            field = path.rsplit('.', 1)[1]
            if isinstance(old, dict) and isinstance(new, dict) and isinstance(old.get(field), str) and isinstance(new.get(field), str):
                continue
        match = re.fullmatch(r'\$\.system\[(\d+)\]\.text', path)
        if match:
            n = int(match.group(1))
            x, y = a['system'][n]['text'], z['system'][n]['text']
            pattern = r'(?<= cch=)[0-9a-f]{5}(?=;)'
            if isinstance(x, str) and isinstance(y, str) and x.startswith('x-anthropic-billing-header:') \
                    and y.startswith('x-anthropic-billing-header:') and re.search(pattern, x) and re.search(pattern, y) \
                    and re.sub(pattern, '<signature>', x) == re.sub(pattern, '<signature>', y):
                continue
        unexpected.append(path)
    transport = {'host', 'content-length', 'connection', 'transfer-encoding'}
    h1 = {k.lower(): v for k, v in sent['headers'].items() if k.lower() not in transport}
    h2 = {k.lower(): v for k, v in received['headers'].items() if k.lower() not in transport}
    for key in sorted(set(h1) | set(h2)):
        if h1.get(key) == h2.get(key):
            continue
        if key == 'authorization' and h1.get(key) and h2.get(key):
            continue
        if key == 'anthropic-beta':
            old = [x.strip() for x in h1.get(key, '').split(',') if x.strip()]
            new = [x.strip() for x in h2.get(key, '').split(',') if x.strip()]
            oauth = 'oauth-2025-04-20'
            if [v for v in new if v != oauth] == [v for v in old if v != oauth] \
                    and new.count(oauth) == max(1, old.count(oauth)):
                continue
        unexpected.append('$.headers.' + key)
    return unexpected


HOME = Path.home()
REPO = Path(__file__).resolve().parent


def free_ports(n):
    sockets = []
    try:
        for _ in range(n):
            sock = socket.socket()
            sock.bind(('127.0.0.1', 0))
            sockets.append(sock)
        return [sock.getsockname()[1] for sock in sockets]
    finally:
        for sock in sockets:
            sock.close()


class Capture(unittest.TestCase):
    report = {}

    def test_captured_engine_requests_through_binary(self):
        binary = os.environ.get('CODEXPOOL_CPA_BINARY')
        fixtures = Path(os.environ.get('CODEXPOOL_CPA_FIXTURES', REPO / 'tests/cpa/captured'))
        if not binary or not Path(binary).is_file() or not (fixtures / 'manifest.json').is_file():
            self.skipTest('E4.2 captures and CPA binary required; no live captures are bundled yet')
        manifest = json.loads((fixtures / 'manifest.json').read_text())
        self.__class__.report = {k: manifest.get(k) for k in ('cpa_sha256', 'claude_version', 'codex_revision')}
        self.__class__.report['unexpected_paths'] = []
        self.assertEqual(manifest['cpa_sha256'], hashlib.sha256(Path(binary).read_bytes()).hexdigest(), 'capture/build mismatch')
        self.assertTrue(manifest.get('claude_version') and manifest.get('codex_revision'))
        required = {'say-ok', 'tools', 'resumed-thinking', 'cache-max', 'helper'}
        self.assertTrue(required <= set(manifest['fixtures']))
        self.assertIn(manifest.get('subagent'), ('captured', 'not emitted in v0'))
        if manifest['subagent'] == 'captured':
            self.assertIn('subagent', manifest['fixtures'])
        captures = queue.Queue()
        blocked = []
        upstream_port, pool_port = free_ports(2)
        class Upstream(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass
            def do_CONNECT(self):
                # Never tunnel to the Internet, including CPA's synthetic OAuth profile lookup.
                self.send_error(403, 'Forbidden')
            def do_GET(self):
                blocked.append(self.path)
                self.send_error(403)
            def do_POST(self):
                target = urllib.parse.urlsplit(self.path)
                if target.hostname not in (None, '127.0.0.1') or target.path != '/v1/messages':
                    blocked.append(self.path)
                    self.send_error(403)
                    return
                body = json.loads(self.rfile.read(int(self.headers.get('Content-Length', 0))))
                captures.put({'body': body, 'headers': dict(self.headers)})
                response = dict(id='msg_test', type='message', role='assistant', model=body['model'],
                                content=[{'type': 'text', 'text': 'ok'}], stop_reason='end_turn',
                                usage={'input_tokens': 1, 'output_tokens': 1})
                if body.get('stream'):
                    events = [('message_start', dict(type='message_start', message=dict(response, content=[], stop_reason=None))),
                              ('message_delta', dict(type='message_delta', delta={'stop_reason': 'end_turn'}, usage={'output_tokens': 1})),
                              ('message_stop', {'type': 'message_stop'})]
                    data = ''.join('event: %s\ndata: %s\n\n' % (kind, json.dumps(value)) for kind, value in events).encode()
                    content_type = 'text/event-stream'
                else:
                    data, content_type = json.dumps(response).encode(), 'application/json'
                self.send_response(200)
                self.send_header('Content-Type', content_type)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)
        server = http.server.ThreadingHTTPServer(('127.0.0.1', upstream_port), Upstream)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(dir=HOME) as temp:
                work = Path(temp)
                (work / 'auth').mkdir()
                proxy = 'http://127.0.0.1:%d' % upstream_port
                # Fabricated OAuth-shaped key triggers credential-bound paths, never a real token.
                fake = 'sk-ant-oat01-' + 'synthetic-test-only'
                config = dict(host='127.0.0.1', port=pool_port, **{'auth-dir': str(work / 'auth'), 'api-keys': [],
                    'remote-management': {'disable-control-panel': True, 'disable-auto-update-panel': True},
                    'proxy-url': proxy, 'disable-claude-cloak-mode': True, 'request-retry': 0,
                    'claude-code': {'disable-cloaking-model-list': True},
                    'claude-api-key': [{'api-key': fake, 'base-url': proxy, 'proxy-url': proxy,
                                        'models': [{'name': model, 'alias': model} for model in manifest['models']]}]})
                (work / 'config.yaml').write_text(json.dumps(config))  # JSON is YAML
                env = dict(PATH=os.environ['PATH'], HOME=str(work), TMPDIR=str(work), CODEXPOOL_GATE_PROFILE='claude',
                           HTTP_PROXY=proxy, HTTPS_PROXY=proxy, ALL_PROXY=proxy)
                with (work / 'output.log').open('w') as log:
                    process = subprocess.Popen([str(Path(binary).resolve()), '-config', str(work / 'config.yaml')],
                                               cwd=work, env=env, stdout=log, stderr=log)
                    try:
                        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
                        headers = {'User-Agent': 'claude-cli/%s (external, sdk-cli)' % manifest['claude_version'], 'x-app': 'cli'}
                        deadline = time.monotonic() + 15
                        while True:
                            try:
                                with opener.open(urllib.request.Request('http://127.0.0.1:%d/v1/models' % pool_port,
                                                                       headers=headers), timeout=1):
                                    break
                            except (OSError, urllib.error.URLError):
                                if process.poll() is not None or time.monotonic() >= deadline:
                                    self.fail('isolated CPA did not start; inspect binary/config compatibility')
                                time.sleep(0.1)
                        for name, filename in manifest['fixtures'].items():
                            with self.subTest(fixture=name):
                                fixture = json.loads((fixtures / filename).read_text())
                                sent = dict(body=fixture['body'], headers=dict(fixture['headers']))
                                sent['headers']['Authorization'] = 'Bearer codexpool'
                                sent['headers']['Content-Type'] = 'application/json'
                                request = urllib.request.Request('http://127.0.0.1:%d/v1/messages' % pool_port,
                                                                  data=json.dumps(sent['body']).encode(), headers=sent['headers'])
                                with opener.open(request, timeout=30) as response:
                                    response.read()
                                received = captures.get(timeout=2)
                                self.assertEqual({k.lower(): v for k, v in received['headers'].items()}.get('authorization'),
                                                 'Bearer ' + fake)
                                paths = allowed_diff(sent, received)
                                known = json.loads(os.environ.get('CODEXPOOL_CPA_KNOWN_NORMALISATIONS', '[]'))
                                paths = [p for p in paths if p not in known]
                                self.__class__.report['unexpected_paths'].extend(paths)
                                self.assertEqual(paths, [], 'pool rewrites Claude requests: ' + ', '.join(paths))
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(2)


if __name__ == '__main__':
    result_path = os.environ.get('CODEXPOOL_CPA_RESULT')
    if result_path:
        program = unittest.main(exit=False)
        report = dict(Capture.report, ok=program.result.wasSuccessful() and not program.result.skipped)
        Path(result_path).write_text(json.dumps(report))
        raise SystemExit(0 if report['ok'] else 1)
    unittest.main()
