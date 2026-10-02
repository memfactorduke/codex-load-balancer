"""The guard's Claude pass: usage from Anthropic's /api/oauth/usage (through the Claude pool's api-call) and from the
passive anthropic-ratelimit-unified-* signals, plan tiers, each credit policy (off; last-resort with its cap), the
misclassification alarm, parks that run out, healing on the Claude pool's own log, notifications, and
claude-status.json / claude-history.jsonl in the Interface contract's shape. The pool's account list, its management
API and Anthropic's answers are made up here (mocks): nothing leaves the process, nothing outside the fake HOME of
tests/_helpers.py is touched, and no Claude Code, token or Keychain item is involved."""
from _helpers import addon
sienna_desktop, desktop_build = addon.desktop, addon.desktop_build

import contextlib
import fnmatch
import urllib.parse
import datetime as dt
import json
import unittest
from unittest import mock

from _helpers import addon, sienna_pool, sienna_guard, sienna_selftest
from _helpers import HOME, cp, preserved

assert str(sienna_pool.CLAUDE_STATUS_FILE).startswith(str(HOME)), 'live state'

MAX_5X = {'account': {'uuid': 'acct', 'email': 'x@test'},
          'organization': {'uuid': 'org', 'organization_type': 'claude_max',
                           'rate_limit_tier': 'default_claude_max_5x'}}
MAX_20X = {'organization': {'organization_type': 'claude_max', 'rate_limit_tier': 'default_claude_max_20x'}}
PRO = {'organization': {'organization_type': 'claude_pro', 'rate_limit_tier': 'default_claude_pro'}}
STATUS_SEAT_KEYS = {'label', 'name', 'provider', 'email', 'plan', 'priority', 'state', 'detail', 'until', 'weight',
                    'reserve', 'week', 'five_hour', 'scoped', 'credits', 'week_used', 'poll_error'}
POOL_KEYS = {'running', 'installed', 'version', 'port', 'used_pct', 'used_pct_all', 'used_pct_regular', 'headline',
             'display', 'balancing', 'route', 'reserve_in_use', 'regular_available', 'counted', 'seats', 'available'}


def at(now, **delta):
    return (now + dt.timedelta(**delta)).isoformat()


def usage_body(now, five=10.0, week=20.0, scoped=None, credits=False, used_cents=0, limit_cents=50000):
    """What /api/oauth/usage answers: utilization 0-100, resets_at ISO (with a fraction), credits in cents."""
    body = {'five_hour': {'utilization': five,
                          'resets_at': (now + dt.timedelta(hours=2)).replace(microsecond=123456).isoformat()},
            'seven_day': {'utilization': week, 'resets_at': at(now, days=3)},
            'seven_day_opus': None, 'seven_day_sonnet': None,
            'limits': [{'kind': 'session', 'percent': five, 'resets_at': at(now, hours=2), 'scope': None},
                       {'kind': 'weekly_all', 'percent': week, 'resets_at': at(now, days=3), 'scope': None}],
            'extra_usage': {'is_enabled': credits, 'monthly_limit': limit_cents, 'used_credits': used_cents,
                            'utilization': None, 'currency': 'USD', 'decimal_places': 2}}
    if scoped is not None:
        body['limits'].append({'kind': 'weekly_scoped', 'percent': scoped, 'resets_at': at(now, days=4),
                               'scope': {'model': {'id': 'claude-fable-5', 'display_name': 'Fable'}},
                               'is_active': scoped >= 100})
    return body


