from _engine_helpers import le
"""lanes/bridge.py: how it adapts Codex requests for a lane provider and adapts the answers back (pure parts)."""
import hashlib
import json
import hashlib
import random
import string
import unittest
import io
import os
import sys
import tempfile
import time
import types
import uuid
from pathlib import Path
from unittest import mock

from _helpers import HOME, REPO
from _engine_helpers import load_bridge, load_core_bridge
core_bridge = load_core_bridge()

b = load_bridge()
KEY = '12345678-1111-4111-8111-123456789abc'
ROUTE = {'upstream': 'x', 'upstream_model': 'muse-up', 'summary_max_output_tokens': 8192}


def user(text, **extra):
    return dict(role='user', content=text, **extra)


def stream_event(kind, **extra):
    return {'type': 'stream_event', 'event': dict(type=kind, **extra)}


def text_events(text='answer', reason='end_turn'):
    return [stream_event('message_start'),
            stream_event('content_block_start', index=0, content_block={'type': 'text', 'text': ''}),
            stream_event('content_block_delta', index=0, delta={'type': 'text_delta', 'text': text}),
            stream_event('content_block_stop', index=0),
            stream_event('message_delta', delta={'stop_reason': reason}),
            {'type': 'assistant', 'message': {'content': [{'type': 'text', 'text': text}],
                                             'usage': {'input_tokens': 20, 'cache_read_input_tokens': 100,
                                                       'cache_creation_input_tokens': 30}}}]


