"""The pooled Claude desktop app in the menu bar app and Settings (addons/sienna/menubar_ext.py's Desktop block:
parse_desktop, desktop_shown, desktop_view, desktop_refusal, desktop_confirm, desktop_command, desktop_closing,
desktop_tip_suffix, the Claude tab's Desktop row and the pool-down banner, the Desktop rows in Settings' Using it
and desktop_offer on the Setup assistant's Done step, all drawn by the core apps through the PoolUI): claude-status.json's pool.desktop, written by
the guard (docs/DESKTOP.md, DESKTOP_3P.md §7). The control keeps configured_mode (the app's files) and running_mode
(the running app's own log) apart: "On the pool" only once the app has logged the pool's address. Choosing the
other segment asks first, then runs `subpool claude desktop pooled|claudeai --relaunch --yes`; what subpool
would refuse (credits on at claude.ai, an interrupted change, an edited "Pool" entry) is said first, in its words.

The copy and state helpers are pure functions over the block, so they run on every Python here, loaded from the
source without importing the module (which needs PyObjC). The popover's row, the banner and Settings need PyObjC,
that is the menu bar's own interpreter (~/.subpool/menubar/.venv/bin/python), and skip without it. The README's
demo data has no desktop block, so the control is not drawn there and docs/images/demo/render.py's output stays
byte-identical."""
import __future__
import ast
import copy
import importlib
import json
import sys
import unittest
from dataclasses import dataclass, field

from _helpers import REPO

MENUBAR = REPO / 'menubar' / 'subpool_menubar.py'
EXT = REPO / 'addons' / 'sienna' / 'menubar_ext.py'
SETTINGS = REPO / 'menubar' / 'subpool_settings.py'
SPEC = REPO / 'addons' / 'sienna' / 'docs' / 'SPEC.md'
DOCS = REPO / 'addons' / 'sienna' / 'docs' / 'MENUBAR.md'
DESKTOP_DOCS = REPO / 'addons' / 'sienna' / 'docs' / 'DESKTOP.md'
DEMO = REPO / 'docs' / 'images' / 'demo'
ADDON_DEMO = REPO / 'addons' / 'sienna' / 'docs' / 'images' / 'demo'
NOW = '2026-09-24T16:41:00Z'
PURE = ('parse_desktop', 'desktop_shown', 'desktop_credit_labels', 'join_labels', 'desktop_credit_lines',
        'desktop_view', 'desktop_refusal', 'desktop_confirm', 'desktop_command', 'desktop_busy_text',
        'desktop_closing', 'desktop_tip_suffix', 'money')
CLASSES = ('Desktop', 'DesktopView')
CONSTANTS = ('DESKTOP_MODES', 'DESKTOP_RUNNING', 'DESKTOP_LABELS', 'DESKTOP_APP_MISSING')
CREDITS_ON = ('credits on at claude.ai for Max B: turn them off there (Settings → Usage). subpool can\'t stop every '
              'paid request.')
STALE = 'no fresh credits reading for Pro C: wait for the guard\'s next poll (subpool sienna status)'
ON_THE_POOL = 'On the pool · Chat, Cowork and Code · history in Claude-3p'

BLOCK = {   # the guard's block for a pooled app that logged the pool's address (DESKTOP_3P.md §4.6)
    'configured_mode': 'pooled', 'chooser_disabled': False, 'running_mode': 'pooled', 'running_host': '127.0.0.1:8321',
    'app_running': True, 'app_started_at': '2026-09-24T15:12:49-07:00', 'app_bundle_ok': True,
    'restart_required': False, 'ours': True, 'current': True, 'owned_drift': False, 'app_version': '2.9939.2',
    'applied_name': 'Pool', 'base_url': 'http://127.0.0.1:8321', 'port_ok': True, 'credits_ok': True,
    'credits_problems': [], 'accepted_credits': [], 'txn_pending': False, 'txn_classes': None,
    'pool_seen_desktop_at': None, 'old_3p_logs': None, 'checked_at': NOW, 'last_backup': None, 'errors': [],
    'owned': {'inferenceProvider': 'gateway'},
}