class ClaudePass(unittest.TestCase):
    """A made-up Claude pool with accounts A (regular), B (regular) and R (the reserve), in that fill order."""

    def setUp(self):
        self.real_load_seats = cp.load_seats
        self.now = cp.now_utc()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(preserved(sienna_pool.CLAUDE_GUARD_FILE, sienna_pool.CLAUDE_STATUS_FILE, sienna_pool.CLAUDE_HISTORY_FILE,
                                      sienna_pool.CLAUDE_SEATS_META, sienna_pool.CLAUDE_MAIN_LOG, cp.GUARD_LOG, cp.STATUS_FILE,
                                      cp.GUARD_FILE, cp.HISTORY_FILE))
        for f in (sienna_pool.CLAUDE_GUARD_FILE, sienna_pool.CLAUDE_STATUS_FILE, sienna_pool.CLAUDE_HISTORY_FILE, sienna_pool.CLAUDE_SEATS_META,
                  sienna_pool.CLAUDE_MAIN_LOG):
            if f.exists():
                f.unlink()
        self.accounts = {}   # name -> the pool's view of it
        self.usage = {}      # name -> (code, body) the usage endpoint answers
        self.profiles = {}   # name -> the profile endpoint's answer
        self.order = []      # every PATCH to the pool, with what claude-guard.json said at that moment
        self.notes, self.calls, self.model_updates = [], [], []
        for name, label, prio, reserve, profile in (('claude-a.json', 'A', 300, False, MAX_5X),
                                                    ('claude-b.json', 'B', 200, False, PRO),
                                                    ('claude-r.json', 'R', 100, True, MAX_20X)):
            self.accounts[name] = {'label': label, 'priority': prio, 'disabled': False, 'unavailable': False,
                                   'status': 'active', 'status_message': '', 'cooldowns': [], 'quota': {}}
            self.usage[name] = (200, usage_body(self.now))
            self.profiles[name] = profile
            cp.update_meta(name, cp.seat_pool('claude'), label=label, **({'reserve': True} if reserve else {}))
        for name, kwargs in (('load_seats', {'side_effect': self.load_seats}), ('api', {'side_effect': self.api}),
                             ('notify', {'side_effect': lambda t, b: self.notes.append((t, b))}),
                             ('claude_seat_call', {'side_effect': self.seat_call}),
                             ('cpa_version', {'return_value': '7.3.18-gate-test'})):
            stack.enter_context(mock.patch.object(sienna_pool if name == 'claude_seat_call' else cp, name, **kwargs))

    # the made-up pool
    def load_seats(self, pool=None, response_headers=None):
        assert pool is not None and pool.name == 'claude', 'the Claude pass reads the Claude pool only'
        meta = cp.read_meta(pool)
        rows = []
        for name, a in sorted(self.accounts.items(), key=lambda kv: -kv[1]['priority']):
            m = meta.get(name) or {}
            rows.append({'name': name, 'id': name, 'path': None, 'provider': 'claude', 'auth_index': name,
                         'label': a['label'], 'weight': float(m.get('weight') or 1), 'reserve': bool(m.get('reserve')),
                         'email': f'{a["label"].lower()}@test', 'plan': None, 'account_id': None,
                         'priority': a['priority'], 'disabled': a['disabled'], 'unavailable': a['unavailable'],
                         'status': a['status'], 'status_message': a['status_message'], 'next_retry_after': None,
                         'cooldowns': a['cooldowns'], 'quota': a['quota'], 'success': 5, 'failed': 0,
                         'excluded_models': a.get('excluded_models', []), 'model_quotas': a.get('model_quotas', {})})
        return rows

    def api(self, method, path, body=None, port=None, **kwargs):
        assert port == sienna_pool.CLAUDE_PORT, f'{method} {path} went to port {port}, not the Claude pool'
        if path == '/v0/management/auth-files/status':
            self.order.append((body['name'], body['disabled'],
                               (cp.read_json(sienna_pool.CLAUDE_GUARD_FILE, {}).get('seats') or {}).get(body['name'], {})))
            self.accounts[body['name']]['disabled'] = body['disabled']
            return {'ok': True}
        if path == '/v0/management/auth-files/fields':
            if 'excluded_models' in body:
                self.accounts[body['name']]['excluded_models'] = body['excluded_models']
                self.model_updates.append(body)
            else:
                self.accounts[body['name']]['priority'] = body['priority']
            return {'ok': True}
        if path.startswith('/v0/management/auth-files/models?'):
            name = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)['name'][0]
            if self.accounts[name]['disabled']:
                return {'models': []}  # CPA unregisters disabled accounts
            excluded = self.accounts[name].get('excluded_models', [])
            return {'models': [{'id': m} for m in ('claude-opus-test', 'claude-sonnet-test', 'claude-fable-test')
                               if not any(fnmatch.fnmatchcase(m, pat) for pat in excluded)]}
        if path == '/v0/management/auth-files/refresh':
            return {'ok': True}
        raise AssertionError(f'unexpected {method} {path}')

    def seat_call(self, seat, url, timeout=30):
        self.calls.append((seat['name'], url))
        if url == sienna_pool.CLAUDE_PROFILE_URL:
            return 200, self.profiles[seat['name']]
        if url == sienna_guard.CLAUDE_USAGE_URL:
            return self.usage[seat['name']]
        raise AssertionError(url)

    # helpers
    def credits(self, name, policy, cap=None):
        cp.update_meta(name, cp.seat_pool('claude'),
                       credits=None if policy == 'off' else {'policy': policy, 'cap': float(cap)})

    def out(self, name, credits=False, used_cents=0, five=100.0, week=40.0, scoped=None):
        self.usage[name] = (200, usage_body(self.now, five=five, week=week, scoped=scoped, credits=credits,
                                            used_cents=used_cents))

    def later(self, **delta):
        """Move the clock on (the pass polls again once its cadence has passed)."""
        self.now += dt.timedelta(**delta)
        return mock.patch.object(cp, 'now_utc', return_value=self.now)

    def run_pass(self):
        with mock.patch.object(cp, 'now_utc', return_value=self.now):
            sienna_guard.claude_guard_pass()
        st = json.loads(sienna_pool.CLAUDE_STATUS_FILE.read_text())
        return st, {r['label']: r for r in st['seats']}

    def guard(self):
        return json.loads(sienna_pool.CLAUDE_GUARD_FILE.read_text())

    def titles(self):
        return [t for t, _ in self.notes]


