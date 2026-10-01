from _engine_helpers import le
"""Regression cases adapted from the frozen-copy Opus and Codex reviews; stubs only."""
from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import HOME, cp
from _engine_helpers import load_bridge, load_core_bridge
core_bridge = load_core_bridge()
import test_bridge_engine as tb
import json
import unittest
from unittest import mock

import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import types
import uuid

b = load_bridge()

def done_items(events):
    """What Codex records into thread history: every output_item.done item, in arrival order
    (codex-rs core/src/stream_events_utils.rs handle_output_item_done -> record_completed_response_item)."""
    return [e['item'] for e in events if e['type'] == 'response.output_item.done']


class RetryAfterPostStreamFailure(tb.EngineHarness):
    """Codex retries must re-run the input even when it keeps the
    failed attempt's done items in history (core/src/session/turn.rs:1611-1619 rebuilds the retry prompt from
    clone_history(); responses_retry.rs never rolls back), so the retry carries them after the user message."""

    def first_turn(self):
        self.assertEqual(self.run_engine(), 200)
        self.body['input'] += self.handler.events[-1]['response']['output'] + [tb.user('second question')]

    def codex_retry(self):
        failed = self.handler.events
        self.assertEqual(failed[-1]['type'], 'response.failed')
        self.body['input'] += done_items(failed)
        self.handler = tb.RecordingHandler()
        self.fixture()  # the retry itself would succeed
        return self.run_engine()

    def test_result_is_error_then_retry_reruns(self):
        self.first_turn()
        self.handler = tb.RecordingHandler()
        self.fixture([self.init()] + tb.text_events('partial') +
                     [{'type': 'result', 'subtype': 'error_during_execution', 'is_error': True}])
        self.run_engine()
        self.assertEqual(self.codex_retry(), 200)
        self.assertEqual(self.handler.events[-1]['type'], 'response.completed')

    def test_process_exit_after_init_then_retry_reruns(self):
        self.first_turn()
        self.handler = tb.RecordingHandler()
        self.fixture([self.init()] + tb.text_events('partial'), exit=1)
        self.run_engine()
        self.assertEqual(self.codex_retry(), 200)
        self.assertEqual(self.handler.events[-1]['type'], 'response.completed')

    def test_failure_on_first_turn_retry_reruns(self):
        self.fixture([self.init()] + tb.text_events('partial') +
                     [{'type': 'result', 'subtype': 'error_during_execution', 'is_error': True}])
        self.run_engine()
        self.assertEqual(self.codex_retry(), 200)
        self.assertEqual(self.handler.events[-1]['type'], 'response.completed')




