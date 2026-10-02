"""Native data contract tests; no real settings actions or credential reads."""
from _helpers import REPO, addon
import importlib
import json
import sys
import unittest
from unittest import mock

try:
    import AppKit
    sys.path.insert(0, str(REPO / 'menubar'))
    mb = importlib.import_module('subpool_menubar')
except ImportError:
    mb = None


@unittest.skipIf(mb is None, 'Native UI tests need the menu-bar Python with PyObjC')
class NativeContract(unittest.TestCase):
    def setUp(self):
        self.raw = json.loads((REPO / 'addons/sienna/docs/images/demo/claude-cli-status.json').read_text())
        self.ui = mb.POOL_UI['claude']

    def model(self):
        return mb.build_model(self.raw, '', [], mb.parse_time(self.raw['generated_at']), pool_name='claude')

    def test_headline_is_selected_account(self):
        model = self.model()
        self.assertEqual(model.headline, 25)
        self.assertEqual(model.serving.label, 'Personal')
        self.assertEqual(model.seats[0].week.used, 80)
        self.assertEqual(self.ui.product_scope, 'CLI')

    def test_display_order_and_plan_badges(self):
        self.raw['seats'][1].update(plan='max_20x', weight=20, capacity_known=True)
        model = self.model()
        self.assertEqual([s.label for s in mb.display_seats(model)], ['Personal', 'Backup', 'Work'])
        self.assertEqual(model.seats[1].plan, 'Max 20×')
        self.raw['seats'][0].update(plan=None, weight=None, capacity_known=False)
        self.assertEqual(self.model().seats[0].plan, 'Plan unknown')

    def test_unknown_selected_usage_cannot_fall_back_to_average(self):
        self.raw['pool']['selected_usage_known'] = False
        self.raw['pool']['used_pct'] = None
        self.assertIsNone(self.model().headline)

    def test_account_menu_has_only_cswap_actions(self):
        add = mock.Mock()
        self.ui.account_menu(self.model().seats[0], add)
        verbs = [c.args[2] for c in add.call_args_list]
        self.assertEqual(verbs, ['switch', 'disable', 'reserve'])
        self.assertFalse(self.ui.lane_providers)
        self.assertFalse(self.ui.show_chart)

    def test_held_reserve_is_labeled_and_remains_manually_switchable(self):
        self.raw['seats'][0].update(reserve=True, reserve_held=True)
        seat = self.model().seats[0]
        self.assertTrue(seat.reserve_held)
        self.assertTrue(seat.reserve)
        self.assertEqual(self.ui.state_label(seat, 'Ready'), 'Held for later')
        add = mock.Mock()
        self.ui.account_menu(seat, add)
        self.assertEqual([c.args[2] for c in add.call_args_list], ['switch', 'disable', 'unreserve'])

    def test_codex_setup_does_not_call_retired_claude_hooks(self):
        self.assertEqual(self.ui.setup_done_rows(object()), [])
        self.assertIsNone(self.ui.snapshot_login('setup-signin', object()))
        self.assertEqual(self.ui.snapshot_panes, ())

    def test_settings_provider_list_excludes_claude_engine(self):
        st = importlib.import_module('subpool_settings')
        self.assertFalse(any(p[0] == 'sienna' for p in st.PROVIDERS))
        self.assertEqual(st.POOL_TITLES['claude'], 'Claude CLI')
        self.assertEqual(st.POOL_TITLES['codex'], 'Codex Desktop/CLI')


if __name__ == '__main__':
    unittest.main()