def block(**patch):
    return {**copy.deepcopy(BLOCK), **patch}


def pure_helpers():
    """The Desktop dataclasses, constants and PURE functions as the source defines them, loaded on their own."""
    tree = ast.parse(EXT.read_text(), str(EXT))
    body = [n for n in tree.body if isinstance(n, ast.ClassDef) and n.name in CLASSES]
    body += [n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
             and n.targets[0].id in CONSTANTS]
    body += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in PURE]
    wanted = set(PURE) | set(CLASSES) | set(CONSTANTS)
    names = {getattr(n, 'name', None) or n.targets[0].id for n in body}
    assert names == wanted, f'{EXT.name} lacks {sorted(wanted - names)}'
    ns = {'dataclass': dataclass, 'field': field}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(EXT), 'exec',
                 __future__.annotations.compiler_flag, True), ns)
    return ns


class Parse(unittest.TestCase):
    """pool.desktop -> Desktop, on any Python."""

    def setUp(self):
        self.ns = pure_helpers()

    def test_no_block_no_desktop(self):
        """An older guard's file has no block: nothing to draw (the README's demo data is such a file)."""
        parse = self.ns['parse_desktop']
        self.assertIsNone(parse(None))
        self.assertIsNone(parse('pooled'))
        for path in sorted(DEMO.glob('claude-status-*.json')):
            self.assertNotIn('desktop', json.loads(path.read_text()).get('pool', {}), path.name)

    def test_fields(self):
        d = self.ns['parse_desktop'](block(pool_seen_desktop_at='2026-09-24T15:14:02-07:00',
                                          accepted_credits=[{'label': 'Max 20x', 'cap': 150}],
                                          owned={'claudeAiImport': {'enabled': True}}))
        self.assertEqual((d.configured_mode, d.running_mode, d.app_running, d.restart_required),
                         ('pooled', 'pooled', True, False))
        self.assertEqual(d.pool_seen_desktop_at, '2026-09-24T15:14:02-07:00')
        self.assertEqual(d.accepted_credits, [{'label': 'Max 20x', 'cap': 150}])
        self.assertTrue(d.import_on)
        self.assertIs(d.credits_ok, True)

    def test_odd_values_never_raise(self):
        d = self.ns['parse_desktop']({'configured_mode': 'weird', 'running_mode': 7, 'credits_ok': 'yes',
                                     'credits_problems': [1, 'x'], 'accepted_credits': ['no', {'cap': 1}],
                                     'errors': None, 'owned': 'nope'})
        self.assertEqual((d.configured_mode, d.running_mode, d.credits_ok), ('unknown', 'unknown', None))
        self.assertEqual((d.credits_problems, d.accepted_credits, d.errors, d.import_on), (['x'], [], [], False))
        self.assertIsNone(self.ns['parse_desktop'](block(running_mode=None)).running_mode)   # not running

    def test_shown_only_with_the_app(self):
        parse, shown = self.ns['parse_desktop'], self.ns['desktop_shown']
        self.assertTrue(shown(parse(block())))
        self.assertFalse(shown(None))
        self.assertFalse(shown(parse(block(app_version=None, configured_mode='none',
                                           errors=['Claude app not found: install it from claude.ai/download']))))


