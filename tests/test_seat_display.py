"""Presentation ordering keeps the pool's authoritative fill order intact."""
from _helpers import REPO
import ast
import types
import unittest
import importlib
import json
import sys
from unittest import mock

try:
    import AppKit
    sys.path.insert(0, str(REPO / 'menubar'))
    mb = importlib.import_module('codexpool_menubar')
except ImportError:
    mb = None


class SeatDisplay(unittest.TestCase):
    def test_available_first_preserves_fill_order_and_priorities(self):
        path = REPO / 'menubar/codexpool_menubar.py'
        body = [n for n in ast.parse(path.read_text()).body
                if isinstance(n, ast.FunctionDef) and n.name == 'display_seats']
        ns = {'Seat': object, 'Model': object}
        exec(compile(ast.Module(body=body, type_ignores=[]), str(path), 'exec'), ns)
        def seat(name, state, reserve=False):
            return types.SimpleNamespace(name=name, available=state in ('active', 'ready'),
                serving=state == 'active', reserve=reserve)
        rows = [seat('out', 'exhausted'), seat('parked', 'parked'), seat('ready-a', 'ready'),
                seat('reserve', 'ready', True), seat('ready-b', 'ready'), seat('serving', 'active'),
                seat('blocked', 'blocked'), seat('off', 'disabled'), seat('cooldown', 'cooldown')]
        model = types.SimpleNamespace(seats=rows, serving=rows[5])
        shown = ns['display_seats'](model)
        self.assertEqual([s.name for s in shown], ['serving', 'ready-a', 'ready-b', 'reserve',
                         'out', 'parked', 'blocked', 'off', 'cooldown'])
        self.assertIs(model.seats, rows)
        self.assertEqual(rows[0].name, 'out')
        model.serving = rows[0]  # A selected account at its limit remains unavailable.
        self.assertEqual(ns['display_seats'](model)[0].name, 'serving')


@unittest.skipIf(mb is None, 'Native layout checks need PyObjC')
class CompactLayout(unittest.TestCase):
    def model(self):
        raw = json.loads((REPO / 'docs/images/demo/status-regular.json').read_text())
        history = mb.load_history(REPO / 'docs/images/demo/history-regular.jsonl')
        return mb.build_model(raw, '', history, mb.parse_time(raw['generated_at']))

    def test_compact_reduces_height_and_keeps_actions_and_all_limits(self):
        model = self.model()
        compact = mb.PopoverLayout(model, seat_view='compact').build()
        full = mb.PopoverLayout(model, seat_view='full').build()
        compact_rows = [r for r, key in compact.regions if key[0] == 'seat']
        full_rows = [r for r, key in full.regions if key[0] == 'seat']
        self.assertLess(compact.height, full.height)
        self.assertLess(sum(r[1][1] for r in compact_rows), sum(r[1][1] for r in full_rows) * .75)
        self.assertEqual([key for r, key in compact.regions if key[0] == 'seat'],
                         [key for r, key in full.regions if key[0] == 'seat'])
        self.assertIn(('seatview', 'full'), [key for r, key in compact.regions])
        self.assertIn(('seatview', 'compact'), [key for r, key in full.regions])
        short = next(s for s in model.seats if s.short)
        tip = compact.tips[('seat', short.name)]
        self.assertIn('Week:', tip)
        self.assertIn('5h:', tip)

    def test_binding_limit_is_first_when_compact_room_is_tight(self):
        model = self.model()
        short = next(s for s in model.seats if s.short)
        short.short.used = 100
        lay = mb.PopoverLayout(model)
        with mock.patch.object(lay, 'text') as text:
            lay.compact_usage(short, 0, 0, mb.font(11), 85)
        self.assertTrue(text.call_args.args[0].startswith('5h 0%'))

    def test_view_toggle_is_local_and_persisted_without_running_commands(self):
        controller = types.SimpleNamespace(seat_view='compact', content=mock.Mock(), render=mock.Mock())
        defaults = mock.Mock()
        with mock.patch.object(mb, 'NSUserDefaults') as factory, mock.patch.object(mb, 'run_codexpool') as command:
            factory.standardUserDefaults.return_value = defaults
            mb.Controller.handle_region(controller, ('seatview', 'full'), None, None)
        self.assertEqual(controller.seat_view, 'full')
        defaults.setObject_forKey_.assert_called_once_with('full', mb.SEAT_VIEW_KEY)
        controller.render.assert_called_once_with(reset_scroll=True)
        command.assert_not_called()

    def test_graph_collapse_is_independent_of_account_density(self):
        model = self.model()
        for mode in ('compact', 'full'):
            expanded = mb.PopoverLayout(model, seat_view=mode).build()
            collapsed = mb.PopoverLayout(model, seat_view=mode, chart_expanded=False).build()
            self.assertLess(collapsed.height, expanded.height)
            self.assertIn(('chart', 'toggle'), [key for _, key in collapsed.regions])
            self.assertEqual(collapsed.tips[('chart', 'toggle')], 'Expand graph')
            self.assertEqual(expanded.tips[('chart', 'toggle')], 'Collapse graph')
            self.assertEqual([rect[1] for rect, key in expanded.regions if key[0] == 'seat'],
                             [rect[1] for rect, key in collapsed.regions if key[0] == 'seat'])
            with mock.patch.object(expanded, 'add') as draw:
                expanded.chart(0)
            self.assertTrue(any(call.args[0] == mb.draw_chart for call in draw.call_args_list))
            with mock.patch.object(collapsed, 'add') as draw:
                collapsed.chart(0)
            self.assertFalse(any(call.args[0] == mb.draw_chart for call in draw.call_args_list))

    def test_graph_toggle_remembers_choice_without_changing_account_view(self):
        controller = types.SimpleNamespace(chart_expanded=True, seat_view='compact', range_key='7d',
                                            content=mock.Mock(), render=mock.Mock())
        defaults = mock.Mock()
        with mock.patch.object(mb, 'NSUserDefaults') as factory, mock.patch.object(mb, 'run_codexpool') as command:
            factory.standardUserDefaults.return_value = defaults
            mb.Controller.handle_region(controller, ('chart', 'toggle'), None, None)
            self.assertFalse(controller.chart_expanded)
            mb.Controller.handle_region(controller, ('chart', 'toggle'), None, None)
        self.assertTrue(controller.chart_expanded)
        self.assertEqual(controller.seat_view, 'compact')
        self.assertEqual(controller.range_key, '7d')
        self.assertEqual(defaults.setBool_forKey_.call_args_list,
                         [mock.call(False, mb.CHART_EXPANDED_KEY), mock.call(True, mb.CHART_EXPANDED_KEY)])
        command.assert_not_called()
