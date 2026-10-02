"""Load balancing: "balancing" in settings.json, subpool order, the guard's soonest-reset-first fill order (no churn,
your order back afterwards, the reserve always last, seats without usage data), and what status.json says about it."""
import contextlib
import datetime as dt
import json
import unittest
from unittest import mock

from _helpers import TEST_KEY, FakePool, cp, fake_seat_file, preserved, run, settings_restored

WEEK = 7 * 24 * 60


def window(reset_in, used=20.0, minutes=WEEK, now=None):
    """A usage window that resets reset_in after now."""
    now = now or cp.now_utc()
    return {'used': used, 'window_min': minutes, 'reset_at': (now + reset_in).isoformat()}


def usage(week=None, short=None, now=None):
    """A guard.json usage cache entry, polled just now (so the guard does not poll again)."""
    now = now or cp.now_utc()
    return {'primary': short, 'secondary': week, 'credits': {}, 'plan': 'plus', 'refused': False, 'allowed': True,
            'at': now.isoformat(), 'source': 'poll', 'polled_at': now.isoformat()}


def row(name, state='ready', reserve=False, week_in=None, short_in=None, now=None):
    """A Codex status row the way build_status writes it, with only what balancing reads."""
    now = now or cp.now_utc()
    return {'name': name, 'label': name.upper(), 'provider': 'codex', 'state': state, 'reserve': reserve,
            'week': window(week_in, now=now) if week_in is not None else None,
            'short': window(short_in, minutes=300, now=now) if short_in is not None else None}


def names(rows):
    return [r['name'] for r in rows]


class SpanText(unittest.TestCase):
    def test_spans(self):
        for seconds, text in ((0, 'under a minute'), (59, 'under a minute'), (60, '1m'), (25 * 60, '25m'),
                              (3600, '1h'), (3 * 3600 + 600, '3h 10m'), (86400, '1d'), (86400 + 4 * 3600 + 59, '1d 4h'),
                              (-30, 'under a minute')):
            self.assertEqual(cp.span_text(seconds), text, seconds)


class OrderReason(unittest.TestCase):
    def test_reasons(self):
        now = cp.now_utc()
        self.assertEqual(cp.order_reason(row('a', week_in=dt.timedelta(days=1, hours=4, minutes=1), now=now), now),
                         'resets in 1d 4h')
        self.assertEqual(cp.order_reason(row('a', short_in=dt.timedelta(hours=2), now=now), now),
                         'resets in 2h (5h window)')
        self.assertEqual(cp.order_reason(row('a', week_in=dt.timedelta(days=2), short_in=dt.timedelta(hours=1),
                                             now=now), now), 'resets in 2d')  # the weekly window counts
        self.assertEqual(cp.order_reason(row('a'), now), 'no usage data yet: kept in your order')
        self.assertEqual(cp.order_reason(row('a', reserve=True, week_in=dt.timedelta(days=1)), now),
                         'reserve: used last')
        self.assertEqual(cp.order_reason(row('a', state='exhausted', week_in=dt.timedelta(days=1)), now),
                         'exhausted: not ordered until it serves again')