class View(unittest.TestCase):
    """The control's state, per the table in DESKTOP_3P.md §7.1: honest about configured vs running."""

    def setUp(self):
        self.ns = pure_helpers()

    def view(self, busy=None, **patch):
        return self.ns['desktop_view'](self.ns['parse_desktop'](block(**patch)), busy)

    def test_on_the_pool_only_with_the_log_s_word(self):
        v = self.view()
        self.assertEqual((v.selected, v.enabled, v.caption, v.action, v.lines), ('pooled', True, ON_THE_POOL, None, []))
        self.assertEqual(self.view(pool_seen_desktop_at='2026-09-24T15:14:02-07:00').caption,
                         ON_THE_POOL + ' · answering')
        self.assertEqual(self.view(running_mode='3p').caption,
                         'In pooled mode · the pool’s address is confirmed on the first request')
        self.assertEqual(self.view(running_mode='unknown').caption,
                         'Configured for the pool · nothing seen from the app yet')
        self.assertEqual(self.view(running_mode=None, app_running=False).caption,
                         'Configured for the pool · opens on the pool next time')

    def test_restart_required_says_reopen(self):
        v = self.view(running_mode='unknown', restart_required=True)
        self.assertEqual((v.caption, v.action), ('Configured for the pool · reopen Claude to switch', 'reopen'))
        v = self.view(configured_mode='claudeai', running_mode='unknown', restart_required=True)
        self.assertEqual((v.selected, v.caption, v.action), ('claudeai', 'Configured for Claude.ai · reopen Claude to '
                                                                         'switch', 'reopen'))

    def test_wrong_address_and_fallback_send_to_doctor(self):
        self.assertEqual((self.view(running_mode='other').caption, self.view(running_mode='other').action),
                         ('Running on another address, not the pool', 'doctor'))
        self.assertEqual((self.view(running_mode='fallback').caption, self.view(running_mode='fallback').action),
                         ('Claude fell back to standard mode', 'doctor'))
        v = self.view(app_bundle_ok=False, running_mode='unknown')
        self.assertEqual((v.caption, v.action), ('A process named Claude runs from another path', 'doctor'))

    def test_claudeai(self):
        v = self.view(configured_mode='claudeai', running_mode='claudeai')
        self.assertEqual((v.selected, v.caption, v.action, v.lines), ('claudeai', 'On its own claude.ai account', None, []))
        self.assertEqual(self.view(configured_mode='claudeai', running_mode=None, app_running=False).caption,
                         'Configured for Claude.ai')
        self.assertEqual(self.view(configured_mode='claudeai', running_mode='unknown').caption,
                         'Configured for Claude.ai · nothing seen from the app yet')
        v = self.view(configured_mode='claudeai', running_mode='3p')   # the mode cache: the process kept its mode
        self.assertEqual((v.caption, v.action), ('Configured for Claude.ai · the running app is still in pooled mode',
                                                 'reopen'))

    def test_neither_greyed_and_not_set_up(self):
        v = self.view(configured_mode='other', applied_name='Work gateway')
        self.assertEqual((v.selected, v.enabled, v.caption, v.action),
                         (None, True, 'Another configuration is applied in the app', None))
        v = self.view(configured_mode='other', chooser_disabled=True)
        self.assertEqual((v.selected, v.caption, v.action),
                         (None, 'Another configuration is applied in the app · it hides the Claude.ai sign-in', 'doctor'))
        v = self.view(configured_mode='none')
        self.assertEqual((v.selected, v.enabled, v.caption, v.action), ('claudeai', True, 'Not set up', 'setup'))
        v = self.view(configured_mode='unknown')
        self.assertEqual((v.selected, v.enabled, v.caption, v.action),
                         (None, False, 'Can’t read the app’s settings', 'doctor'))
        v = self.view(txn_pending=True, txn_classes={'mode': 'written', 'entry': 'foreign', 'meta': 'written'})
        self.assertEqual((v.selected, v.enabled, v.caption, v.action), ('pooled', False, 'A change was interrupted',
                                                                        'doctor'))
        v = self.view(busy='Switching Claude to the pool…')
        self.assertEqual((v.enabled, v.caption, v.action), (False, 'Switching Claude to the pool…', None))

    def test_credit_lines(self):
        v = self.view(credits_ok=False, credits_problems=[CREDITS_ON, STALE])
        self.assertEqual(v.lines, [('Credits on at claude.ai for Max B · the pooled app can spend them', True)])
        v = self.view(credits_ok=False, credits_problems=[STALE])
        self.assertEqual(v.lines, [('No fresh credits reading for Pro C · wait for the guard’s next poll', True)])
        v = self.view(credits_ok=False, credits_problems=['no account can serve'])
        self.assertEqual(v.lines, [('No account can serve the desktop app', True)])
        v = self.view(accepted_credits=[{'label': 'Max 20x', 'cap': 150}])
        self.assertEqual(v.lines, [('Paid use accepted on Max 20x, up to $150 · the pooled app can spend credits', False)])
        self.assertEqual(self.ns['join_labels'](['Max A', 'Max B', 'Pro C']), 'Max A, Max B and Pro C')
        # only a pooled desktop warns: on Claude.ai the pooled app cannot spend anything
        self.assertEqual(self.view(configured_mode='claudeai', running_mode='claudeai', credits_ok=False,
                                   credits_problems=[CREDITS_ON]).lines, [])

    def test_edited_and_out_of_date_pool_entry(self):
        v = self.view(owned_drift=True, current=False)
        self.assertEqual(v.lines, [('The “Pool” configuration was edited in the app', False)])
        self.assertEqual(v.action, 'doctor')
        v = self.view(current=False)
        self.assertEqual(v.lines, [('The “Pool” configuration is out of date', False)])
        v = self.view(current=False, port_ok=False)
        self.assertEqual(v.lines, [('The “Pool” configuration points at another port', False)])
        v = self.view(current=False, restart_required=True, running_mode='unknown')
        self.assertEqual(v.action, 'reopen')   # the reopen comes first; doctor is a click away in the footer


