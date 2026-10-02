"""Lanes from the Settings window's side: lane edit (every flag, the order they apply in, validation), lane providers,
lane models (xAI from a fake pool, OpenCode and responses from a fake HTTP server), keys from stdin and the xAI
sign-in link on the clipboard."""
import contextlib
import copy
import http.server
import io
import json
import re
import sys
import threading
import unittest
from unittest import mock

from _helpers import CLIPBOARD, EXAMPLE_LANES, TEST_KEY, FakePool, cp, fake_cpa, preserved, run


def edit_args(*argv):
    """lane edit's argparse Namespace for argv (after `lane edit`), as the command line gives it."""
    return cp.build_parser().parse_args(['lane', 'edit'] + list(argv))


def edited(*argv, raw=None):
    """lanes.json as lane edit argv would leave it (pure)."""
    args = edit_args(*argv)
    return cp.lane_edit_raw(copy.deepcopy(raw or EXAMPLE_LANES), args.name, args.ops, args.base_url,
                            args.session_header)


def members(raw, lane='bulk'):
    return [(m['id'], m['provider'], m['model']) for m in raw['lanes'][lane]['members']]


class ParseOps(unittest.TestCase):
    def test_changes_keep_their_order(self):
        args = edit_args('bulk', '--add-member', 'opencode-zen:kimi-9', '--move-member', 'kimi', '--to', '1',
                         '--role', 'r', '--no-display', '--effort', 'high')
        self.assertEqual(args.ops, [('add_member', 'opencode-zen:kimi-9', None), ('move', 'kimi', 1),
                                    ('role', 'r', None), ('no_display', [], None), ('effort', 'high', None)])
        self.assertIs(args.lane_fn, cp.cmd_lane_edit)
        self.assertIsNone(edit_args('bulk').ops)

    def test_parser_refusals(self):
        for argv in (['bulk', '--to', '2'], ['bulk', '--move-member', 'a', '--to', '1', '--to', '2'],
                     ['bulk', '--effort', 'max'], ['bulk', '--display', 'X', '--no-display'],
                     ['bulk', '--move-member', 'a', '--to', 'first']):
            with self.subTest(argv=argv), contextlib.redirect_stderr(io.StringIO()):
                with self.assertRaises(SystemExit) as ctx:
                    edit_args(*argv)
                self.assertEqual(ctx.exception.code, 2)