class Shape(ClaudePass):
    def test_desktop_uses_guard_listing_version_without_extra_requests(self):
        build_id = 'v7.3.18-gate-abcdef0123'
        record = desktop_build.desktop_wire_record_path(build_id)
        with preserved(record), mock.patch.object(cp, 'load_seats', self.real_load_seats), \
                mock.patch.object(cp, 'cpa_version', return_value='7.3.18-gate-abcdef0123'), \
                mock.patch.object(cp, 'running_version', side_effect=AssertionError('extra version request')):
            cp.write_json(record, {'build_id': build_id, 'version': '7.3.18', 'result': 'PASS'})
            first = ({'files': []}, {'X-CPA-VERSION': '7.3.18+gate.abcdef0123'})
            for headers, expected in (({'X-CPA-VERSION': '7.3.18+gate.abcdef0123'}, True),
                                      ({'x-cpa-version': '7.3.18+gate.abcdef0123'}, True),
                                      ({'X-CPA-VERSION': '7.3.19+gate.abcdef0123'}, False),
                                      ({}, False), (cp.PoolDown('connection refused'), False)):
                second = headers if isinstance(headers, Exception) else ({'files': []}, headers)
                with self.subTest(headers=headers), mock.patch.object(cp, 'api', side_effect=[first, second]) as api:
                    st, _ = self.run_pass()
                    self.assertEqual(st['pool']['desktop']['cpa_compatible'], expected)
                    self.assertEqual(api.call_count, 2)
                    for call in api.call_args_list:
                        self.assertEqual(call.args, ('GET', '/v0/management/auth-files'))
                        self.assertEqual(call.kwargs, {'port': sienna_pool.CLAUDE_PORT, 'want_headers': True})

    def test_team_defaults_refresh_without_overwriting_user_weights(self):
        old = (self.now - dt.timedelta(days=2)).isoformat()
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, {'plans': {
            name: {'plan': 'team', 'weight': 1.0, 'at': old} for name in self.accounts}})
        self.profiles['claude-a.json'] = {'organization': {'organization_type': 'claude_team'}}
        premium = {'organization': {'organization_type': 'claude_team', 'seat_tier': 'team_tier_1'}}
        self.profiles['claude-b.json'] = self.profiles['claude-r.json'] = premium
        # Even an explicit weight equal to an old default belongs to the user.
        cp.update_meta('claude-r.json', cp.seat_pool('claude'), weight=1.0)
        meta = sienna_pool.CLAUDE_SEATS_META.read_bytes()
        _, rows = self.run_pass()
        self.assertEqual((rows['A']['plan'], rows['A']['weight']), ('team', 1.25))
        self.assertEqual((rows['B']['plan'], rows['B']['weight']), ('team_premium', 6.25))
        self.assertEqual((rows['R']['plan'], rows['R']['weight']), ('team_premium', 1.0))
        self.assertEqual(self.guard()['plans']['claude-r.json']['weight'], 6.25)
        self.assertEqual(sienna_pool.CLAUDE_SEATS_META.read_bytes(), meta)
        # A later pass uses the saved profile defaults and still keeps the explicit size.
        with self.later(minutes=1):
            _, rows = self.run_pass()
        self.assertEqual([rows[label]['weight'] for label in ('A', 'B', 'R')], [1.25, 6.25, 1.0])

    def test_status_file_has_the_contract_shape(self):
        self.usage['claude-a.json'] = (200, usage_body(self.now, five=62, week=55, scoped=36, credits=True,
                                                       used_cents=1234, limit_cents=50000))
        self.credits('claude-b.json', 'last-resort', 200)
        st, rows = self.run_pass()
        self.assertEqual(set(st), {'generated_at', 'pool', 'active', 'active_name', 'next_back', 'seats'})
        self.assertTrue(POOL_KEYS <= set(st['pool']), POOL_KEYS - set(st['pool']))
        self.assertTrue(st['pool']['installed'])
        self.assertEqual(st['pool']['route'], sienna_pool.claude_route())
        self.assertEqual(st['pool']['balancing'], 'priority')
        self.assertEqual([r['label'] for r in st['seats']], ['A', 'B', 'R'])
        self.assertEqual((st['active'], st['active_name']), ('A', 'claude-a.json'))
        a = rows['A']
        self.assertTrue(STATUS_SEAT_KEYS <= set(a), STATUS_SEAT_KEYS - set(a))
        self.assertEqual(a['provider'], 'claude')
        self.assertEqual(a['state'], 'active')
        self.assertEqual((a['plan'], a['weight']), ('max_5x', 5.0))
        self.assertEqual((rows['B']['plan'], rows['B']['weight']), ('pro', 1.0))
        self.assertEqual((rows['R']['plan'], rows['R']['weight'], rows['R']['reserve']), ('max_20x', 20.0, True))
        self.assertEqual(a['five_hour']['used'], 62.0)
        self.assertEqual(cp.parse_time(a['five_hour']['reset_at']).replace(microsecond=0),
                         (self.now + dt.timedelta(hours=2)).replace(microsecond=0))
        self.assertEqual((a['week']['used'], a['week_used']), (55.0, 55.0))
        self.assertEqual([(x['name'], x['used']) for x in a['scoped']], [('Fable', 36.0)])
        self.assertTrue(all(set(x) >= {'name', 'used', 'reset_at'} for x in a['scoped']))
        self.assertEqual({k: a['credits'][k] for k in ('enabled', 'used', 'limit', 'policy', 'cap')},
                         {'enabled': True, 'used': 12.34, 'limit': 500.0, 'policy': 'off', 'cap': None})
        self.assertEqual({k: rows['B']['credits'][k] for k in ('policy', 'cap')},
                         {'policy': 'last-resort', 'cap': 200.0})
        self.assertIsNone(a['poll_error'])
        # the headline: weekly used, weighted by plan (A 5x at 55%, B 1x at 20%, R 20x at 20%)
        self.assertEqual(st['pool']['used_pct_all'], round((5 * 55 + 20 + 20 * 20) / 26, 1))
        self.assertEqual(st['pool']['used_pct_regular'], round((5 * 55 + 20) / 6, 1))
        # and the plans are kept for the day: a second pass makes no profile call
        n = len([c for c in self.calls if c[1] == sienna_pool.CLAUDE_PROFILE_URL])
        self.assertEqual(n, 3)
        with self.later(minutes=1):
            self.run_pass()
        self.assertEqual(len([c for c in self.calls if c[1] == sienna_pool.CLAUDE_PROFILE_URL]), 3)

    def test_history_sample_and_codex_files_untouched(self):
        codex = [cp.STATUS_FILE.read_bytes() if cp.STATUS_FILE.exists() else None,
                 cp.GUARD_FILE.read_bytes() if cp.GUARD_FILE.exists() else None,
                 cp.HISTORY_FILE.read_bytes() if cp.HISTORY_FILE.exists() else None]
        self.run_pass()
        with self.later(minutes=1):
            self.run_pass()   # within HISTORY_EVERY: no second sample
        lines = sienna_pool.CLAUDE_HISTORY_FILE.read_text().splitlines()
        self.assertEqual(len(lines), 1)
        sample = json.loads(lines[0])
        self.assertEqual(set(sample), {'t', 'used', 'all', 'reserve', 'seats', 'credits'})
        self.assertEqual(sample['seats'], {'A': 20.0, 'B': 20.0, 'R': 20.0})
        self.assertEqual([cp.STATUS_FILE.read_bytes() if cp.STATUS_FILE.exists() else None,
                          cp.GUARD_FILE.read_bytes() if cp.GUARD_FILE.exists() else None,
                          cp.HISTORY_FILE.read_bytes() if cp.HISTORY_FILE.exists() else None], codex)

    def test_poll_cadence_serving_and_idle(self):
        self.run_pass()
        polls = lambda: [n for n, u in self.calls if u == sienna_guard.CLAUDE_USAGE_URL]  # noqa: E731
        self.assertEqual(polls(), ['claude-a.json', 'claude-b.json', 'claude-r.json'])
        self.calls.clear()
        with self.later(minutes=4):   # past 3 min: only the serving account (A) is due
            self.run_pass()
        self.assertEqual(polls(), ['claude-a.json'])
        self.calls.clear()
        with self.later(minutes=7):   # 11 min after the first: the idle ones too
            self.run_pass()
        self.assertEqual(sorted(polls()), ['claude-a.json', 'claude-b.json', 'claude-r.json'])

    def test_a_profile_call_that_times_out_is_tried_once_per_pass(self):
        """Anthropic not answering the pool's profile call costs one timeout per pass, not one per account."""
        def seat_call(seat, url, timeout=30):
            self.calls.append((seat['name'], url))
            if url == sienna_pool.CLAUDE_PROFILE_URL:
                raise cp.PoolTimeout('api.anthropic.com did not answer')
            return self.usage[seat['name']]
        with mock.patch.object(sienna_pool, 'claude_seat_call', side_effect=seat_call):
            self.run_pass()
        self.assertEqual(len([c for c in self.calls if c[1] == sienna_pool.CLAUDE_PROFILE_URL]), 1)
        plans = self.guard()['plans']
        self.assertEqual(list(plans), ['claude-a.json'])
        self.assertIn('did not answer', plans['claude-a.json']['error'])

    def test_a_429_backs_off(self):
        self.usage['claude-b.json'] = (429, {'error': {'type': 'rate_limit_error'}})
        _, rows = self.run_pass()
        self.assertIn('429', rows['B']['poll_error'])
        self.assertEqual(self.guard()['usage']['claude-b.json']['backoff'], 2 * sienna_guard.CLAUDE_POLL_IDLE)
        self.calls.clear()
        with self.later(minutes=11):   # idle cadence passed, the backoff has not
            self.run_pass()
        self.assertNotIn(('claude-b.json', sienna_guard.CLAUDE_USAGE_URL), self.calls)

    def test_passive_signals_when_anthropic_refuses_the_api_call(self):
        refused = (403, {'_raw': '<!DOCTYPE html><html><title>Just a moment...</title>'})
        for name in self.usage:
            self.usage[name] = refused
        observed = self.now - dt.timedelta(minutes=5)
        self.accounts['claude-a.json']['quota'] = {'observed_at': observed.isoformat(), 'signals': {
            'Anthropic-Ratelimit-Unified-5h-Utilization': '0.42',
            'Anthropic-Ratelimit-Unified-5h-Reset': str(int((self.now + dt.timedelta(hours=1)).timestamp())),
            'Anthropic-Ratelimit-Unified-7d-Utilization': '0.30',
            'Anthropic-Ratelimit-Unified-7d-Reset': str(int((self.now + dt.timedelta(days=2)).timestamp())),
            'Anthropic-Ratelimit-Unified-Status': 'allowed'}}
        st, rows = self.run_pass()
        self.assertEqual([n for n, u in self.calls if u == sienna_guard.CLAUDE_USAGE_URL], ['claude-a.json'])  # stops at once
        self.assertEqual(self.titles(), ['Claude usage polling is off'])
        self.assertEqual(st['pool']['usage_polling'], 'passive')
        self.assertEqual((rows['A']['five_hour']['used'], rows['A']['week']['used']), (42.0, 30.0))
        self.assertTrue(rows['A']['poll_error'].startswith('usage as of last served'))
        self.assertIsNone(rows['B']['week'])
        self.calls.clear()
        with self.later(minutes=11):   # no more usage calls until CLAUDE_PASSIVE_RETRY
            self.run_pass()
        self.assertFalse([c for c in self.calls if c[1] == sienna_guard.CLAUDE_USAGE_URL])
        self.assertEqual(self.titles(), ['Claude usage polling is off'])


