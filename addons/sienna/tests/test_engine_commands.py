from _engine_helpers import le
"""Engine command rendering and acceptance plumbing; never calls Claude or launchd."""
from _helpers import addon
import unittest
if addon is None:
    raise unittest.SkipTest('sienna add-on absent; dependent desktop/engine tests')
from _helpers import HOME, cp
import copy
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest import mock


class Commands(unittest.TestCase):
    def plan(self):
        return cp.lane_plan(cp.validate_lanes({'lanes': {'sienna': {'role': 'Review the workspace.',
            'members': [{'provider': 'sienna', 'model': 'claude-test-model'}]}}}))

    def prep(self):
        return {'bridge_new': cp.render_bridge_config(self.plan(), login_path='/usr/bin:/bin'), 'provider_prep': {'sienna': {'directories': []}}}

    def test_engine_role_and_agents_never_request_edits(self):
        lane = self.plan()[0]
        role = cp.render_role(lane)
        self.assertIn('read-only', role)
        self.assertIn('Read, Grep and Glob', role)
        self.assertNotIn('apply_patch <<', role)
        self.assertIn('apps = false', role)
        self.assertEqual(le.ENGINE_ROLE_INSTRUCTIONS.splitlines()[0], cp.ROLE_INSTRUCTIONS.splitlines()[0])
        agents = cp.render_agents_block([lane])
        self.assertIn('Read-only', agents)
        self.assertIn('cannot edit', agents)
        self.assertIn('fork_turns "none"', agents)

    def test_parser_accept_and_terminal_resume(self):
        args = cp.build_parser().parse_args(['lane', 'apply', '--accept-engine', '--dry-run'])
        self.assertTrue(args.accept_engine and args.dry_run)
        args = cp.build_parser().parse_args(['claude', 'lane-resume', 'session'])
        self.assertEqual(args.claude_fn, le.cmd_claude_lane_resume)

    def test_accept_dry_never_probes_and_missing_engine_refuses(self):
        bridge = mock.Mock()
        bridge.BridgeError = type('BridgeError', (Exception,), {})
        bridge.installed_engine_version.return_value = '2.1.283'
        prep = self.prep()
        original = copy.deepcopy(prep)
        with mock.patch.object(le, 'engine_bridge_module', return_value=bridge), \
                mock.patch.object(le, 'engine_launcher_ready', return_value=True):
            le.accept_lane_engines(prep, True)
        bridge.accept_engine.assert_not_called()
        self.assertEqual(prep, original)
        with mock.patch.object(le, 'engine_bridge_module', return_value=bridge), \
                mock.patch.object(le, 'engine_launcher_ready', return_value=False), self.assertRaises(RuntimeError):
            le.accept_lane_engines(prep, False)
        bridge.accept_engine.assert_not_called()
        with self.assertRaises(RuntimeError):
            le.accept_lane_engines({'bridge_new': None}, False)

    def test_accept_commits_only_after_success_and_normal_apply_preserves_it(self):
        bridge = mock.Mock()
        bridge.BridgeError = type('BridgeError', (Exception,), {})
        bridge.installed_engine_version.return_value = '2.1.283'
        expect = dict(tools=['Read', 'Grep', 'Glob'], permissionMode='default', apiKeySource='none', mcp_servers=[], plugins=[])
        bridge.accept_engine.return_value = ('2.1.283', expect)
        prep = self.prep()
        with mock.patch.object(le, 'engine_bridge_module', return_value=bridge), \
                mock.patch.object(le, 'engine_launcher_ready', return_value=True):
            le.accept_lane_engines(prep, False)
        config = json.loads(prep['bridge_new'])
        self.assertEqual(config['engines']['sienna']['init_expect'], {'2.1.283': expect})
        self.assertEqual(json.loads(cp.render_bridge_config(self.plan(), config, login_path='/usr/bin:/bin')), config)
        prep = self.prep()
        original = prep['bridge_new']
        failure = bridge.BridgeError()
        failure.message = 'probe failed'
        bridge.accept_engine.side_effect = failure
        with mock.patch.object(le, 'engine_bridge_module', return_value=bridge), \
                mock.patch.object(le, 'engine_launcher_ready', return_value=True), self.assertRaises(RuntimeError):
            le.accept_lane_engines(prep, False)
        self.assertEqual(prep['bridge_new'], original)

    def test_cpa_diagnostic_is_explicit_and_records_failures(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            state = Path(temp)
            binary = state / 'fake-cpa'
            binary.write_text('not executable')
            def run(argv, **kwargs):
                env = kwargs['env']
                self.assertNotEqual(env['HOME'], str(HOME))
                self.assertEqual(env['CODEXPOOL_CPA_BINARY'], str(binary))
                Path(env['CODEXPOOL_CPA_RESULT']).write_text(json.dumps(dict(ok=True, cpa_sha256='fixture')))
                return types.SimpleNamespace(returncode=0)
            with mock.patch.object(cp, 'STATE', state), mock.patch.object(cp, 'cpa_binary', return_value=binary), \
                    mock.patch.object(cp.subprocess, 'run', side_effect=run):
                le.run_cpa_passthrough(state)
            report = json.loads((state / 'engine-cpa-check.json').read_text())
            self.assertTrue(report['ok'])
            self.assertIn('checked_at', report)
            with mock.patch.object(cp, 'STATE', state), mock.patch.object(cp, 'cpa_binary', return_value=binary), \
                    mock.patch.object(cp.subprocess, 'run', return_value=types.SimpleNamespace(returncode=0)):
                le.run_cpa_passthrough(state)
            self.assertFalse(json.loads((state / 'engine-cpa-check.json').read_text())['ok'])

    def test_owner_cpa_exceptions_survive_apply_and_invalid_shape_refuses(self):
        previous = json.loads(self.prep()['bridge_new'])
        previous['cpa_known_normalisations'] = ['$.messages[0].content']
        rendered = json.loads(cp.render_bridge_config(self.plan(), previous, login_path='/usr/bin:/bin'))
        self.assertEqual(rendered['cpa_known_normalisations'], previous['cpa_known_normalisations'])
        previous['cpa_known_normalisations'] = '*'
        with self.assertRaises(cp.LaneError):
            cp.render_bridge_config(self.plan(), previous)

    def test_engine_lane_task_reads_and_checks_without_edit_requirement(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            state = Path(temp)
            (state / 'engine-sessions').mkdir()
            profile = state / 'profile'
            profile.mkdir()
            bridge = mock.Mock()
            seen = []
            def run(argv, **kwargs):
                work = Path(argv[argv.index('-C') + 1])
                check = (work / 'README.md').read_text().split(': ')[1].strip()
                seen.append(argv[-1])
                (state / 'engine-sessions' / 'test.json').write_text(json.dumps(dict(cwd=str(work), member='test:model', uuid='test')))
                (profile / 'test.jsonl').write_text('{}\n')
                return dict(code=0, thread_id='test', errors=[], answer=check + '\nalpha\n-beta')
            with mock.patch.object(cp, 'STATE', state), mock.patch.object(le, 'engine_bridge_module', return_value=bridge), \
                    mock.patch.object(cp, '_run_codex', side_effect=run), mock.patch.object(cp, '_child_rollout', return_value=state), \
                    mock.patch.object(cp, '_child_facts', return_value=dict(models={'sienna'}, compactions=0, parse_failures=0)), \
                    mock.patch.object(le, 'recorded_engines', return_value={'test': {'profile': str(profile)}}):
                result = le.engine_test('stub-codex', 'sienna', 'sienna', False, 'seed')
            self.assertTrue(result[0], result)
            self.assertIn('Read a.txt', seen[0])
            self.assertNotIn('using apply_patch', seen[0])
            self.assertIn('Do not change any file', seen[0])


if __name__ == '__main__':
    unittest.main()
