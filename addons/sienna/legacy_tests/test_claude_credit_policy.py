"""F1 regressions: only synthetic accounts and mocked management calls; no live services."""
from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import cp, REPO, run

import datetime as dt
import json
import re
import pathlib
import tempfile
import unittest
from unittest import mock

from test_claude_guard import ClaudePass, usage_body


class CreditPolicy(ClaudePass):
    def test_scoped_cap_keeps_plan_spending_status_and_history(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        st, rows = self.run_pass()
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*'])
        self.assertTrue(rows['A']['credits']['mismatch'])
        self.assertFalse(rows['A']['credits']['spending'])
        self.assertIsNone(st['pool']['spending'])
        self.assertEqual((st['pool']['available'], st['pool']['regular_available']), (3, 2))
        history = json.loads(sienna_pool.CLAUDE_HISTORY_FILE.read_text().splitlines()[-1])
        self.assertFalse(history['credits'])

    def test_newer_credits_off_poll_clears_old_allowed_header_everywhere(self):
        self.live('claude-b.json', **{'5h-utilization': '.2', 'overage-status': 'allowed'})
        self.out('claude-b.json', credits=True, five=20)
        _, rows = self.run_pass()
        self.assertTrue(rows['B']['credits']['mismatch'])
        self.assertEqual(self.accounts['claude-b.json']['excluded_models'], ['claude-fable*'])
        self.out('claude-b.json', credits=False, five=20)
        with self.later(minutes=11):
            _, rows = self.run_pass()
            self.assertFalse(rows['B']['credits']['mismatch'])
            self.assertEqual(self.accounts['claude-b.json']['excluded_models'], [])
            # A metadata-only refresh may start from a status file predating the poll.
            status = cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})
            next(r for r in status['seats'] if r['label'] == 'B')['credits'].update(enabled=True, mismatch=True)
            cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, status)
            self.assertTrue(sienna_pool.refresh_claude_status_file())
            status = cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})
            self.assertFalse(next(r for r in status['seats'] if r['label'] == 'B')['credits']['mismatch'])
            rep = cp.DoctorReport(echo=False)
            sienna_pool.doctor_claude_accounts(rep)
            self.assertFalse(any(c['status'] == 'fail' and 'Usage credits are on at claude.ai for B' in c['text']
                                 for section in rep.sections for c in section['checks']))
        # New permission evidence must still override the earlier credits-off poll.
        with self.later(minutes=1):
            self.live('claude-b.json', **{'overage-status': 'allowed_warning'})
            _, rows = self.run_pass()
        self.assertTrue(rows['B']['credits']['mismatch'])
        self.assertEqual(self.accounts['claude-b.json']['excluded_models'], ['claude-fable*'])

    def test_would_spend_orders_poll_and_overage_evidence(self):
        for signal in ({'overage': 'allowed'}, {'overage': 'allowed_warning'}, {'overage_in_use': True}):
            for seconds, expected in ((-1, False), (0, True), (1, True)):
                with self.subTest(signal=signal, seconds=seconds):
                    usage = dict(signal, credits={'enabled': False}, polled_at=self.now.isoformat(),
                                 overage_at=(self.now + dt.timedelta(seconds=seconds)).isoformat())
                    self.assertIs(sienna_guard.claude_would_spend(usage), expected)
        self.assertTrue(sienna_guard.claude_would_spend({'overage': 'allowed'}))
        self.assertTrue(sienna_guard.claude_would_spend({'credits': {'enabled': True}}))

    def test_old_alarm_does_not_pin_later_plain_limit_park(self):
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, {'seats': {'claude-a.json': {
            'alarms': 1, 'alarm_at': (self.now - dt.timedelta(minutes=5)).isoformat()}}})
        self.out('claude-a.json', credits=True)
        self.run_pass()
        with self.later(minutes=1):
            self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'credits')
        self.assertNotIn('parked_not_before', g)
        self.out('claude-a.json', credits=True, five=5)
        with self.later(minutes=11):
            self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])

    def test_legacy_exclusion_alarm_keeps_its_backoff(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        with mock.patch.object(cp, 'api', side_effect=self.stuck_registration):
            self.run_pass()
        guard = self.guard()
        g = guard['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'credits')
        until = g.pop('parked_not_before')
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, guard)
        with self.later(minutes=11):
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_not_before'], until)

    def live(self, name='claude-a.json', **signals):
        self.accounts[name]['quota'] = {'observed_at': self.now.isoformat(), 'signals': {
            'anthropic-ratelimit-unified-' + key: value for key, value in signals.items()}}

    def passive(self):
        guard = self.guard() if sienna_pool.CLAUDE_GUARD_FILE.exists() else {}
        guard['passive'] = {'since': self.now.isoformat(), 'why': 'test',
                            'retry_at': (self.now + dt.timedelta(hours=6)).isoformat()}
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, guard)

    def test_restart_keeps_limit_park_in_passive_mode_until_reset(self):
        self.passive()
        self.live(**{'5h-utilization': '1', '5h-reset': str(int((self.now + dt.timedelta(hours=2)).timestamp())),
                     'overage-status': 'allowed'})
        self.run_pass()
        self.accounts['claude-a.json']['quota'] = {}
        with self.later(minutes=1):
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        with self.later(hours=2):
            self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])

    def test_restart_stale_poll_cannot_lift_live_limit_park(self):
        self.out('claude-a.json', credits=True, five=97)
        self.run_pass()
        with self.later(minutes=1):
            self.live(**{'5h-utilization': '1.02', 'overage-status': 'allowed',
                         '5h-reset': str(int((self.now + dt.timedelta(hours=2)).timestamp()))})
            self.run_pass()
        parked = self.guard()['seats']['claude-a.json']['parked_at']
        self.accounts['claude-a.json']['quota'] = {}
        with self.later(minutes=1):
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_at'], parked)
        self.usage['claude-a.json'] = (429, {})
        with self.later(minutes=11):
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.out('claude-a.json', credits=True, five=5)
        with self.later(minutes=21):
            self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])

    def test_weekly_limit_park_requires_successful_newer_poll_too(self):
        self.out('claude-a.json', credits=True, five=20, week=98)
        self.run_pass()
        with self.later(minutes=1):
            self.live(**{'7d-utilization': '1', 'overage-status': 'allowed',
                         '7d-reset': str(int((self.now + dt.timedelta(days=3)).timestamp()))})
            self.run_pass()
        self.accounts['claude-a.json']['quota'] = {}
        self.passive()
        with self.later(hours=2):
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertGreater(cp.parse_time(self.guard()['seats']['claude-a.json']['parked_until']), self.now)

    def test_passive_allowed_header_drives_mismatch_exclusions_and_refresh(self):
        self.passive()
        self.live('claude-b.json', **{'5h-utilization': '.2', 'overage-status': 'allowed'})
        _, rows = self.run_pass()
        self.assertTrue(rows['B']['credits']['mismatch'])
        self.assertEqual(self.accounts['claude-b.json']['excluded_models'], ['claude-fable*'])
        sienna_pool.refresh_claude_status_file()
        status = cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})
        self.assertTrue(next(r for r in status['seats'] if r['label'] == 'B')['credits']['mismatch'])
        rep = cp.DoctorReport(echo=False)
        sienna_pool.doctor_claude_accounts(rep)
        self.assertTrue(any(c['status'] == 'fail' and 'Usage credits are on at claude.ai for B' in c['text']
                            for section in rep.sections for c in section['checks']))

    def stuck_registration(self, method, path, *args, **kwargs):
        if '/models?' in path and 'claude-a.json' in path and not self.accounts['claude-a.json']['disabled']:
            return {'models': [{'id': 'claude-fable-test'}, {'id': 'claude-opus-test'}]}
        return self.api(method, path, *args, **kwargs)

    def test_exclusion_park_holds_backoff_without_repeated_notifications(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        with mock.patch.object(cp, 'api', side_effect=self.stuck_registration):
            for _ in range(6):
                with self.later(minutes=1):
                    self.run_pass()
            self.assertEqual([d for n, d, _ in self.order if n == 'claude-a.json'], [True])
            self.assertEqual(self.titles().count('Claude model exclusion pending'), 1)
            with self.later(minutes=61):
                self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(g['alarms'], 2)
        self.assertEqual(cp.parse_time(g['parked_until']) - self.now, dt.timedelta(hours=2))
        self.assertEqual(self.titles().count('Claude model exclusion pending'), 1)

    def enable(self):
        with mock.patch.object(cp, 'refresh_status_file'), mock.patch.object(cp, 'now_utc', return_value=self.now):
            return run(cp.cmd_enable, pool='claude', seat='A')

    def test_off_enable_override_survives_new_overage_until_expiry(self):
        self.out('claude-a.json', credits=True, five=20)
        self.run_pass()
        self.signal()
        self.run_pass()
        code, out, err = self.enable()
        self.assertEqual(code, 0, err)
        self.assertIn('overridden', out)
        with self.later(minutes=2):
            self.signal()
            self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        with self.later(minutes=61):
            self.signal()
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_off_enable_override_survives_failed_exclusion_until_expiry(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        with mock.patch.object(cp, 'api', side_effect=self.stuck_registration):
            self.run_pass()
            self.assertEqual(self.enable()[0], 0)
            with self.later(minutes=2):
                self.run_pass()
            self.assertFalse(self.accounts['claude-a.json']['disabled'])
            with self.later(minutes=61):
                self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_last_resort_enable_does_not_promise_override(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, five=20)
        self.run_pass()
        code, out, err = self.enable()
        self.assertNotEqual(code, 0)
        self.assertIn('last-resort', err)
        self.assertNotIn('overridden', out)
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertNotIn('override_until', self.guard()['seats']['claude-a.json'])

    def test_legitimate_last_resort_spending_reparks_without_alarm_after_other_reset(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, five=20, used_cents=1000)
        self.out('claude-b.json')
        self.out('claude-r.json')
        self.run_pass()
        self.out('claude-a.json', credits=True, five=25, used_cents=3000)
        self.out('claude-b.json', five=5)
        with self.later(minutes=11):
            self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'last-resort')
        self.assertNotIn('alarms', g)
        self.assertNotIn('Claude credit policy violation', self.titles())
        self.assertIn('Claude account parked again', self.titles())

    def test_shared_limit_cannot_shorten_escalated_alarm(self):
        self.out('claude-a.json', credits=True, five=20)
        self.run_pass()
        guard = self.guard()
        guard['seats']['claude-a.json'].update(alarms=4, alarm_at=self.now.isoformat())
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, guard)
        self.signal()
        self.run_pass()
        until = self.guard()['seats']['claude-a.json']['parked_until']
        self.out('claude-a.json', credits=True, five=100)
        with self.later(minutes=11):
            self.run_pass()
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_until'], until)
        with self.later(hours=2, minutes=1):
            self.out('claude-a.json', credits=True, five=5)
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_shared_weekly_limit_can_extend_alarm_and_legacy_alarm_is_preserved(self):
        self.out('claude-a.json', credits=True, five=20)
        self.run_pass()
        self.signal()
        self.run_pass()
        guard = self.guard()
        # An existing install has no minimum-deadline field yet.
        guard['seats']['claude-a.json'].pop('parked_not_before', None)
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, guard)
        self.out('claude-a.json', credits=True, five=20, week=100)
        with self.later(minutes=11):
            self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'credits')
        self.assertEqual(cp.parse_time(g['parked_until']).replace(microsecond=0),
                         cp.parse_time(self.usage['claude-a.json'][1]['seven_day']['resets_at']).replace(microsecond=0))
        self.assertIn('parked_not_before', g)

    def test_last_resort_own_plan_is_not_spending_but_overage_is(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, five=20)
        self.out('claude-b.json')
        self.out('claude-r.json')
        st, rows = self.run_pass()
        self.assertFalse(rows['A']['credits']['spending'])
        self.assertIsNone(st['pool']['spending'])
        self.assertNotIn('Claude is spending usage credits', self.titles())
        self.signal()
        st, rows = self.run_pass()
        self.assertTrue(rows['A']['credits']['spending'])
        self.assertEqual(st['pool']['spending'], 'A')

    def test_exclusion_failure_at_shared_limits_parks_until_latest_reset(self):
        self.accounts['claude-a.json']['excluded_models'] = None
        self.out('claude-a.json', credits=True, five=100, week=100, scoped=95)
        self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual((g['parked_reason'], g['alarms']), ('credits', 1))
        self.assertEqual(cp.parse_time(g['parked_until']).replace(microsecond=0),
                         (self.now + dt.timedelta(days=3)).replace(microsecond=0))

    def test_overage_at_shared_limit_parks_until_reset_without_below_limit_alarm(self):
        self.out('claude-a.json', credits=False, five=100)
        self.run_pass()  # the shared limit is known before the overage observation
        self.signal()
        self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        reset = cp.parse_time(self.usage['claude-a.json'][1]['five_hour']['resets_at'])
        self.assertEqual((g['parked_reason'], cp.parse_time(g['parked_until']).replace(microsecond=0)),
                         ('credits', reset.replace(microsecond=0)))
        self.assertNotIn('alarms', g)
        self.assertFalse(any('below its limit' in title for title in self.titles()))

    def test_poll_replaces_early_overage_alarm_with_shared_limit_reset(self):
        self.out('claude-a.json', credits=True, five=100)
        self.signal()  # no usage reading yet: the early pass must park before polling
        self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'credits')
        self.assertEqual(cp.parse_time(g['parked_until']).replace(microsecond=0),
                         (self.now + dt.timedelta(hours=2)).replace(microsecond=0))

    def test_existing_last_resort_park_tracks_new_shared_limit_reset(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, five=20)
        self.run_pass()  # reserve still has quota: the account waits below its own limit
        self.out('claude-a.json', credits=True, five=100)
        with self.later(minutes=11):
            self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'last-resort')
        reset = cp.parse_time(self.usage['claude-a.json'][1]['five_hour']['resets_at'])
        self.assertEqual(cp.parse_time(g['parked_until']).replace(microsecond=0), reset.replace(microsecond=0))

    def test_pro_and_team_exclude_fable_from_first_token(self):
        now = self.now
        for plan in ('pro', 'team', None, 'enterprise'):
            self.assertEqual(sienna_guard.claude_model_exclusions({'plan': plan, 'usage': {'credits': {'enabled': True}}}, [], now), ['claude-fable*'])
        for plan in ('max_5x', 'max_20x', 'team_premium'):
            self.assertEqual(sienna_guard.claude_model_exclusions({'plan': plan, 'usage': {'credits': {'enabled': True}}}, [], now), [])

    def test_manual_exclusions_survive_cap_and_reset(self):
        self.accounts['claude-a.json']['excluded_models'] = ['manual-model*']
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*', 'manual-model*'])
        self.usage['claude-a.json'] = (200, usage_body(self.now, scoped=10))
        with self.later(minutes=4):
            self.run_pass()
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['manual-model*'])

    def test_existing_manual_fable_pattern_is_never_owned_or_removed(self):
        self.accounts['claude-a.json']['excluded_models'] = ['CLAUDE-FABLE*']
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        self.assertEqual(self.guard()['seats']['claude-a.json']['excluded_models_owned'], [])
        self.usage['claude-a.json'] = (200, usage_body(self.now, scoped=10))
        with self.later(minutes=4):
            self.run_pass()
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['CLAUDE-FABLE*'])

    def test_missing_metadata_does_not_overwrite_exclusions(self):
        self.accounts['claude-a.json']['excluded_models'] = None
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        self.assertIn('metadata unavailable', self.guard()['seats']['claude-a.json']['exclusion_error'])
        self.assertFalse([x for x in self.model_updates if x['name'] == 'claude-a.json'])

    def test_patch_success_is_not_registration_success(self):
        original = self.api
        def lagging(method, path, *args, **kwargs):
            if '/models?' in path:
                return {'models': [{'id': 'claude-fable-test'}, {'id': 'claude-opus-test'}]}
            return original(method, path, *args, **kwargs)
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        with mock.patch.object(cp, 'api', side_effect=lagging):
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'credits')
        self.usage['claude-a.json'] = (200, usage_body(self.now, scoped=100, credits=False))
        self.now += dt.timedelta(minutes=61)  # exclusion alarm must finish its backoff
        self.run_pass()
        self.assertNotIn('exclusion_error', self.guard()['seats']['claude-a.json'])
        self.assertFalse(self.accounts['claude-a.json']['disabled'])

    def test_failed_patch_retains_ownership_journal_and_retries(self):
        original = self.api
        def failing(method, path, body=None, **kwargs):
            if body and 'excluded_models' in body:
                raise cp.PoolDown('unavailable')
            return original(method, path, body, **kwargs)
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        with mock.patch.object(cp, 'api', side_effect=failing):
            self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['excluded_models_owned'], ['claude-fable*'])
        self.assertIn('exclusion_error', g)
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(g['parked_reason'], 'credits')
        self.run_pass()
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*'])

    def test_last_resort_parks_even_with_own_opus_quota_while_reserve_has_quota(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.out('claude-b.json')
        _, rows = self.run_pass()
        self.assertEqual(rows['A']['state'], 'parked')
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*'])
        self.assertIsNone(self.guard().get('spending'))

    def test_last_resort_clears_guard_exclusion_only_when_all_shared_plans_spent(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        for name in self.accounts:
            self.out(name, credits=name == 'claude-a.json', scoped=100)
        with self.later(minutes=11):
            self.run_pass()
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], [])
        self.assertEqual(self.guard()['spending'], 'claude-a.json')

    def test_disabled_reserve_is_not_proof_its_quota_was_spent(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True)
        self.out('claude-b.json')
        self.accounts['claude-r.json']['disabled'] = True
        self.run_pass()
        self.assertIsNone(self.guard().get('spending'))
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_unknown_quota_never_authorizes_last_resort(self):
        row = {'name': 'last', 'credits': {'policy': 'last-resort', 'used': 0, 'cap': 200},
               'usage': {'polled_at': self.now.isoformat()}}
        self.assertFalse(sienna_guard.claude_credit_allowed(row, [row, {'name': 'reserve', 'usage': None}], self.now))
        spent = {'name': 'reserve', 'usage': sienna_guard.claude_usage_from_body(usage_body(self.now, five=100), self.now)}
        self.assertTrue(sienna_guard.claude_credit_allowed(row, [row, spent], self.now))

    def signal(self, at=None, model=None):
        self.accounts['claude-a.json']['quota'] = {'observed_at': (at or self.now).isoformat(),
            'signals': {'aNtHrOpIc-RaTeLiMiT-Unified-Overage-In-Use': ' TRUE '}}
        if model:
            self.accounts['claude-a.json']['model_quotas'] = {model: self.accounts['claude-a.json']['quota']}

    def test_overage_header_parks_before_slow_poll_without_credit_increase(self):
        self.run_pass()
        self.now += dt.timedelta(seconds=60)
        self.signal()
        original = sienna_guard.claude_poll_usage
        def poll(*args, **kwargs):
            self.assertTrue(self.accounts['claude-a.json']['disabled'])
            return original(*args, **kwargs)
        before = len(self.calls)
        with mock.patch.object(sienna_guard, 'claude_poll_usage', side_effect=poll):
            self.run_pass()
        self.assertEqual(len(self.calls), before)  # no usage poll was due
        self.assertIn('overage_seen_at', self.guard()['seats']['claude-a.json'])
        count = len(self.order)
        self.run_pass()
        self.assertEqual(len(self.order), count)  # retained observation is not another event

    def test_future_signal_does_not_park(self):
        self.signal(self.now + dt.timedelta(days=1))
        self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])

    def test_overage_on_last_resort_below_limit_parks(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.run_pass()
        self.signal()
        self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_scoped_overage_keeps_unaffected_models(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        self.signal(model='claude-fable-test')
        self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*'])

    def test_credit_refusal_config_covers_entitlements_only(self):
        text = (REPO / 'addons/sienna/examples/config-claude.yaml').read_text().split('oauth-request-scoped-errors:\n')[1]
        self.assertIn('status: 429', text)
        self.assertIn('action: stop', text)
        patterns = re.findall(r"        - '([^']+)'", text)
        self.assertNotIn('match: ["credits_required"]', text)
        for body in ('Usage credits are required for fast mode',
                     'Usage credits are required for long context', 'FAST REQUEST REJECTED'):
            self.assertTrue(any(re.search(p, body) for p in patterns))
        self.assertFalse(any(re.search(p, '{"error":{"details":{"error_code":"credits_required"}}}')
                             for p in patterns))
        self.assertFalse(any(re.search(p, 'Weekly rate limit reached') for p in patterns))

    def test_stale_signal_does_not_replay_an_old_charge(self):
        self.signal(self.now - dt.timedelta(hours=1))
        self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])

    def test_overage_is_permitted_after_every_shared_plan_is_spent(self):
        self.credits('claude-a.json', 'last-resort', 200)
        for name in self.accounts:
            self.out(name, credits=name == 'claude-a.json')
        self.run_pass()
        self.signal()
        self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.guard()['spending'], 'claude-a.json')

    def test_old_scoped_park_migrates_when_upstream_credits_are_off(self):
        self.out('claude-a.json', credits=False, five=20, scoped=100)
        self.run_pass()
        guard = self.guard()
        guard['seats']['claude-a.json'].update(parked_reason='credits',
            parked_until=(self.now + dt.timedelta(days=4)).isoformat(), parked_at=self.now.isoformat())
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, guard)
        self.accounts['claude-a.json']['disabled'] = True
        self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        with self.later(minutes=11):  # only a successful poll newer than the park can migrate it early
            self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertNotIn('parked_until', self.guard()['seats']['claude-a.json'])

    def test_old_scoped_park_recovers_from_saved_exclusion_when_credits_are_on(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        guard = self.guard()
        guard['seats']['claude-a.json'].update(parked_reason='credits',
            parked_until=(self.now + dt.timedelta(days=4)).isoformat(), parked_at=self.now.isoformat())
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, guard)
        self.accounts['claude-a.json']['disabled'] = True
        self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        with self.later(minutes=11):
            self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertNotIn('exclusion_pending', g)
        self.assertNotIn('exclusion_error', g)

    def test_registration_lag_parks_for_exclusions_not_misclassification(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        self.signal(model='claude-fable-test')
        original = self.api
        def lag(method, path, *args, **kwargs):
            if '/models?' in path:
                return {'models': [{'id': 'claude-fable-test'}, {'id': 'claude-opus-test'}]}
            return original(method, path, *args, **kwargs)
        with mock.patch.object(cp, 'api', side_effect=lag):
            self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'credits')
        self.assertFalse(any('below its limit' in t for t in self.titles()))

    def test_scoped_margin_only_when_credits_can_pay(self):
        for enabled, used, wanted in ((True, 94, []), (True, 95, ['claude-fable*']),
                                      (False, 95, []), (False, 100, ['claude-fable*'])):
            usage = sienna_guard.claude_usage_from_body(usage_body(self.now, scoped=used, credits=enabled), self.now)
            self.assertEqual(sienna_guard.claude_model_exclusions({'plan': 'max_5x', 'usage': usage}, [], self.now), wanted)

    def test_overage_alarms_escalate(self):
        self.run_pass()
        self.signal()
        self.run_pass()
        first = self.guard()['seats']['claude-a.json']
        self.assertEqual(first['alarms'], 1)
        with self.later(minutes=61):
            self.run_pass()
        self.signal()
        self.run_pass()
        second = self.guard()['seats']['claude-a.json']
        self.assertEqual(second['alarms'], 2)
        self.assertEqual(cp.parse_time(second['parked_until']) - self.now, dt.timedelta(hours=2))

    def test_exclusion_failure_does_not_reenable_at_hourly_expiry(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        original = self.api
        def failing(method, path, body=None, **kwargs):
            if body and 'excluded_models' in body:
                raise cp.ApiError(400, 'unsupported')
            return original(method, path, body, **kwargs)
        with mock.patch.object(cp, 'api', side_effect=failing):
            self.run_pass()
            with self.later(minutes=61):
                self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertFalse(any(name == 'claude-a.json' and not disabled for name, disabled, _ in self.order))

    def test_shared_park_does_not_report_missing_registration(self):
        self.out('claude-b.json', credits=True)
        self.run_pass()
        self.run_pass()
        g = self.guard()['seats']['claude-b.json']
        self.assertNotIn('exclusion_error', g)
        self.assertNotIn('exclusion_recovery_pending', g)

    def test_credit_off_recovery_requires_newer_successful_reading(self):
        usage = {'credits': {'enabled': False}, 'polled_at': self.now.isoformat(),
                 'overage_in_use': True, 'overage_at': self.now.isoformat()}
        self.assertFalse(sienna_guard.claude_credits_disabled(usage, self.now))
        usage['overage_at'] = (self.now - dt.timedelta(seconds=1)).isoformat()
        self.assertTrue(sienna_guard.claude_credits_disabled(usage, self.now))
        self.assertFalse(sienna_guard.claude_credits_disabled(usage, self.now + dt.timedelta(hours=1)))

    def test_doctor_errors_on_credit_mismatch(self):
        self.out('claude-a.json', credits=True)
        self.run_pass()
        rep = cp.DoctorReport(echo=False)
        sienna_pool.doctor_claude_accounts(rep)
        checks = [c for section in rep.sections for c in section['checks']]
        self.assertTrue(any(c['status'] == 'fail' and 'Usage credits are on at claude.ai for A' in c['text']
                            for c in checks))

    def test_config_doctor_catches_old_and_unsafe_rules(self):
        config = (REPO / 'addons/sienna/examples/config-claude.yaml').read_text()
        self.assertEqual(sienna_pool.claude_config_problems(config), [])
        self.assertTrue(sienna_pool.claude_config_problems(config.replace('disable-claude-cloak-mode: true',
                                                                'disable-claude-cloak-mode: false')))
        self.assertTrue(sienna_pool.claude_config_problems(config.split('oauth-request-scoped-errors:')[0]))
        self.assertTrue(sienna_pool.claude_config_problems(config.replace('      match-regexr:',
                           '      match: ["credits_required"]\n      match-regexr:')))
        self.assertTrue(sienna_pool.claude_config_problems(config.replace('      action: stop', '      action: retry')))

    def test_fable_cap_does_not_hide_fast_opus_overage(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        self.signal(model='claude-opus-test')
        self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_fable_cap_does_not_attribute_unknown_overage_to_fable(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        self.signal()
        self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_credit_off_exclusion_failures_are_optional_and_logged_once(self):
        self.out('claude-a.json', credits=False, five=20, scoped=100)
        original = self.api
        def failing(method, path, body=None, **kwargs):
            if body and 'excluded_models' in body:
                raise cp.ApiError(400, 'unsupported')
            return original(method, path, body, **kwargs)
        with mock.patch.object(cp, 'api', side_effect=failing), mock.patch.object(cp, 'log_line') as log:
            self.run_pass()
            self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertNotIn('alarms', self.guard()['seats']['claude-a.json'])
        self.assertEqual(sum('model exclusion not verified' in call.args[0] for call in log.call_args_list), 1)

    def test_unknown_plan_with_upstream_credits_off_needs_no_exclusion(self):
        for plan in (None, 'enterprise', 'pro', 'team'):
            row = {'plan': plan, 'usage': {'credits': {'enabled': False}}}
            self.assertEqual(sienna_guard.claude_model_exclusions(row, [], self.now), [])

    def test_registration_catches_up_before_final_verification_without_park(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        original, gets = self.api, []
        def lag_once(method, path, *args, **kwargs):
            if '/models?' in path and 'claude-a.json' in path:
                gets.append(path)
                if len(gets) == 1:
                    return {'models': [{'id': 'claude-fable-test'}, {'id': 'claude-opus-test'}]}
            return original(method, path, *args, **kwargs)
        with mock.patch.object(cp, 'api', side_effect=lag_once):
            self.run_pass()
        self.assertGreaterEqual(len(gets), 2)
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertNotIn('alarms', self.guard()['seats']['claude-a.json'])

    def test_failed_exclusion_parks_as_credits_and_counts_alarm(self):
        self.accounts['claude-a.json']['excluded_models'] = None
        self.out('claude-a.json', credits=True, five=20, scoped=95)
        self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual((g['parked_reason'], g['alarms']), ('credits', 1))
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        record = next(o[2] for o in self.order if o[0] == 'claude-a.json' and o[1])
        self.assertTrue(record.get('parked_until'))

    def test_disabled_scoped_migration_does_not_query_registration_until_enabled(self):
        self.out('claude-a.json', credits=True, five=20, scoped=100)
        self.run_pass()
        g = self.guard()
        g['seats']['claude-a.json'].update(parked_reason='credits', parked_at=self.now.isoformat(),
            parked_until=(self.now + dt.timedelta(days=4)).isoformat())
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, g)
        self.accounts['claude-a.json']['disabled'] = True
        original = self.api
        def no_disabled_models(method, path, *args, **kwargs):
            if '/models?' in path and 'claude-a.json' in path:
                self.assertFalse(self.accounts['claude-a.json']['disabled'])
            return original(method, path, *args, **kwargs)
        with mock.patch.object(cp, 'api', side_effect=no_disabled_models), self.later(minutes=11):
            self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])

    def test_last_resort_own_plan_remaining_serves_after_other_plans_spent(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, five=20)
        self.out('claude-b.json')
        self.out('claude-r.json')
        self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.guard()['spending'], 'claude-a.json')
        self.signal(model='claude-opus-test')
        self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.out('claude-r.json', five=10)
        with self.later(minutes=11):
            self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_status_mismatch_contract_and_doctor_error_clear_when_fixed(self):
        self.out('claude-a.json', credits=True, five=20)
        self.credits('claude-b.json', 'last-resort', 200)
        self.out('claude-b.json', credits=True, five=20)
        _, rows = self.run_pass()
        self.assertIs(rows['A']['credits']['mismatch'], True)
        self.assertIs(rows['B']['credits']['mismatch'], False)
        self.assertIs(rows['R']['credits']['mismatch'], False)
        self.out('claude-a.json', credits=False, five=20)
        with self.later(minutes=11):
            _, rows = self.run_pass()
        self.assertIs(rows['A']['credits']['mismatch'], False)
        rep = cp.DoctorReport(echo=False)
        sienna_pool.doctor_claude_accounts(rep)
        self.assertFalse(any('Usage credits are on at claude.ai for A' in c['text']
                             for section in rep.sections for c in section['checks']))

    def test_refusal_rules_match_whole_message_not_incidental_text(self):
        text = (REPO / 'addons/sienna/examples/config-claude.yaml').read_text()
        patterns = re.findall(r"        - '([^']+)'", text)
        for message in ('Usage credits are required for fast mode.', 'Usage credits are required for long context.',
                        'Fast request rejected'):
            self.assertTrue(any(re.search(p, '{"error":{"message":"' + message + '"}}') for p in patterns))
            for body in ('{"error":{"message":"Weekly limit; ' + message + '"}}',
                         '{"error":{"details":{"note":"' + message + '"}}}',
                         'Weekly limit; ' + message):
                self.assertFalse(any(re.search(p, body) for p in patterns), body)

    def test_install_rewrites_old_safety_config_and_is_idempotent(self):
        config = cp.render_config('synthetic-key', cp.pool_instance('claude'))
        with mock.patch.object(sienna_pool, 'CLAUDE_CONFIG', cp.STATE / 'synthetic-claude.yaml'):
            sienna_pool.CLAUDE_CONFIG.write_text(config.replace('disable-claude-cloak-mode: true',
                                                     'disable-claude-cloak-mode: false'))
            self.assertFalse(sienna_pool.claude_config_current())
            with mock.patch.object(cp, 'mgmt_key', return_value='synthetic-key'):
                sienna_pool.claude_write_config()
            self.assertEqual(sienna_pool.CLAUDE_CONFIG.read_text(), config)
            self.assertTrue(sienna_pool.claude_config_current())
            sienna_pool.CLAUDE_CONFIG.write_text(config.split('oauth-request-scoped-errors:')[0])
            self.assertFalse(sienna_pool.claude_config_current())

    def test_policy_refresh_recomputes_mismatch_without_waiting_for_guard(self):
        self.out('claude-a.json', credits=True, five=20)
        self.run_pass()
        self.credits('claude-a.json', 'last-resort', 200)
        sienna_pool.refresh_claude_status_file(self.load_seats(cp.seat_pool('claude')))
        rows = cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})['seats']
        self.assertFalse(next(r for r in rows if r['name'] == 'claude-a.json')['credits']['mismatch'])
        self.credits('claude-a.json', 'off')
        sienna_pool.refresh_claude_status_file(self.load_seats(cp.seat_pool('claude')))
        rows = cp.read_json(sienna_pool.CLAUDE_STATUS_FILE, {})['seats']
        self.assertTrue(next(r for r in rows if r['name'] == 'claude-a.json')['credits']['mismatch'])

    def test_last_resort_park_expiry_never_briefly_enables_while_reserve_ready(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, five=20)
        self.run_pass()
        before = len(self.order)
        with self.later(hours=2):
            self.run_pass()
        self.assertFalse(any(name == 'claude-a.json' and not disabled for name, disabled, _ in self.order[before:]))
        self.assertTrue(self.accounts['claude-a.json']['disabled'])

    def test_switching_last_resort_to_off_recovers_guard_park(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.credits('claude-a.json', 'off')
        self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])

    def test_doctor_recommends_upstream_member_limit(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.run_pass()
        rep = cp.DoctorReport(echo=False)
        sienna_pool.doctor_claude_accounts(rep)
        self.assertTrue(any('matching $200 member spend limit at claude.ai' in c['text']
                            for section in rep.sections for c in section['checks']))

    def test_gate_detector_dependency_fails_with_actionable_message(self):
        with tempfile.TemporaryDirectory() as directory:
            tree = pathlib.Path(directory)
            with self.assertRaisesRegex(SystemExit, 'subpool:.*lacks helps.DetectClaudeCodeRequest'):
                addon.gate_build.check_gate_detector(tree, 'test')
            helpers = tree / 'internal/runtime/executor/helps'
            helpers.mkdir(parents=True)
            (helpers / 'detector_test.go').write_text('func DetectClaudeCodeRequest() {}')
            with self.assertRaisesRegex(SystemExit, 'No running build was changed'):
                addon.gate_build.check_gate_detector(tree, 'test')
            (helpers / 'detector.go').write_text('func DetectClaudeCodeRequest(headers Header) Detection {}')
            addon.gate_build.check_gate_detector(tree, 'test')

    def test_doctor_old_config_error_names_install_fix(self):
        pool = cp.pool_instance('claude')
        with mock.patch.object(cp, 'launchd_loaded', return_value=(True, 123)), \
                mock.patch.object(cp, 'port_open', return_value=True), \
                mock.patch.object(cp, 'mgmt_key', return_value='synthetic-key'), \
                mock.patch.object(cp, 'running_version', return_value=None), \
                mock.patch.object(cp, 'doctor_build_checks'), mock.patch.object(sienna_pool, 'doctor_claude_accounts'), \
                mock.patch.object(sienna_pool, 'doctor_claude_launch'), mock.patch.object(sienna_pool, 'doctor_claude_log'):
            pool.config.write_text('host: "127.0.0.1"\napi-keys: []\ndisable-claude-cloak-mode: false\n')
            self.addCleanup(pool.config.unlink)
            rep = cp.DoctorReport(echo=False)
            sienna_pool.doctor_claude_checks(rep)
        problems = [c for section in rep.sections for c in section['checks']
                    if 'cloaking' in c['text'] or 'refusal rules' in c['text']]
        self.assertEqual(len(problems), 2)
        self.assertTrue(all(c['status'] == 'fail' and 'subpool claude install' in c['fix'] for c in problems))


class ParseTime(unittest.TestCase):
    def test_fractions_of_any_length(self):
        """Go writes up to nine fractional digits and Python 3.9 reads only three or six: every length parses."""
        want = dt.datetime(2026, 9, 28, 1, 30, 0, 123456, tzinfo=dt.timezone.utc)
        for text in ('2026-09-28T01:30:00.123456789Z', '2026-09-28T01:30:00.123456+00:00',
                     '2026-09-28T01:30:00.1234567+00:00'):
            self.assertEqual(cp.parse_time(text), want, text)
        self.assertEqual(cp.parse_time('2026-09-28T01:30:00.12Z'), want.replace(microsecond=120000))
        self.assertEqual(cp.parse_time('2026-09-28T01:30:00Z'), want.replace(microsecond=0))