class EditRaw(unittest.TestCase):
    def test_rename_keeps_the_place(self):
        raw = dict(EXAMPLE_LANES, lanes={'first': EXAMPLE_LANES['lanes']['bulk'], **EXAMPLE_LANES['lanes']})
        new = edited('bulk', '--rename', 'heavy', raw=raw)
        self.assertEqual(list(new['lanes']), ['first', 'heavy'])
        self.assertEqual(new['lanes']['heavy']['members'], raw['lanes']['bulk']['members'])
        cp.validate_lanes(new)

    def test_role_effort_display(self):
        new = edited('bulk', '--role', 'Sweeps\nand  bulk edits.', '--effort', 'medium', '--display', 'Bulk: Grok')
        lane = new['lanes']['bulk']
        self.assertEqual((lane['role'], lane['effort'], lane['display']), ('Sweeps and bulk edits.', 'medium',
                                                                           'Bulk: Grok'))
        self.assertEqual(list(lane), ['role', 'effort', 'display', 'members'])   # a new key goes before members
        self.assertNotIn('display', edited('bulk', '--no-display', raw=new)['lanes']['bulk'])
        self.assertNotIn('display', edited('bulk', '--no-display')['lanes']['bulk'])   # none: nothing to remove

    def test_add_remove_move(self):
        new = edited('bulk', '--add-member', 'opencode-zen:kimi-9:Kimi 9', '--add-member', 'opencode-zen:kimi-10')
        self.assertEqual(members(new), [('grok', 'xai', 'grok-4.7-build-fast'),
                                        ('muse', 'opencode-go', 'muse-spark-1.3-contributor'),
                                        ('kimi', 'opencode-zen', 'kimi-9'), ('kimi2', 'opencode-zen', 'kimi-10')])
        self.assertEqual(new['lanes']['bulk']['members'][2], {'id': 'kimi', 'provider': 'opencode-zen',
                                                              'model': 'kimi-9', 'name': 'Kimi 9'})
        new = edited('bulk', '--move-member', 'muse', '--to', '1', '--remove-member', 'grok')
        self.assertEqual(members(new), [('muse', 'opencode-go', 'muse-spark-1.3-contributor')])
        new = edited('bulk', '--move-member', 'grok', '--to', '2')
        self.assertEqual([m[0] for m in members(new)], ['muse', 'grok'])

    def test_order_given_is_the_order_applied(self):
        # the member added first can be moved and removed in the same call
        new = edited('bulk', '--add-member', 'opencode-zen:kimi-9', '--move-member', 'kimi', '--to', '1')
        self.assertEqual([m[0] for m in members(new)], ['kimi', 'grok', 'muse'])
        new = edited('bulk', '--remove-member', 'muse', '--add-member', 'opencode-go:muse-2')
        self.assertEqual([m[0] for m in members(new)], ['grok', 'muse'])    # its id is free again
        with self.assertRaises(SystemExit):
            edited('bulk', '--move-member', 'kimi', '--to', '1', '--add-member', 'opencode-zen:kimi-9')

    def test_ids_are_written_out_so_moves_keep_them(self):
        raw = {'lanes': {'bulk': {'role': 'r', 'members': [{'provider': 'opencode-go', 'model': 'kimi-9'},
                                                           {'provider': 'opencode-zen', 'model': 'kimi-10'}]}}}
        self.assertEqual([m['id'] for m in cp.validate_lanes(raw)[0]['members']], ['kimi', 'kimi2'])
        new = edited('bulk', '--move-member', 'kimi2', '--to', '1', raw=raw)
        self.assertEqual(members(new), [('kimi2', 'opencode-zen', 'kimi-10'), ('kimi', 'opencode-go', 'kimi-9')])

    def test_responses_member(self):
        new = edited('bulk', '--add-member', 'responses:acme-1', '--base-url', 'https://api.example.invalid/v1',
                     '--session-header', 'x-session')
        self.assertEqual(new['lanes']['bulk']['members'][-1], {'id': 'acme', 'provider': 'responses',
                                                               'model': 'acme-1',
                                                               'base_url': 'https://api.example.invalid/v1',
                                                               'session_header': 'x-session'})
        cp.validate_lanes(new)
        with self.assertRaises(cp.LaneError) as ctx:
            cp.validate_lanes(edited('bulk', '--add-member', 'responses:acme-1'))
        self.assertIn('a responses member needs "base_url"', str(ctx.exception))

    def test_refusals_that_stop_before_validation(self):
        raw = dict(EXAMPLE_LANES, lanes=dict(EXAMPLE_LANES['lanes'], other=EXAMPLE_LANES['lanes']['bulk']))
        for argv, text in ((['bulk', '--rename', 'other'], 'lane "other" is already in lanes.json'),
                           (['bulk', '--rename', '_hidden'], 'is not a lane name'),
                           (['bulk', '--remove-member', 'nope'], 'lane "bulk" has no member "nope" (its members, in '
                                                                 'order: grok, muse)'),
                           (['bulk', '--move-member', 'muse'], '--move-member muse needs --to POS'),
                           (['bulk', '--move-member', 'muse', '--to', '3'], '--to 3: a position from 1 to 2'),
                           (['bulk', '--move-member', 'muse', '--to', '0'], '--to 0: a position from 1 to 2'),
                           (['bulk', '--add-member', 'xai'], 'write PROVIDER:MODEL')):
            with self.subTest(argv=argv), self.assertRaises(SystemExit) as ctx:
                edited(*argv, raw=raw)
            self.assertIn(text, str(ctx.exception.code))

    def test_validated_like_add(self):
        for argv, text in ((['bulk', '--remove-member', 'grok', '--remove-member', 'muse'],
                            '"members" must be a non-empty list'),
                           (['bulk', '--display', 'x' * 41], '"display" must be a one-line string of 1 to 40'),
                           (['bulk', '--rename', 'Bad Name'], 'a lane name is lowercase letters'),
                           (['bulk', '--rename', 'default'], 'is not "default"'),
                           (['bulk', '--role', '<!-- x -->'], '"role" must be a non-empty one-line string'),
                           (['bulk', '--add-member', 'xai:grok-5'], 'at most one xai member per lane'),
                           (['bulk', '--add-member', 'nope:model'], 'provider "nope" is not one of'),
                           (['bulk', '--add-member', 'opencode-go:bad model'], '"model" must be')):
            with self.subTest(argv=argv), self.assertRaises(cp.LaneError) as ctx:
                cp.validate_lanes(edited(*argv))
            self.assertIn(text, str(ctx.exception))


