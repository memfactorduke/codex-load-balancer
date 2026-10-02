from _engine_helpers import le
"""Lanes: lanes.json validation, the lane plan and what lane apply renders from it, config.yaml and AGENTS.md
splicing, status lines, and the checks that need a pool (against a stand-in pool). Member aliases (<lane>-<id>)
exist only while lane test runs: lane_plan(..., with_members=True)."""
import contextlib
import io
import json
import os
import random
import shutil
import socket
import threading
import time
import unittest
from unittest import mock

from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import EXAMPLE_LANES, HOME, FakePool, cp, preserved, run
from _engine_helpers import load_bridge

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

DEFS = {'grok-4.7-build-fast': 500000}
GOOD = {'provider': 'xai', 'model': 'grok-1'}
MULTI = {'lanes': {
    'bulk': EXAMPLE_LANES['lanes']['bulk'],
    'fast': {'role': 'Quick lookups.', 'effort': 'low', 'members': [{'provider': 'xai', 'model': 'grok-mini'}]},
    'cheap': {'role': 'Drafts', 'effort': 'medium', 'members': [{'id': 'zen', 'provider': 'opencode-zen',
                                                                 'model': 'm-zen'}]}}}
CHEAP = {'lanes': {'cheap': MULTI['lanes']['cheap']}}


def plan_of(raw=EXAMPLE_LANES, defs=DEFS, members=False):
    return cp.lane_plan(cp.validate_lanes(raw), defs, with_members=members)


def problems(raw):
    try:
        cp.validate_lanes(raw)
    except cp.LaneError as e:
        return e.problems
    return []


def lane(members, **spec):
    return {'lanes': {'b': dict({'role': 'r', 'members': members}, **spec)}}


def base_config():
    return cp.CONFIG.read_text()


