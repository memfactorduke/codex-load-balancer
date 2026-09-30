"""Pool descriptors dispatch shared operations without depending on a built-in pool name.
All state and management records are synthetic and stay in the helper's throwaway home.
"""
from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import HOME, cp, run

import copy
import datetime as dt
import json
import pathlib
import tempfile
import unittest
from unittest import mock

from test_claude_guard import ClaudePass, usage_body


class Descriptors(unittest.TestCase):
    def test_balancing_log_prefix_comes_from_descriptor(self):
        pool = cp.seat_pool('claude')
        pool.name, pool.log_tag = 'fixture', 'fixture '
        guard = {'balancing': {'mode': 'reset'}}
        with mock.patch.dict(cp.SETTINGS, {pool.balancing: 'priority'}), \
                mock.patch.object(cp, 'read_meta', return_value={}), \
                mock.patch.object(cp, 'manual_targets', return_value=({}, False)), \
                mock.patch.object(cp, 'set_pool_order', side_effect=cp.PoolDown('fixture failure')), \
                mock.patch.object(cp, 'log_line') as log:
            self.assertFalse(cp.balance_pass(guard, [], cp.now_utc(), pool))
        log.assert_called_once_with('guard: fixture balancing: could not set the fill order: fixture failure')


    def test_claude_claims_keep_unknown_exclusions_and_weight_rules(self):
        pool = cp.seat_pool('claude')
        pool.name = 'renamed'
        pool.claims_context = mock.Mock(return_value={})
        with mock.patch.object(cp, 'read_meta', return_value={}):
            rows = cp.load_seats(pool, listing={'files': [{'name': 'one', 'provider': 'claude'}]})
        self.assertEqual(rows[0]['model_quotas'], {})
        self.assertIsNone(rows[0]['excluded_models'])
        self.assertEqual(pool.default_weight('5', {'weight': 20}), 20.0)
        self.assertEqual(cp.seat_pool().default_weight('5', {'plan': 'pro'}), 5.0)


    def test_login_flags_come_from_each_descriptor(self):
        codex, claude = cp.seat_pool(), cp.seat_pool('claude')
        for no_open in (False, True):
            for device in (False, True):
                self.assertEqual(codex.login_flow(no_open, device),
                    ['-codex-device-login', '-no-browser'] if device else
                    ['-codex-login'] + (['-no-browser'] if no_open else []))
                self.assertEqual(claude.login_flow(no_open, device),
                    ['-claude-login'] + (['-no-browser'] if no_open else []))
        pool = copy.copy(claude)
        pool.name = pool.provider = 'fixture'
        pool.login_flow = mock.Mock(return_value=['-fixture-login'])
        process = mock.MagicMock()
        process.__enter__.return_value = process
        process.stdout = []
        process.poll.return_value = 0
        with mock.patch.object(cp.subprocess, 'Popen', return_value=process) as popen:
            self.assertEqual(cp.login_seat(no_open=True, device=True, pool=pool), (None, []))
        pool.login_flow.assert_called_once_with(True, True)
        self.assertEqual(popen.call_args.args[0][-1], '-fixture-login')
        self.assertEqual(popen.call_args.kwargs['cwd'], pool.work)


    def test_commands_dispatch_callbacks_for_an_unrecognized_name(self):
        pool = cp.seat_pool('claude')
        pool.name = 'fixture'
        seat = {'name': 'fixture.json', 'label': 'Fixture', 'provider': 'fixture'}
        pool.refresh_status = mock.Mock(return_value=True)
        pool.on_remove = mock.Mock()
        pool.enable_refusal = mock.Mock(return_value='fixture refusal')
        with mock.patch.object(cp, 'seat_pool', return_value=pool), \
                mock.patch.object(cp, 'load_seats', return_value=[seat]), \
                mock.patch.object(cp, 'read_guard', return_value={}), \
                mock.patch.object(cp, 'api') as api, mock.patch.object(cp, 'set_disabled') as disable:
            code, out, err = run(cp.cmd_enable, seat='Fixture')
            self.assertNotEqual(code, 0)
            self.assertIn('fixture refusal', err)
            disable.assert_not_called()
            self.assertTrue(cp.refresh_status_file(pool))
            pool.refresh_status.assert_called_once_with()
            code, _, _ = run(cp.cmd_remove, seat='Fixture', yes=True)
            self.assertEqual(code, 0)
            pool.on_remove.assert_called_once_with(pool, seat)
            api.assert_called_once_with('DELETE', '/v0/management/auth-files?name=fixture.json', port=pool.port)


    def test_history_journal_and_new_metadata_use_descriptor(self):
        with tempfile.TemporaryDirectory(dir=HOME) as work:
            pool = cp.seat_pool('claude')
            pool.name = pool.provider = 'fixture'
            pool.history_file = pathlib.Path(work) / 'history'
            pool.guard = pathlib.Path(work) / 'guard'
            pool.selftest_journal = pathlib.Path(work) / 'journal'
            pool.new_seat_fields = lambda: {'fixture': True}
            st = {'pool': {'spending': True}, 'seats': [{'label': 'A', 'provider': 'fixture', 'week_used': 12}]}
            cp.record_history(st, {}, cp.now_utc(), pool)
            sample = json.loads(pool.history_file.read_text())
            self.assertEqual(sample['seats'], {'A': 12})
            self.assertIs(sample['credits'], True)
            cp.write_json(pool.selftest_journal, {'pid': 123, 'toggled': {'one': False, 'two': True}})
            with mock.patch.object(cp, 'pid_alive', return_value=False), \
                    mock.patch.object(cp, 'set_disabled') as disabled, mock.patch.object(cp, 'notify') as notify:
                cp.recover_selftest({}, pool)
            self.assertEqual(disabled.call_args_list, [mock.call('one', False, pool), mock.call('two', True, pool)])
            self.assertEqual(notify.call_args.args[0], 'codexpool restored accounts')
            self.assertFalse(pool.selftest_journal.exists())
            with mock.patch.object(cp, 'update_meta') as update:
                cp.fresh_seat_meta('one', 'A', pool)
            self.assertTrue(update.call_args.kwargs['fixture'])


    def test_heal_uses_descriptor_state_vendor_log_and_port(self):
        pool = cp.seat_pool('claude')
        pool.name = pool.provider = 'fixture'
        pool.vendor, pool.noun_title, pool.cli = 'Vendor', 'Fixture account', 'codexpool fixture'
        pool.seat_state = mock.Mock(return_value=('blocked', 'unauthorized', None))
        seat = {'name': 'one', 'id': 'one', 'label': 'A', 'priority': 100, 'success': 0}
        with mock.patch.object(cp, 'sign_ins_ended_in_log', return_value=({}, None)) as logs, \
                mock.patch.object(cp, 'api', side_effect=cp.ApiError(401, 'invalid_grant')) as api, \
                mock.patch.object(cp, 'log_line'), mock.patch.object(cp, 'notify') as notify:
            cp.heal_pass({'seats': {}}, [seat], cp.now_utc(), {}, lambda: None, pool)
        logs.assert_called_once_with(mock.ANY, pool.log, pool.refresh_failed_line)
        pool.seat_state.assert_called_once()
        self.assertEqual(api.call_args.kwargs['port'], pool.port)
        self.assertEqual(notify.call_args.args[0], 'Fixture account needs a re-login')
        self.assertIn('Vendor ended this sign-in', notify.call_args.args[1])
        self.assertIn('codexpool fixture login A', notify.call_args.args[1])


    def test_config_and_doctor_use_descriptor_even_after_rename(self):
        for name, gate_title, subject, rebuild, next_rebuild in (
                ('codex', 'origin gate', 'the pool', 'codexpool upgrade <version>', 'codexpool upgrade'),
                ('claude', 'origin gate (claude profile)', 'the Claude pool',
                 'codexpool claude install', 'codexpool claude install')):
            pool = cp.pool_instance(name)
            rendered = cp.render_config('synthetic-key', pool)
            pool.name = pool.profile = 'fixture'
            self.assertEqual(cp.render_config('synthetic-key', pool), rendered)
            with mock.patch.object(cp, '_probe_status', return_value=403) as probe:
                cases = cp.gate_probe_cases(pool)
            self.assertEqual(len(cases), 2 if name == 'codex' else 4)
            self.assertTrue(all(call.args[0] == pool.port for call in probe.call_args_list))
            rep = cp.DoctorReport(echo=False)
            rep.section('test')
            with mock.patch.object(cp, 'running_version', return_value='old'), \
                    mock.patch.object(cp, 'cpa_version', return_value='installed'), \
                    mock.patch.object(cp, 'gate_probe_cases', return_value=cases):
                cp.doctor_build_checks(rep, pool, None)
            build, gate = rep.sections[0]['checks']
            self.assertEqual(build['fix'], f'{subject} must run a +gate build: {rebuild}')
            self.assertEqual(gate['text'], gate_title + ': ' + ', '.join(f'{w} {g}' for w, g, _ in cases))
            with mock.patch.object(cp, 'gate_id', return_value='bbbbbb'):
                self.assertEqual(cp.gate_source_note('1.0+gate.aaaaaa', pool),
                    f'; the gate source is now bbbbbb: the next {next_rebuild} picks it up')