class Actions(unittest.TestCase):
    """What a click does: the refusals said first, the confirm, the command, the closing line, the tooltip."""

    def setUp(self):
        self.ns = pure_helpers()

    def desktop(self, **patch):
        return self.ns['parse_desktop'](block(**patch))

    def test_refusals_in_subpool_s_words(self):
        refuse = self.ns['desktop_refusal']
        self.assertEqual(refuse(self.desktop(), 'pooled'), '')
        self.assertEqual(refuse(self.desktop(), 'claudeai'), '')
        why = refuse(self.desktop(credits_ok=False, credits_problems=[CREDITS_ON, STALE]), 'pooled')
        self.assertEqual(why.splitlines(), ['Credits on at claude.ai for Max B: turn them off there (Settings → Usage). '
                                            'subpool can\'t stop every paid request.',
                                            'No fresh credits reading for Pro C: wait for the guard\'s next poll '
                                            '(subpool sienna status)'])
        self.assertIn('--reclaim', refuse(self.desktop(owned_drift=True), 'pooled'))
        self.assertIn('rollback', refuse(self.desktop(txn_pending=True), 'claudeai'))
        self.assertIn('can’t answer', refuse(self.desktop(), 'pooled', pool_down=True))
        self.assertEqual(refuse(self.desktop(), 'claudeai', pool_down=True), '')   # going back is the way out
        self.assertIn('hides the Claude.ai sign-in',
                      refuse(self.desktop(configured_mode='other', chooser_disabled=True, applied_name='Managed'),
                             'claudeai'))
        self.assertIsNone(self.desktop(credits_ok=None).credits_ok)
        self.assertEqual(refuse(self.desktop(credits_ok=None), 'pooled'), '')   # unknown: the command decides

    def test_confirms(self):
        confirm = self.ns['desktop_confirm']
        title, body, ok = confirm('pooled', self.desktop())
        self.assertEqual((title, ok), ('Switch the desktop app to the pool?', 'Switch'))
        self.assertTrue(body.startswith('Claude quits and opens again on the pool.'))
        self.assertIn('Finish what you are doing in Claude first.', body)
        self.assertIn('Mobile and web sync, cloud Cowork, Remote Control and Claude in Chrome stay', body)
        _, body, _ = confirm('pooled', self.desktop(app_running=False, running_mode=None))
        self.assertTrue(body.startswith('Claude opens on the pool.'))
        self.assertNotIn('Finish', body)
        title, body, ok = confirm('claudeai', self.desktop())
        self.assertEqual((title, ok), ('Go back to Claude.ai?', 'Go Back'))
        self.assertIn('Pooled conversations stay in the pool history.', body)
        self.assertIn('“Go back to Claude.ai”', body)
        title, body, ok = confirm('reopen', self.desktop())
        self.assertEqual((title, ok), ('Reopen Claude now?', 'Reopen'))
        title, body, ok = confirm('import', self.desktop())
        self.assertEqual((title, ok), ('Import claude.ai history?', 'Import'))
        self.assertIn('stores that sign-in in the pooled profile', body)

    def test_commands_always_relaunch_and_never_override(self):
        cmd = self.ns['desktop_command']
        self.assertEqual(cmd('pooled'), ['claude', 'desktop', 'pooled', '--relaunch', '--yes'])
        self.assertEqual(cmd('claudeai'), ['claude', 'desktop', 'claudeai', '--relaunch', '--yes'])
        self.assertEqual(cmd('reopen'), ['claude', 'desktop', 'relaunch', '--yes'])
        self.assertEqual(cmd('import'), ['claude', 'desktop', 'pooled', '--import', '--relaunch', '--yes'])
        for target in ('pooled', 'claudeai', 'reopen', 'import'):
            for flag in ('--reclaim', '--force-with-credits', '--delete-edited'):
                self.assertNotIn(flag, cmd(target))

    def test_closing_line(self):
        closing = self.ns['desktop_closing']
        out = '\n[1/7] Check the ground\n  ✓ ok\nSet up. Open Claude and it runs on the pool.\nClaude opened on the pool.\n'
        self.assertEqual(closing(out, 0, ''), 'Claude opened on the pool')
        self.assertEqual(closing('', 0, ''), 'Done')
        self.assertEqual(closing('', 1, '✗ Claude did not quit; finish what it is doing and try again'),
                         'Claude did not quit; finish what it is doing and try again')
        self.assertEqual(closing('', 3, ''), 'subpool claude desktop failed (exit 3)')
        self.assertEqual(self.ns['desktop_busy_text']('pooled'), 'Switching Claude to the pool…')
        self.assertEqual(self.ns['desktop_busy_text']('reopen'), 'Reopening Claude…')

    def test_tooltip_suffix(self):
        tip = self.ns['desktop_tip_suffix']
        self.assertEqual(tip(self.desktop()), ' · desktop pooled')
        self.assertEqual(tip(self.desktop(restart_required=True)), ' · desktop switch pending')
        self.assertEqual(tip(self.desktop(configured_mode='claudeai', running_mode='claudeai')), '')
        self.assertEqual(tip(None), '')

    def test_spec_and_docs_describe_it(self):
        spec, docs, desk = SPEC.read_text(), DOCS.read_text(), DESKTOP_DOCS.read_text()
        for text in (spec, docs, desk):
            self.assertIn('Pooled | Claude.ai', text)
            self.assertIn('--relaunch --yes', text)
        self.assertIn(ON_THE_POOL, spec)
        self.assertIn('reopen Claude to switch', spec)
        self.assertIn('Import claude.ai history', docs)
        self.assertIn('Import claude.ai history', desk)


