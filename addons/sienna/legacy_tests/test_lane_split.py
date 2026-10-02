"""Step 5 boundaries and transactional installation, entirely inside the test HOME."""
from _helpers import addon, cp, HOME, REPO, EXAMPLE_LANES
from _engine_helpers import le, load_core_bridge
import ast
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock


class LaneSplit(unittest.TestCase):
    def plan(self):
        return cp.lane_plan(cp.validate_lanes({'lanes': {'review': {'role': 'Review.', 'members': [
            {'provider': 'sienna', 'model': 'claude-review-model'}]}}}))

    def config(self):
        config = json.loads(cp.render_bridge_config(self.plan(), login_path='/usr/bin:/bin'))
        entry = config['engines']['review']
        entry.update(accepted_versions=['2.1.283'], init_expect={'2.1.283': {
            'permissionMode': 'default', 'apiKeySource': 'none', 'tools': ['Read', 'Grep', 'Glob'],
            'mcp_servers': [], 'plugins': []}})
        return config

    def test_no_engine_implementation_in_core(self):
        for name in ('engine_settings', 'engine_profile', 'engine_environment', 'engine_bridge_module',
                     'engine_state', 'doctor_engine', 'run_cpa_passthrough', 'cmd_claude_lane_resume'):
            self.assertFalse(hasattr(cp, name), name)
        bridge = (REPO / 'lanes/bridge.py').read_text()
        self.assertNotIn("route.get('engine')", bridge)
        self.assertFalse(hasattr(load_core_bridge(), 'EngineService'))

    def test_old_and_new_command_spellings_reach_addon(self):
        parser = cp.build_parser()
        for family in ('sienna', 'claude'):
            for command in ('resume', 'lane-resume'):
                self.assertIs(parser.parse_args([family, command, 'session']).claude_fn, le.cmd_claude_lane_resume)
            self.assertIs(parser.parse_args([family, 'accept-engine', '--dry-run']).claude_fn, le.cmd_accept_engine)
            self.assertIs(parser.parse_args([family, 'cpa-check', 'fixtures']).claude_fn, le.cmd_cpa_check)
        args = parser.parse_args(['lane', 'apply', '--accept-engine', '--dry-run'])
        self.assertIs(args.before_apply, le.accept_lane_engines)
        with mock.patch.object(le, 'run_cpa_passthrough') as check:
            addon.before_doctor(parser.parse_args(['doctor', '--cpa-passthrough', 'fixtures']))
        check.assert_called_once_with('fixtures')

    def test_legacy_config_migration_preserves_acceptance_and_other_routes(self):
        config = self.config()
        config['models']['plain'] = {'upstream': 'plain', 'upstream_model': 'plain-model'}
        del config['extensions']
        del config['models']['lane-review-review']['extension']
        before = copy.deepcopy(config)
        self.assertTrue(le.install_bridge_config(config))
        self.assertEqual(config['engines'], before['engines'])
        self.assertEqual(config['models']['plain'], before['models']['plain'])
        self.assertEqual(config['models']['lane-review-review']['extension'], 'sienna')
        self.assertFalse(le.install_bridge_config(config))
        rendered = json.loads(cp.render_bridge_config(self.plan(), config, login_path='/usr/bin:/bin'))
        self.assertEqual(rendered['engines'], before['engines'])

    def test_code_config_commit_and_rollback_no_restarts(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            root = Path(temp)
            bridge_config = root / 'lanes/bridge.json'
            bridge_config.parent.mkdir()
            legacy = self.config()
            del legacy['extensions']
            for route in legacy['models'].values():
                route.pop('extension')
            before = json.dumps(legacy, separators=(',', ':')) + '\n'
            bridge_config.write_text(before)
            bridge_config.chmod(0o600)
            (root / 'bin').mkdir()
            (root / 'bin/subpool').write_text('old CLI')
            replace = cp.os.replace
            def interrupted(source, target):
                if Path(target) == root / 'bin/subpool' and 'payload' in Path(source).parts:
                    # The complete extension and matching config are present before the final CLI swap.
                    current = json.loads(bridge_config.read_text())
                    self.assertTrue(Path(current['extensions']['sienna']).is_file())
                    raise OSError('interrupted final publish')
                return replace(source, target)
            with mock.patch.multiple(cp, ROOT=root, BRIDGE_CONFIG=bridge_config, ADDON_REGISTRY=root/'registry.json'), \
                    mock.patch.object(cp, 'kick_agent', side_effect=AssertionError('must not restart')), \
                    mock.patch.object(cp, 'load_agent', side_effect=AssertionError('must not launch')):
                with mock.patch.object(cp.os, 'replace', side_effect=interrupted), self.assertRaises(OSError):
                    cp.copy_code()
                self.assertEqual(bridge_config.read_text(), before)
                self.assertEqual((root / 'bin/subpool').read_text(), 'old CLI')
                self.assertFalse((root / 'addons/sienna').exists())
                cp.copy_code()
                current = json.loads(bridge_config.read_text())
                self.assertEqual(current['engines'], legacy['engines'])
                for filename in ('lane_engine.py', 'bridge_engine.py', 'cpa_check.py'):
                    self.assertEqual((root / 'addons/sienna' / filename).read_bytes(), (addon.path / filename).read_bytes())
                self.assertEqual(bridge_config.stat().st_mode & 0o777, 0o600)

    def test_install_with_ordinary_config_keeps_exact_bytes(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            root = Path(temp)
            config = root / 'lanes/bridge.json'
            config.parent.mkdir()
            before = '{ "models": {}, "upstreams": {} }\n'
            config.write_text(before)
            with mock.patch.multiple(cp, ROOT=root, BRIDGE_CONFIG=config, ADDON_REGISTRY=root/'registry.json'):
                cp.copy_code()
            self.assertEqual(config.read_text(), before)

    def test_runtime_checker_is_installed_without_test_helpers(self):
        source = (addon.path / 'cpa_check.py').read_text()
        self.assertNotIn('_helpers', source)
        self.assertIn('cpa_check.py', addon.manifest['code_files'])
        self.assertNotIn('tests/test_cpa_passthrough.py', cp.CODE_FILES)

    def test_provider_readiness_runs_with_the_addon_loaded(self):
        """The engine's readiness check reaches the core through cp (a bare core name raised NameError live)."""
        self.assertIsInstance(le.engine_launcher_ready(), bool)
        states = cp.lane_provider_states() if hasattr(cp, 'lane_provider_states') else None
        if states is not None:
            self.assertNotIn('is not defined', json.dumps(states, default=str))