class CreditPolicy(ClaudePass):
    def parked_before_disabled(self, name):
        """The park record was in claude-guard.json before the pool was told to disable the account."""
        patch = next(o for o in self.order if o[0] == name and o[1] is True)
        self.assertTrue(patch[2].get('parked_until'), f'{name} disabled before its park record was written')

    def test_off_parks_an_account_at_its_limit_that_would_spend(self):
        self.run_pass()   # A serves
        self.out('claude-a.json', credits=True)
        self.now += dt.timedelta(minutes=4)
        st, rows = self.run_pass()
        self.parked_before_disabled('claude-a.json')
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'credits')
        self.assertEqual(cp.parse_time(g['parked_until']).replace(microsecond=0),
                         (self.now + dt.timedelta(hours=2, minutes=-4)).replace(microsecond=0))   # the 5-hour reset
        self.assertEqual((rows['A']['state'], rows['A']['detail']),
                         ('parked', 'credits off: plan limit or model exclusion unverified'))
        self.assertEqual(st['active'], 'B')
        self.assertIn('Claude account parked', self.titles())
        self.assertIn(('Claude now on B', 'A: credits off: plan limit or model exclusion unverified, back '
                       f'{cp.when(g["parked_until"])}'), self.notes)

    def test_off_leaves_an_account_out_without_credits_to_the_pool(self):
        self.out('claude-a.json', credits=False)
        st, rows = self.run_pass()
        self.assertFalse(self.order)   # nothing to park: Anthropic refuses it, nothing is spent
        self.assertEqual((rows['A']['state'], rows['A']['detail']), ('exhausted', '5-hour limit reached'))
        self.assertEqual(st['active'], 'B')

    def test_off_excludes_at_a_model_cap_but_opus_stays_available(self):
        self.out('claude-a.json', credits=True, five=20.0, scoped=100.0)
        _, rows = self.run_pass()
        self.assertEqual(rows['A']['state'], 'active')
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.accounts['claude-a.json']['excluded_models'], ['claude-fable*'])
        models = self.api('GET', '/v0/management/auth-files/models?name=claude-a.json', port=sienna_pool.CLAUDE_PORT)
        self.assertEqual([m['id'] for m in models['models']], ['claude-opus-test', 'claude-sonnet-test'])

    def test_a_model_cap_without_credits_keeps_serving_other_models(self):
        self.out('claude-a.json', credits=False, five=20.0, scoped=100.0)
        st, rows = self.run_pass()
        self.assertEqual((rows['A']['state'], rows['A']['detail']), ('active', 'Fable weekly limit reached'))
        self.assertFalse(self.order)

    def test_last_resort_waits_while_another_account_can_serve(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, used_cents=3100)
        st, rows = self.run_pass()
        self.parked_before_disabled('claude-a.json')
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'last-resort')
        self.assertEqual((rows['A']['state'], rows['A']['detail']),
                         ('parked', 'last resort: waits until every account is out'))
        self.assertEqual({k: rows['A']['credits'][k] for k in ('policy', 'cap', 'used', 'enabled', 'spending')},
                         {'policy': 'last-resort', 'cap': 200.0, 'used': 31.0, 'enabled': True, 'spending': False})
        self.assertIsNone(st['pool']['spending'])

    def test_last_resort_waits_while_only_the_reserve_can_serve(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True)
        self.out('claude-b.json')                    # out, credits off at Anthropic
        st, rows = self.run_pass()
        self.assertEqual(rows['A']['state'], 'parked')
        self.assertEqual((st['active'], st['pool']['reserve_in_use']), ('R', True))

    def all_out_but_a(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, used_cents=3100)
        self.out('claude-b.json')
        self.out('claude-r.json', week=100.0)

    def test_last_resort_spends_once_every_other_account_is_out(self):
        self.all_out_but_a()
        self.run_pass()   # the pass that first sees A at its limit, with B and R out: A spends
        st, rows = self.run_pass()
        # Before the first poll, unknown quota/credit amounts keep last-resort parked.
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(rows['A']['state'], 'active')
        self.assertTrue(rows['A']['credits']['spending'])
        self.assertEqual(st['pool']['spending'], 'A')
        self.assertEqual(self.guard()['spending'], 'claude-a.json')
        self.assertEqual(self.titles().count('Claude is spending usage credits'), 1)
        self.assertEqual((rows['B']['state'], rows['R']['state']), ('exhausted', 'exhausted'))

    def test_a_waiting_last_resort_account_is_enabled_when_the_rest_run_out(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.out('claude-a.json', credits=True, used_cents=3100)
        self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.out('claude-b.json')
        self.out('claude-r.json', week=100.0)
        with self.later(minutes=11):
            st, rows = self.run_pass()
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertNotIn('parked_until', self.guard()['seats']['claude-a.json'])
        self.assertEqual((st['active'], st['pool']['spending']), ('A', 'A'))
        self.assertIn('Claude is spending usage credits', self.titles())

    def test_spending_stops_when_another_account_comes_back(self):
        self.all_out_but_a()
        self.run_pass()
        self.usage['claude-r.json'] = (200, usage_body(self.now))   # R reset
        with self.later(minutes=11):
            st, rows = self.run_pass()
        self.parked_before_disabled('claude-a.json')
        self.assertEqual(rows['A']['state'], 'parked')
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'last-resort')
        self.assertIn('Claude account parked again', self.titles())
        self.assertNotIn('spending', self.guard())
        self.assertEqual((st['active'], st['pool']['spending']), ('R', None))

    def test_spending_stops_at_the_cap(self):
        self.all_out_but_a()
        self.run_pass()
        self.out('claude-a.json', credits=True, used_cents=20000)   # $200 of the $200 cap
        with self.later(minutes=4):
            st, rows = self.run_pass()
        self.parked_before_disabled('claude-a.json')
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'cap')
        self.assertEqual((rows['A']['state'], rows['A']['detail']), ('parked', 'credit cap reached'))
        self.assertIn('Claude credit cap reached', self.titles())
        self.assertIsNone(st['active'])
        self.assertIn('All Claude accounts are out', self.titles())

    def test_last_resort_at_its_cap_never_spends(self):
        self.all_out_but_a()
        self.out('claude-a.json', credits=True, used_cents=25000)
        _, rows = self.run_pass()
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'cap')
        self.assertNotIn('Claude is spending usage credits', self.titles())

    def test_last_resort_without_a_credit_reading_never_spends(self):
        self.all_out_but_a()
        body = usage_body(self.now, five=100.0, credits=True)
        body['extra_usage']['used_credits'] = None
        self.usage['claude-a.json'] = (200, body)
        _, rows = self.run_pass()
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'no-reading')
        self.assertNotIn('Claude is spending usage credits', self.titles())

    def test_spending_stops_without_a_current_credit_reading(self):
        self.all_out_but_a()
        self.run_pass()
        self.assertEqual(self.guard()['spending'], 'claude-a.json')
        self.usage['claude-a.json'] = (429, {'error': {'type': 'rate_limit_error'}})
        for _ in range(4):   # the usage endpoint keeps saying 429: the last reading gets old
            with self.later(minutes=5):
                _, rows = self.run_pass()
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'no-reading')
        self.assertEqual(rows['A']['state'], 'parked')
        self.assertNotIn('spending', self.guard())

    def test_an_account_you_disabled_is_never_enabled(self):
        self.all_out_but_a()
        self.accounts['claude-a.json']['disabled'] = True   # subpool claude disable A
        self.run_pass()
        with self.later(hours=3):   # past every reset A's usage named
            st, rows = self.run_pass()
        self.assertFalse([o for o in self.order if o[0] == 'claude-a.json'])
        self.assertEqual((rows['A']['state'], rows['A']['detail']), ('disabled', 'disabled by you'))
        self.assertIsNone(st['pool']['spending'])

    def test_a_park_runs_out_at_the_reset(self):
        self.out('claude-a.json', credits=True)
        self.run_pass()
        until = self.guard()['seats']['claude-a.json']['parked_until']
        self.usage['claude-a.json'] = (200, usage_body(self.now + dt.timedelta(hours=3)))
        with self.later(hours=2, minutes=1):
            st, rows = self.run_pass()
        self.assertGreater(self.now, cp.parse_time(until))
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.assertNotIn('parked_until', self.guard()['seats']['claude-a.json'])
        self.assertIn(('Claude account back in the pool', 'A has reset.'), self.notes)
        self.assertEqual(st['active'], 'A')

    def test_a_park_that_runs_out_while_another_limit_is_used_up_holds(self):
        self.out('claude-a.json', credits=True)   # the 5-hour limit: parked until its reset, two hours on
        self.run_pass()
        until = cp.parse_time(self.guard()['seats']['claude-a.json']['parked_until'])
        later = self.now + dt.timedelta(hours=2, minutes=1)
        body = usage_body(later, five=5.0, week=100.0, credits=True)   # meanwhile the weekly limit ran out
        self.usage['claude-a.json'] = (200, body)
        self.notes.clear()
        with self.later(hours=2, minutes=1):
            st, rows = self.run_pass()
        self.assertGreater(self.now, until)
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        g = self.guard()['seats']['claude-a.json']
        self.assertAlmostEqual(cp.parse_time(g['parked_until']).timestamp(),
                               cp.parse_time(body['seven_day']['resets_at']).timestamp(), delta=1)
        self.assertEqual(g['parked_reason'], 'credits')
        self.assertIn('claude-a.json', self.guard()['usage'])   # the reading it was held on is kept
        self.assertEqual(rows['A']['state'], 'parked')
        self.assertNotIn('Claude account back in the pool', self.titles())

    def test_an_expired_park_never_reopens_an_account_past_its_cap(self):
        self.all_out_but_a()
        self.out('claude-a.json', credits=True, used_cents=25000)   # $250 of a $200 cap
        self.run_pass()
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'cap')
        g = self.guard()   # an estimated reset that came early: the park runs out, the limit has not reset
        g['seats']['claude-a.json']['parked_until'] = at(self.now, minutes=1)
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, g)
        with self.later(minutes=4):
            st, rows = self.run_pass()
        self.assertTrue(self.accounts['claude-a.json']['disabled'])
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'cap')
        self.assertEqual(rows['A']['state'], 'parked')
        self.assertIsNone(st['pool']['spending'])

    def test_enable_overrides_the_park(self):
        self.out('claude-a.json', credits=True)
        self.run_pass()
        g = self.guard()   # what subpool claude enable A does: enabled, parked_until becomes override_until
        g['seats']['claude-a.json']['override_until'] = g['seats']['claude-a.json'].pop('parked_until')
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, g)
        self.accounts['claude-a.json']['disabled'] = False
        self.order.clear()
        with self.later(minutes=4):
            _, rows = self.run_pass()
        self.assertFalse(self.order)
        self.assertEqual(rows['A']['state'], 'active')

    def spend_below_limit(self, name, cents_from, cents_to, minutes=4):
        """Two readings of an account below its plan limits whose used credits went from cents_from to cents_to."""
        self.usage[name] = (200, usage_body(self.now, five=30.0, credits=True, used_cents=cents_from))
        self.run_pass()
        self.usage[name] = (200, usage_body(self.now, five=35.0, credits=True, used_cents=cents_to))
        with self.later(minutes=minutes):
            return self.run_pass()

    def test_misclassification_alarm(self):
        """Credits off: credits spent below the plan limit (fast mode, or billing as third-party) park it for an
        hour, not until its weekly reset."""
        st, rows = self.spend_below_limit('claude-a.json', 1000, 1450)
        self.parked_before_disabled('claude-a.json')
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'misclassified')
        self.assertAlmostEqual((cp.parse_time(g['parked_until']) - self.now).total_seconds(), 3600, delta=1)
        self.assertEqual((rows['A']['state'], rows['A']['detail']), ('parked', 'billed to credits below its limit'))
        title, body = next(n for n in self.notes if n[0] == 'Claude account billed to credits below its limit')
        self.assertIn('A spent usage credits below its plan limit ($10.00 → $14.50), and its credits are off in '
                      'subpool; parked until', body)
        self.assertIn('Fast mode (/fast) bills credits like this', body)
        self.assertEqual(st['active'], 'B')

    def test_a_repeated_alarm_parks_longer(self):
        self.spend_below_limit('claude-a.json', 1000, 1450)
        self.usage['claude-a.json'] = (200, usage_body(self.now + dt.timedelta(hours=1), five=35.0, credits=True,
                                                       used_cents=1450))
        with self.later(hours=1, minutes=1):
            _, rows = self.run_pass()   # the hour is up: back in the pool
        self.assertFalse(self.accounts['claude-a.json']['disabled'])
        self.usage['claude-a.json'] = (200, usage_body(self.now, five=36.0, credits=True, used_cents=1900))
        with self.later(minutes=4):
            self.run_pass()
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual((g['parked_reason'], g['alarms']), ('misclassified', 2))
        self.assertAlmostEqual((cp.parse_time(g['parked_until']) - self.now).total_seconds(), 7200, delta=1)

    def test_fast_mode_on_last_resort_waits_for_all_plan_quota(self):
        self.credits('claude-a.json', 'last-resort', 200)
        _, rows = self.spend_below_limit('claude-a.json', 1000, 1450)
        self.assertEqual(rows['A']['state'], 'parked')
        self.assertEqual(self.guard()['seats']['claude-a.json']['parked_reason'], 'last-resort')
        self.assertNotIn('Claude account spending usage credits', self.titles())

    def test_a_last_resort_account_spending_below_its_limit_stops_at_the_cap(self):
        self.credits('claude-a.json', 'last-resort', 200)
        self.spend_below_limit('claude-a.json', 19000, 20500)
        with self.later(minutes=11):  # parked seats poll at the idle interval
            st, rows = self.run_pass()
        self.parked_before_disabled('claude-a.json')
        g = self.guard()['seats']['claude-a.json']
        self.assertEqual(g['parked_reason'], 'cap')
        self.assertEqual(rows['A']['detail'], 'credit cap reached')
        self.assertEqual(st['active'], 'B')

    def test_no_alarm_while_you_override_the_guard(self):
        self.usage['claude-a.json'] = (200, usage_body(self.now, five=30.0, credits=True, used_cents=1000))
        self.run_pass()
        g = self.guard()
        g['seats']['claude-a.json']['override_until'] = at(self.now, hours=2)
        cp.write_json(sienna_pool.CLAUDE_GUARD_FILE, g)
        self.usage['claude-a.json'] = (200, usage_body(self.now, five=35.0, credits=True, used_cents=1450))
        with self.later(minutes=4):
            self.run_pass()
        self.assertFalse([o for o in self.order if o[0] == 'claude-a.json'])
        self.assertFalse(self.guard()['credits_seen']['claude-a.json']['rising'])

    def test_no_alarm_for_credits_spent_at_the_limit(self):
        self.all_out_but_a()
        self.run_pass()
        self.out('claude-a.json', credits=True, used_cents=6000)
        with self.later(minutes=4):
            self.run_pass()
        self.assertNotIn('Claude account billed to credits below its limit', self.titles())
        self.assertEqual(self.guard()['spending'], 'claude-a.json')


