"""Enable for a parked Claude account in the menu bar app and Settings (addons/sienna/menubar_ext.py's
last_resort_parked, last_resort_tip, LAST_RESORT_ITEM and rotation_row; the core's account menu takes the line from
the PoolUI): `codexpool claude enable` overrides a park only while the account's credit policy is Off (a
credits-off park), for the override's duration. An account parked by its last-resort policy stays parked until every
other account's plan quota is spent, the reserve's included, and the CLI refuses to override that; so the menu offers
no Enable (spends credits)… for it but a disabled line saying when it serves, with the reason in its tooltip, and
Settings' In rotation switch is off and disabled, with the reason as the row's subtitle.

The copy helpers are pure functions over a seat's fields, so they run on every Python here, loaded from the source
without importing the module (which needs PyObjC). The menu and Settings' row need PyObjC, that is the menu bar's
own interpreter (~/.codexpool/menubar/.venv/bin/python), and skip without it. The README's demo data has one such
account, claude-status-reserve.json's Max B, parked in the guard's own words; its popover row is not what changes
here (the menu and Settings' Seats pane are, and neither is rendered), so docs/images/demo/render.py's output stays
byte-identical."""
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
PURE = ('last_resort_parked', 'last_resort_tip')
ITEM = 'Serves once every other account is out'
ENABLE = 'Enable (spends credits)…'
TIP = ('Max B is the last resort: codexpool brings it back once every other account’s plan quota is spent, the '
       'reserve’s included. Enable can’t override that; change its credit policy in Settings → Balancing.')
LAST_RESORT_DETAIL = 'last resort: waits until every account is out'          # the CLI's park detail, verbatim
CREDITS_OFF_DETAIL = 'credits off: plan limit or model exclusion unverified'
SUB = ('Parked as the last resort: serves once every other account is out, the reserve included. To use it sooner, '
       'change its credit policy under Usage credits.')


def pure_helpers():
    """LAST_RESORT_ITEM and the PURE functions as the source defines them, loaded on their own."""
    tree = ast.parse(EXT.read_text(), str(EXT))
    body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in PURE]
    body += [node for node in tree.body if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
             and node.targets[0].id == 'LAST_RESORT_ITEM']
    wanted = set(PURE) | {'LAST_RESORT_ITEM'}
    names = {getattr(n, 'name', None) or n.targets[0].id for n in body}
    assert names == wanted, f'{EXT.name} lacks {sorted(wanted - names)}'
    ns = {'POOL': 'claude'}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(EXT), 'exec',
                 __future__.annotations.compiler_flag, True), ns)
    return ns


def seat_like(provider='claude', state='parked', policy='last-resort'):
    """Just what last_resort_parked reads: the pool, the state and the credit policy (None: no credits at all)."""
    credits = None if policy is None else types.SimpleNamespace(last_resort=policy == 'last-resort')
    return types.SimpleNamespace(label='Max B', provider=provider, state=state, extra=credits, reserve=False)


class Copy(unittest.TestCase):
    """The words and the rule, on any Python."""

    def setUp(self):
        self.ns = pure_helpers()

    def test_menu_line_says_when_it_serves(self):
        self.assertEqual(self.ns['LAST_RESORT_ITEM'], ITEM)

    def test_tip_names_the_account_and_where_the_policy_is(self):
        tip = self.ns['last_resort_tip']
        self.assertEqual(tip('Max B'), TIP)
        self.assertIn('Pro C is the last resort', tip('Pro C'))
        self.assertNotIn('reserve', tip('Max B', reserve=True))   # the reserve's own line skips 'the reserve's included'
        self.assertIn('every other account’s plan quota is spent.', tip('Max B', reserve=True))

    def test_only_a_parked_last_resort_claude_account(self):
        f = self.ns['last_resort_parked']
        self.assertTrue(f(seat_like()))
        self.assertFalse(f(seat_like(policy='off')))          # a credits-off park: Enable overrides it
        self.assertFalse(f(seat_like(policy=None)))           # no credit data at all
        self.assertFalse(f(seat_like(state='ready')))         # serving or ready: nothing to enable
        self.assertFalse(f(seat_like(state='disabled')))      # turned off by hand: plain Enable
        self.assertFalse(f(seat_like(provider='codex')))      # Codex seats have no credit policy

    def test_demo_reserve_scenario_has_the_case(self):
        """claude-status-reserve.json parks Max B, the last resort, the way the guard writes it: the fixture the
        PyObjC tests below build on. Its popover row is untouched by this change, so the README renders stay."""
        raw = json.loads((ADDON_DEMO / 'claude-status-reserve.json').read_text())
        seat = next(s for s in raw['seats'] if s['label'] == 'Max B')
        self.assertEqual((seat['state'], seat['detail'], seat['credits']['policy'], seat.get('reserve', False)),
                         ('parked', LAST_RESORT_DETAIL, 'last-resort', False))

    def test_spec_and_docs_describe_it(self):
        spec, docs = SPEC.read_text(), DOCS.read_text()
        self.assertIn(ITEM, spec)
        self.assertIn('`rotation_row`', spec)
        self.assertIn(ITEM, docs)
        self.assertIn('parked by its last-resort policy', docs)


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