class EnrichedCreditPolicy(ClaudePass):
    """Exercise credit protection with real load_seats, claims and enrichment, not prebuilt seat rows."""

    def setUp(self):
        super().setUp()
        temporary = tempfile.TemporaryDirectory(dir=HOME)
        self.addCleanup(temporary.cleanup)
        self.auth = pathlib.Path(temporary.name)
        self.enrichments = []

    def load_seats(self, pool=None, response_headers=None):
        records = []
        for name, account in self.accounts.items():
            path = self.auth / name
            cp.write_json(path, {'email': account['label'].lower() + '@test',
                                 'excluded_models': account.get('excluded_models', [])})
            records.append(dict(account, name=name, id=name, path=str(path), provider='claude',
                                auth_index=name, success=5, failed=0,
                                # Conflicting management metadata must not replace claims' manual exclusions.
                                excluded_models=['management-only*']))
        descriptor = copy.copy(pool)
        descriptor.name = 'renamed-provider'
        original = descriptor.enrich_row
        def enrich(row, claims, raw):
            self.enrichments.append((copy.deepcopy(claims), copy.deepcopy(raw)))
            original(row, claims, raw)
        descriptor.enrich_row = enrich
        return self.real_load_seats(descriptor, listing={'files': records}, response_headers=response_headers)

    def test_manual_exclusions_survive_enriched_load_cap_and_reset(self):
        self.accounts['claude-a.json']['excluded_models'] = ['manual-model*']
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*', 'manual-model*'])
        self.usage['claude-a.json'] = (200, usage_body(self.now, scoped=10))
        with self.later(minutes=4):
            self.run_pass()
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['manual-model*'])
        claims, raw = self.enrichments[0]
        self.assertEqual(claims['excluded_models'], ['manual-model*'])
        self.assertEqual(raw['excluded_models'], ['management-only*'])

    def test_disabled_seat_recovers_with_saved_exclusions(self):
        self.accounts['claude-a.json']['excluded_models'] = ['manual-model*']
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        guard = self.guard()
        guard['seats']['claude-a.json'].update(parked_reason='credits', parked_at=self.now.isoformat(),
            parked_until=(self.now + dt.timedelta(days=4)).isoformat())
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, guard)
        self.accounts['claude-a.json']['disabled'] = True
        original = self.api
        def api(method, path, *args, **kwargs):
            if '/models?' in path and 'claude-a.json' in path:
                self.assertFalse(self.accounts['claude-a.json']['disabled'])
            return original(method, path, *args, **kwargs)
        with mock.patch.object(cp, 'api', side_effect=api), self.later(minutes=11):
            self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*', 'manual-model*'])
        self.assertTrue(any(raw['disabled'] for _, raw in self.enrichments))

    def test_model_specific_overage_retains_attribution(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        quota = {'observed_at': self.now.isoformat(),
                 'signals': {'aNtHrOpIc-RaTeLiMiT-Unified-Overage-In-Use': ' TRUE '}}
        self.accounts['claude-a.json'].update(quota=quota, model_quotas={'claude-fable-test': quota})
        self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*'])
        self.assertTrue(any('claude-fable-test' in raw.get('model_quotas', {}) for _, raw in self.enrichments))
        # The same overage on an unaffected model must park, even while the scoped model is excluded.
        self.now += dt.timedelta(seconds=1)
        quota = dict(quota, observed_at=self.now.isoformat())
        self.accounts['claude-a.json'].update(quota=quota, model_quotas={'claude-opus-test': quota})
        self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
