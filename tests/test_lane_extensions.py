"""Provider/bridge contracts with synthetic local extensions; never opens a socket or launches a client."""
from _helpers import cp, HOME, REPO, load_bridge, EXAMPLE_LANES
import contextlib
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock

b = load_bridge()


class Providers(unittest.TestCase):
    def test_standard_plans_match_pre_split_bytes(self):
        variants = {'standard': EXAMPLE_LANES, 'custom': {'lanes': {'review': {'role': 'Review.', 'members': [
            {'provider': 'responses', 'model': 'example-model', 'base_url': 'https://example.test/v1',
             'session_header': 'x-session'}]}}}}
        for name, raw in variants.items():
            with self.subTest(plan=name), mock.patch.object(cp, 'BRIDGE_PORT', 8320):
                actual = cp.render_bridge_config(cp.lane_plan(cp.validate_lanes(raw)))
                self.assertEqual(actual, (REPO / 'tests/fixtures' / ('bridge-' + name + '.json')).read_text())

    def test_hooks_preserve_class_methods_and_member_id_and_headers(self):
        class Provider:
            kind = 'fixture'
            def member_id(self, model):
                return model[7:] if model.startswith('vendor-') else model
            def cpa_headers(self, member):
                return {'x-fixture-id': '$x-fixture-id'}
        with mock.patch.dict(cp.LANE_PROVIDERS, fixture=cp.LaneProvider(Provider())):
            self.assertEqual(cp.default_member_id('vendor-review-1', {'review'}, 'fixture'), 'review2')
            lanes = cp.validate_lanes({'lanes': {'review': {'role': 'Review.', 'members': [
                {'provider': 'fixture', 'model': 'vendor-review-1'}]}}})
            plan = cp.lane_plan(lanes)
            block = cp.render_lanes_block(plan, 'synthetic')
            self.assertIn('headers: { x-fixture-id: "$x-fixture-id" }', block)
            self.assertIn('protocol: "meta"', block)

    def test_unused_broken_provider_does_not_block_ordinary_plan(self):
        class Provider:
            kind = 'fixture'
            def validate_lane(self, *args):
                raise RuntimeError('broken')
        with mock.patch.dict(cp.LANE_PROVIDERS, fixture=cp.LaneProvider(Provider())):
            self.assertTrue(cp.validate_lanes(EXAMPLE_LANES))

    def test_systemexit_hook_becomes_local_error(self):
        provider = cp.LaneProvider({'kind': 'fixture', 'member_id': lambda model: (_ for _ in ()).throw(SystemExit(2))})
        with mock.patch.dict(cp.LANE_PROVIDERS, fixture=provider):
            with self.assertRaises(cp.LaneError):
                cp.default_member_id('m', set(), 'fixture')
            self.assertTrue(cp.lane_plan(cp.validate_lanes(EXAMPLE_LANES)))