if mb is not None:
    from Foundation import NSObject

    class MenuOwner(NSObject):
        """What seat_menu reads of the controller (its model, set per test) and the items' target. PyObjC registers
        an Objective-C class once per process, so it is defined here, not inside a test."""


@unittest.skipIf(mb is None, 'needs PyObjC (run with the menu bar app\'s interpreter, ~/.codexpool/menubar/.venv/bin/python)')
class WithAppKit(unittest.TestCase):
    """The account menu and Settings' In rotation row, on the demo's Claude pool: Max B is the last resort, Pro C
    has its credits off."""

    def setUp(self):
        self.raw = json.loads((ADDON_DEMO / 'claude-status-regular.json').read_text())
        self.now = mb.parse_time(NOW)

    def model(self, raw):
        return mb.build_model(raw, '', [], self.now, pool_name='claude')

    def parked(self, label, detail, reserve=False):
        """The demo with `label` parked by the guard, as claude-status.json has it."""
        raw = copy.deepcopy(self.raw)
        seat = next(s for s in raw['seats'] if s['label'] == label)
        seat['state'], seat['detail'], seat['until'] = 'parked', detail, '2026-09-27T00:00:00Z'
        if reserve:
            seat['reserve'] = True
        return raw

    @staticmethod
    def seat(m, label):
        return next(s for s in m.seats if s.label == label)

    def menu(self, m, label):
        """The account's menu as the popover builds it: (title, enabled, tooltip) per item, separators left out."""
        owner = MenuOwner.alloc().init()
        owner.model = m
        menu = mb.Controller.seat_menu(owner, self.seat(m, label))
        return [(str(i.title()), bool(i.isEnabled()), str(i.toolTip() or '')) for i in menu.itemArray()
                if not i.isSeparatorItem()]

    def test_the_demo_reserve_scenario_as_the_guard_wrote_it(self):
        """The real thing: Max B parked by its policy while Max 20x, the reserve, serves."""
        load('codexpool_settings')
        m = self.model(json.loads((ADDON_DEMO / 'claude-status-reserve.json').read_text()))
        seat = self.seat(m, 'Max B')
        self.assertTrue(ext.last_resort_parked(seat))
        self.assertIn((ITEM, False, TIP), self.menu(m, 'Max B'))
        self.assertEqual(ext.rotation_row(seat), (SUB, False))
        self.assertEqual(ext.credits_line(seat), ('Credits · $31 of $150 cap', False))   # the row, as before

    def test_a_parked_last_resort_account_has_no_enable(self):
        m = self.model(self.parked('Max B', LAST_RESORT_DETAIL))
        seat = self.seat(m, 'Max B')
        self.assertTrue(ext.last_resort_parked(seat))
        items = self.menu(m, 'Max B')
        titles = [t for t, _, _ in items]
        self.assertNotIn(ENABLE, titles)
        self.assertNotIn('Enable', titles)
        self.assertNotIn('Disable', titles)
        self.assertIn((ITEM, False, TIP), items)
        self.assertIn('Make first', titles)   # the rest of the menu is as before
        self.assertIn('Open claude.ai usage page', titles)

    def test_the_reserve_as_last_resort_says_so_without_the_reserve(self):
        m = self.model(self.parked('Max B', LAST_RESORT_DETAIL, reserve=True))
        line = next(i for i in self.menu(m, 'Max B') if i[0] == ITEM)
        self.assertEqual(line, (ITEM, False, ext.last_resort_tip('Max B', reserve=True)))

    def test_a_credits_off_park_keeps_enable(self):
        m = self.model(self.parked('Pro C', CREDITS_OFF_DETAIL))
        seat = self.seat(m, 'Pro C')
        self.assertFalse(ext.last_resort_parked(seat))
        items = self.menu(m, 'Pro C')
        self.assertIn((ENABLE, True, ''), items)
        self.assertNotIn(ITEM, [t for t, _, _ in items])

    def test_ready_and_disabled_accounts_are_as_before(self):
        m = self.model(self.raw)
        self.assertIn(('Disable', True, ''), self.menu(m, 'Max B'))   # ready, last resort: no park to explain
        raw = copy.deepcopy(self.raw)
        next(s for s in raw['seats'] if s['label'] == 'Max B')['state'] = 'disabled'
        self.assertIn(('Enable', True, ''), self.menu(self.model(raw), 'Max B'))

    def test_settings_row_is_off_and_says_why(self):
        load('codexpool_settings')
        parked = self.model(self.parked('Max B', LAST_RESORT_DETAIL))
        self.assertEqual(ext.rotation_row(self.seat(parked, 'Max B')), (SUB, False))
        reserve = self.model(self.parked('Max B', LAST_RESORT_DETAIL, reserve=True))
        sub, live = ext.rotation_row(self.seat(reserve, 'Max B'))
        self.assertFalse(live)
        self.assertNotIn('reserve included', sub)
        off = self.model(self.parked('Pro C', CREDITS_OFF_DETAIL))
        self.assertEqual(ext.rotation_row(self.seat(off, 'Pro C')),
                         ('Parked by the guard: its plan limit is used up, and more would spend credits.', True))
        plain = self.model(self.raw)
        self.assertEqual(ext.rotation_row(self.seat(plain, 'Max B')),
                         ('Takes new sessions in its turn in the fill order.', True))


if __name__ == '__main__':
    unittest.main()