class ResetFillOrder(unittest.TestCase):
    """reset_fill_order: rows are in the pool's current order; None means it already is what reset balancing wants."""

    def setUp(self):
        self.now = cp.now_utc()
        d = dt.timedelta(days=1)
        self.rows = {'a': row('a', week_in=5 * d, now=self.now), 'b': row('b', week_in=d, now=self.now),
                     'c': row('c', now=self.now), 'e': row('e', now=self.now),
                     'x': row('x', state='exhausted', week_in=d / 2, now=self.now),
                     'r': row('r', reserve=True, week_in=d / 4, now=self.now)}

    def order(self, pool, manual=None):
        return cp.reset_fill_order([self.rows[n] for n in pool], list(manual or pool))

    def test_soonest_reset_first_then_no_data_then_the_rest_reserve_last(self):
        self.assertEqual(self.order('acxber', 'acbexr'), list('bacexr'))

    def test_already_in_order_is_left_alone(self):
        self.assertIsNone(self.order('bacer'))
        self.assertIsNone(self.order('bxacer'))   # a seat that serves nothing may sit anywhere
        self.assertIsNone(self.order('baxcer'))

    def test_close_reset_times_do_not_reorder(self):
        self.rows['a'] = row('a', week_in=dt.timedelta(days=1, minutes=3), now=self.now)
        self.assertIsNone(self.order('abr'))      # 3 minutes apart: within the slack
        self.rows['a'] = row('a', week_in=dt.timedelta(days=1, minutes=30), now=self.now)
        self.assertEqual(self.order('abr'), list('bar'))

    def test_small_gaps_do_not_add_up(self):
        m = dt.timedelta(minutes=1)
        self.rows['a'] = row('a', week_in=dt.timedelta(days=1) + 10 * m, now=self.now)
        self.rows['b'] = row('b', week_in=dt.timedelta(days=1) + 6 * m, now=self.now)
        self.rows['c'] = row('c', week_in=dt.timedelta(days=1) + 2 * m, now=self.now)
        self.assertEqual(self.order('abcr'), list('cbar'))   # 4 minutes apart each, but a resets 8 after c
        self.assertIsNone(self.order('cbar'))
        self.assertIsNone(self.order('bcar'))   # b resets 4 minutes after c: within the slack

    def test_reserve_goes_last_even_when_it_resets_first(self):
        self.assertEqual(self.order('rba'), list('bar'))
        self.assertEqual(self.order('brxa'), list('baxr'))

    def test_reserves_keep_your_order(self):
        self.rows['s'] = row('s', reserve=True, now=self.now)
        self.assertIsNone(self.order('bars', 'bars'))
        self.assertEqual(self.order('bars', 'basr'), list('basr'))

    def test_seats_without_usage_data_keep_your_order(self):
        self.assertEqual(self.order('bcer', 'bec r'.replace(' ', '')), list('becr'))
        self.assertIsNone(self.order('becr', 'becr'))
        self.assertEqual(self.order('cber', 'cebr'), list('bcer'))   # after every seat with data

    def test_equal_times_follow_your_order(self):
        self.rows['a'] = row('a', week_in=dt.timedelta(days=1), now=self.now)
        self.rows['b'] = row('b', week_in=dt.timedelta(days=1), now=self.now)
        self.rows['c'] = row('c', week_in=dt.timedelta(days=9), now=self.now)
        self.assertEqual(self.order('cab', 'bac'), list('bac'))

    def test_nothing_to_order(self):
        self.assertIsNone(cp.reset_fill_order([], []))
        self.assertIsNone(self.order('r'))