class EditCommand(unittest.TestCase):
    def run_edit(self, *argv):
        args = edit_args(*argv)
        return run(args.lane_fn, **{k: v for k, v in vars(args).items() if k not in ('fn', 'lane_fn')})

    def test_applies_through_lane_apply(self):
        before = cp.LANES_FILE.read_text()
        with mock.patch.object(cp, 'lane_apply') as apply:
            code, _, err = self.run_edit('bulk', '--rename', 'heavy', '--effort', 'high')
        self.assertEqual((code, err), (0, ''))
        (lanes, dry), kwargs = apply.call_args
        self.assertFalse(dry)
        self.assertEqual([(lane['name'], lane['effort']) for lane in lanes], [('heavy', 'high')])
        old, new = kwargs['lanes_change']
        self.assertEqual(old, before)
        self.assertEqual(list(json.loads(new)['lanes']), ['heavy'])
        self.assertEqual(cp.LANES_FILE.read_text(), before)   # lane apply writes it, only once nothing stops it

    def test_dry_run_prints_the_diff_and_writes_nothing(self):
        before = cp.LANES_FILE.read_bytes()
        with FakePool(models=['gpt-test']):
            code, out, err = self.run_edit('bulk', '--display', 'Bulk: Grok', '--dry-run')
        self.assertIn(code, (0, 1), err)
        self.assertEqual(cp.LANES_FILE.read_bytes(), before)
        self.assertIn('+      "display": "Bulk: Grok",', out)
        self.assertIn('display-name: "Bulk: Grok"', out)

    def test_rename_takes_a_responses_members_key_along(self):
        raw = {'lanes': {'own': {'role': 'Own endpoint.', 'effort': 'high', 'members': [
            {'id': 'gpt', 'provider': 'responses', 'model': 'gpt-x', 'base_url': 'https://api.example.invalid/v1'}]}}}
        old, new = cp.lane_key_path('own-gpt'), cp.lane_key_path('mine-gpt')
        with preserved(cp.LANES_FILE, old, new):
            cp.LANES_FILE.write_text(json.dumps(raw))
            cp.write_private(old, 'own-secret\n')
            seen = []

            def apply(lanes, dry, lanes_change=None):
                seen.append((dry, cp.lane_key_path('mine-gpt').is_file(), new.exists()))
            with mock.patch.object(cp, 'lane_apply', side_effect=apply):
                code, out, err = self.run_edit('own', '--rename', 'mine', '--dry-run')
                self.assertEqual((code, err), (0, ''))
                self.assertIn('would copy the own-gpt key to mine-gpt', out)
                self.assertEqual(seen, [(True, True, False)])   # the dry run checks the old key, and copies nothing
                self.assertEqual(cp.lane_key_path('mine-gpt'), new)
                code, out, err = self.run_edit('own', '--rename', 'mine')
            self.assertEqual((code, err), (0, ''))
            self.assertEqual(seen[-1], (False, True, True))
            self.assertIn('Moved the own-gpt key to mine-gpt', out)
            self.assertEqual((new.read_text(), old.exists(), oct(new.stat().st_mode & 0o777)),
                             ('own-secret\n', False, '0o600'))

    def test_rename_that_stops_leaves_the_key_where_it_was(self):
        raw = {'lanes': {'own': {'role': 'Own endpoint.', 'effort': 'high', 'members': [
            {'id': 'gpt', 'provider': 'responses', 'model': 'gpt-x', 'base_url': 'https://api.example.invalid/v1'}]}}}
        old, new = cp.lane_key_path('own-gpt'), cp.lane_key_path('mine-gpt')
        with preserved(cp.LANES_FILE, old, new):
            cp.LANES_FILE.write_text(json.dumps(raw))
            cp.write_private(old, 'own-secret\n')
            with mock.patch.object(cp, 'lane_apply', side_effect=SystemExit('lane apply stopped')):
                code, _, _ = self.run_edit('own', '--rename', 'mine')
            self.assertEqual((code, old.exists(), new.exists()), (1, True, False))

    def test_no_such_lane_and_nothing_to_do(self):
        for argv, text in ((['nope', '--effort', 'low'], 'there is no lane "nope"'),
                           (['bulk'], 'nothing to change')):
            code, _, err = self.run_edit(*argv)
            self.assertEqual(code, 1, argv)
            self.assertIn(text, err)
        with preserved(cp.LANES_FILE):
            cp.LANES_FILE.unlink()
            code, _, err = self.run_edit('bulk', '--effort', 'low')
        self.assertEqual(code, 1)
        self.assertIn('there is no lane "bulk"', err)