def load(name):
    """The menu bar app or the Settings window as a module; None without PyObjC."""
    try:
        import AppKit  # noqa: F401
    except ImportError:
        return None
    if str(MENUBAR.parent) not in sys.path:
        sys.path.insert(0, str(MENUBAR.parent))
    return importlib.import_module(name)


mb = load('subpool_menubar')
ext = mb.POOL_UI['claude'].module if mb is not None else None   # the add-on's menubar_ext.py, as mb loaded it


@unittest.skipIf(mb is None, 'needs PyObjC (run with the menu bar app\'s interpreter, ~/.subpool/menubar/.venv/bin/python)')
class WithAppKit(unittest.TestCase):
    """The Claude tab's Desktop row, the pool-down banner, the tooltips and Settings, on the demo's Claude pool with
    a desktop block added."""

    def setUp(self):
        self.raw = json.loads((ADDON_DEMO / 'claude-status-regular.json').read_text())
        self.down = json.loads((ADDON_DEMO / 'claude-status-down.json').read_text())
        self.now = mb.parse_time(NOW)

    def model(self, raw, desktop=None):
        raw = copy.deepcopy(raw)
        if desktop is not None:
            raw['pool']['desktop'] = desktop
        return mb.build_model(raw, '', [], self.now, pool_name='claude')

    @staticmethod
    def keys(layout, kind):
        return [key for _, key in layout.regions if key[0] == kind]

    def test_model_carries_the_block(self):
        self.assertIsNone(self.model(self.raw).extra.desktop)
        d = self.model(self.raw, block()).extra.desktop
        self.assertEqual((d.configured_mode, d.running_mode), ('pooled', 'pooled'))
        codex = mb.build_model(json.loads((DEMO / 'status-regular.json').read_text()), '', [], self.now)
        self.assertIsNone(codex.extra)

    def test_row_only_with_the_block_and_the_app(self):
        plain = mb.PopoverLayout(self.model(self.raw)).build()
        self.assertEqual(self.keys(plain, 'desktop'), [])
        shown = mb.PopoverLayout(self.model(self.raw, block())).build()
        self.assertEqual(self.keys(shown, 'desktop'), [('desktop', 'pooled'), ('desktop', 'claudeai')])
        self.assertGreater(shown.height, plain.height)
        self.assertIn('Claude-3p', shown.tips[('desktop', 'pooled')])
        gone = mb.PopoverLayout(self.model(self.raw, block(app_version=None, configured_mode='none', errors=[
            'Claude app not found: install it from claude.ai/download']))).build()
        self.assertEqual(self.keys(gone, 'desktop'), [])
        self.assertEqual(gone.height, plain.height)

    def test_row_actions(self):
        reopen = mb.PopoverLayout(self.model(self.raw, block(running_mode='unknown', restart_required=True))).build()
        self.assertEqual(self.keys(reopen, 'desktop'), [('desktop', 'pooled'), ('desktop', 'claudeai'),
                                                        ('desktop', 'reopen')])
        self.assertIn('asks first', reopen.tips[('desktop', 'reopen')])
        setup = mb.PopoverLayout(self.model(self.raw, block(configured_mode='none', running_mode='unknown'))).build()
        self.assertIn(('desktop', 'setup'), self.keys(setup, 'desktop'))
        doctor = mb.PopoverLayout(self.model(self.raw, block(running_mode='fallback'))).build()
        self.assertIn(('desktop', 'doctor'), self.keys(doctor, 'desktop'))
        busy = mb.PopoverLayout(self.model(self.raw, block()), busy='Switching Claude to the pool…').build()
        self.assertEqual(self.keys(busy, 'desktop'), [('desktop', 'pooled'), ('desktop', 'claudeai')])

    def test_warning_lines_take_room(self):
        plain = mb.PopoverLayout(self.model(self.raw, block())).build()
        warned = mb.PopoverLayout(self.model(self.raw, block(credits_ok=False, credits_problems=[CREDITS_ON]))).build()
        self.assertGreater(warned.height, plain.height)

    def test_pool_down_banner_offers_the_way_back(self):
        m = self.model(self.down, block(running_mode='3p'))
        self.assertEqual(m.status, 'down')
        title, body, buttons = mb.PopoverLayout(m).banner_copy()
        self.assertEqual(title, 'Claude pool is down')
        self.assertIn('desktop app is pooled', body)
        self.assertEqual(buttons, [('Run Doctor', 'doctor'), ('Back to Claude.ai', ('desktop', 'claudeai'))])
        layout = mb.PopoverLayout(m).build()
        self.assertIn(('action', 'doctor'), [k for _, k in layout.regions])
        self.assertEqual(self.keys(layout, 'desktop').count(('desktop', 'claudeai')), 2)   # the banner and the row
        _, _, single = mb.PopoverLayout(self.model(self.down)).banner_copy()   # no block: the banner as before
        self.assertEqual(single, ('Run Doctor', 'doctor'))
        _, _, single = mb.PopoverLayout(self.model(self.down, block(configured_mode='claudeai',
                                                                    running_mode='claudeai'))).banner_copy()
        self.assertEqual(single, ('Run Doctor', 'doctor'))

    def test_tooltip_and_signature(self):
        plain, pooled = self.model(self.raw), self.model(self.raw, block())
        pending = self.model(self.raw, block(running_mode='unknown', restart_required=True))
        self.assertEqual(mb.pool_line(pooled), mb.pool_line(plain) + ' · desktop pooled')
        self.assertEqual(mb.pool_line(pending), mb.pool_line(plain) + ' · desktop switch pending')
        self.assertNotEqual(mb.menubar_signature(plain), mb.menubar_signature(pooled))   # the strip's tooltip
        self.assertNotEqual(mb.menubar_signature(pooled), mb.menubar_signature(pending))
        self.assertEqual(mb.headline_text(plain), mb.headline_text(pooled))   # nothing else in the item moves
        self.assertEqual(mb.pool_severity(plain), mb.pool_severity(pooled))

    def test_settings_done_step_offer(self):
        load('subpool_settings')
        offer = ext.desktop_offer
        self.assertIsNone(offer(self.model(self.raw)))
        self.assertIsNone(offer(self.model(self.raw, block())))   # already pooled: nothing to offer
        text, go = offer(self.model(self.raw, block(configured_mode='claudeai', running_mode='claudeai')))
        self.assertTrue(go)
        self.assertTrue(text.startswith('Also run the Claude desktop app on the pool?'))
        text, go = offer(self.model(self.raw, block(configured_mode='claudeai', running_mode='claudeai',
                                                    credits_ok=False, credits_problems=[CREDITS_ON])))
        self.assertFalse(go)
        self.assertTrue(text.startswith('Turn credits off at claude.ai for Max B first'))
        text, go = offer(self.model(self.raw, block(credits_ok=False, credits_problems=[CREDITS_ON])))
        self.assertFalse(go)
        self.assertIn('pooled desktop app can spend them', text)
        self.assertIsNone(offer(self.model(self.raw, block(configured_mode='claudeai', running_mode='claudeai',
                                                           credits_ok=None))))

    def test_settings_row_builds_in_every_state(self):
        """The Using it card with the Desktop rows, built as the window builds it (no window shown, no command)."""
        st = load('subpool_settings')
        for patch in ({}, dict(running_mode='unknown', restart_required=True), dict(configured_mode='none'),
                      dict(txn_pending=True), dict(configured_mode='unknown'),
                      dict(credits_ok=False, credits_problems=[CREDITS_ON]),
                      dict(configured_mode='other', chooser_disabled=True),
                      dict(owned={'claudeAiImport': {'enabled': True}})):
            store = st.Store(DEMO / 'status-regular.json', self.now, None, None, None)
            src = store.sources['claude'] = mb.DataSource(ADDON_DEMO / 'claude-status-regular.json', None, pool='claude')
            src.poll(force=True)
            src.raw['pool']['desktop'] = block(**patch)
            pane = st.OverviewPane(FakeApp(store))
            pane.ext['desktop_more'] = True
            rows = ext.desktop_rows(pane, store.model('claude'), False)
            self.assertGreaterEqual(len(rows), 2, patch)


class FakeApp:
    """What OverviewPane.desktop_rows reads of the controller."""
    pool = 'claude'

    def __init__(self, store):
        self.store = store

    def rebuild(self, pane):
        pass

    def show_pane(self, key):
        pass


if __name__ == '__main__':
    unittest.main()