class Balancer(unittest.TestCase):
    """balance_pass and guard_pass with the pool's seat list, its API and the usage cache made up here."""

    def setUp(self):
        self.now = cp.now_utc()
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(preserved(cp.GUARD_FILE, cp.STATUS_FILE, cp.HISTORY_FILE, cp.GUARD_LOG, cp.SEATS_META))
        stack.enter_context(settings_restored())
        for f in (cp.GUARD_FILE, cp.SEATS_META, cp.GUARD_LOG):
            if f.exists():
                f.unlink()
        self.pool = {}      # name -> seat row as load_seats returns it
        self.patches = []   # (name, priority) the management API was asked for, in order
        self.notes = []
        d = dt.timedelta(days=1)
        for name, prio, reserve, week_in in (('a', 300, False, 5 * d), ('b', 200, False, d), ('c', 100, False, None),
                                             ('r', 50, True, d / 4)):
            self.add_seat(name, prio, reserve, week_in)
        for name, kwargs in (('load_seats', {'side_effect': self.seats}), ('api', {'side_effect': self.api}),
                             ('notify', {'side_effect': lambda t, b: self.notes.append((t, b))}),
                             ('poll_usage', {'side_effect': cp.ApiError(500, 'not here')}),
                             ('poll_resets', {'side_effect': cp.ApiError(500, 'not here')})):
            stack.enter_context(mock.patch.object(cp, name, **kwargs))
        self.guard = {'usage': self.usage}

    def add_seat(self, name, priority, reserve=False, week_in=None, **pool):
        self.pool[name] = dict({'name': name, 'id': name, 'path': None, 'provider': 'codex', 'auth_index': name,
                                'label': name.upper(), 'weight': 1.0, 'reserve': reserve, 'email': None,
                                'plan': 'plus', 'account_id': None, 'priority': priority, 'disabled': False,
                                'unavailable': False, 'status': 'active', 'status_message': '',
                                'next_retry_after': None, 'cooldowns': [], 'quota': {}, 'success': 1, 'failed': 0},
                               **pool)
        self.usage = getattr(self, 'usage', {})
        if week_in is not None:
            self.usage[name] = usage(week=window(week_in, now=self.now), now=self.now)
        cp.update_meta(name, reserve=True if reserve else None)

    def seats(self, pool=None):
        assert pool is None or pool.name == 'codex', pool
        rows = [dict(s) for s in self.pool.values()]
        rows.sort(key=lambda s: (-s['priority'], s['id']))
        return rows

    def api(self, method, path, body=None, **kwargs):
        if method == 'PATCH' and path == '/v0/management/auth-files/fields':
            self.patches.append((body['name'], body['priority']))
            self.pool[body['name']]['priority'] = body['priority']
            return {'status': 'ok'}
        raise AssertionError(f'unexpected {method} {path}')

    def order(self):
        return [s['name'] for s in self.seats()]

    def balance(self):
        return cp.balance_pass(self.guard, self.seats(), cp.now_utc())

    def meta(self, key='manual_priority'):
        return {n: e.get(key) for n, e in cp.read_meta().items()}

    def log(self):
        return cp.GUARD_LOG.read_text() if cp.GUARD_LOG.exists() else ''

    def test_priority_mode_changes_nothing(self):
        self.assertFalse(self.balance())
        self.assertEqual((self.patches, self.order(), self.log()), ([], list('abcr'), ''))
        self.assertNotIn('balancing', self.guard)

    def test_reset_orders_once_then_leaves_it(self):
        cp.SETTINGS['balancing'] = 'reset'
        self.assertTrue(self.balance())
        self.assertEqual(self.order(), list('bacr'))
        self.assertEqual(self.meta(), {'a': 300, 'b': 200, 'c': 100, 'r': 50})   # your order, kept for later
        self.assertEqual({n: s['priority'] for n, s in self.pool.items()}, {'b': 1000, 'a': 990, 'c': 980, 'r': 970})
        self.assertIn('guard: balancing: new fill order B > A > C > R (soonest reset first)', self.log())
        self.assertEqual(self.guard['balancing']['mode'], 'reset')
        self.assertEqual(self.notes, [])
        self.patches.clear()
        self.assertFalse(self.balance())
        self.assertEqual(self.patches, [])    # no churn
        self.assertEqual(self.log().count('new fill order'), 1)

    def test_switching_back_restores_your_order(self):
        cp.SETTINGS['balancing'] = 'reset'
        self.balance()
        cp.SETTINGS['balancing'] = 'priority'
        self.patches.clear()
        self.assertTrue(self.balance())
        self.assertEqual({n: s['priority'] for n, s in self.pool.items()}, {'a': 300, 'b': 200, 'c': 100, 'r': 50})
        self.assertIn('guard: balancing: your fill order is back: A > B > C > R', self.log())
        self.assertNotIn('balancing', self.guard)
        self.patches.clear()
        self.assertFalse(self.balance())
        self.assertEqual(self.patches, [])

    def test_order_set_while_on_reset_comes_back(self):
        cp.SETTINGS['balancing'] = 'reset'
        self.balance()
        self.patches.clear()
        with mock.patch.object(cp, 'refresh_status_file'):
            code, out, err = run(cp.cmd_order, seats=['c', 'b'])
        self.assertEqual(code, 0, err)
        self.assertIn('Fill order: C > B > A > R (reserve)', out)
        self.assertIn('Balancing is "reset"', out)
        self.assertEqual(self.meta(), {'c': 1000, 'b': 990, 'a': 980, 'r': 970})
        self.assertEqual((self.patches, self.order()), ([], list('bacr')))   # the live order stays soonest reset
        self.assertFalse(self.balance())
        self.assertEqual(self.log().count('new fill order'), 1)
        cp.SETTINGS['balancing'] = 'priority'
        self.balance()
        self.assertEqual(self.order(), list('cbar'))

    def test_priority_while_on_reset_changes_only_your_order(self):
        cp.SETTINGS['balancing'] = 'reset'
        self.balance()
        self.patches.clear()
        code, out, err = run(cp.cmd_priority, seat='c', priority=400)
        self.assertEqual(code, 0, err)
        self.assertIn('C: priority in your order 100 → 400', out)
        self.assertIn('takes effect with: subpool set balancing priority', out)
        self.assertEqual((self.patches, self.order()), ([], list('bacr')))
        cp.SETTINGS['balancing'] = 'priority'
        self.balance()
        self.assertEqual(self.order(), list('cabr'))

    def test_equal_priorities_come_back_in_the_same_order(self):
        for name in 'abcr':
            self.pool[name]['priority'] = 0       # a seat file without a priority: the pool orders them by id
        cp.SETTINGS['balancing'] = 'reset'
        self.balance()
        self.assertEqual(self.order(), list('bacr'))
        self.assertEqual(self.meta(), {'a': 1000, 'b': 990, 'c': 980, 'r': 970})   # the order in effect, not the ties
        cp.SETTINGS['balancing'] = 'priority'
        self.balance()
        self.assertEqual(self.order(), list('abcr'))

    def test_equal_manual_priorities_go_by_seat_id(self):
        cp.write_manual_order({'a': 5, 'b': 5, 'c': 5, 'r': 1})
        pool = sorted(self.seats(), key=lambda s: ['c', 'b', 'a', 'r'].index(s['name']))   # the guard's order
        self.assertEqual(cp.manual_order(pool, cp.read_meta()), list('abcr'))

    def test_your_reorder_is_not_announced(self):
        cp.write_json(cp.GUARD_FILE, {'usage': self.usage})
        cp.guard_pass()
        self.assertEqual(json.loads(cp.GUARD_FILE.read_text())['announced'], 'a')
        with mock.patch.object(cp, 'refresh_status_file'):
            code, _, err = run(cp.cmd_order, seats=['c'])
        self.assertEqual(code, 0, err)
        self.assertTrue(json.loads(cp.GUARD_FILE.read_text())['you_reordered'])
        cp.guard_pass()
        guard = json.loads(cp.GUARD_FILE.read_text())
        self.assertEqual((self.notes, guard['announced']), ([], 'c'))
        self.assertNotIn('you_reordered', guard)
        self.pool['c']['disabled'] = True          # a seat that really goes is still news
        cp.guard_pass()
        self.assertEqual([t for t, _ in self.notes], ['Codex now on A'])

    def test_priority_and_reserve_take_the_guard_lock(self):
        held = []

        @contextlib.contextmanager
        def lock(wait=True, timeout=240, pool=None):
            held.append(True)
            yield True
            held.pop()
        seen = []
        with mock.patch.object(cp, 'guard_lock', lock), \
                mock.patch.object(cp, 'update_meta', side_effect=lambda *a, **k: seen.append(bool(held))):
            run(cp.cmd_priority, seat='b', priority=400)
            run(cp.cmd_reserve, seat='c', off=False)
        self.assertEqual(seen, [True, True])

    def test_seat_added_while_on_reset_goes_after_your_seats(self):
        cp.SETTINGS['balancing'] = 'reset'
        self.balance()
        self.add_seat('n', 5000, week_in=dt.timedelta(days=3))   # login placed it anywhere
        self.balance()
        self.assertEqual(self.order(), list('bnacr'))
        cp.SETTINGS['balancing'] = 'priority'
        self.balance()
        self.assertEqual(self.order(), list('abcnr'))
        self.assertEqual(self.meta(), {'a': 1000, 'b': 990, 'c': 980, 'n': 970, 'r': 960})  # renumbered, kept

    def test_reserve_stays_last(self):
        cp.SETTINGS['balancing'] = 'reset'
        self.pool['r']['priority'] = 900        # a reserve above the regular seats
        self.balance()
        self.assertEqual(self.order()[-1], 'r')

    def test_seats_that_serve_nothing_are_not_ordered(self):
        cp.SETTINGS['balancing'] = 'reset'
        self.balance()
        self.patches.clear()
        self.pool['b']['disabled'] = True       # the first seat goes off: the others stay as they are
        self.assertFalse(self.balance())
        self.assertEqual(self.patches, [])

    def test_api_failure_is_logged_and_retried(self):
        cp.SETTINGS['balancing'] = 'reset'
        with mock.patch.object(cp, 'set_fields', side_effect=cp.PoolDown('connection refused')):
            self.assertFalse(self.balance())
        self.assertIn('guard: balancing: could not set the fill order: connection refused', self.log())
        self.assertTrue(self.balance())
        self.assertEqual(self.order(), list('bacr'))

    def test_guard_pass_reorders_without_a_notification(self):
        cp.SETTINGS['balancing'] = 'reset'
        cp.write_json(cp.GUARD_FILE, {'usage': self.usage, 'announced': 'a', 'announced_known': True})
        cp.guard_pass()
        st = json.loads(cp.STATUS_FILE.read_text())
        self.assertEqual(st['active'], 'B')
        self.assertEqual(st['pool']['balancing'], 'reset')
        self.assertEqual([r['name'] for r in st['seats']], list('bacr'))
        self.assertEqual([r['order_reason'] for r in st['seats']],
                         ['resets in 23h 59m', 'resets in 4d 23h', 'no usage data yet: kept in your order',
                          'reserve: used last'])
        self.assertEqual(self.notes, [])        # "Codex now on B" would only confuse
        self.assertEqual(json.loads(cp.GUARD_FILE.read_text())['announced'], 'b')

    def test_guard_pass_on_priority_keeps_the_order(self):
        cp.write_json(cp.GUARD_FILE, {'usage': self.usage})
        cp.guard_pass()
        st = json.loads(cp.STATUS_FILE.read_text())
        self.assertEqual((st['active'], st['pool']['balancing']), ('A', 'priority'))
        self.assertEqual([r['order_reason'] for r in st['seats']], [None] * 4)
        self.assertEqual(self.patches, [])


