"""Pro $500 uses its announced Plus-relative capacity; explicit overrides remain authoritative."""
from _helpers import cp

import ast
import math
import pathlib
import types
import unittest
from unittest import mock


class Capacity(unittest.TestCase):
    def test_manual_weight_is_authoritative(self):
        pool = cp.seat_pool()
        pool.claims_context = lambda: None
        pool.claims = lambda *args: {'plan': 'promax'}
        for weight, expected in ((None, 25), (0, 25), (float('nan'), 25), (20, 20), (30, 30)):
            with self.subTest(weight=weight), mock.patch.object(cp, 'read_meta', return_value={'one': {'weight': weight}}):
                seat = cp.load_seats(pool, listing={'files': [{'name': 'one', 'provider': 'codex'}]})[0]
                self.assertTrue(seat['capacity_known'])
                self.assertEqual(seat['weight'], expected)

    def test_promax_aggregate_uses_plus_baseline(self):
        self.assertEqual(cp.default_weight('promax'), 25)
        self.assertEqual(cp.default_weight('pro'), 20)
        rows = [dict(weight=cp.default_weight('promax'), week_used=20, state='ready', reserve=False),
                dict(weight=1, week_used=100, state='ready', reserve=False)]
        with mock.patch.dict(cp.SETTINGS, {'headline': 'all'}):
            pool = {}
            cp.set_headline(pool, rows)
        self.assertEqual(pool['used_pct'], 23.1)
        self.assertTrue(pool['capacity_known_all'])
        self.assertEqual(cp.total_size_text(rows), '26× total  ·  ')

    def test_unknown_capacity_and_scope(self):
        rows = [dict(weight=5, week_used=40, state='ready', reserve=False),
                dict(weight=None, week_used=10, state='ready', reserve=True, capacity_known=False)]
        with mock.patch.dict(cp.SETTINGS, {'headline': 'all'}):
            pool = {}
            cp.set_headline(pool, rows)
            self.assertIsNone(pool['used_pct'])
            self.assertEqual(pool['used_pct_regular'], 40)
            self.assertIn('5× known + unknown capacity', cp.total_size_text(rows))
            rows[1]['week_used'] = None
            cp.set_headline(pool, rows)
            self.assertIsNone(pool['used_pct'])
            rows[1]['state'] = 'disabled'
            cp.set_headline(pool, rows)
            self.assertEqual(pool['used_pct'], 40)
            self.assertEqual(cp.total_size_text(rows), '5× total  ·  ')

    def test_status_does_not_present_partial_remaining_capacity(self):
        seat = dict(label='Example', name='one', provider='codex', email=None, plan='unmeasured',
                    priority=100, weight=1, capacity_known=False, reserve=False)
        with mock.patch.object(cp, 'seat_state', return_value=('ready', '', None)), \
                mock.patch.object(cp, 'best_usage', return_value=None), \
                mock.patch.object(cp, 'cpa_version', return_value='test'):
            st = cp.build_status([seat], {})
        self.assertIsNone(st['seats'][0]['weight'])
        self.assertIsNone(st['pool']['left_weight'])
        self.assertIsNone(st['pool']['used_pct'])
        self.assertEqual(st['pool']['known_left_weight'], 0)

    def test_known_aggregation_unchanged(self):
        self.assertEqual(cp._weighted([dict(weight=5, week_used=40), dict(weight=20, week_used=10)]), 16)

    def test_menu_badge_totals_and_projection(self):
        path = pathlib.Path(cp.__file__).resolve().parent.parent / 'menubar' / 'codexpool_menubar.py'
        tree = ast.parse(path.read_text())
        names = {'plan_badge', 'weighted_used', 'total_text', 'pace_text'}
        body = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
        ns = dict(PLAN_NAMES=(('promax', 'Pro $500'), ('pro', 'Pro')), Seat=object, Model=object, math=math)
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), ns)
        self.assertEqual(ns['plan_badge']('promax', None), 'Pro $500')
        self.assertEqual(ns['plan_badge']('promax', 25), 'Pro $500 25×')
        seats = [types.SimpleNamespace(capacity_known=False, weight=None, state='ready', week=types.SimpleNamespace(used=12))]
        self.assertIsNone(ns['weighted_used'](seats))
        model = types.SimpleNamespace(seats=seats, headline=None, headline_mode='all', status='regular')
        self.assertEqual(ns['total_text'](model), 'Unknown capacity')
        self.assertIsNone(ns['pace_text'](model))