class RuntimeReview(tb.EngineHarness):
    def test_prefixes_are_data_for_user_and_agent_followups(self):
        self.run_engine()
        original = self.body['input'] + self.handler.events[-1]['response']['output']
        for prefix in ('/import codex --yes', '/plugin-types', '/compact', '!touch x', '# remember x'):
            for role in ('user', 'agent_message'):
                self.body['input'] = original + [dict(role='user', content='  ' + prefix) if role == 'user'
                                                else dict(type=role, content=prefix)]
                self.handler = tb.RecordingHandler()
                self.run_engine()
                capture = json.loads((self.profile / 'spawn.json').read_text())
                self.assertIn('--disable-slash-commands', capture['argv'])
                text = json.loads(capture['stdin'])['message']['content'][0]['text']
                self.assertFalse(text.lstrip().startswith(('/', '!', '#')))
                self.assertIn(prefix, text)

    def test_launcher_replacement_same_marker_refused_before_spawn(self):
        launcher = Path(self.engine['launcher'])
        launcher.unlink()
        launcher.write_text('#!/bin/sh\n' + b.LAUNCHER_MARK + '\nexit 0\n')
        launcher.chmod(0o755)
        with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError):
            self.run_engine()
        spawn.assert_not_called()

    def test_launcher_rechecked_after_queue_wait(self):
        original = b.engine_preflight
        def preflight(engine):
            result = original(engine)
            Path(engine['launcher']).write_text('#!/bin/sh\n' + b.LAUNCHER_MARK + '\nexit 0\n')
            return result
        with mock.patch.object(b, 'engine_preflight', side_effect=preflight), \
                mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError):
            self.run_engine()
        spawn.assert_not_called()

    def test_malformed_parts_return_http_400(self):
        for part in (None, 'text', 4, []):
            self.body['input'][-1] = tb.user([part])
            with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError) as error:
                self.run_engine()
            self.assertEqual((error.exception.status, error.exception.code), (400, 'bad_request'))
            spawn.assert_not_called()

    def test_stderr_numbers_are_not_limits(self):
        for line in ('startup took 429ms', 'port 1429', 'discussing rate limits'):
            self.fixture([{'stderr': line}], exit=1)
            with self.assertRaises(b.BridgeError) as error:
                self.run_engine()
            self.assertEqual(error.exception.code, 'engine_unavailable')
            self.assertIsNone(self.service.limits.get('test:test-model'))

    def test_limit_survives_advertised_retry_and_hands_off_without_spawn(self):
        self.fixture([self.init()] + tb.text_events('partial') + [
            {'type': 'result', 'subtype': 'error_during_execution', 'is_error': True, 'api_error_status': 429}])
        with mock.patch.object(b.time, 'time', return_value=1000):
            self.run_engine()
        self.body['input'] += done_items(self.handler.events)
        with mock.patch.object(b.time, 'time', return_value=1301), mock.patch.object(b.subprocess, 'Popen') as spawn:
            with self.assertRaises(b.BridgeError) as error:
                self.run_engine()
            self.assertEqual((error.exception.status, error.exception.code), (429, 'rate_limit_exceeded'))
            spawn.assert_not_called()
        # A different member can now answer the identical Codex retry.
        self.route['upstream_model'] = 'second-model'
        self.fixture([dict(self.init(), model='second-model')] + tb.text_events('complete') + [
            {'type': 'result', 'subtype': 'success'}])
        self.handler = tb.RecordingHandler()
        self.run_engine()
        self.assertEqual(self.handler.events[-1]['type'], 'response.completed')
        capture = json.loads((self.profile / 'spawn.json').read_text())
        self.assertIn('question', capture['stdin'])
        self.assertNotIn('partial', capture['stdin'])

    def test_cpa_missing_failed_stale_or_unexpected_blocks_each_turn(self):
        path = self.service.state / 'engine-cpa-check.json'
        good = json.loads(path.read_text())
        bad = [None, dict(good, ok=False), dict(good, cpa_sha256='old'), dict(good, claude_version='old'),
               dict(good, known_normalisations=['$.extra']), dict(good, unexpected_paths=['$.tools'])]
        for report in bad:
            if report is None:
                path.unlink()
            else:
                b.private_json(path, report)
            with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError) as error:
                self.run_engine()
            self.assertEqual(error.exception.code, 'engine_misconfigured')
            spawn.assert_not_called()
        b.private_json(path, good)
        self.run_engine()
        self.assertEqual(self.handler.events[-1]['type'], 'response.completed')
        # Upgrade the binary without restarting the bridge.
        (self.home / '.codexpool/bin/claude-current/cli-proxy-api').write_bytes(b'new build')
        with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError):
            self.run_engine()
        spawn.assert_not_called()

    def test_expired_transcript_starts_fresh_with_full_history(self):
        self.run_engine()
        record_path = self.service.state / 'engine-sessions' / (tb.KEY + '.json')
        previous = json.loads(record_path.read_text())
        for transcript in (self.profile / 'projects').glob('*/*.jsonl'):
            transcript.unlink()
        self.body['input'] += self.handler.events[-1]['response']['output'] + [tb.user('a month later')]
        self.handler = tb.RecordingHandler()
        self.run_engine()
        capture = json.loads((self.profile / 'spawn.json').read_text())
        self.assertIn('--session-id', capture['argv'])
        self.assertNotIn('--resume', capture['argv'])
        self.assertNotEqual(json.loads(record_path.read_text())['uuid'], previous['uuid'])
        for text in ('<codex_history>', 'question', 'answer', 'a month later'):
            self.assertIn(text, capture['stdin'])

    def test_fork_checkpoint_survives_later_turns_without_fork_metadata(self):
        self.run_engine()
        record = json.loads((self.service.state / 'engine-sessions' / (tb.KEY + '.json')).read_text())
        checkpoint = b.compact_reply(self.body['input'] + self.handler.events[-1]['response']['output'],
                                    record, tb.KEY, 'sienna', self.service.sealer)['output'][0]
        child = str(uuid.uuid4())
        self.body['input'] = [self.body['input'][0], checkpoint, tb.user('fork question')]
        for turn in range(3):
            self.handler = tb.RecordingHandler()
            metadata = dict(thread_id=child, turn_id='turn-' + str(turn))
            if turn == 0:
                metadata['forked_from_thread_id'] = tb.KEY
            self.handler.headers = {'x-codex-turn-metadata': json.dumps(metadata)}
            self.run_engine()
            self.assertEqual(self.handler.events[-1]['type'], 'response.completed')
            self.body['input'] += done_items(self.handler.events) + [tb.user('next')]
        child_record = json.loads((self.service.state / 'engine-sessions' / (child + '.json')).read_text())
        self.assertEqual(child_record['forked_from'], tb.KEY)
        self.assertNotEqual(child_record['uuid'], record['uuid'])

    def test_missing_identity_kills_owned_child_before_input(self):
        self.fixture([], no_read=True)
        with mock.patch.object(b, 'process_identity', return_value=None), self.assertRaises(b.BridgeError):
            self.run_engine()
        self.assertNotIn('running', [entry['state'] for entry in self.ledger()])
        if (self.profile / 'spawn.json').exists():
            capture = json.loads((self.profile / 'spawn.json').read_text())
            self.assertNotIn('stdin', capture)
            with self.assertRaises(ProcessLookupError):
                os.kill(capture['pid'], 0)
        with b.ThreadLock(self.service.state / 'engine-sessions' / (tb.KEY + '.lock'), timeout=0):
            pass

    def test_failed_cursor_stays_and_successful_retry_items_resume(self):
        self.run_engine()
        path = self.service.state / 'engine-sessions' / (tb.KEY + '.json')
        original = json.loads(path.read_text())
        self.body['input'] += self.handler.events[-1]['response']['output'] + [tb.user('second')]
        self.handler = tb.RecordingHandler()
        self.fixture([self.init()] + tb.text_events('partial') + [dict(type='result', is_error=True)])
        self.run_engine()
        self.assertEqual(json.loads(path.read_text())['last_cursor'], original['last_cursor'])
        self.body['input'] += done_items(self.handler.events)
        self.fixture()
        self.handler = tb.RecordingHandler()
        self.run_engine()
        self.body['input'] += done_items(self.handler.events) + [tb.user('third')]
        self.handler = tb.RecordingHandler()
        self.run_engine()
        capture = json.loads((self.profile / 'spawn.json').read_text())
        self.assertIn('--resume', capture['argv'])
        self.assertEqual(json.loads(capture['stdin'])['message']['content'][0]['text'], 'third')