class Providers(unittest.TestCase):
    def providers(self, seats=(), error=None):
        facts = {'seats': None if error else list(seats), 'defs': None, 'error': error}
        with mock.patch.object(cp, 'pool_facts', return_value=facts):
            code, out, err = run(cp.cmd_lane_providers, json=True)
        self.assertEqual((code, err), (0, ''))
        data = json.loads(out)
        self.assertEqual(list(data), ['providers'])
        for p in data['providers']:
            self.assertEqual(set(p), {'id', 'title', 'kind', 'needs', 'key_name', 'ready', 'detail'})
        return {p['id']: p for p in data['providers']}

    @staticmethod
    def xai(**state):
        return dict({'name': 'xai-test.json', 'id': 'xai-test.json', 'provider': 'xai', 'disabled': False,
                     'unavailable': False, 'status': 'active', 'status_message': '', 'next_retry_after': None,
                     'cooldowns': []}, **state)

    def test_shape_and_keys(self):
        p = {k: v for k, v in self.providers().items() if k in ('xai', 'opencode-go', 'opencode-zen', 'responses')}
        self.assertEqual(list(p), ['xai', 'opencode-go', 'opencode-zen', 'responses'])
        self.assertEqual({k: (v['kind'], v['needs'], v['key_name']) for k, v in p.items()},
                         {'xai': ('native', 'login', None), 'opencode-go': ('bridge', 'key', 'opencode-go'),
                          'opencode-zen': ('bridge', 'key', 'opencode-zen'), 'responses': ('bridge', 'key', None)})
        self.assertTrue(p['opencode-go']['ready'])       # the fake home has its key
        self.assertEqual(p['opencode-go']['detail'], 'key saved')
        self.assertFalse(p['opencode-zen']['ready'])
        self.assertIn('subpool lane key opencode-zen -', p['opencode-zen']['detail'])
        self.assertFalse(p['responses']['ready'])
        self.assertTrue(all(isinstance(v['title'], str) and v['title'] for v in p.values()))

    def test_xai_states(self):
        self.assertEqual((self.providers()['xai']['ready'], self.providers()['xai']['detail']),
                         (False, 'not signed in: subpool lane login xai'))
        self.assertTrue(self.providers([self.xai()])['xai']['ready'])
        self.assertFalse(self.providers([self.xai(disabled=True)])['xai']['ready'])
        blocked = self.providers([self.xai(status='error', unavailable=True, status_message='unauthorized')])['xai']
        self.assertFalse(blocked['ready'])
        self.assertIn('sign in again', blocked['detail'])
        down = self.providers(error='the pool is not answering on 127.0.0.1:1')['xai']
        self.assertEqual((down['ready'], down['detail']), (False, 'cannot tell: the pool is not answering on 127.0.0.1:1'))

    def test_text(self):
        with mock.patch.object(cp, 'pool_facts', return_value={'seats': [], 'defs': None, 'error': None}):
            code, out, _ = run(cp.cmd_lane_providers, json=False)
        self.assertEqual(code, 0)
        self.assertIn('opencode-go', out)
        self.assertIn('not signed in', out)