class EngineInput(unittest.TestCase):
    def setUp(self):
        self.sealer = b.Sealer(b't' * 32)

    def test_metadata_is_labels_only_and_key_fallback(self):
        raw = dict(thread_id=KEY, parent_thread_id=KEY, forked_from_thread_id=KEY, subagent_kind='sienna',
                   sandbox_mode='danger-full-access', cwd='/forged', env={'bad': 'bad'}, extra={'tools': ['Bash']})
        meta = b.turn_metadata({'X-Codex-Turn-Metadata': json.dumps(raw)})
        self.assertEqual(set(meta), {'thread_id', 'parent_thread_id', 'forked_from_thread_id', 'subagent_kind'})
        self.assertEqual(b.thread_key(meta, {}), KEY)
        expected = str(uuid.uuid5(uuid.NAMESPACE_URL, 'codexpool:cache'))
        self.assertEqual(b.thread_key(None, {'prompt_cache_key': 'cache'}), expected)
        for raw in (None, 'garbled', '[]', 'null'):
            self.assertIsNone(b.turn_metadata({'x-codex-turn-metadata': raw}))
        with self.assertRaises(b.BridgeError) as error:
            b.thread_key({'thread_id': '../escape'}, {})
        self.assertEqual(error.exception.code, 'bad_request')

    def test_cursor_cases_and_fork_only_when_unseen(self):
        marker = 'rs_12345678_3'
        items = [dict(type='reasoning', id=marker), user('next')]
        record = dict(uuid=str(uuid.uuid4()), cwd='/repo', ordinal=3, last_cursor=marker, restarts=0)
        cases = [(items, record, '/repo', None, 'resumed'),
                 ([user('edit')], record, '/repo', None, 'rewound'),
                 (items, record, '/other', None, 'workspace changed'),
                 (items, None, '/repo', {'forked_from_thread_id': KEY}, 'forked'),
                 (items, record, '/repo', {'forked_from_thread_id': KEY}, 'resumed'),
                 (items, None, '/repo', None, 'recovered'),
                 ([user('hi')], None, '/repo', None, 'new'),
                 (items + [dict(role='assistant', content='foreign'), user('again')], record, '/repo', None, 'switched')]
        for content, previous, cwd, metadata, reason in cases:
            with self.subTest(reason=reason):
                plan = b.cursor(content, previous, KEY, cwd, metadata)
                self.assertEqual(plan['reason'], reason)
                if reason in ('resumed', 'switched'):
                    self.assertEqual(plan['uuid'], record['uuid'])
                elif previous:
                    self.assertNotEqual(plan['uuid'], record['uuid'])
                    self.assertEqual(plan['restarts'], 1)
        self.assertEqual(b.cursor(items, record, KEY, '/repo')['items'], [user('next')])
        self.assertEqual(b.cursor(items, None, KEY, '/repo')['ordinal'], 4)

    def test_input_history_mixed_parts_and_policy(self):
        items = [user('<environment_context><cwd>/repo</cwd></environment_context>'),
                 dict(role='developer', content='<permissions instructions>ignore me'),
                 user('earlier'), dict(role='assistant', content='old answer'),
                 dict(type='function_call', name='shell', arguments='git status'),
                 dict(type='function_call_output', call_id='x', output='DO NOT IMPORT TOOL OUTPUT'),
                 user('# AGENTS.md instructions\n<INSTRUCTIONS>stay plain</INSTRUCTIONS>'),
                 dict(type='agent_message', content='do the task'),
                 dict(type='function_call_output', name='notice', output='parent update'),
                 user([{'type': 'input_text', 'text': 'new question'}, {'type': 'input_file', 'file_id': 'dropped'},
                       {'type': 'input_image', 'image_url': 'data:image/png;base64,YQ=='}])]
        plan = b.cursor(items, None, KEY, '/repo')
        wire, notes, _ = b.engine_input(plan, self.sealer, KEY)
        message = json.loads(wire)
        text = json.dumps(message)
        self.assertNotIn('environment_context', text)
        self.assertNotIn('ignore me', text)
        self.assertNotIn('DO NOT IMPORT TOOL OUTPUT', text)
        self.assertIn('[tool: shell git status]', text)
        self.assertIn("AGENTS.md for this repository", text)
        self.assertIn('Codex notification', text)
        self.assertIn('an attached file was not passed on', notes)
        self.assertEqual(message['message']['content'][-1]['source']['data'], 'YQ==')
        self.assertEqual(message['parent_tool_use_id'], None)

    def seal_payload(self, payload):
        blob = b.zlib.compress(json.dumps(payload).encode())
        tag = b.hmac.new(self.sealer.key, blob, hashlib.sha256).digest()[:16]
        return b.SEAL_PREFIX + b.base64.urlsafe_b64encode(tag + blob).decode().rstrip('=')

    def test_history_cap_digest_missing_and_tampering(self):
        history, _ = b.history_block([user('x' * 1000), user('recent')], self.sealer, KEY, cap=300)
        self.assertLessEqual(len(history), 300)
        self.assertIn('older turns omitted', history)
        self.assertIn('recent', history)
        for payload, expected in (({'v': 3, 'thread': KEY, 'digest': 'a useful digest'}, 'a useful digest'),
                                  ({'v': 2, 'thread': KEY}, 'earlier history was compacted')):
            item = dict(type='compaction', encrypted_content=self.seal_payload(payload))
            text, compacted = b.history_block([item], self.sealer, KEY)
            self.assertIn(expected, text)
            self.assertEqual(compacted, 'digest' not in payload)
            _, notes, _ = b.engine_input({'items': [item, user('next')]}, self.sealer, KEY)
            self.assertEqual('earlier turns compacted' in ' '.join(notes), compacted)
        for value in (self.seal_payload({'thread': str(uuid.uuid4()), 'digest': 'wrong'}), b.SEAL_PREFIX + 'bad'):
            with self.assertRaises(b.BridgeError) as error:
                b.history_block([dict(type='compaction', encrypted_content=value)], self.sealer, KEY)
            self.assertEqual(error.exception.code, 'bad_checkpoint')

    def test_bad_input_and_workspace_selection(self):
        for items in ([user('')], [user([{'type': 'input_image', 'image_url': 'https://example.invalid/x'}])],
                      [user('x' * b.ENGINE_INPUT_LIMIT)]):
            with self.assertRaises(b.BridgeError) as error:
                b.engine_input({'items': items}, self.sealer, KEY)
            self.assertEqual(error.exception.code, 'bad_request')
        self.assertEqual(b.workspace({'input': [user('<environment_context><cwd>/one</cwd></environment_context>'),
                                                 user('<environment_context><cwd>/two</cwd></environment_context>')]}), '/two')
        with self.assertRaises(b.BridgeError) as error:
            b.workspace({'input': [user('no workspace')]})
        self.assertEqual(error.exception.code, 'no_workspace')