class Notifications(ClaudePass):
    def test_account_change_reserve_and_all_out(self):
        self.run_pass()
        self.out('claude-a.json')
        reset = self.now + dt.timedelta(hours=2)
        with self.later(minutes=4):
            self.run_pass()
        self.assertEqual(self.notes[-1], ('Claude now on B', f'A: 5-hour limit reached, back {cp.when(reset)}'))
        self.out('claude-b.json')
        with self.later(minutes=11):
            st, _ = self.run_pass()
        self.assertEqual(st['pool']['reserve_in_use'], True)
        self.assertIn(('Claude is now on the reserve account', 'R is serving: the regular accounts are spent.'),
                      self.notes)
        self.out('claude-r.json', week=100.0)
        with self.later(minutes=11):
            st, _ = self.run_pass()
        self.assertIsNone(st['active'])
        self.assertEqual(self.titles()[-1], 'All Claude accounts are out')
        self.assertEqual(st['next_back']['label'], 'A')

    def test_pool_down(self):
        with mock.patch.object(cp, 'load_seats', side_effect=cp.PoolDown('connection refused')):
            for _ in range(cp.DOWN_ALERT_AFTER):
                with mock.patch.object(cp, 'now_utc', return_value=self.now):
                    sienna_guard.claude_guard_pass()
        st = json.loads(sienna_pool.CLAUDE_STATUS_FILE.read_text())
        self.assertEqual((st['pool']['running'], st['pool']['installed'], st['seats']), (False, True, []))
        self.assertEqual(self.titles(), ['Claude pool is not responding'])
        self.run_pass()
        self.assertEqual(self.titles()[-1], 'Claude pool is back')