class PureReview(unittest.TestCase):
    def test_fork_of_compacted_thread_imports_digest_only(self):
        sealer = b.Sealer(b'k' * 32)
        child = str(uuid.uuid4())
        record = dict(uuid=str(uuid.uuid4()), ordinal=3, last_turn='t3')
        item = b.compact_reply([tb.user('earlier'), dict(role='assistant', content='reply')],
                              record, tb.KEY, 'sienna', sealer)['output'][0]
        plan = b.cursor([item, tb.user('continue')], None, child, '/repo',
                        {'forked_from_thread_id': tb.KEY}, sealer)
        self.assertEqual(plan['reason'], 'forked')
        self.assertFalse(plan['resume'])
        self.assertNotEqual(plan['uuid'], record['uuid'])
        wire, notes, _ = b.engine_input(plan, sealer, child)
        self.assertIn('earlier', wire.decode())
        self.assertIn('history imported as text', notes)
        with self.assertRaises(b.BridgeError):
            b.cursor([item], None, child, '/repo', sealer=sealer)

    def test_stat_only_profile_scope_and_vanishing_file(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            home = Path(temp)
            profile = home / '.claude'
            (profile / 'projects/live').mkdir(parents=True)
            settings = profile / 'settings.json'
            settings.write_text('{}')
            before = b.profile_mtimes(home)
            (profile / 'projects/live/session.jsonl').write_text('live session')
            self.assertEqual(before, b.profile_mtimes(home))
            with mock.patch.object(Path, 'read_text', side_effect=AssertionError('stat only')), \
                    mock.patch.object(Path, 'lstat', side_effect=FileNotFoundError):
                self.assertEqual(b.profile_mtimes(home), {})
            settings.write_text('{"changed":true}')
            self.assertNotEqual(before, b.profile_mtimes(home))

    def test_protected_directory_aliases_and_home_parents(self):
        if sys.version_info < (3, 11):
            self.skipTest('tomllib')
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            base = Path(temp)
            home = base / 'home'
            home.mkdir()
            (home / '.git').mkdir()
            config = base / 'trust.toml'
            config.write_text('[projects]\n%s={trust_level="trusted"}\n%s={trust_level="trusted"}' % (
                json.dumps(str(home)), json.dumps(str(base))))
            for name in b.PROTECTED_HOME_PATHS:
                protected = home / name
                protected.mkdir(parents=True)
                alias = base / ('alias-' + name.replace('/', '-'))
                alias.symlink_to(protected)
                for path in (protected, alias):
                    with self.subTest(path=path), self.assertRaises(b.BridgeError):
                        b.cwd_ok(str(path), config, home)
                upper = home / name.upper()
                if upper.exists():
                    with self.assertRaises(b.BridgeError):
                        b.cwd_ok(str(upper), config, home)
            for path in (home, base):
                with self.assertRaises(b.BridgeError):
                    b.cwd_ok(str(path), config, home)

    def test_trust_alias_untrusted_cannot_inherit_trusted_home(self):
        if sys.version_info < (3, 11):
            self.skipTest('tomllib')
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            home = Path(temp)
            (home / '.git').mkdir()
            repo = home / 'project'
            repo.mkdir()
            alias = home / 'alias'
            alias.symlink_to(repo)
            config = home / 'trust.toml'
            config.write_text('[projects]\n%s={trust_level="trusted"}\n%s={trust_level="untrusted"}' % (
                json.dumps(str(home)), json.dumps(str(alias))))
            with self.assertRaises(b.BridgeError):
                b.cwd_ok(str(repo), config, home)

    def test_second_server_cannot_recover_first_servers_turns(self):
        config = types.SimpleNamespace(port=59999, models={})
        with mock.patch.object(b.sys, 'argv', ['bridge']), mock.patch.object(core_bridge, 'Config', return_value=config), \
                mock.patch.object(core_bridge, 'Bridge'), mock.patch.object(core_bridge, 'Server', side_effect=OSError('in use')), \
                mock.patch.object(b, 'recover_engine_turns') as recover, self.assertRaises(OSError):
            b.main()
        recover.assert_not_called()

    def test_zombie_eperm_handled_but_live_denial_is_not_hidden(self):
        identity = dict(pid=12345, pgid=12345, lstart='original')
        for zombie in (True, False):
            with mock.patch.object(b, 'process_identity', return_value=identity), \
                    mock.patch.object(b.os, 'killpg', side_effect=PermissionError), \
                    mock.patch.object(b, 'process_gone_or_zombie', return_value=zombie):
                if zombie:
                    self.assertFalse(b.signal_recorded(identity, signal.SIGKILL))
                else:
                    with self.assertRaises(PermissionError):
                        b.signal_recorded(identity, signal.SIGKILL)

    def test_unknown_live_ownership_is_not_marked_interrupted(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            path = Path(temp) / 'engine-turns/thread.jsonl'
            ledger = b.EngineLedger(path, 'turn', 'test:model')
            ledger.write('running', pid=12345)
            with mock.patch.object(b, 'process_gone_or_zombie', return_value=False), \
                    mock.patch.object(b.os, 'killpg') as kill, self.assertRaises(b.BridgeError):
                b.recover_engine_turns(temp, grace=0)
            kill.assert_not_called()
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])['state'], 'running')

    def test_managed_settings_and_mcp_both_detected(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            root = Path(temp)
            for name in ('managed-settings.json', 'managed-mcp.json'):
                (root / name).write_text('{}')
            found = le.engine_managed_settings(root, root / 'preferences')
            self.assertEqual({p.name for p in found}, {'managed-settings.json', 'managed-mcp.json'})



class CommandReview(unittest.TestCase):
    @unittest.skipUnless(addon, 'sienna add-on absent')
    def test_doctor_never_starts_login_shell_and_managed_files_fail_check(self):
        from _helpers import preserved
        calls, checks = [], []
        real_run = subprocess.run
        def run(argv, *args, **kwargs):
            if isinstance(argv, list) and len(argv) > 1 and argv[1] == '-lc':
                calls.append(argv)
                raise AssertionError('doctor executed login shell')
            return real_run(argv, *args, **kwargs)
        with preserved(cp.BRIDGE_CONFIG, cp.LANES_FILE), \
                mock.patch.object(cp.subprocess, 'run', side_effect=run), \
                mock.patch.object(le, 'engine_launcher_ready', return_value=True), \
                mock.patch.object(sienna_pool, 'real_claude', return_value=None), \
                mock.patch.object(sienna_guard, 'claude_code_version', return_value=None), \
                mock.patch.object(cp, 'port_open', return_value=False), \
                mock.patch.object(cp, 'bridge_health', return_value=None), \
                mock.patch.object(le, 'engine_managed_settings', return_value=[Path('/synthetic/managed-mcp.json')]), \
                mock.patch.object(cp, '_probe_status', return_value=0), \
                mock.patch.object(cp, 'pool_model_ids', return_value=set()), \
                mock.patch.object(cp, 'pool_facts', return_value={'defs': None, 'seats': [], 'error': None}), \
                mock.patch.object(cp, 'launchd_loaded', return_value=(False, None)):
            cp.write_json(cp.LANES_FILE, {'lanes': {'sienna': {'role': 'Review.', 'effort': 'medium',
                'members': [{'provider': 'sienna', 'model': 'test-model'}]}}})
            cp.doctor_lanes(lambda ok, msg, *a, **kw: checks.append((ok, msg)), [])
        self.assertEqual(calls, [])
        managed = [(ok, msg) for ok, msg in checks if 'managed settings still apply' in msg]
        self.assertTrue(managed)
        self.assertTrue(all(not ok for ok, msg in managed))

    @unittest.skipUnless(addon, 'sienna add-on absent')
    def test_launcher_fingerprint_is_generated_from_full_current_text(self):
        import hashlib
        import test_engine_commands as commands
        config = json.loads(cp.render_bridge_config(commands.Commands().plan(), login_path='/usr/bin:/bin'))
        self.assertEqual(config['engines']['sienna']['launcher_sha256'],
                         hashlib.sha256(sienna_pool.launcher_text().encode()).hexdigest())


class ResumeLockReview(RuntimeReview):
    def test_killed_terminal_wrapper_leaves_child_holding_lock(self):
        self.run_engine()
        state = self.service.state
        record = json.loads((state / 'engine-sessions' / (tb.KEY + '.json')).read_text())
        ready = self.home / 'terminal-child.json'
        original_popen = subprocess.Popen
        def spawn(args, **kwargs):
            # A harmless stand-in for the exec'd terminal engine; inherits exactly the wrapper's descriptors.
            code = ('import os,pathlib,time; pathlib.Path(%r).write_text(str(os.getpid())); time.sleep(30)' % str(ready))
            return original_popen([sys.executable, '-c', code], **kwargs)
        wrapper = os.fork()
        if wrapper == 0:
            try:
                with mock.patch.object(cp, 'STATE', state), mock.patch.object(le, 'engine_bridge_module', return_value=b), \
                        mock.patch.object(le, 'recorded_engines', return_value={'test': self.engine}), \
                        mock.patch.object(cp.subprocess, 'Popen', side_effect=spawn):
                    le.cmd_claude_lane_resume(types.SimpleNamespace(session=record['uuid']))
            finally:
                os._exit(0)
        child = None
        try:
            deadline = time.monotonic() + 3
            while not ready.exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            self.assertTrue(ready.exists(), 'terminal fixture did not start')
            child = int(ready.read_text())
            os.kill(wrapper, signal.SIGKILL)
            os.waitpid(wrapper, 0)
            wrapper = None
            with self.assertRaises(b.BridgeError):
                with b.ThreadLock(state / 'engine-sessions' / (tb.KEY + '.lock'), timeout=0):
                    pass
        finally:
            if wrapper is not None:
                os.kill(wrapper, signal.SIGKILL)
                os.waitpid(wrapper, 0)
            if child is not None:
                os.kill(child, signal.SIGKILL)
        with b.ThreadLock(state / 'engine-sessions' / (tb.KEY + '.lock'), timeout=3):
            pass


def load_tests(loader, tests, pattern):
    return unittest.TestSuite(cls(name) for cls in (RetryAfterPostStreamFailure, RuntimeReview, PureReview,
                                                   CommandReview, ResumeLockReview)
                              for name in vars(cls) if name.startswith('test_'))