class OrderCommand(unittest.TestCase):
    """subpool order against the fake pool: priorities from 1000 down, reserve seats last, seats.json keeps it."""

    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(preserved(cp.SEATS_META, cp.STATUS_FILE, cp.GUARD_FILE))
        stack.enter_context(settings_restored())
        stack.enter_context(mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY))
        self.files = {}
        for label, prio in (('Alpha', 300), ('Bravo', 200), ('Charlie', 100), ('Spare', 50)):
            path = fake_seat_file(f'{label.lower()}@test', priority=prio)
            self.addCleanup(path.unlink)
            self.files[label] = path.name
            cp.update_meta(path.name, label=label)
        cp.update_meta(self.files['Spare'], reserve=True)
        self.fake = stack.enter_context(FakePool())

    def prios(self):
        return {label: self.fake.priorities.get(name) for label, name in self.files.items()}

    def manual(self):
        meta = cp.read_meta()
        return {label: meta[name].get('manual_priority') for label, name in self.files.items()}

    def test_named_first_then_the_rest_then_the_reserve(self):
        code, out, err = run(cp.cmd_order, seats=['Charlie', 'Alpha'])
        self.assertEqual(code, 0, err)
        self.assertIn('Fill order: Charlie > Alpha > Bravo > Spare (reserve)', out)
        self.assertEqual(self.prios(), {'Charlie': 1000, 'Alpha': 990, 'Bravo': 980, 'Spare': 970})
        self.assertEqual(self.manual(), self.prios())
        self.assertNotIn('Balancing is', out)

    def test_a_named_reserve_stays_last(self):
        code, out, _ = run(cp.cmd_order, seats=['Spare', 'Bravo'])
        self.assertEqual(code, 0)
        self.assertIn('Fill order: Bravo > Alpha > Charlie > Spare (reserve)', out)
        self.assertIn('Spare is a reserve seat: a reserve seat always fills after every regular seat', out)

    def test_unchanged_priorities_are_not_sent_again(self):
        run(cp.cmd_order, seats=['Alpha'])
        self.fake.patches.clear()
        code, _, _ = run(cp.cmd_order, seats=['Alpha', 'Bravo'])
        self.assertEqual((code, self.fake.patches), (0, []))

    def test_refusals(self):
        for seats, text in ((['Nobody'], 'matches no seats'), (['Alpha', 'alpha'], 'Alpha is named twice')):
            code, _, err = run(cp.cmd_order, seats=seats)
            self.assertEqual(code, 1, seats)
            self.assertIn(text, err)
        self.assertEqual(self.fake.patches, [])

    def test_parser(self):
        args = cp.build_parser().parse_args(['order', 'Bravo', 'Alpha'])
        self.assertEqual((args.seats, args.fn), (['Bravo', 'Alpha'], cp.cmd_order))

    def test_priority_records_your_order_too(self):
        code, out, _ = run(cp.cmd_priority, seat='Bravo', priority=450)
        self.assertEqual(code, 0)
        self.assertIn('Bravo priority 200 → 450', out)
        self.assertEqual(self.manual()['Bravo'], 450)
        self.assertEqual(self.fake.priorities[self.files['Bravo']], 450)
        cp.SETTINGS['balancing'] = 'reset'
        code, out, _ = run(cp.cmd_priority, seat='Bravo', priority=460)
        self.assertIn('Balancing is "reset"', out)
        self.assertEqual(self.manual()['Bravo'], 460)
        self.assertEqual(self.fake.priorities[self.files['Bravo']], 450)   # the guard owns the live order