class Extensions(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=HOME)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def extension(self, name='good', fail=''):
        p = self.root / (name + '.py')
        p.write_text('''def bind(core):
    global BridgeError
    BridgeError = core.BridgeError
class Service:
    def __init__(self, cfg, sealer, state):
        self.stopped = False
    def recover(self):
        pass
    def validate(self, route):
        pass
    def run(self, handler, route, body):
        if body.get('shared'):
            raise BridgeError(429, 'fixture quota', 'fixture_quota')
        return 201
    def shutdown(self):
        self.stopped = True
''' + fail)
        return p

    def bridge(self, extensions):
        return b.Bridge(types.SimpleNamespace(seal_key=b'a' * 32, extensions=extensions, models={}))

    def test_shared_error_is_exact_core_class(self):
        path = self.extension()
        self.assertIs(b.load_extension_module(path).BridgeError, b.BridgeError)
        bridge = self.bridge({'good': str(path)})
        with self.assertRaises(b.BridgeError) as caught:
            bridge.run_extension(None, {'extension': 'good'}, {'shared': True})
        self.assertEqual((caught.exception.status, caught.exception.code), (429, 'fixture_quota'))
        self.assertEqual(bridge.run_extension(None, {'extension': 'good'}, {}), 201)

    def test_missing_broken_import_and_constructor_are_isolated(self):
        broken = self.extension('broken', '\nraise SystemExit(3)\n')
        ctor = self.extension('ctor', '\nService.__init__ = lambda *args: 1 / 0\n')
        with contextlib.redirect_stderr(io.StringIO()):
            bridge = self.bridge({'good': str(self.extension()), 'absent': str(self.root / 'absent.py'),
                                  'broken': str(broken), 'ctor': str(ctor)})
        self.assertEqual(set(bridge.extensions), {'good'})
        for name in ('absent', 'broken', 'ctor'):
            with self.assertRaises(b.BridgeError) as caught:
                bridge.run_extension(None, {'extension': name}, {})
            self.assertEqual(caught.exception.code, 'extension_unavailable')
        self.assertEqual(bridge.run_extension(None, {'extension': 'good'}, {}), 201)

    def test_recovery_and_runtime_failures_only_disable_their_extension(self):
        bridge = self.bridge({'good': str(self.extension()), 'bad': str(self.extension('bad'))})
        with mock.patch.object(bridge.extensions['bad'], 'recover', side_effect=RuntimeError('broken')), \
                contextlib.redirect_stderr(io.StringIO()):
            bridge.recover()
        self.assertEqual(set(bridge.extensions), {'good'})
        service = bridge.extensions['good']
        with mock.patch.object(service, 'run', side_effect=SystemExit(4)):
            with self.assertRaises(b.BridgeError):
                bridge.run_extension(None, {'extension': 'good'}, {})
        self.assertTrue(service.stopped)
        self.assertFalse(bridge.extensions)

    def test_config_keeps_standard_route_when_extension_is_absent(self):
        key = self.root / 'key'; key.write_text('synthetic')
        seal = self.root / 'seal'; seal.write_text('aa' * 32)
        config = self.root / 'bridge.json'
        config.write_text(json.dumps({'key_file': str(key), 'seal_key_file': str(seal),
            'extensions': {'missing': str(self.root / 'absent.py')},
            'upstreams': {'plain': {'base_url': 'https://example.test/v1', 'key_file': str(key)}},
            'models': {'plain': {'upstream': 'plain'}, 'private': {'extension': 'missing'},
                       'legacy': {'private_route': 'old'}}}))
        cfg = b.Config(config)
        with contextlib.redirect_stderr(io.StringIO()):
            bridge = b.Bridge(cfg)
        wire, _, _, _ = b.prepare({'model': 'plain', 'input': 'hello'}, cfg.models['plain'], bridge.sealer)
        self.assertEqual(wire['model'], 'plain')
        self.assertEqual(set(cfg.models), {'plain', 'private', 'legacy'})
        self.assertEqual(set(bridge.available_models()), {'plain'})

    def test_server_binds_before_recovery_and_shuts_down_on_exit(self):
        calls = []
        bridge = mock.Mock()
        bridge.recover.side_effect = lambda: calls.append('recover')
        bridge.shutdown.side_effect = lambda: calls.append('shutdown')
        server = mock.Mock()
        server.serve_forever.side_effect = lambda: calls.append('serve')
        server.server_close.side_effect = lambda: calls.append('close')
        config = types.SimpleNamespace(port=59999, models={})
        with mock.patch.object(b.sys, 'argv', ['bridge']), mock.patch.object(b, 'Config', return_value=config), \
                mock.patch.object(b, 'Bridge', return_value=bridge), \
                mock.patch.object(b, 'Server', side_effect=lambda *a: (calls.append('bind'), server)[1]), \
                mock.patch.object(b, 'log'), mock.patch.object(b.signal, 'signal'):
            b.main()
        self.assertEqual(calls, ['bind', 'recover', 'serve', 'shutdown', 'close'])