class SignInEnded(ClaudePass):
    def test_a_refresh_anthropic_refused_in_the_claude_pool_log(self):
        path = sienna_pool.CLAUDE_AUTH / 'claude-a.json'
        sienna_pool.CLAUDE_AUTH.mkdir(parents=True, exist_ok=True)
        self.addCleanup(lambda: path.unlink() if path.exists() else None)
        path.write_text(json.dumps({'type': 'claude', 'email': 'a@test', 'last_refresh': at(self.now, days=-1)}))
        sienna_pool.CLAUDE_LOGS.mkdir(parents=True, exist_ok=True)
        self.run_pass()   # notes A's tokens
        with self.later(minutes=1):
            self.run_pass()   # marks where the log is
        stamp = self.now.astimezone().strftime('%Y-%m-%d %H:%M:%S')
        with sienna_pool.CLAUDE_MAIN_LOG.open('a') as f:
            f.write(f'[{stamp}] [--------] [warn ] [conductor_refresh.go:585] credential refresh failed for claude '
                    '(claude-a.json): token refresh failed with status 400: {"error": "invalid_grant", '
                    '"error_description": "Refresh token not found or invalid"}; retaining active credential as '
                    'access token is unexpired\n')
            f.write(f'[{stamp}] [--------] [warn ] [conductor_refresh.go:585] credential refresh failed for codex '
                    '(claude-b.json): invalid_grant; retaining active credential as access token is unexpired\n')
        with self.later(minutes=1):
            _, rows = self.run_pass()
        self.assertEqual((rows['A']['state'], rows['A']['detail']),
                         ('active', 'Anthropic ended this sign-in: sign in again soon'))
        self.assertTrue(rows['A']['sign_in_ended'])
        self.assertFalse(rows['B']['sign_in_ended'])   # a Codex line names no Claude account
        self.assertEqual(self.notes[-1], ('Claude account needs a re-login', 'A: Anthropic ended this sign-in. Sign '
                                          'in again: subpool claude login A --priority 300 (or click it in the '
                                          'menu bar).'))