class Sienna(unittest.TestCase):
    """Configuration only: no real engine, launchd, sockets, credentials or acceptance probe."""
    MEMBER = {'provider': 'sienna', 'model': 'claude-opus-5-5', 'name': 'Opus 5.5'}
    PATH = '/usr/bin:/bin'

    def plan(self, **member):
        return plan_of({'lanes': {'sienna': {'role': 'Read and review.', 'effort': 'medium',
                                           'members': [dict(self.MEMBER, **member)]}}})

    def config(self, **member):
        return json.loads(cp.render_bridge_config(self.plan(**member), login_path=self.PATH))

    def test_validation_defaults_ids_display(self):
        raw = {'lanes': {'sienna': {'role': 'Review.', 'members': [
            dict(self.MEMBER), {'provider': 'sienna', 'model': 'claude-sonnet-5'},
            {'provider': 'sienna', 'model': 'claude-fable-5-1'}, dict(self.MEMBER)]}}}
        planned, = plan_of(raw)
        self.assertEqual([m['id'] for m in planned['members']], ['opus', 'sonnet', 'fable', 'opus2'])
        self.assertEqual(planned['display'], 'Claude')
        self.assertEqual(planned['context'], 200000)
        for m in planned['members']:
            self.assertEqual(m['kind'], 'engine')
            self.assertNotIn('key_name', m)
            self.assertNotIn('upstream_url', m)
            self.assertEqual({k: m[k] for k in le.ENGINE_MEMBER_DEFAULTS}, le.ENGINE_MEMBER_DEFAULTS)
        self.assertEqual(self.plan(context_1m=True)[0]['context'], 1000000)
        self.assertEqual(self.plan(context=50000)[0]['context'], 50000)
        self.assertEqual(self.plan()[0]['members'][0]['display'], 'Claude: Opus 5.5')

    def test_validation_refusals(self):
        for field, value in [('base_url', 'https://example.com'), ('session_header', 'x-session'),
                             ('max_turns', True), ('max_turns', 0), ('max_turns', 1.5),
                             ('turn_timeout', -1), ('turn_timeout', '60'), ('context_1m', 1),
                             ('ultracode', True), ('pool', 'work'), ('pool', {}),
                             ('accepted_versions', ['2.1.283']), ('env', {})]:
            with self.subTest(field=field, value=value):
                with self.assertRaises(cp.LaneError):
                    self.plan(**{field: value})
        for field, value in le.ENGINE_MEMBER_DEFAULTS.items():
            with self.subTest(field=field):
                with self.assertRaises(cp.LaneError):
                    plan_of(lane([dict(GOOD, **{field: value})]))

    def test_lane_limits_and_member_override(self):
        planned, = plan_of(lane([dict(self.MEMBER, max_turns=4)], max_turns=10, turn_timeout=90))
        self.assertEqual((planned['members'][0]['max_turns'], planned['members'][0]['turn_timeout']), (4, 90))
        for value in (False, 0, '60'):
            with self.assertRaises(cp.LaneError):
                plan_of(lane([self.MEMBER], max_turns=value))

    def test_edit_derives_engine_ids(self):
        raw = {'lanes': {'sienna': {'role': 'Review.', 'members': [self.MEMBER]}}}
        edited = cp.lane_edit_raw(raw, 'sienna', [('add_member', 'sienna:claude-sonnet-5:Sonnet 5', None)])
        self.assertEqual([m['id'] for m in edited['lanes']['sienna']['members']], ['opus', 'sonnet'])

    def test_bridge_engine_environment_and_settings(self):
        with mock.patch.dict(os.environ, {'ANTHROPIC_API_KEY': 'not-forwarded', 'CLAUDE_CODE_OAUTH_TOKEN': 'not-forwarded',
                                          'CODEXPOOL_EXTRA': 'not-forwarded'}):
            config = self.config(max_turns=4, turn_timeout=30)
        self.assertEqual(config['upstreams'], {})
        engine = config['engines']['sienna']
        self.assertEqual(engine['launcher'], str(sienna_pool.CLAUDE_LAUNCHER))
        self.assertEqual(engine['profile'], str(cp.LANES_DIR / 'sienna-home'))
        self.assertEqual((engine['accepted_versions'], engine['init_expect']), ([], {}))
        env = engine['env']
        self.assertEqual(set(env), {'PATH', 'HOME', 'USER', 'LANG', 'SHELL', 'TMPDIR', 'CLAUDE_CONFIG_DIR',
                                   'ANTHROPIC_AUTH_TOKEN', 'CLAUDEPOOL', 'DISABLE_AUTOUPDATER',
                                   'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB'})
        self.assertEqual((env['CLAUDE_CONFIG_DIR'], env['ANTHROPIC_AUTH_TOKEN'], env['CLAUDEPOOL']),
                         (engine['profile'], 'subpool', 'required'))
        self.assertEqual(env['PATH'], self.PATH)
        self.assertEqual(env['TMPDIR'], str(le.engine_profile('sienna') / 'tmp'))
        # One policy, two copies: lane apply writes it and the bridge checks the acceptance fingerprint against its own
        self.assertEqual(engine['settings'], le.engine_settings())
        self.assertEqual(le.engine_settings(), load_bridge().engine_settings())
        settings = engine['settings']
        self.assertIs(settings['disableAllHooks'], True)
        self.assertIs(settings['disableSkillShellExecution'], True)
        deny = settings['permissions']['deny']
        for rule in ('Bash', 'Edit', 'Write', 'NotebookEdit', 'WebFetch', 'WebSearch', 'Agent', 'Skill',
                     'Read(~/.subpool/**)', 'Read(~/.codex/**)', 'Read(~/.claude/**)', 'Read(~/.claude.json)',
                     'Read(~/.ssh/**)', 'Read(~/.config/**)', 'Read(~/Library/**)', 'Read(~/.gnupg/**)'):
            self.assertIn(rule, deny)
        model = config['models']['lane-sienna-opus']
        self.assertEqual((model['engine'], model['upstream_model'], model['effort']),
                         ('sienna', 'claude-opus-5-5', 'medium'))
        self.assertEqual((model['max_turns'], model['turn_timeout'], model['pool']), (4, 30, 'default'))
        self.assertNotIn('upstream', model)

    def accept(self, config):
        engine = config['engines']['sienna']
        engine['accepted_versions'] = ['2.1.283']
        engine['init_expect'] = {'2.1.283': {'tools': ['Read', 'Grep', 'Glob'], 'permissionMode': 'default',
                                            'apiKeySource': 'none', 'mcp_servers': [], 'plugins': []}}
        return config

    def test_normal_apply_preserves_only_matching_acceptance(self):
        previous = self.accept(self.config())
        rendered = json.loads(cp.render_bridge_config(self.plan(), previous, self.PATH))
        self.assertEqual(rendered, previous)
        changed = json.loads(cp.render_bridge_config(self.plan(), previous, '/different/bin'))
        self.assertEqual(changed['engines']['sienna']['accepted_versions'], [])
        previous['engines']['sienna']['init_expect']['2.1.283']['tools'].append('Bash')
        self.assertEqual(json.loads(cp.render_bridge_config(self.plan(), previous, self.PATH))
                         ['engines']['sienna']['accepted_versions'], [])

    def test_headers_are_engine_credentials_only(self):
        raw = {'lanes': dict(EXAMPLE_LANES['lanes'], sienna={'role': 'Review.', 'members': [self.MEMBER]})}
        plan = plan_of(raw)
        block = cp.render_lanes_block(plan, 'test-bridge-key')
        self.assertEqual(block.count('    headers:'), 1)
        self.assertIn('x-codex-turn-metadata: "$x-codex-turn-metadata"', block)
        self.assertIn('x-codex-parent-thread-id: "$x-codex-parent-thread-id"', block)
        self.assertIn('alias: "sienna", display-name: "Claude"', block)
        self.assertNotIn('ANTHROPIC_AUTH_TOKEN', block)
        self.assertNotIn('claude-opus-5-5', block)  # the pool sees the bridge model, not a seat/upstream model
        bulk = cp.render_lanes_block(plan_of(), 'test-bridge-key')
        self.assertNotIn('headers:', bulk)
        config = base_config()
        self.assertTrue(cp.lanes_config_text(config, plan, 'test-bridge-key', []).startswith(config.rstrip()))

    def test_profiles_private_and_symlinks_refused(self):
        plan = self.plan()
        paths = le.engine_directories(plan)
        self.addCleanup(shutil.rmtree, le.engine_profile('sienna'), True)
        for path in paths:
            le.make_engine_directory(path)
            self.assertEqual(path.stat().st_mode & 0o777, 0o700)
        le.make_engine_directory(le.engine_profile('sienna'))  # idempotent
        self.assertFalse((le.engine_profile('sienna') / '.credentials.json').exists())
        self.assertFalse((le.engine_profile('sienna') / 'settings.json').exists())  # inline --settings only
        link = le.engine_profile('sienna') / 'tmp'
        link.rmdir()
        link.symlink_to(HOME)
        with self.assertRaises(cp.LaneError):
            le.make_engine_directory(link)

    def test_prepare_and_dry_run_create_no_profile(self):
        with preserved(cp.BRIDGE_CONFIG), mock.patch.object(le, 'engine_login_path', return_value=self.PATH), \
                mock.patch.object(cp, 'pool_model_ids', return_value=set()), \
                mock.patch.object(cp, 'launchd_loaded', return_value=(False, None)), \
                mock.patch.object(cp, 'load_agent') as load:
            prep = cp.lane_prepare(cp.validate_lanes({'lanes': {'sienna': {
                'role': 'Review.', 'members': [self.MEMBER]}}}), {'defs': None, 'seats': []})
            self.assertEqual(prep['missing_keys'], [])
            with contextlib.redirect_stdout(io.StringIO()) as output:
                cp.lane_apply_steps(cp.LaneSteps(True), prep, True, None)
            self.assertIn('isolated lane state', output.getvalue())
            self.assertFalse(le.engine_profile('sienna').exists())
            load.assert_not_called()

    def test_status_and_doctor_refuse_unaccepted_engine(self):
        plan = self.plan()
        self.addCleanup(shutil.rmtree, le.engine_profile('sienna'), True)
        for path in le.engine_directories(plan):
            le.make_engine_directory(path)
        with preserved(cp.BRIDGE_CONFIG), preserved(cp.LANES_FILE), \
                mock.patch.object(le, 'engine_launcher_ready', return_value=True), \
                mock.patch.object(sienna_pool, 'real_claude', return_value=cp.CURRENT / 'cli-proxy-api'), \
                mock.patch.object(sienna_guard, 'claude_code_version', return_value='2.1.283'), \
                mock.patch.object(cp, 'port_open', return_value=True), \
                mock.patch.object(cp, 'bridge_health', return_value={'lane-sienna-opus'}), \
                mock.patch.object(le, 'engine_login_path', return_value=self.PATH), \
                mock.patch.object(le, 'engine_managed_settings', return_value=[]), \
                mock.patch.object(cp, '_probe_status', return_value=200), \
                mock.patch.object(cp, 'pool_model_ids', return_value={'sienna'}), \
                mock.patch.object(cp, 'pool_facts', return_value={'defs': None, 'seats': [], 'error': None}), \
                mock.patch.object(cp, 'launchd_loaded', return_value=(True, 123)):
            cp.write_json(cp.LANES_FILE, {'lanes': {'sienna': {'role': 'Review.', 'effort': 'medium',
                                                            'members': [self.MEMBER]}}})
            cp.write_json(cp.BRIDGE_CONFIG, self.config())
            self.assertEqual(cp.lane_status_lines({'seats': []}), ['lane sienna: opus ✕ untested engine'])
            checks = []
            cp.doctor_lanes(lambda ok, text, *a, **kw: checks.append((ok, text)), [])
            self.assertIn((False, 'sienna: engine version 2.1.283 untested'), checks)
            self.assertTrue(any('pool-required launcher' in t and ok for ok, t in checks))
            cp.write_json(cp.BRIDGE_CONFIG, self.accept(self.config()))
            self.assertEqual(cp.lane_status_lines({'seats': []}), ['lane sienna: opus ● engine ok'])
            self.assertEqual(cp.lane_state_cell(plan[0]['members'][0], plan[0], [], None), ('✕', 'bridge down'))
            with mock.patch.object(sienna_guard, 'claude_code_version', return_value='2.1.284'):
                self.assertEqual(le.engine_state('sienna'), 'untested engine')

    def test_models_has_no_remote_key_path(self):
        with mock.patch.object(cp, 'remote_models') as remote:
            with self.assertRaisesRegex(cp.LaneError, 'no provider key'):
                cp.lane_models_data('sienna')
            remote.assert_not_called()

    def test_managed_settings_names_only(self):
        system = HOME / 'managed-system'
        preferences = HOME / 'managed-preferences'
        self.addCleanup(shutil.rmtree, system, True)
        self.addCleanup(shutil.rmtree, preferences, True)
        domain = 'com.anthropic.claudecode.plist'
        paths = [system / 'managed-settings.json', system / 'managed-settings.d' / 'policy.json',
                 preferences / domain, preferences / cp.getpass.getuser() / domain]
        for path in paths:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('not parsed or printed')
        self.assertEqual(set(le.engine_managed_settings(system, preferences)), set(paths))

    def test_login_path_capture_uses_clean_environment(self):
        result = mock.Mock(returncode=0, stdout=b'login banner\n\0/usr/bin:/bin\0')
        with mock.patch.object(cp.subprocess, 'run', return_value=result) as run_shell:
            self.assertEqual(le.engine_login_path(), self.PATH)
        args, kwargs = run_shell.call_args
        self.assertEqual(args[0][1], '-lc')
        self.assertEqual(set(kwargs['env']), {'HOME', 'USER', 'SHELL', 'LANG', 'PATH'})
        for output in (b'/usr/bin', b'\0relative:/usr/bin\0', b'\0/usr/bin:\0'):
            with mock.patch.object(cp.subprocess, 'run', return_value=mock.Mock(returncode=0, stdout=output)):
                with self.assertRaises(cp.LaneError):
                    le.engine_login_path()

    def test_state_and_provider_do_not_require_a_key(self):
        with mock.patch.object(le, 'engine_launcher_ready', return_value=False):
            self.assertEqual(le.engine_state(), 'no launcher')
        with mock.patch.object(le, 'engine_launcher_ready', return_value=True), \
                mock.patch.object(sienna_pool, 'real_claude', return_value=None):
            self.assertEqual(le.engine_state(), 'no engine')
        with mock.patch.object(le, 'engine_state', return_value='untested engine'), \
                mock.patch.object(cp, 'pool_facts', return_value={'seats': [], 'error': None}):
            provider = next(p for p in cp.lane_providers_data()['providers'] if p['id'] == 'sienna')
        self.assertEqual((provider['needs'], provider['key_name'], provider['ready']), ('engine', None, False))
        self.assertIn('--accept-engine', provider['detail'])

    def test_apply_creates_profile_in_throwaway_home(self):
        plan = self.plan()
        self.addCleanup(shutil.rmtree, le.engine_profile('sienna'), True)
        with contextlib.ExitStack() as stack:
            for path in (cp.CONFIG, cp.CONFIG_BEFORE_LANES, cp.BRIDGE_CONFIG, cp.INSTALL_FILE,
                         cp.CODEX_AGENTS_MD, cp.CODEX_AGENTS_DIR / 'sienna.toml', cp.ROOT / 'lanes' / 'bridge.py'):
                stack.enter_context(preserved(path))
            for name, value in [('engine_login_path', self.PATH), ('pool_model_ids', set()),
                                ('launchd_loaded', (False, None)), ('load_agent', None), ('wait_for', True)]:
                stack.enter_context(mock.patch.object(le if name == 'engine_login_path' else cp, name, return_value=value))
            prep = cp.lane_prepare(cp.validate_lanes({'lanes': {'sienna': {
                'role': 'Review.', 'members': [self.MEMBER]}}}), {'defs': None, 'seats': []})
            with contextlib.redirect_stdout(io.StringIO()):
                cp.lane_apply_steps(cp.LaneSteps(False), prep, False, None)
            for path in le.engine_directories(plan):
                self.assertEqual(path.stat().st_mode & 0o777, 0o700)
            saved = json.loads(cp.BRIDGE_CONFIG.read_text())
            self.assertEqual(saved['engines']['sienna']['accepted_versions'], [])
            self.assertEqual(cp.BRIDGE_CONFIG.stat().st_mode & 0o777, 0o600)
            self.assertEqual(list(le.engine_profile('sienna').iterdir()), [le.engine_profile('sienna') / 'tmp'])
            self.assertIn('headers:', cp.CONFIG.read_text())


if __name__ == '__main__':
    unittest.main()