class EngineWorkspace(unittest.TestCase):
    def setUp(self):
        if sys.version_info < (3, 11):
            self.skipTest('Codex trust parsing needs the engine runtime Python 3.11+')
        self.temp = tempfile.TemporaryDirectory(dir=HOME)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo = self.home / 'repo'
        self.repo.mkdir()
        (self.repo / '.git').mkdir()
        self.config = self.home / 'trust.toml'

    def check(self, path):
        return b.cwd_ok(str(path), self.config, self.home)

    def test_table_inline_canonical_spelled_and_git_root(self):
        child = self.repo / 'src'
        child.mkdir()
        alias = self.home / 'link'
        alias.symlink_to(self.repo, target_is_directory=True)
        for spelling in (self.repo, alias):
            for text in ('[projects.%s]\ntrust_level="trusted"' % json.dumps(str(spelling)),
                         '[projects]\n%s={trust_level="trusted"}' % json.dumps(str(spelling))):
                self.config.write_text(text)
                self.assertEqual(self.check(alias), str(self.repo))
                self.assertEqual(self.check(alias / 'src'), str(child))
        self.config.write_text('[projects]\n%s={trust_level="untrusted"}\n%s={trust_level="trusted"}' % (
            json.dumps(str(child)), json.dumps(str(self.repo))))
        with self.assertRaises(b.BridgeError):
            self.check(child)

    def test_protected_untrusted_absent_nested_worktree(self):
        protected = self.home / '.codexpool' / 'nested'
        protected.mkdir(parents=True)
        alias = self.home / 'protected-link'
        alias.symlink_to(protected, target_is_directory=True)
        nested = self.repo / 'nested'
        nested.mkdir()
        (nested / '.git').mkdir()
        worktree = self.home / 'worktree'
        worktree.mkdir()
        (worktree / '.git').write_text('gitdir: somewhere')
        paths = [self.home, Path('/'), protected, alias, nested, worktree]
        for path in paths:
            self.config.write_text('[projects]\n%s={trust_level="trusted"}' % json.dumps(str(path)))
            with self.subTest(path=path), self.assertRaises(b.BridgeError):
                self.check(path)
        for config in ('[projects]', '[projects]\n%s={trust_level="untrusted"}' % json.dumps(str(self.repo)), 'invalid TOML:'):
            self.config.write_text(config)
            with self.assertRaises(b.BridgeError):
                self.check(self.repo)
        for path in ('relative', self.home / 'absent'):
            with self.assertRaises(b.BridgeError):
                self.check(path)


