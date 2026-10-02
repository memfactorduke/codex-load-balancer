"""The read-only Claude lane (lane provider `sienna`, kind engine, picker label "Claude"; docs/LANES.md, Engine
members) in the Settings window's Lanes pane and lane editor (menubar/subpool_settings.py's PROVIDERS,
LANE_STATE, ENGINE_STATE, engine_state_text, member_line, infer_ready, credentials and the Add Model sheet's
lines, with the engine's tables and words from addons/sienna/menubar_ext.py's LANE_PROVIDERS, LANE_STATE,
ENGINE_STATE, ENGINE_HINT and ENGINE_COPY): the provider is named Claude, its members say read-only, its state is the engine's (accepted once, by
version: `subpool lane apply --accept-engine`), and the Credentials row offers Accept Engine… with that command
streaming into a sheet. The xai and bridge providers are as before.

The tables and the copy helpers are pure, so they run on every Python here, loaded from the source without
importing the module (which needs PyObjC). The pane's rows and the sheets need PyObjC, that is the menu bar's own
interpreter (~/.subpool/menubar/.venv/bin/python), and skip without it. The README's demo lanes have no engine
member, so docs/images/demo/render.py's output stays byte-identical."""
import __future__
import ast
import copy
import importlib
import json
import re
import sys
import types
import unittest

from _helpers import REPO

MENUBAR = REPO / 'menubar' / 'subpool_menubar.py'
EXT = REPO / 'addons' / 'sienna' / 'menubar_ext.py'
SETTINGS = REPO / 'menubar' / 'subpool_settings.py'
SPEC = REPO / 'addons' / 'sienna' / 'docs' / 'SPEC.md'
DOCS = REPO / 'addons' / 'sienna' / 'docs' / 'MENUBAR.md'
DEMO = REPO / 'docs' / 'images' / 'demo'
ADDON_DEMO = REPO / 'addons' / 'sienna' / 'docs' / 'images' / 'demo'
NOW = '2026-09-24T16:41:00Z'
PURE = ('plain_detail', 'engine_state_text', 'member_line', 'infer_ready')
CONSTANTS = ('PROVIDERS', 'LANE_STATE', 'ENGINE_STATE', 'ENGINE_HINT', 'CLI_HINT')
UNTESTED = 'untested engine: subpool lane apply --accept-engine'
ENGINE_STATES = ('engine ok', 'untested engine', 'no engine', 'no launcher', 'pool down', 'no profile')


EXT_CONSTANTS = ('LANE_PROVIDERS', 'LANE_STATE', 'ENGINE_STATE', 'ENGINE_HINT')


def assigns(tree, names):
    return [n for n in tree.body if isinstance(n, ast.Assign) and isinstance(n.targets[0], ast.Name)
            and n.targets[0].id in names]


def pure_helpers():
    """The core's tables and helpers, with the add-on's engine entries merged in as the Settings window does it
    (PoolUI.settings_loaded), and the core's PROVIDERS built as it is, from the add-on's LANE_PROVIDERS."""
    ext = ast.parse(EXT.read_text(), str(EXT))
    ext_ns = {}
    exec(compile(ast.Module(body=assigns(ext, EXT_CONSTANTS), type_ignores=[]), str(EXT), 'exec',
                 __future__.annotations.compiler_flag, True), ext_ns)
    assert set(ext_ns) >= set(EXT_CONSTANTS), f'{EXT.name} lacks {sorted(set(EXT_CONSTANTS) - set(ext_ns))}'
    tree = ast.parse(SETTINGS.read_text(), str(SETTINGS))
    body = assigns(tree, CONSTANTS)
    body += [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in PURE]
    wanted = set(PURE) | set(CONSTANTS)
    names = {getattr(n, 'name', None) or n.targets[0].id for n in body}
    assert names == wanted, f'{SETTINGS.name} lacks {sorted(wanted - names)}'
    ui = types.SimpleNamespace(lane_providers=ext_ns['LANE_PROVIDERS'])
    ns = {'re': re, 'mb': types.SimpleNamespace(POOL_UI={'claude': ui})}
    exec(compile(ast.Module(body=body, type_ignores=[]), str(SETTINGS), 'exec',
                 __future__.annotations.compiler_flag, True), ns)
    for name in ('LANE_STATE', 'ENGINE_STATE', 'ENGINE_HINT'):
        ns[name].update(ext_ns[name])
    return ns


def provider(needs='engine', title='Claude'):
    return types.SimpleNamespace(id='sienna', title=title, needs=needs)


def member(pid, state):
    return types.SimpleNamespace(provider=pid, state=state)


