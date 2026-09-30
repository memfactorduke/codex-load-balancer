from _engine_helpers import le
"""Steps 6–7: only the synthetic engine under _helpers' throwaway HOME can execute."""
from _helpers import HOME, cp
from _engine_helpers import load_bridge
import test_bridge_engine as tb
import json
import os
from pathlib import Path
import queue
import signal
import socket
import subprocess
import threading
import time
import types
import unittest
from unittest import mock

b = load_bridge()


class Identity(unittest.TestCase):
    def process(self, reaped=False):
        obj = object.__new__(b.EngineProcess)
        obj.process = types.SimpleNamespace(pid=12345, returncode=0 if reaped else None)
        obj.identity = dict(pid=12345, pgid=12345, lstart='Mon Sep 28 12:00:00 2026')
        obj.identity_lock = threading.RLock()
        return obj

    def test_unreaped_group_needs_no_ps_even_without_initial_identity(self):
        obj = self.process()
        obj.identity = None
        with mock.patch.object(b, 'process_identity', side_effect=AssertionError('must not call ps')), \
                mock.patch.object(b.os, 'killpg') as kill:
            obj.signal(signal.SIGINT)
            kill.assert_called_once_with(12345, signal.SIGINT)

    def test_reaped_requires_exact_identity_no_fallback(self):
        obj = self.process(True)
        for identity in (None, dict(obj.identity, lstart='different'), dict(obj.identity, pgid=12346), obj.identity):
            with self.subTest(identity=identity), mock.patch.object(b, 'process_identity', return_value=identity), \
                    mock.patch.object(b.os, 'killpg') as kill:
                obj.signal(signal.SIGKILL)
                self.assertEqual(kill.called, identity == obj.identity)

    def test_crash_recovery_checks_each_escalation_and_run(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            state = Path(temp)
            path = state / 'engine-turns' / 'thread.jsonl'
            for pid in (101, 102, 103):
                ledger = b.EngineLedger(path, 'turn', 'test:model')
                ledger.write('running', pid=pid, pgid=pid, lstart='original')
            checks = {101: 0}
            def identity(pid):
                if pid == 101:
                    checks[pid] += 1
                    return dict(pid=pid, pgid=pid, lstart='original' if checks[pid] == 1 else 'reused')
                return dict(pid=pid, pgid=pid, lstart='reused') if pid == 102 else None
            with mock.patch.object(b, 'process_identity', side_effect=identity), mock.patch.object(b.os, 'killpg') as kill:
                b.recover_engine_turns(state, grace=0)
                kill.assert_called_once_with(101, signal.SIGTERM)
            entries = [json.loads(line) for line in path.read_text().splitlines()]
            self.assertEqual([e['state'] for e in entries[-3:]], ['interrupted'] * 3)
            self.assertTrue(all(e['ended'] for e in entries[-3:]))
            with mock.patch.object(b.os, 'killpg') as kill:
                b.recover_engine_turns(state, grace=0)
                kill.assert_not_called()

    def test_crash_partial_line_is_separated_before_append(self):
        import tempfile
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            path = Path(temp) / 'engine-turns' / 'thread.jsonl'
            ledger = b.EngineLedger(path, 'turn', 'test:model')
            ledger.write('running')
            with path.open('a') as f:
                f.write('null\n{"partial":')
            b.recover_engine_turns(Path(temp), grace=0)
            self.assertEqual(json.loads(path.read_text().splitlines()[-1])['state'], 'interrupted')


class Runtime(unittest.TestCase):
    setUp = tb.EngineHarness.setUp
    init = tb.EngineHarness.init
    fixture = tb.EngineHarness.fixture
    run_engine = tb.EngineHarness.run_engine
    ledger = tb.EngineHarness.ledger

    def test_large_line_and_chatty_client_cancellation(self):
        self.fixture([self.init(), {'oversize': True}], linger=True)
        self.run_engine()
        self.assertEqual(self.handler.events[-1]['response']['error']['code'], 'engine_error')
        self.handler = tb.RecordingHandler()
        self.fixture([self.init(), {'chatty': 10000}], linger=True)
        started = time.monotonic()
        with mock.patch.object(b, 'peer_closed', side_effect=lambda _: time.monotonic() - started > 0.15):
            with self.assertRaises(ConnectionAbortedError):
                self.run_engine()
        self.assertLess(time.monotonic() - started, 1.5)
        self.assertEqual(self.ledger()[-1]['state'], 'cancelled')

    def test_real_socket_close_signals_stub_within_one_second(self):
        try:
            server, client = socket.socketpair()
        except OSError as error:
            self.skipTest('socketpair unavailable: %s' % error)
        self.addCleanup(server.close)
        self.addCleanup(client.close)
        self.peer.stop()
        self.handler.connection = server
        self.fixture([self.init()], linger=True)
        closed = []
        def close_peer():
            deadline = time.monotonic() + 2
            while not self.handler.statuses and time.monotonic() < deadline:
                time.sleep(0.01)
            closed.append(time.time())
            client.close()
        thread = threading.Thread(target=close_peer)
        thread.start()
        try:
            with self.assertRaises(ConnectionAbortedError):
                self.run_engine()
            received = json.loads((self.profile / 'signal.json').read_text())
            self.assertLess(received['at'] - closed[0], 1)
        finally:
            thread.join(3)

    def test_shutdown_cancels_active_and_refuses_queued(self):
        self.fixture([self.init()], linger=True)
        errors = []
        def run():
            try:
                self.run_engine()
            except Exception as e:
                errors.append(e)
        thread = threading.Thread(target=run)
        thread.start()
        deadline = time.monotonic() + 10
        while not self.handler.statuses and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(self.handler.statuses, 'synthetic engine did not initialize')
        self.service.shutdown()
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.ledger()[-1]['state'], 'cancelled')
        with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(ConnectionAbortedError):
            self.run_engine()
        spawn.assert_not_called()

    def test_blocked_sse_writer_does_not_block_watchdog(self):
        release = threading.Event()
        blocked = threading.Event()
        self.handler.send_event = lambda _: (blocked.set(), release.wait(5))
        delta = tb.stream_event('content_block_delta', index=0, delta={'type': 'text_delta', 'text': 'x'})
        self.fixture([self.init()] + tb.text_events()[:2] + [delta] * 1500, linger=True)
        started = time.monotonic()
        try:
            with mock.patch.object(b, 'peer_closed', side_effect=lambda _: blocked.is_set()):
                with self.assertRaises(ConnectionAbortedError):
                    self.run_engine()
            received = json.loads((self.profile / 'signal.json').read_text())
            capture = json.loads((self.profile / 'spawn.json').read_text())
            self.assertEqual(received['signal'], signal.SIGINT)
            self.assertEqual(self.ledger()[-1]['state'], 'cancelled')
            with self.assertRaises(ProcessLookupError):
                os.kill(capture['pid'], 0)
        finally:
            release.set()

    def test_escalation_int_term_kill(self):
        self.fixture([], no_read=True, ignore_interrupt=True, ignore_term=True)
        process = b.EngineProcess([self.engine['launcher']], self.engine['env'], str(self.repo), b'x' * 100000)
        original_wait = process.wait
        signals = []
        original_signal = process.signal
        def send(sig):
            signals.append(sig)
            original_signal(sig)
        def short_wait(timeout):
            return original_wait(min(timeout, 0.1))
        try:
            deadline = time.monotonic() + 2
            while not (self.profile / 'spawn.json').exists() and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.03)
            with mock.patch.object(process, 'wait', side_effect=short_wait), mock.patch.object(process, 'signal', side_effect=send):
                process.stop()
            self.assertEqual(signals[:3], [signal.SIGINT, signal.SIGTERM, signal.SIGKILL])
            self.assertIsNotNone(process.process.returncode)
        finally:
            process.close()

    def test_stdout_queue_byte_limit_and_shutdown_unblocks_producer(self):
        process = object.__new__(b.EngineProcess)
        process.lines = queue.Queue(1000)
        process.stopped = threading.Event()
        process.bytes, process.byte_lock = 0, threading.Condition()
        thread = threading.Thread(target=lambda: [process.enqueue(b'x' * (1024 * 1024)) for _ in range(10)])
        thread.start()
        time.sleep(0.05)
        self.assertLessEqual(process.bytes, 8 * 1024 * 1024)
        self.assertLessEqual(process.lines.qsize(), 8)
        process.stopped.set()
        with process.byte_lock:
            process.byte_lock.notify_all()
        thread.join(1)
        self.assertFalse(thread.is_alive())

    def test_compaction_no_spawn_and_pruned_cursor_resumes(self):
        self.run_engine()
        output = self.handler.events[-1]['response']['output']
        self.body['input'] += output + [{'type': 'compaction_trigger'}]
        replies = []
        self.handler.reply_compaction = lambda response, stream: replies.append(response)
        self.engine['accepted_versions'] = []
        with mock.patch.object(b.subprocess, 'Popen') as spawn:
            self.run_engine()
        spawn.assert_not_called()
        item = replies[0]['output'][0]
        payload = self.service.sealer.unseal_payload(item['encrypted_content'])
        self.assertEqual((payload['v'], payload['thread']), (3, tb.KEY))
        self.assertIn('answer', payload['digest'])
        self.assertLessEqual(len(payload['digest']), b.HISTORY_LIMIT)
        # The visible id is pruned; the sealed session and last_turn must still seed the cursor.
        item.pop('id')
        self.body['input'] = [self.body['input'][0], item, tb.user('next')]
        self.engine['accepted_versions'] = ['2.1.283']
        self.handler = tb.RecordingHandler()
        self.run_engine()
        capture = json.loads((self.profile / 'spawn.json').read_text())
        self.assertIn('--resume', capture['argv'])
        self.assertNotIn('history imported', json.dumps(self.handler.events))
        (self.home / 'state' / 'engine-sessions' / (tb.KEY + '.json')).unlink()
        self.handler = tb.RecordingHandler()
        self.run_engine()
        self.assertIn('session record was missing', json.dumps(self.handler.events))
        self.assertNotIn('earlier turns compacted', json.dumps(self.handler.events))
        capture = json.loads((self.profile / 'spawn.json').read_text())
        self.assertIn('answer', capture['stdin'])

    def test_checkpoint_tamper_thread_and_old_digest(self):
        item = b.compact_reply([tb.user('context')], dict(uuid=tb.KEY, ordinal=2, last_turn='t'),
                              tb.KEY, 'sienna', self.service.sealer)['output'][0]
        item['encrypted_content'] += 'x'
        with self.assertRaises(b.BridgeError):
            b.cursor([item], None, tb.KEY, str(self.repo), sealer=self.service.sealer)
        item['encrypted_content'] = self.service.sealer.seal_payload(dict(v=2, thread=tb.KEY, session=tb.KEY, last_turn='t'))
        block, compacted = b.history_block([item], self.service.sealer, tb.KEY)
        self.assertTrue(compacted)
        self.assertIn('not available', block)
        with self.assertRaises(b.BridgeError):
            b.cursor([item], None, '11111111-1111-4111-8111-111111111111', str(self.repo), sealer=self.service.sealer)

    def probe_fixture(self, **extra):
        self.fixture([self.init(), {'type': 'result', 'subtype': 'success', 'permission_denials': []}], probe=True, **extra)

    def test_accept_probe_stub_only_and_no_mutation_on_failure(self):
        # No patch of Popen here: both executable paths point to the fixture in fake HOME.
        self.probe_fixture()
        version, expectation = b.accept_engine(self.engine, self.route, self.home / '.codexpool' / 'state', timeout=2)
        self.assertEqual(version, '2.1.283')
        self.assertEqual(expectation, self.expect)
        capture = json.loads((self.profile / 'spawn.json').read_text())
        self.assertEqual(capture['argv'][capture['argv'].index('--max-turns') + 1], '1')
        self.assertIn('Reply with the single word ok.', capture['stdin'])
        self.fixture([dict(self.init(), tools=['Read', 'Bash'])], probe=True, linger=True)
        with self.assertRaises(b.BridgeError) as error:
            b.accept_engine(self.engine, self.route, self.home / '.codexpool' / 'state', timeout=2)
        self.assertEqual(error.exception.code, 'engine_misconfigured')

    def test_probe_refuses_missing_engine_and_checks_transcript_denials_warning(self):
        for events, probe in (([self.init(), {'type': 'result', 'subtype': 'success', 'permission_denials': ['Read']}], True),
                              ([self.init(), {'stderr': 'Warning: settings invalid'},
                                {'type': 'result', 'subtype': 'success', 'permission_denials': []}], True)):
            self.fixture(events, probe=probe)
            with self.assertRaises(b.BridgeError):
                b.accept_engine(self.engine, self.route, self.home / '.codexpool' / 'state', timeout=2)
        Path(self.engine['launcher']).unlink()
        with mock.patch.object(b.subprocess, 'Popen') as spawn, self.assertRaises(b.BridgeError):
            b.accept_engine(self.engine, self.route, self.home / '.codexpool' / 'state', timeout=2)
        spawn.assert_not_called()

    def test_probe_checks_transcript_and_normal_profile_mtimes(self):
        for option, field in (('no_transcript', 'lane transcript'), ('mutate_profile', 'normal profile changed')):
            self.probe_fixture(**{option: True})
            with self.assertRaises(b.BridgeError) as error:
                b.accept_engine(self.engine, self.route, self.home / '.codexpool' / 'state', timeout=2)
            self.assertIn(field, error.exception.message)

    def test_resume_holds_lock_and_uses_isolated_env(self):
        self.run_engine()
        state = self.home / 'state'
        record = json.loads((state / 'engine-sessions' / (tb.KEY + '.json')).read_text())
        child = mock.Mock()
        def wait(*args, **kwargs):
            with self.assertRaises(b.BridgeError):
                with b.ThreadLock(state / 'engine-sessions' / (tb.KEY + '.lock'), timeout=0):
                    pass
            return 0
        child.wait.side_effect = wait
        child.poll.return_value = 0
        with mock.patch.object(cp, 'STATE', state), mock.patch.object(le, 'engine_bridge_module', return_value=b), \
                mock.patch.object(le, 'recorded_engines', return_value={'test': self.engine}), \
                mock.patch.object(cp.subprocess, 'Popen', return_value=child) as spawn:
            le.cmd_claude_lane_resume(types.SimpleNamespace(session=record['uuid']))
        self.assertEqual(spawn.call_args.kwargs['env'], self.engine['env'])
        self.assertEqual(spawn.call_args.args[0], [self.engine['launcher'], '--resume', record['uuid']])


if __name__ == '__main__':
    unittest.main()