class EngineTranslation(unittest.TestCase):
    def translator(self):
        return b.EngineEvents(KEY, 4, 'lane-model', str(HOME / 'repo'), 'Claude · read-only', 2)

    def test_text_once_phases_ids_and_usage(self):
        for reason, phase in (('end_turn', 'final_answer'), ('tool_use', 'commentary')):
            t = self.translator()
            out = t.start()
            for value in text_events(reason=reason):
                out += t.feed(value)
            out += t.feed({'type': 'result', 'subtype': 'success', 'usage': {'output_tokens': 40}})
            deltas = [e['delta'] for e in out if e['type'] == 'response.output_text.delta']
            self.assertEqual(deltas, ['answer'])
            message = next(e['item'] for e in out if e['type'] == 'response.output_item.done' and e['item']['type'] == 'message')
            self.assertEqual(message['phase'], phase)
            self.assertEqual(message['id'], 'msg_12345678_4_1')
            self.assertEqual([e['sequence_number'] for e in out], list(range(len(out))))
            self.assertNotIn('encrypted_content', json.dumps(out))
            usage = out[-1]['response']['usage']
            self.assertEqual((usage['input_tokens'], usage['input_tokens_details']['cached_tokens'], usage['output_tokens']), (150, 100, 40))
            self.assertEqual(out[-1]['type'], 'response.completed')

    def test_multiple_text_blocks_only_last_final(self):
        t = self.translator()
        out = t.start()
        for n in range(2):
            out += t.feed(stream_event('content_block_start', index=n, content_block={'type': 'text'}))
            out += t.feed(stream_event('content_block_delta', index=n, delta={'type': 'text_delta', 'text': str(n)}))
            out += t.feed(stream_event('content_block_stop', index=n))
        self.assertFalse(any(e['type'] == 'response.output_item.done' for e in out))
        done = t.feed(stream_event('message_delta', delta={'stop_reason': 'end_turn'}))
        self.assertEqual([e['item']['phase'] for e in done if 'item' in e], ['commentary', 'final_answer'])

    def test_tools_thinking_denial_and_redaction(self):
        t = self.translator()
        out = t.start()
        out += t.feed(stream_event('content_block_delta', index=0, delta={'type': 'thinking_delta', 'thinking': 'thinking'}))
        for n, name in enumerate(('Read', 'Grep', 'Glob')):
            block = {'type': 'tool_use', 'id': 'tool%d' % n, 'name': name,
                     'input': {'file_path': str(HOME / 'repo' / 'src.py'), 'pattern': 'SECRET PATTERN'}}
            out += t.feed(stream_event('content_block_start', index=n, content_block=block))
            out += t.feed(stream_event('content_block_stop', index=n))
            out += t.feed({'type': 'assistant', 'message': {'content': [block]}})
            out += t.feed({'type': 'user', 'message': {'content': [{'type': 'tool_result', 'tool_use_id': block['id'],
                'is_error': n == 1, 'content': 'SECRET ERROR'}]}})
        out += t.feed({'type': 'system', 'subtype': 'permission_denied', 'tool_name': 'Read',
                       'tool_input': {'file_path': str(HOME / 'outside' / 'secret')}})
        out += t.feed({'type': 'system', 'subtype': 'api_retry', 'error': 'SECRET ERROR'})
        out += t.feed({'type': 'result', 'subtype': 'error_max_turns', 'is_error': True})
        summary = [p['text'] for p in out[-1]['response']['output'][0]['summary']]
        self.assertEqual(summary[1:4], ['Read src.py · ok', 'Grep · error', 'Glob · ok'])
        self.assertIn('denied: Read (outside workspace)', summary)
        self.assertIn('stopped: turn limit (2)', summary)
        self.assertNotIn('SECRET', json.dumps(out))
        self.assertNotIn(str(HOME), json.dumps(out))
        self.assertEqual(out[-1]['response']['output'][0]['content'][0]['text'], 'thinking')
        self.assertFalse(any(e.get('item', {}).get('type') == 'function_call' for e in out))

    def test_errors_limits_and_codes(self):
        expected = {'pool_down': 503, 'engine_unavailable': 502, 'engine_timeout': 504, 'session_busy': 503,
                    'engine_untested': 400, 'engine_misconfigured': 400, 'untrusted_workspace': 400,
                    'no_workspace': 400, 'bad_request': 400, 'bad_checkpoint': 400,
                    'engine_error': 502, 'engine_exited': 502, 'rate_limit_exceeded': 429}
        reserved = {'context_length_exceeded', 'insufficient_quota', 'credit_balance_exhausted',
                    'organization_spend_limit_exceeded', 'project_spend_limit_exceeded', 'usage_not_included',
                    'cyber_policy', 'bio_policy', 'misalignment_policy_violation', 'invalid_prompt'}
        for code, status in expected.items():
            error = b.engine_error(code)
            self.assertEqual((error.status, error.body()['error']['type']), (status, code))
            self.assertIsInstance(error.body()['error']['code'], str)
            self.assertNotIn(code, reserved)
        t = self.translator()
        t.feed({'type': 'rate_limit_event', 'rate_limit_info': {'status': 'rejected', 'resetsAt': int(time.time()) + 60}})
        failed = t.feed({'type': 'result', 'subtype': 'error_during_execution', 'is_error': True})[-1]
        detail = failed['response']['error']
        self.assertEqual(detail['code'], 'rate_limit_exceeded')
        self.assertRegex(detail['message'], r'(?i)try again in\s*(\d+(?:\.\d+)?)\s*(s|ms|seconds?)')
        for subtype in ('error_during_execution', 'error_max_budget_usd', 'error_max_structured_output_retries'):
            error = self.translator().feed({'type': 'result', 'subtype': subtype, 'is_error': True})[-1]['response']['error']
            self.assertEqual((error['code'], error['type']), ('engine_error', 'server_error'))
        memory = b.LimitMemory()
        with mock.patch.object(b.time, 'time', return_value=100):
            memory.set('one', 120)
            self.assertEqual(memory.get('one'), 120)
            self.assertIsNone(memory.get('two'))
        with mock.patch.object(b.time, 'time', return_value=121):
            self.assertEqual(memory.get('one', consume=True), 120)
            self.assertIsNone(memory.get('one'))
        memory.set('one')
        memory.clear('one')
        self.assertIsNone(memory.get('one'))


class RecordingHandler:
    def __init__(self):
        self.headers = {'x-codex-turn-metadata': json.dumps({'thread_id': KEY, 'turn_id': 'turn-one'})}
        self.events, self.statuses, self.json = [], [], None
        self.wfile = io.BytesIO()

    def start_sse(self):
        self.statuses.append(200)

    def send_event(self, event):
        self.events.append(event)

    def send_json(self, status, response):
        self.statuses.append(status)
        self.json = response