class SetBalancing(unittest.TestCase):
    def setUp(self):
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(settings_restored())
        stack.enter_context(preserved(cp.STATUS_FILE))

    def test_set_and_say_what_happens(self):
        code, out, _ = run(cp.cmd_set, key='balancing', value='reset')
        self.assertEqual(code, 0)
        self.assertIn('balancing: priority → reset. On its next pass (within a minute) the guard orders', out)
        self.assertIn('running threads stay on their seat', out)
        self.assertEqual(json.loads(cp.SETTINGS_FILE.read_text())['balancing'], 'reset')
        self.assertEqual(cp.SETTINGS['balancing'], 'reset')
        code, out, _ = run(cp.cmd_set, key='balancing', value='reset')
        self.assertEqual(out.strip(), 'balancing is already reset.')
        code, out, _ = run(cp.cmd_set, key='balancing', value='priority')
        self.assertIn('balancing: reset → priority. The guard puts your fill order back', out)

    def test_status_file_follows_while_the_pool_is_down(self):
        cp.STATUS_FILE.write_text(json.dumps(cp.status_down('connection refused', False)))
        self.assertEqual(json.loads(cp.STATUS_FILE.read_text())['pool']['balancing'], 'priority')
        run(cp.cmd_set, key='balancing', value='reset')
        self.assertEqual(json.loads(cp.STATUS_FILE.read_text())['pool']['balancing'], 'reset')