class ModelServer:
    """A stand-in for an OpenCode-style GET /v1/models. answer: (status, body, headers); seen: request headers."""

    def __init__(self, status=200, body=None, headers=()):
        self.status, self.body, self.headers, self.seen = status, body, list(headers), []

    def __enter__(self):
        server = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                server.seen.append((self.path, dict(self.headers)))
                data = self.body_bytes()
                self.send_response(server.status)
                for k, v in server.headers:
                    self.send_header(k, v)
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def body_bytes(self):
                b = server.body
                return b if isinstance(b, bytes) else json.dumps(b).encode()

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.httpd.daemon_threads = True
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.url = f'http://127.0.0.1:{self.httpd.server_address[1]}/v1'
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


class Models(unittest.TestCase):
    LIST = {'object': 'list', 'data': [{'id': 'muse-2', 'object': 'model'},
                                       {'id': 'kimi-9', 'name': 'Kimi 9', 'context_length': 262144},
                                       {'id': 'glm-5', 'limit': {'context': 200000}},
                                       {'id': 'bad model'}, {'nope': 1}, 'junk']}

    def models(self, provider, *extra, want=0):
        args = cp.build_parser().parse_args(['lane', 'models', provider] + list(extra) + ['--json'])
        code, out, err = run(args.lane_fn, **{k: v for k, v in vars(args).items() if k not in ('fn', 'lane_fn')})
        self.assertEqual(code, want, err)
        return json.loads(out), err

    def key(self, name='opencode-go'):
        return cp.lane_key_path(name).read_text().strip()

    def test_xai_from_the_pool(self):
        defs = {'models': [{'id': 'grok-5', 'display_name': 'Grok 5', 'context_length': 2000000},
                           {'id': 'grok-4.7-build-fast', 'display_name': '', 'context_length': True}, {'x': 1}]}
        with mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY), FakePool(xai_models=defs):
            data, _ = self.models('xai')
        self.assertEqual(data, {'models': [{'id': 'grok-4.7-build-fast', 'name': None, 'context': None},
                                           {'id': 'grok-5', 'name': 'Grok 5', 'context': 2000000}]})
        with mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY), FakePool(xai_models=defs):
            self.assertEqual(cp.xai_model_defs(), {'grok-5': 2000000, 'grok-4.7-build-fast': None})  # lane apply's

    def test_xai_errors(self):
        data, err = self.models('xai', want=1)
        self.assertIn('the pool is not answering', data['error'])
        self.assertIn('subpool lane models: the pool is not answering', err)
        with mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY), FakePool():   # no model definitions: 404
            data, _ = self.models('xai', want=1)
        self.assertIn('the pool would not list its xAI models', data['error'])

    def test_opencode_with_the_stored_key(self):
        with ModelServer(body=self.LIST) as srv, \
                mock.patch.dict(cp.LANE_PROVIDERS['opencode-go'], {'base_url': srv.url}):
            data, _ = self.models('opencode-go')
        self.assertEqual(data['models'], [{'id': 'glm-5', 'name': None, 'context': 200000},
                                          {'id': 'kimi-9', 'name': 'Kimi 9', 'context': 262144},
                                          {'id': 'muse-2', 'name': None, 'context': None}])
        ((path, headers),) = srv.seen
        self.assertEqual(path, '/v1/models')
        self.assertEqual(headers.get('Authorization'), 'Bearer ' + self.key())
        self.assertEqual(headers.get('User-Agent'), 'subpool-bridge/1')

    def test_without_a_key_it_asks_anyway(self):
        with ModelServer(body={'data': [{'id': 'zen-1'}]}) as srv, \
                mock.patch.dict(cp.LANE_PROVIDERS['opencode-zen'], {'base_url': srv.url}):
            data, _ = self.models('opencode-zen')
        self.assertEqual(data['models'], [{'id': 'zen-1', 'name': None, 'context': None}])
        self.assertNotIn('Authorization', srv.seen[0][1])

    def test_errors_never_show_the_key(self):
        key = self.key()
        cases = ((401, {'error': {'message': f'bad key {key}'}}, (), 'answered HTTP 401: bad key <key> (check the key: '
                                                                     'subpool lane key opencode-go -)'),
                 (500, b'<html>oops</html>', (), 'answered HTTP 500'),
                 (200, b'not json', (), 'did not answer'),
                 (200, {'something': 'else'}, (), 'something other than a model list'),
                 (302, b'', (('Location', 'http://127.0.0.1:9/steal'),), 'a redirect'))
        for status, body, headers, text in cases:
            with self.subTest(status=status), ModelServer(status, body, headers) as srv, \
                    mock.patch.dict(cp.LANE_PROVIDERS['opencode-go'], {'base_url': srv.url}):
                data, err = self.models('opencode-go', want=1)
                self.assertEqual(len(srv.seen), 1)   # a redirect is not followed
            self.assertIn(text, data['error'])
            self.assertNotIn(key, data['error'] + err)

    def test_unreachable(self):
        with mock.patch.dict(cp.LANE_PROVIDERS['opencode-go'], {'base_url': 'http://127.0.0.1:9/v1'}):
            data, _ = self.models('opencode-go', want=1)
        self.assertIn('cannot reach 127.0.0.1:9', data['error'])

    def test_responses_needs_base_url_and_key_name(self):
        for extra, text in (([], 'needs --base-url'), (['--base-url', 'http://plain.invalid'], 'needs --base-url'),
                            (['--base-url', 'https://api.example.invalid/v1'], 'needs --key-name'),
                            (['--base-url', 'https://api.example.invalid/v1', '--key-name', 'Bad Name'],
                             'needs --key-name')):
            data, _ = self.models('responses', *extra, want=1)
            self.assertIn(text, data['error'])
        data, _ = self.models('opencode-go', '--key-name', 'x', want=1)
        self.assertIn('--base-url and --key-name are for the responses provider', data['error'])
        data, _ = self.models('bogus', want=1)
        self.assertIn('"bogus" is not a lane provider', data['error'])

    def test_responses_never_gets_another_providers_key(self):
        with ModelServer(body={'data': [{'id': 'acme-1'}]}) as srv, \
                mock.patch.object(cp, 'HTTPS_OK', re.compile(r'^http://127\.0\.0\.1:\d+/v1$')):
            for name in ('opencode-go', 'opencode-zen', 'bridge'):
                data, _ = self.models('responses', '--base-url', srv.url, '--key-name', name, want=1)
                self.assertIn('needs --key-name' if name == 'bridge' else f'"{name}" is the {name} provider\'s key',
                              data['error'])
            self.assertEqual(srv.seen, [])
        data, _ = self.models('responses', '--base-url', 'https://api.example.invalid/v1', '--key-name', 'opencode-go',
                              want=1)
        self.assertIn('"opencode-go" is the opencode-go provider\'s key, which would go to '
                      'https://api.example.invalid/v1', data['error'])

    def test_responses_uses_its_own_key(self):
        path = cp.lane_key_path('quick-acme')
        with preserved(path), ModelServer(body={'data': [{'id': 'acme-1'}]}) as srv, \
                mock.patch.object(cp, 'HTTPS_OK', re.compile(r'^http://127\.0\.0\.1:\d+/v1$')):
            cp.write_private(path, 'acme-secret\n')
            data, _ = self.models('responses', '--base-url', srv.url, '--key-name', 'quick-acme')
        self.assertEqual(data['models'], [{'id': 'acme-1', 'name': None, 'context': None}])
        self.assertEqual(srv.seen[0][1].get('Authorization'), 'Bearer acme-secret')

    def test_text(self):
        with ModelServer(body=self.LIST) as srv, \
                mock.patch.dict(cp.LANE_PROVIDERS['opencode-go'], {'base_url': srv.url}):
            code, out, _ = run(cp.cmd_lane_models, provider='opencode-go', base_url=None, key_name=None, json=False)
        self.assertEqual(code, 0)
        self.assertIn('kimi-9', out)
        self.assertIn('262144', out)


