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
    mb = importlib.import_module('codexpool_menubar')
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

    def test_unknown_selected_usage_cannot_fall_back_to_average(self):
        self.raw['pool']['selected_usage_known'] = False
        self.raw['pool']['used_pct'] = None
        self.assertIsNone(self.model().headline)

    def test_account_menu_has_only_cswap_actions(self):
        add = mock.Mock()
        self.ui.account_menu(self.model().seats[0], add)
        verbs = [c.args[2] for c in add.call_args_list]
        self.assertEqual(verbs, ['switch', 'disable'])
        self.assertFalse(self.ui.lane_providers)
        self.assertFalse(self.ui.show_chart)

    def test_settings_provider_list_excludes_claude_engine(self):
        st = importlib.import_module('codexpool_settings')
        self.assertFalse(any(p[0] == 'sienna' for p in st.PROVIDERS))
        self.assertEqual(st.POOL_TITLES['claude'], 'Claude CLI')
        self.assertEqual(st.POOL_TITLES['codex'], 'Codex Desktop/CLI')


if __name__ == '__main__':
    unittest.main()