class StatusFields(unittest.TestCase):
    def setUp(self):
        ctx = settings_restored()
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        self.now = cp.now_utc()
        self.seats = [dict(name=n, id=n, label=n.upper(), provider='codex', priority=p, reserve=r, disabled=False,
                           unavailable=False, status='active', status_message='', next_retry_after=None, cooldowns=[],
                           quota={}, weight=1.0, email=None, plan='plus', success=1, failed=0)
                      for n, p, r in (('a', 300, False), ('b', 200, False), ('r', 100, True))]
        self.guard = {'usage': {'a': usage(week=window(dt.timedelta(days=1, hours=4, minutes=1), now=self.now),
                                           now=self.now),
                                'r': usage(week=window(dt.timedelta(days=6), now=self.now), now=self.now)}}

    def test_priority(self):
        st = cp.build_status(self.seats, self.guard)
        self.assertEqual(st['pool']['balancing'], 'priority')
        self.assertTrue(all('order_reason' in r and r['order_reason'] is None for r in st['seats']))

    def test_reset(self):
        cp.SETTINGS['balancing'] = 'reset'
        st = cp.build_status(self.seats, self.guard)
        self.assertEqual(st['pool']['balancing'], 'reset')
        self.assertEqual([r['order_reason'] for r in st['seats']],
                         ['resets in 1d 4h', 'no usage data yet: kept in your order', 'reserve: used last'])

    def test_status_header_says_the_mode(self):
        for mode, text in (('priority', 'fill: your order'), ('reset', 'fill: soonest reset first')):
            cp.SETTINGS['balancing'] = mode
            st = cp.build_status(self.seats, self.guard)
            code, out, _ = run(lambda args: cp.print_status(st))
            self.assertIn(text, out.splitlines()[0], mode)
            if mode == 'reset':
                self.assertIn('resets in 1d 4h', out)

    def test_down_status(self):
        cp.SETTINGS['balancing'] = 'reset'
        self.assertEqual(cp.status_down('x', False)['pool']['balancing'], 'reset')


if __name__ == '__main__':
    unittest.main()