class KeyFromStdin(unittest.TestCase):
    def test_dash_reads_stdin_and_never_prints_the_key(self):
        """What the Settings window does: `subpool lane key NAME -` with the key on stdin, never in argv."""
        path = cp.lane_key_path('opencode-zen')
        args = cp.build_parser().parse_args(['lane', 'key', 'opencode-zen', '-'])
        stdin = io.StringIO('zen-secret-123\n')
        stdin.isatty = lambda: True    # even from a terminal, "-" means stdin, not the prompt
        with preserved(path), mock.patch.object(sys, 'stdin', stdin), \
                mock.patch.object(cp.getpass, 'getpass', side_effect=AssertionError('prompted')):
            code, out, err = run(args.lane_fn, keyname=args.keyname, file=args.file)
            self.assertEqual(code, 0, err)
            self.assertEqual(path.read_text(), 'zen-secret-123\n')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertIn('saved the opencode-zen key', out)
        self.assertNotIn('zen-secret-123', out + err)

    def test_empty_stdin_saves_nothing(self):
        path = cp.lane_key_path('opencode-zen')
        with preserved(path), mock.patch.object(sys, 'stdin', io.StringIO('\n')):
            code, _, err = run(cp.cmd_lane_key, keyname='opencode-zen', file='-')
            self.assertFalse(path.exists())
        self.assertEqual(code, 1)
        self.assertIn('nothing was saved', err)


class XaiLoginLink(unittest.TestCase):
    def login(self, **args):
        with fake_cpa(), preserved(CLIPBOARD), \
                mock.patch.object(cp, 'clipboard_wanted', lambda no_copy=False: not no_copy):
            with contextlib.suppress(FileNotFoundError):
                CLIPBOARD.unlink()
            code, out, _ = run(cp.cmd_lane_login, provider='xai', no_open=True, **args)
            copied = CLIPBOARD.read_text() if CLIPBOARD.exists() else None
        return code, out, copied

    def test_link_is_copied(self):
        code, out, copied = self.login(no_copy=False)
        self.assertEqual(code, 0)
        self.assertEqual(copied, 'https://auth.example.invalid/oauth/authorize?client_id=app_test&state=s1')
        self.assertIn('  Link copied to the clipboard.', out)

    def test_no_copy(self):
        code, out, copied = self.login(no_copy=True)
        self.assertEqual(code, 0)
        self.assertIsNone(copied)
        self.assertNotIn('Link copied', out)

    def test_parser(self):
        args = cp.build_parser().parse_args(['lane', 'login', 'xai', '--no-open'])
        self.assertEqual((args.no_open, args.no_copy), (True, False))


if __name__ == '__main__':
    unittest.main()
