"""The credits mismatch warning in the menu bar app and Settings (addons/sienna/menubar_ext.py's credits_mismatch,
mismatch_text, credits_line, credits_tip, credits_text and credits_summary, drawn by the core apps through the PoolUI):
claude-status.json's seats[].credits.mismatch, set by the guard when an account's credit policy is Off yet claude.ai
says its usage credits are on. Only turning them off at claude.ai closes that, so the rows say so, a parked
account's too; a file from an older guard has no flag and no warning; and the menu bar is left alone.

The copy helpers are pure functions over a seat's fields and a label, so they run on every Python here, loaded from
the source without importing the module (which needs PyObjC). Parsing, the rows and Settings' wording need PyObjC,
that is the menu bar's own interpreter (~/.codexpool/menubar/.venv/bin/python), and skip without it. The README's
demo data must stay free of the flag, so docs/images/demo/render.py's output stays byte-identical."""
import __future__
import ast
import copy
import importlib
import json
import sys
import types
import unittest

from _helpers import REPO

MENUBAR = REPO / 'menubar' / 'codexpool_menubar.py'
EXT = REPO / 'addons' / 'sienna' / 'menubar_ext.py'
SPEC = REPO / 'addons' / 'sienna' / 'docs' / 'SPEC.md'
DOCS = REPO / 'addons' / 'sienna' / 'docs' / 'MENUBAR.md'
DEMO = REPO / 'docs' / 'images' / 'demo'
ADDON_DEMO = REPO / 'addons' / 'sienna' / 'docs' / 'images' / 'demo'
NOW = '2026-09-24T16:41:00Z'
PURE = ('credits_mismatch', 'mismatch_text')
LINE = 'Turn credits off at claude.ai (Settings → Usage)'
SENTENCE = ('Usage credits are on at claude.ai for Max 20x: turn them off there (Settings → Usage). '
            'codexpool can’t stop every paid request.')
QUIET = 'Credits on at claude.ai · not used by the pool'   # the line an older guard's file still gets


def pure_helpers():
    """MISMATCH_LINE and the PURE functions as the source defines them, loaded on their own."""
    tree = ast.parse(EXT.read_text(), str(EXT))
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in PURE]
    body += [node for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
             and node.targets[0].id == 'MISMATCH_LINE']
    wanted = set(PURE) | {'MISMATCH_LINE'}
    names = {getattr(n, 'name', None) or n.targets[0].id for n in body}
    assert names == wanted, f'{EXT.name} lacks {sorted(wanted - names)}'
    ns = {}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(EXT), 'exec',
                 __future__.annotations.compiler_flag, True), ns)
    return ns


def seat_like(mismatch=None):
    """Just what credits_mismatch reads: a seat with credits (the core seat's `extra`, or None) and its flag."""
    credits = None if mismatch is None else types.SimpleNamespace(mismatch=mismatch)
    return types.SimpleNamespace(label='Max 20x', extra=credits)


class Copy(unittest.TestCase):
    """The words, on any Python."""

    def setUp(self):
        self.ns = pure_helpers()

    def test_row_line_is_the_action_and_where(self):
        self.assertEqual(self.ns['MISMATCH_LINE'], LINE)

    def test_full_sentence_names_the_account(self):
        self.assertEqual(self.ns['mismatch_text']('Max 20x'), SENTENCE)
        self.assertIn('for Pro C:', self.ns['mismatch_text']('Pro C'))

    def test_flag_is_read_from_the_credits(self):
        f = self.ns['credits_mismatch']
        self.assertFalse(f(seat_like(None)))       # no credits at all
        self.assertFalse(f(seat_like(False)))
        self.assertTrue(f(seat_like(True)))

    def test_demo_data_has_no_mismatch(self):
        """The README renders come from this data; a flag here would change them (they must stay byte-identical)."""
        files = sorted(ADDON_DEMO.glob('claude-status-*.json'))
        self.assertTrue(files)
        for path in files:
            for seat in json.loads(path.read_text()).get('seats') or []:
                self.assertNotIn('mismatch', seat.get('credits') or {}, f'{path.name}: {seat.get("label")}')

    def test_spec_and_docs_describe_it(self):
        spec, docs = SPEC.read_text(), DOCS.read_text()
        self.assertIn('`mismatch`', spec)
        self.assertIn(LINE, spec)
        self.assertIn(LINE, docs)


def load(name):
    """The menu bar app or the Settings window as a module; None without PyObjC."""
    try:
        import AppKit  # noqa: F401
    except ImportError:
        return None
    if str(MENUBAR.parent) not in sys.path:
        sys.path.insert(0, str(MENUBAR.parent))
    return importlib.import_module(name)


mb = load('codexpool_menubar')
ext = mb.POOL_UI['claude'].module if mb is not None else None   # the add-on's menubar_ext.py, as mb loaded it