class DoctorReadsThePass(ClaudePass):
    def test_passive_polling_is_a_doctor_warning(self):
        for name in self.usage:
            self.usage[name] = (403, {'_raw': '<html>challenge</html>'})
        self.run_pass()
        rep = cp.DoctorReport(echo=False)
        with mock.patch.object(sienna_pool, 'claude_status_now', side_effect=AssertionError('reads the file')):
            sienna_pool.doctor_claude_accounts(rep)
        checks = {c['text']: c for s in rep.sections for c in s['checks']}
        passive = next(c for text, c in checks.items() if text.startswith('usage polling is passive'))
        self.assertEqual(passive['status'], 'warn')
        self.assertEqual(checks['A: active']['status'], 'ok')


class GuardCommand(ClaudePass):
    def test_the_claude_pass_runs_only_while_installed(self):
        plist = cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist'
        with preserved(plist), mock.patch.object(cp, 'guard_pass') as codex_pass, \
                mock.patch.object(sienna_guard, 'claude_guard_pass') as claude_pass:
            if plist.exists():
                plist.unlink()
            cp.cmd_guard(None)
            self.assertEqual((codex_pass.call_count, claude_pass.call_count), (1, 0))
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text('<plist/>')
            cp.cmd_guard(None)
            self.assertEqual((codex_pass.call_count, claude_pass.call_count), (2, 1))
            codex_pass.side_effect = RuntimeError('codex pass broke')
            with self.assertRaises(RuntimeError):
                cp.cmd_guard(None)
            self.assertEqual(claude_pass.call_count, 2)   # a Codex failure never stops the Claude pass

    def test_a_selftest_in_one_pool_never_stops_the_other_pools_guard(self):
        plist = cp.LAUNCH_AGENTS / f'{sienna_pool.CLAUDE_JOB}.plist'
        with preserved(plist), mock.patch.object(cp, 'guard_pass') as codex_pass, \
                mock.patch.object(sienna_guard, 'claude_guard_pass') as claude_pass:
            plist.parent.mkdir(parents=True, exist_ok=True)
            plist.write_text('<plist/>')
            with cp.guard_lock(pool=cp.seat_pool('claude')):   # what subpool claude selftest holds
                cp.cmd_guard(None)
            self.assertEqual((codex_pass.call_count, claude_pass.call_count), (1, 0))
            with cp.guard_lock():   # what subpool selftest holds
                cp.cmd_guard(None)
            self.assertEqual((codex_pass.call_count, claude_pass.call_count), (1, 1))
            cp.cmd_guard(None)
            self.assertEqual((codex_pass.call_count, claude_pass.call_count), (2, 2))
        self.assertNotEqual(cp.seat_pool('claude').lock, cp.seat_pool('codex').lock)
        self.assertEqual(cp.seat_pool('codex').lock, cp.LOCK_FILE)