class Tables(unittest.TestCase):
    """The words, on any Python."""

    def setUp(self):
        self.ns = pure_helpers()

    def test_claude_is_a_provider_the_window_knows_before_asking(self):
        providers = {pid: (title, needs, key) for pid, title, needs, key in self.ns['PROVIDERS']}
        self.assertEqual(providers['sienna'], ('Claude', 'engine', None))
        self.assertEqual(providers['xai'], ('xAI', 'login', None))          # untouched
        self.assertEqual(providers['opencode-go'], ('OpenCode Go', 'key', 'opencode-go'))

    def test_engine_states_have_pills(self):
        states = self.ns['LANE_STATE']
        for state in ENGINE_STATES:
            self.assertIn(state, states)
        self.assertEqual(states['engine ok'], ('Engine OK', 'green'))
        self.assertEqual(states['untested engine'], ('Not accepted', 'orange'))
        self.assertEqual(states['ready'], ('Ready', 'green'))                 # xai as before
        self.assertEqual(states['bridge ok'], ('Bridge OK', 'green'))

    def test_engine_state_text(self):
        f = self.ns['engine_state_text']
        self.assertEqual(f(UNTESTED), 'Not accepted yet')
        self.assertEqual(f('engine ok'), 'Accepted')
        self.assertEqual(f('no engine: install Claude Code first'), 'Claude Code isn’t installed')
        self.assertEqual(f('pool down: subpool claude status ; subpool doctor'), 'The Claude pool is down')
        self.assertEqual(f('something else: subpool doctor'), 'Something else')
        self.assertEqual(f(''), 'Not ready')
        self.assertIn('--accept-engine', self.ns['ENGINE_HINT']['untested engine'])

    def test_member_line_says_read_only(self):
        f = self.ns['member_line']
        self.assertEqual(f(provider(), 'claude-opus-5-5'), 'Claude · claude-opus-5-5 · read-only')
        self.assertEqual(f(provider('login', 'xAI'), 'grok-4.7-build-fast'), 'xAI · grok-4.7-build-fast')

    def test_infer_ready_for_an_engine(self):
        f = self.ns['infer_ready']
        lanes = [types.SimpleNamespace(members=[member('sienna', 'untested engine'), member('xai', 'ready')])]
        self.assertFalse(f('sienna', lanes, 'engine'))
        self.assertTrue(f('xai', lanes))
        lanes = [types.SimpleNamespace(members=[member('sienna', 'engine ok')])]
        self.assertTrue(f('sienna', lanes, 'engine'))
        self.assertIsNone(f('sienna', [], 'engine'))

    def test_spec_and_docs_describe_it(self):
        spec, docs = SPEC.read_text(), DOCS.read_text()
        for text in (spec, docs):
            self.assertIn('Accept Engine', text)
            self.assertIn('--accept-engine', text)
            self.assertIn('read-only', text)


def load(name):
    try:
        import AppKit  # noqa: F401
    except ImportError:
        return None
    if str(MENUBAR.parent) not in sys.path:
        sys.path.insert(0, str(MENUBAR.parent))
    return importlib.import_module(name)


mb = load('subpool_menubar')


@unittest.skipIf(mb is None, 'needs PyObjC (run with the menu bar app\'s interpreter, ~/.subpool/menubar/.venv/bin/python)')
class WithAppKit(unittest.TestCase):
    """The pane's data and the sheets' lines, on the demo lanes with a Claude lane added."""

    def setUp(self):
        self.st = load('subpool_settings')
        lanes = json.loads((DEMO / 'lanes.json').read_text())
        lanes['lanes'].append({'name': 'sienna', 'display': 'Claude', 'effort': 'medium', 'role': 'Review.',
                               'members': [{'id': 'opus', 'provider': 'sienna', 'model': 'claude-opus-5-5',
                                            'name': 'Opus 5.5', 'state': 'untested engine', 'last_test': None}]})
        providers = json.loads((DEMO / 'lane-providers.json').read_text())
        providers['providers'].append({'id': 'sienna', 'title': 'Claude', 'kind': 'engine', 'needs': 'engine',
                                       'key_name': None, 'ready': False, 'detail': UNTESTED})
        self.lanes, self.providers = lanes, providers

    def store(self, lanes=None, providers=None):
        st = self.st
        store = st.Store(DEMO / 'status-regular.json', mb.parse_time(NOW), None, None, None)
        store.lanes = st.parse_lanes(lanes or self.lanes)
        store.providers = st.parse_providers(providers or self.providers)
        return store

    def test_provider_parses_as_an_engine(self):
        p = self.store().provider('sienna')
        self.assertEqual((p.title, p.needs, p.kind, p.ready, p.key_name), ('Claude', 'engine', 'engine', False, None))
        self.assertEqual(self.store().provider('xai').needs, 'login')

    def test_credentials_row(self):
        rows = {c.title: c for c in self.st.credentials(self.store())}
        c = rows['Claude']
        self.assertEqual((c.needs, c.ready, c.uses, c.detail), ('engine', False, ['sienna'], UNTESTED))
        self.assertEqual((rows['xAI'].needs, rows['xAI'].ready), ('login', True))
        self.assertEqual((rows['OpenCode Go'].needs, rows['OpenCode Go'].ready), ('key', True))
        accepted = copy.deepcopy(self.providers)
        accepted['providers'][-1].update(ready=True, detail='engine ok')
        self.assertTrue({c.title: c for c in self.st.credentials(self.store(providers=accepted))}['Claude'].ready)
        # a subpool without `lane providers`: the members' states say
        store = self.store()
        store.providers = None
        self.assertFalse({c.title: c for c in self.st.credentials(store)}['Claude'].ready)

    def test_add_model_lines(self):
        st = self.st
        sheet = st.AddModelSheet.__new__(st.AddModelSheet)
        sheet.loading, sheet.models, sheet.models_note = False, None, ''
        p = self.store().provider('sienna')
        self.assertEqual(sheet.provider_line(p),
                         'Claude Code, read-only, through the Claude pool. Accept the engine once the lane is saved.')
        self.assertEqual(sheet.model_line(p), 'Type a Claude model id the pool serves, e.g. claude-opus-5-5.')
        p.ready = True
        self.assertEqual(sheet.provider_line(p), 'Claude Code, read-only, through the Claude pool. Engine accepted.')
        xai = self.store().provider('xai')
        self.assertEqual(sheet.provider_line(xai), 'Signed in.')

    def test_state_pill_knows_the_engine_states(self):
        for state in ENGINE_STATES:
            self.assertIsNotNone(self.st.state_pill(state))


if __name__ == '__main__':
    unittest.main()