@unittest.skipIf(mb is None, 'needs PyObjC (run with the menu bar app\'s interpreter, ~/.codexpool/menubar/.venv/bin/python)')
class WithAppKit(unittest.TestCase):
    """Parsing, the popover's rows and Settings' wording, on the demo's Claude pool: Max 20x has credits on at
    claude.ai with policy off (the case the guard flags), Max A has them off, Max B is the last resort."""

    def setUp(self):
        self.raw = json.loads((ADDON_DEMO / 'claude-status-regular.json').read_text())
        self.now = mb.parse_time(NOW)

    def model(self, raw):
        return mb.build_model(raw, '', [], self.now, pool_name='claude')

    def flagged(self, label, parked=False):
        """The demo with `label` flagged (and parked by the guard, when asked)."""
        raw = copy.deepcopy(self.raw)
        seat = next(s for s in raw['seats'] if s['label'] == label)
        seat['credits']['mismatch'] = True
        if parked:
            seat['state'], seat['detail'] = 'parked', 'Parked: 5-hour limit used up, more would spend credits'
        return raw

    @staticmethod
    def seat(m, label):
        return next(s for s in m.seats if s.label == label)

    def test_parse_credits_reads_the_flag(self):
        base = {'enabled': True, 'used': 12.0, 'limit': 500.0, 'policy': 'off', 'cap': None, 'spending': False}
        self.assertTrue(ext.parse_credits({**base, 'mismatch': True}).mismatch)
        self.assertFalse(ext.parse_credits(base).mismatch)              # an older guard's file
        self.assertFalse(ext.parse_credits({**base, 'mismatch': 'yes'}).mismatch)   # only a real true counts
        self.assertIsNone(ext.parse_credits(None))

    def test_a_file_without_the_flag_keeps_the_quiet_line(self):
        seat = self.seat(self.model(self.raw), 'Max 20x')
        self.assertFalse(ext.credits_mismatch(seat))
        self.assertEqual(ext.credits_line(seat), (QUIET, False))
        self.assertNotIn('turn them off', ext.credits_tip(seat))

    def test_flagged_row_warns_and_its_tip_says_why(self):
        seat = self.seat(self.model(self.flagged('Max 20x')), 'Max 20x')
        self.assertTrue(ext.credits_mismatch(seat))
        self.assertEqual(ext.credits_line(seat), (LINE, False))   # never the 'spending' orange
        self.assertEqual(ext.credits_tip(seat), SENTENCE)

    def test_parked_account_warns_too(self):
        """Under the ⏸ line that says why the guard parked it, one more line: the warning (the row's tooltip is
        the sentence in full; the parked reason is the ⏸ line itself, never the tip)."""
        raw = self.flagged('Pro C', parked=True)
        flagged = self.model(raw)
        seat = self.seat(flagged, 'Pro C')
        self.assertEqual(seat.state, 'parked')
        self.assertEqual(ext.credits_line(seat), (LINE, False))
        layout = mb.PopoverLayout(flagged)
        h_flagged = layout.seat_row(seat, 0.0)
        self.assertEqual(layout.tips[('seat', seat.name)], ext.mismatch_text('Pro C'))
        del next(s for s in raw['seats'] if s['label'] == 'Pro C')['credits']['mismatch']
        parked = self.model(raw)   # parked the same way, no flag: just the ⏸ line
        h_parked = mb.PopoverLayout(parked).seat_row(self.seat(parked, 'Pro C'), 0.0)
        self.assertEqual(h_flagged - h_parked, mb.SMALL_LH + 1)

    def test_popover_row_replaces_the_quiet_line_or_adds_one(self):
        """Max 20x already had a credits line (replaced: same height); Max A had none (one line more)."""
        plain, flagged = self.model(self.raw), self.model(self.flagged('Max 20x'))
        h_plain = mb.PopoverLayout(plain).seat_row(self.seat(plain, 'Max 20x'), 0.0)
        h_flagged = mb.PopoverLayout(flagged).seat_row(self.seat(flagged, 'Max 20x'), 0.0)
        self.assertEqual(h_plain, h_flagged)
        flagged_a = self.model(self.flagged('Max A'))
        h_plain_a = mb.PopoverLayout(plain).seat_row(self.seat(plain, 'Max A'), 0.0)
        h_flagged_a = mb.PopoverLayout(flagged_a).seat_row(self.seat(flagged_a, 'Max A'), 0.0)
        self.assertEqual(h_flagged_a - h_plain_a, mb.SMALL_LH + 1)

    def test_menu_bar_and_headline_are_left_alone(self):
        """A standing account setting must not take the red or the warning triangle from the pool's live states."""
        plain, flagged = self.model(self.raw), self.model(self.flagged('Max 20x'))
        for m in (plain, flagged):
            self.assertEqual(m.status, 'regular')
            self.assertFalse(m.hot)
            self.assertFalse(m.warn)
        self.assertEqual(mb.pool_severity(plain), mb.pool_severity(flagged))
        self.assertEqual(mb.menubar_signature(plain), mb.menubar_signature(flagged))
        self.assertEqual(mb.pool_line(plain), mb.pool_line(flagged))
        self.assertEqual(mb.headline_text(plain), mb.headline_text(flagged))

    def test_settings_say_the_same_and_the_seats_pane_in_full(self):
        load('codexpool_settings')
        plain, flagged = self.model(self.raw), self.model(self.flagged('Max 20x'))
        self.assertEqual(ext.credits_text(self.seat(flagged, 'Max 20x'), flagged), (LINE, False))
        self.assertEqual(ext.credits_summary(self.seat(flagged, 'Max 20x'), flagged),
                         'Off, but usage credits are on at claude.ai ($12 of $500 this month): turn them off there '
                         '(Settings → Usage). codexpool can’t stop every paid request.')
        self.assertEqual(ext.credits_summary(self.seat(plain, 'Max 20x'), plain),
                         'Off: parked at its plan limit · credits on at claude.ai, $12 of $500 this month.')


if __name__ == '__main__':
    unittest.main()