class Parsing(unittest.TestCase):
    def test_usage_body(self):
        now = cp.now_utc()
        u = sienna_guard.claude_usage_from_body(usage_body(now, five=100, week=64, scoped=92, credits=True, used_cents=3100,
                                                 limit_cents=20000), now)
        self.assertEqual((u['five_hour']['used'], u['week']['used'], u['week']['window_min']), (100.0, 64.0, 10080))
        self.assertEqual(u['scoped'], [{'name': 'Fable', 'used': 92.0, 'reset_at': u['scoped'][0]['reset_at'],
                                        'active': False}])
        self.assertEqual(u['credits'], {'enabled': True, 'used': 31.0, 'limit': 200.0, 'currency': 'USD'})
        self.assertEqual([w for w, _ in sienna_guard.claude_limits_hit(u, now)], ['5-hour'])
        spend = {'extra_usage': {'is_enabled': True, 'currency': 'JPY', 'decimal_places': 0},
                 'spend': {'used': {'amount_minor': 500, 'currency': 'JPY', 'exponent': 0}}}
        self.assertEqual(sienna_guard.claude_credits_from_body(spend), {'enabled': True, 'used': 500.0, 'limit': None,
                                                              'currency': 'JPY'})
        self.assertEqual(sienna_guard.claude_credits_from_body({}), {})

    def test_signals(self):
        now = cp.now_utc()
        reset = int((now + dt.timedelta(hours=3)).timestamp())
        u = sienna_guard.claude_usage_from_signals({'observed_at': now.isoformat(), 'signals': {
            'Anthropic-Ratelimit-Unified-5h-Status': 'rejected', 'Anthropic-Ratelimit-Unified-5h-Reset': str(reset),
            'Anthropic-Ratelimit-Unified-7d-Utilization': '0.5', 'Anthropic-Ratelimit-Unified-7d_oi-Utilization': '1.0',
            'Anthropic-Ratelimit-Unified-Overage-Status': 'allowed', 'Anthropic-Ratelimit-Unified-Status': 'rejected'}})
        self.assertEqual(u['five_hour']['used'], 100.0)
        self.assertEqual(cp.parse_time(u['five_hour']['reset_at']).timestamp(), reset)
        self.assertEqual(u['week']['used'], 50.0)
        self.assertEqual([(x['name'], x['used']) for x in u['scoped']], [('Fable', 100.0)])
        self.assertTrue(u['refused'])
        self.assertTrue(sienna_guard.claude_would_spend(u))
        self.assertIsNone(sienna_guard.claude_usage_from_signals({'signals': {'X-Codex-Primary-Used-Percent': '3'}}))

    def test_times(self):
        self.assertEqual(sienna_guard.claude_time('2026-09-24T18:45:46.1234567Z'), '2026-09-24T18:45:46+00:00')
        self.assertEqual(sienna_guard.claude_time(1790000000), sienna_guard.claude_time(1790000000000))
        self.assertIsNone(sienna_guard.claude_time('soon'))
        self.assertIsNone(sienna_guard.claude_time(True))


if __name__ == '__main__':
    unittest.main()