class EngineHarness(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HOME)
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.repo, self.profile = self.home / 'repo', self.home / '.codexpool' / 'lanes' / 'test-home'
        self.repo.mkdir()
        (self.profile / 'tmp').mkdir(parents=True)
        (self.home / '.codex').mkdir()
        (self.home / '.codex' / 'config.toml').write_text('[projects]\n%s={trust_level="trusted"}' % json.dumps(str(self.repo)))
        binary = self.home / '.local' / 'versions' / '2.1.283'
        binary.parent.mkdir(parents=True)
        source = (Path(__file__).parent / 'fixtures' / 'stub_claude.py').read_text().split('\n', 1)[1]
        binary.write_text('#!' + sys.executable + '\n' + source)
        binary.chmod(0o755)
        bindir = self.home / '.local' / 'bin'
        bindir.mkdir()
        (bindir / 'claude').symlink_to(binary)
        (bindir / 'claude-pool').symlink_to(binary)
        self.expect = {'tools': ['Read', 'Grep', 'Glob'], 'permissionMode': 'default', 'apiKeySource': 'none',
                       'mcp_servers': [], 'plugins': []}
        env = {'PATH': '/usr/bin:/bin', 'HOME': str(self.home), 'USER': 'test', 'LANG': 'en_US.UTF-8',
               'SHELL': '/bin/sh', 'TMPDIR': str(self.profile / 'tmp'), 'CLAUDE_CONFIG_DIR': str(self.profile),
               'ANTHROPIC_AUTH_TOKEN': 'codexpool', 'CLAUDEPOOL': 'required', 'DISABLE_AUTOUPDATER': '1',
               'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB': '1'}
        self.engine = dict(launcher=str(bindir / 'claude-pool'), launcher_sha256=hashlib.sha256(binary.read_bytes()).hexdigest(),
                           profile=str(self.profile), env=env,
                           settings=b.engine_settings(), accepted_versions=['2.1.283'], init_expect={'2.1.283': self.expect})
        self.route = dict(extension='sienna', engine='test', upstream_model='test-model', effort='medium', max_turns=3, turn_timeout=20)
        self.cfg = types.SimpleNamespace(engines={'test': self.engine})
        self.service = b.EngineService(self.cfg, b.Sealer(b'k' * 32), self.home / 'state')
        cpa = self.home / '.codexpool' / 'bin' / 'claude-current' / 'cli-proxy-api'
        cpa.parent.mkdir(parents=True)
        cpa.write_bytes(b'synthetic CPA; never executed')
        b.private_json(self.service.state / 'engine-cpa-check.json', dict(ok=True, claude_version='2.1.283',
                       cpa_sha256=hashlib.sha256(cpa.read_bytes()).hexdigest(), known_normalisations=[]))
        self.service.startup_timeout = 5
        self.body = {'model': 'sienna', 'stream': True, 'input': [
            user('<environment_context><cwd>%s</cwd></environment_context>' % self.repo), user('question')]}
        self.handler = RecordingHandler()
        self.fixture()
        self.peer = mock.patch.object(b, 'peer_closed', return_value=False)
        self.peer.start()
        self.addCleanup(self.peer.stop)
        # The sandbox may forbid ps even for our own child; return an identity only for this test's new PID.
        self.identity = mock.patch.object(b, 'process_identity', side_effect=lambda pid: dict(pid=pid, pgid=pid, lstart='test'))
        self.identity.start()
        self.addCleanup(self.identity.stop)
        if sys.version_info < (3, 11):
            self.trust = mock.patch.object(b, 'cwd_ok', return_value=str(self.repo))
            self.trust.start()
            self.addCleanup(self.trust.stop)

    def init(self, **changes):
        return dict(self.expect, type='system', subtype='init', claude_code_version='2.1.283',
                    cwd=str(self.repo), model='test-model', **changes)

    def fixture(self, events=None, **extra):
        if events is None:
            events = [self.init()] + text_events() + [{'type': 'result', 'subtype': 'success', 'usage': {'output_tokens': 5}}]
        (self.profile / 'fixture.json').write_text(json.dumps(dict(events=events, **extra)))

    def run_engine(self):
        return self.service.run(self.handler, self.route, self.body)

    def ledger(self):
        return [json.loads(line) for line in (self.home / 'state' / 'engine-turns' / (KEY + '.jsonl')).read_text().splitlines()]

    def test_preflight_settings_environment_and_launch(self):
        version, env = b.engine_preflight(self.engine)
        self.assertEqual(version, '2.1.283')
        self.assertEqual(set(env), set(b.ENGINE_ENV_KEYS))
        from _helpers import cp
        self.assertEqual(b.engine_settings(), le.engine_settings())
        for resume in (False, True):
            args = b.launch_args(self.engine, self.route, {'resume': resume, 'uuid': KEY}, str(self.repo), 'assigned role', True)
            self.assertIn('--resume' if resume else '--session-id', args)
            self.assertEqual(args[args.index('--tools') + 1], 'Read,Grep,Glob')
            self.assertEqual(json.loads(args[args.index('--settings') + 1]), le.engine_settings())
            self.assertEqual(json.loads(args[args.index('--mcp-config') + 1]), {'mcpServers': {}})
            for flag in ('--permission-mode', '--dangerously-skip-permissions', '--add-dir', '--bare'):
                self.assertNotIn(flag, args)
        self.engine['env']['UNEXPECTED'] = 'not inherited'
        with self.assertRaises(b.BridgeError) as error:
            b.engine_preflight(self.engine)
        self.assertEqual(error.exception.code, 'engine_misconfigured')

    def test_init_each_field_and_version_refusal_without_spawn(self):
        b.init_check(self.init(), self.engine, '2.1.283', str(self.repo), 'test-model')
        for field, value in (('tools', ['Read', 'Grep', 'Glob', 'Bash']), ('permissionMode', 'plan'),
                             ('apiKeySource', 'oauth'), ('mcp_servers', ['remote']), ('plugins', ['plugin']),
                             ('cwd', '/wrong'), ('model', 'wrong'), ('claude_code_version', '0.0.0')):
            init = self.init()
            init[field] = value
            with self.subTest(field=field), self.assertRaises(b.BridgeError) as error:
                b.init_check(init, self.engine, '2.1.283', str(self.repo), 'test-model')
            self.assertIn(field, error.exception.message)
        self.engine['accepted_versions'] = []
        with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError) as error:
            self.run_engine()
        spawn.assert_not_called()
        self.assertEqual((error.exception.status, error.exception.code), (400, 'engine_untested'))

    def test_stub_success_ledger_session_and_resume(self):
        self.assertEqual(self.run_engine(), 200)
        self.assertEqual(self.handler.statuses, [200])
        self.assertEqual(self.handler.events[-1]['type'], 'response.completed')
        self.assertEqual([line['state'] for line in self.ledger()], ['accepted', 'running', 'completed'])
        capture = json.loads((self.profile / 'spawn.json').read_text())
        # macOS can inject this CoreFoundation bookkeeping variable at process startup.
        self.assertEqual(set(capture['env']) - {'__CF_USER_TEXT_ENCODING'}, set(b.ENGINE_ENV_KEYS))
        self.assertEqual(capture['cwd'], str(self.repo))
        self.assertEqual(json.loads(capture['stdin'])['message']['content'], [{'type': 'text', 'text': 'question'}])
        path = self.home / 'state' / 'engine-sessions' / (KEY + '.json')
        record = json.loads(path.read_text())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.body['input'] += self.handler.events[-1]['response']['output'] + [user('next')]
        self.handler = RecordingHandler()
        self.run_engine()
        capture = json.loads((self.profile / 'spawn.json').read_text())
        self.assertIn('--resume', capture['argv'])
        self.assertEqual(json.loads(path.read_text())['uuid'], record['uuid'])
        self.assertNotIn('question', capture['stdin'])
        self.assertNotIn('answer', capture['stdin'])
        self.assertNotIn('history imported', json.dumps(self.handler.events))

    def test_nonstream_success(self):
        self.body['stream'] = False
        self.run_engine()
        self.assertEqual(self.handler.json['status'], 'completed')
        self.assertEqual(self.handler.events, [])

    def test_held_200_kills_before_error_and_holds_lock(self):
        init = self.init()
        init['apiKeySource'] = 'oauth'
        self.fixture([init], linger=True)
        with self.assertRaises(b.BridgeError) as error:
            self.run_engine()
        self.assertEqual(error.exception.code, 'engine_misconfigured')
        self.assertEqual(self.handler.statuses, [])
        capture = json.loads((self.profile / 'spawn.json').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(capture['pid'], 0)
        self.assertEqual(self.ledger()[-1]['state'], 'failed')
        with b.ThreadLock(self.home / 'state' / 'engine-sessions' / (KEY + '.lock'), timeout=0):
            pass

    def test_preinit_exit_timeout_limit_and_invalid_json(self):
        for fixture, expected in ((dict(events=[], exit=75), 'pool_down'),
                                  (dict(events=[], exit=1), 'engine_unavailable'),
                                  (dict(events=[], linger=True), 'engine_timeout'),
                                  (dict(events=[{'type': 'system', 'subtype': 'api_retry', 'api_error_status': 429},
                                                {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True}]), 'rate_limit_exceeded'),
                                  (dict(events=[{'raw': 'not json'}]), 'engine_error')):
            self.service.startup_timeout = 0.15 if fixture.get("linger") else 5
            self.service.limits.clear('test:test-model')
            self.fixture(**fixture)
            with self.subTest(expected=expected), self.assertRaises(b.BridgeError) as error:
                self.run_engine()
            self.assertEqual(error.exception.code, expected)
            self.assertEqual(self.handler.statuses, [])

    def test_postinit_error_limit_memory_and_no_second_spawn(self):
        self.fixture([self.init()] + text_events() + [
            {'type': 'system', 'subtype': 'api_retry', 'api_error_status': 429},
            {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True}])
        self.run_engine()
        self.assertEqual(self.handler.events[-1]['response']['error']['code'], 'rate_limit_exceeded')
        self.assertEqual(self.ledger()[-1]['state'], 'failed')
        with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError) as error:
            self.run_engine()
        self.assertEqual(error.exception.status, 429)
        spawn.assert_not_called()

    def test_postinit_exit_time_limit_and_stdin_deadline(self):
        self.fixture([self.init()] + text_events(), exit=1)
        self.run_engine()
        self.assertEqual(self.handler.events[-1]['response']['error']['code'], 'engine_exited')
        self.handler = RecordingHandler()
        self.route['turn_timeout'] = 0.1
        self.fixture([self.init()], linger=True)
        self.run_engine()
        self.assertEqual(self.handler.events[-1]['type'], 'response.completed')
        self.assertIn('stopped: time limit', json.dumps(self.handler.events))
        self.handler = RecordingHandler()
        self.fixture([], no_read=True)
        self.body['input'].append(user('x' * (1024 * 1024)))
        self.service.stdin_timeout = 0.15
        with self.assertRaises(b.BridgeError) as error:
            self.run_engine()
        self.assertEqual(error.exception.code, 'bad_request')
        self.assertEqual(self.handler.statuses, [])

    def test_lock_wait_busy_and_release(self):
        path = self.home / 'test.lock'
        with b.ThreadLock(path):
            start = time.monotonic()
            with self.assertRaises(b.BridgeError) as error:
                with b.ThreadLock(path, timeout=0.1):
                    self.fail('lock admitted two turns')
            self.assertGreaterEqual(time.monotonic() - start, 0.09)
            self.assertEqual((error.exception.status, error.exception.code), (503, 'session_busy'))
        with b.ThreadLock(path, timeout=0):
            pass

    def test_engine_config_routes(self):
        config = self.home / 'bridge.json'
        raw = {'engines': {'test': self.engine}, 'models': {'sienna': self.route}}
        config.write_text(json.dumps(raw))
        with mock.patch.object(core_bridge, 'read_secret', return_value='fake'), mock.patch.object(core_bridge, 'load_seal_key', return_value=b'k' * 32):
            cfg = b.Config(config)
        self.assertEqual(cfg.models['sienna'], self.route)
        self.assertEqual(b.Service(cfg, b.Sealer(b'k' * 32), self.home / 'state').cfg.engines['test'], self.engine)

    def test_exact_popen_environment_and_no_shell(self):
        original = b.subprocess.Popen
        with mock.patch.object(b.subprocess, 'Popen', wraps=original) as spawn:
            self.run_engine()
        args, kwargs = spawn.call_args
        self.assertEqual(kwargs['env'], self.engine['env'])
        self.assertTrue(kwargs['start_new_session'])
        self.assertFalse(kwargs.get('shell', False))
        self.assertEqual(args[0][0], self.engine['launcher'])

    def test_queued_cancel_never_spawns_and_records_admission(self):
        path = self.home / 'state' / 'engine-sessions' / (KEY + '.lock')
        with b.ThreadLock(path), mock.patch.object(b, 'peer_closed', return_value=True), \
                mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(ConnectionAbortedError):
            self.run_engine()
        spawn.assert_not_called()
        self.assertEqual([x['state'] for x in self.ledger()], ['accepted', 'cancelled'])
        self.assertIsNone(self.ledger()[-1]['started'])

    def test_cancel_after_init_terminates_stub(self):
        self.fixture([self.init()], linger=True)
        cancelled_at = []
        def cancel_when_initialized(_):
            if self.handler.statuses:
                if not cancelled_at:
                    cancelled_at.append(time.monotonic())
                return True
            return False
        with mock.patch.object(b, 'peer_closed', side_effect=cancel_when_initialized):
            with self.assertRaises(ConnectionAbortedError):
                self.run_engine()
        self.assertLess(time.monotonic() - cancelled_at[0], 1)
        self.assertEqual(self.ledger()[-1]['state'], 'cancelled')
        self.assertTrue((self.profile / 'signal.json').exists())
        capture = json.loads((self.profile / 'spawn.json').read_text())
        with self.assertRaises(ProcessLookupError):
            os.kill(capture['pid'], 0)

    def test_lock_is_held_during_kill_not_just_translation(self):
        init = self.init()
        init['tools'] = ['Bash']
        self.fixture([init], linger=True)
        original = b.EngineProcess.stop
        observed = []
        def stop(process):
            with self.assertRaises(b.BridgeError) as error:
                with b.ThreadLock(self.home / 'state' / 'engine-sessions' / (KEY + '.lock'), timeout=0):
                    pass
            observed.append(error.exception.code)
            return original(process)
        with mock.patch.object(b.EngineProcess, 'stop', stop), self.assertRaises(b.BridgeError):
            self.run_engine()
        self.assertTrue(observed)
        self.assertEqual(set(observed), {'session_busy'})

    def test_config_misconfiguration_refuses_without_spawn(self):
        self.expect['tools'].append('Bash')
        with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError) as error:
            self.run_engine()
        self.assertEqual(error.exception.code, 'engine_misconfigured')
        spawn.assert_not_called()

    def test_limit_from_result_and_stderr_does_not_leak(self):
        for events in ([{'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'api_error_status': 429}],
                       [self.init(), {'stderr': '{"error":{"type":"rate_limit_error","message":"SECRET QUOTA"}}'}, {'sleep': 0.05},
                        {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True}]):
            self.service.limits.clear('test:test-model')
            self.handler = RecordingHandler()
            self.fixture(events)
            if events[0]['type'] == 'result':
                with self.assertRaises(b.BridgeError) as error:
                    self.run_engine()
                self.assertEqual(error.exception.code, 'rate_limit_exceeded')
            else:
                self.run_engine()
                self.assertEqual(self.handler.events[-1]['response']['error']['code'], 'rate_limit_exceeded')
            self.assertNotIn('SECRET', json.dumps(self.handler.events))

    def test_malformed_postinit_event_is_sse_failure(self):
        self.fixture([self.init(), {'type': 'stream_event', 'event': {'type': 'content_block_delta', 'delta': 'wrong'}}])
        self.run_engine()
        self.assertEqual(self.handler.statuses, [200])
        self.assertEqual(self.handler.events[-1]['response']['error']['code'], 'engine_error')

    def test_no_workspace_and_untrusted_never_spawn(self):
        self.body['input'] = [user('question')]
        with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError) as error:
            self.run_engine()
        self.assertEqual(error.exception.code, 'no_workspace')
        spawn.assert_not_called()

    def test_success_clears_warning_limit_and_turn_limit_is_normal(self):
        self.fixture([self.init(), {'type': 'system', 'subtype': 'api_retry', 'api_error_status': 429}] + text_events() + [
            {'type': 'result', 'subtype': 'error_max_turns', 'is_error': True}])
        self.run_engine()
        self.assertEqual(self.handler.events[-1]['type'], 'response.completed')
        self.assertIsNone(self.service.limits.get('test:test-model'))
        self.assertEqual(self.ledger()[-1]['state'], 'completed')

    def test_writer_serializes_events_and_comments(self):
        handler = RecordingHandler()
        handler.send_event = lambda e: handler.wfile.write(('event: ' + e['type'] + '\ndata: {}\n\n').encode())
        writer = b.EngineWriter(handler)
        writer.put('start')
        for n in range(20):
            writer.put({'type': 'event-%d' % n})
            writer.put('keepalive')
        writer.close()
        expected = b''.join(('event: event-%d\ndata: {}\n\n: keepalive\n\n' % n).encode() for n in range(20))
        self.assertEqual(handler.wfile.getvalue(), expected)
        self.assertEqual(handler.statuses, [200])


    def test_handler_dispatch_and_wire_status(self):
        for mismatch in (False, True):
            init = self.init()
            if mismatch:
                init['apiKeySource'] = 'oauth'
            self.fixture([init] + text_events() + [{'type': 'result', 'subtype': 'success'}])
            handler = object.__new__(b.Handler)
            raw = json.dumps(self.body).encode()
            handler.headers = dict(self.handler.headers, Host='127.0.0.1:1234', Authorization='Bearer fake',
                                   **{'Content-Length': str(len(raw))})
            handler.path = '/lane/test/member/v1/responses'
            handler.request_version, handler.command = 'HTTP/1.1', 'POST'
            handler.requestline = 'POST /v1/responses HTTP/1.1'
            handler.wfile, handler.rfile = io.BytesIO(), io.BytesIO(raw)
            handler.bridge = types.SimpleNamespace(cfg=types.SimpleNamespace(key='fake', models={'sienna': self.route}),
                                                   run_extension=self.service.run)
            with mock.patch.object(core_bridge, 'log'), mock.patch.object(core_bridge, 'prepare') as prepare:
                handler.do_POST()
            prepare.assert_not_called()
            wire = handler.wfile.getvalue().decode()
            self.assertEqual(wire.count('HTTP/1.1'), 1, repr(wire))
            if mismatch:
                self.assertIn('400 Bad Request', wire)
                self.assertIn('engine_misconfigured', wire)
                self.assertNotIn('response.created', wire)
            else:
                self.assertIn('200 OK', wire)
                self.assertIn('event: response.created', wire)
                self.assertIn('event: response.completed', wire)


if __name__ == '__main__':
    unittest.main()
