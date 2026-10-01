#!/usr/bin/env python3
"""codexpool Settings window and Setup assistant.

A native macOS window (Python + PyObjC/AppKit) in the style of System Settings: a sidebar and a content pane
(Overview, Seats, Balancing, Lanes, General, Health, About), plus a Setup assistant that adds ChatGPT accounts. It
runs as its own process, started by the menu bar app ("Settings…", "Add a ChatGPT account…") or by the installer.
The spec is the GUI section of menubar/SPEC.md.

Same rules as the menu bar app: no Keychain, no network, no management API. It reads
~/.codexpool/state/status.json (parsed by the menu bar app's own code, imported from codexpool_menubar.py) and the
JSON that `codexpool doctor --json`, `codexpool lane list --json`, `codexpool lane providers --json`,
`codexpool lane models PROVIDER --json` and `codexpool version` print. Every change is a `codexpool …` command run
in the background, the way the menu bar app runs its actions; the main thread never waits for one. A provider key
typed into the Lanes pane goes to `codexpool lane key NAME -` on its stdin, never in argv or a file.

Run:
    <menubar python> ~/.codexpool/menubar/codexpool_settings.py [--pane NAME]
    NAME: overview | seats | balancing | lanes | general | health | about | setup-welcome | setup-accounts |
          setup-done

Snapshot (no UI shown, no command run; for humans and agents checking the design, and for the docs):
    codexpool_settings.py --snapshot OUT.png --pane NAME --appearance light|dark --status PATH
                          [--doctor PATH] [--lanes PATH] [--providers PATH] [--models PATH] [--now ISO-8601]
                          [--height PT] [--marks drawn|app]
    NAME also takes setup-signin (the sign-in step, with a made-up link), setup-added (a seat just added),
    setup-again (the browser signed in to an account that is already a seat), and the Lanes pane's sheets:
    lanes-edit (the lane editor on the first lane), lanes-new (a new lane), lanes-model (Add Model on a ready
    provider), lanes-model-key (Add Model on a provider that needs a key), lanes-key (Add Key) and lanes-signin
    (the xAI sign-in, with a made-up link).
    --now defaults to the status file's generated_at, so demo data reads as fresh.
    --marks app draws the pool switcher's marks as the live window does, from the pools' apps on this Mac;
    the default, drawn, uses plain drawn shapes and reads nothing from /Applications, so snapshots are reproducible.

Layout of this file:
    1. Paths and constants            6. Panes: Overview, Seats, Balancing, Lanes, General, Health, About
    2. Running codexpool              7. The Settings window (sidebar + content)
    3. Data: status, doctor, lanes    8. Sheets: lane editor, Add Model, Add Key, xAI sign-in
    4. Look: colours, the app icon    9. Setup assistant
    5. Building blocks (views)       10. App controller (single instance, menus, Dock icon)
                                     11. Snapshot mode and main()
"""
from __future__ import annotations

import argparse
import datetime as dt
import fcntl
import json
import math
import os
import re
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

sys.dont_write_bytecode = True   # importing the menu bar app must not leave a __pycache__ in ~/.codexpool/menubar
sys.path.insert(0, str(Path(__file__).resolve().parent))
import codexpool_menubar as mb   # noqa: E402  (the status model, colours and drawing primitives)

import objc  # noqa: E402
from AppKit import (  # noqa: E402
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAlertStyleWarning,
    NSAppearance,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSApplicationActivationPolicyProhibited,
    NSApplicationActivationPolicyRegular,
    NSAttributedString,
    NSBackingStoreBuffered,
    NSBezierPath,
    NSBox,
    NSButton,
    NSColor,
    NSComboBox,
    NSCompositingOperationSourceOver,
    NSControlSizeSmall,
    NSFont,
    NSFontAttributeName,
    NSFontWeightBold,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSGridView,
    NSImage,
    NSImageSymbolConfiguration,
    NSImageView,
    NSLayoutAttributeCenterY,
    NSLayoutAttributeLeading,
    NSLayoutConstraint,
    NSLineBreakByTruncatingMiddle,
    NSLineBreakByTruncatingTail,
    NSMenu,
    NSMenuItem,
    NSNoBorder,
    NSPasteboard,
    NSPasteboardTypeString,
    NSPopUpButton,
    NSProgressIndicator,
    NSProgressIndicatorStyleSpinning,
    NSRunningApplication,
    NSScrollView,
    NSSecureTextField,
    NSSegmentedControl,
    NSShadow,
    NSStackView,
    NSStringDrawingUsesLineFragmentOrigin,
    NSSwitch,
    NSTextAlignmentCenter,
    NSTextAlignmentRight,
    NSTextField,
    NSTextFieldRoundedBezel,
    NSTextView,
    NSToolbar,
    NSView,
    NSVisualEffectBlendingModeBehindWindow,
    NSVisualEffectMaterialSidebar,
    NSVisualEffectStateFollowsWindowActiveState,
    NSVisualEffectView,
    NSViewHeightSizable,
    NSWindow,
    NSWindowStyleMaskClosable,
    NSWindowStyleMaskFullSizeContentView,
    NSWindowStyleMaskMiniaturizable,
    NSWindowStyleMaskResizable,
    NSWindowStyleMaskTitled,
    NSWindowTitleHidden,
    NSWindowToolbarStyleUnified,
    NSWorkspace,
)
from AppKit import NSClipView, NSCursor, NSTerminateLater, NSTerminateNow  # noqa: E402
from Foundation import (  # noqa: E402
    NSAffineTransform,
    NSBundle,
    NSDistributedNotificationCenter,
    NSEdgeInsets,
    NSObject,
    NSPointInRect,
    NSProcessInfo,
    NSRunLoop,
    NSRunLoopCommonModes,
    NSTimer,
    NSUserDefaults,
)
from PyObjCTools import AppHelper  # noqa: E402

# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 1. Paths and constants
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

POOL_DIR = mb.POOL_DIR
STATE_DIR = POOL_DIR / 'state'
PID_FILE = STATE_DIR / 'settings.pid'         # single instance: a locked file holding the running pid
REQUEST_FILE = STATE_DIR / 'settings-request' # the pane a second launch asked for (also sent as a notification)
SETUP_SHOWN = STATE_DIR / 'setup-shown'       # written by the menu bar app when it opens the assistant on first run
LOGS_DIR = POOL_DIR / 'logs'

BUNDLE_ID = 'com.codexpool.settings'
SHOW_NOTE = 'com.codexpool.settings.show'     # a second launch asks the first to show a pane (object: the pane)
CODEX_BUNDLE = 'com.openai.codex'
CHROME_BUNDLE = 'com.google.Chrome'

REPO_URL = 'https://github.com/memfactorduke/codex-load-balancer'
SITE_URL = 'https://memfactorduke.github.io/codex-load-balancer/'
DOCS_URL = REPO_URL + '/tree/main/docs'
LANES_DOCS_URL = REPO_URL + '/blob/main/docs/LANES.md'
ISSUES_URL = REPO_URL + '/issues/new/choose'

LOGIN_TTL_S = 300          # sign-in links expire; the assistant counts down and offers Try Again
NOTE_S = 8                 # how long a success note stays
POLL_EVERY_S = 10          # how often the status file is checked
REOPEN_TIMEOUT_S = 20      # how long Codex gets to quit before we give up
QUIT_WAIT_S = 45           # how long quitting waits for a sign-in that is finishing (codexpool gives the pool 20 s)

PANES = ('overview', 'seats', 'balancing', 'lanes', 'general', 'health', 'about')
SETUP_PANES = ('setup-welcome', 'setup-accounts', 'setup-signin', 'setup-added', 'setup-again', 'setup-done')
LANE_SHOTS = ('lanes-edit', 'lanes-new', 'lanes-model', 'lanes-model-key', 'lanes-model-engine', 'lanes-key',
              'lanes-signin')  # snapshots

# The pools: the Codex pool and, with an add-on (mb.POOL_UI, its menubar_ext.py), one more. The switcher on
# Overview, Seats and Balancing and in the Setup assistant is drawn only when there are two.
POOLS = mb.POOLS
POOL_TITLES = {p: f"{name} {getattr(mb.POOL_UI.get(p), 'product_scope', 'Desktop/CLI')}"
               for p, name in mb.POOL_NAME.items()}
POOL_DEFAULT = 'pool'                # NSUserDefaults key: the switcher's last choice

BALANCING = ('priority', 'reset')    # pool.balancing: your order (fill-first) / soonest weekly reset first
EFFORTS = ('low', 'medium', 'high', 'xhigh')
LANE_NAME_OK = re.compile(r'^[a-z][a-z0-9-]{0,30}$')   # what `codexpool lane add` accepts (it checks again)
DISPLAY_MAX = 40                     # a lane's entry in the Codex model picker
PROVIDERS = (   # what `lane providers --json` says, for a codexpool that can't say it yet: (id, title, needs, key)
    ('xai', 'xAI', 'login', None), ('opencode-go', 'OpenCode Go', 'key', 'opencode-go'),
    ('opencode-zen', 'OpenCode Zen', 'key', 'opencode-zen'), ('responses', 'Responses API', 'key', None)) + \
    tuple(p for ui in mb.POOL_UI.values() for p in getattr(ui, 'lane_providers', ()))   # + an add-on's engine

WIN_W, WIN_H, MIN_H = 820.0, 640.0, 480.0
SIDEBAR_W = 220.0
CONTENT_W = WIN_W - SIDEBAR_W
MARGIN = 24.0                       # content side margin
GROUP_W = CONTENT_W - 2 * MARGIN    # width of every section
BAR_H = 52.0                        # the toolbar band (traffic lights, pane title)
ROW_X = 12.0                        # horizontal padding inside a group row
RADIUS = 10.0                       # group corner radius

SETUP_W, SETUP_H = 640.0, 560.0
SETUP_BODY_W = SETUP_W - 2 * 56.0

SHEET_W = 560.0                     # the lane editor; the smaller sheets are narrower

DEMO_VERSION = '1.2.0'
DEMO_SIGNIN_URL = ('https://auth.openai.com/oauth/authorize?response_type=code&client_id=app_demo'
                   '&redirect_uri=http%3A%2F%2Flocalhost%3A1455%2Fauth%2Fcallback&state=demo')
DEMO_XAI_URL = 'https://auth.x.ai/oauth2/auth?response_type=code&client_id=demo&state=demo'

SNAPSHOT = False           # set in snapshot mode: nothing may run a command, open a URL or touch a file

S = mb.fresh               # every str handed to AppKit goes through this (see codexpool_menubar.fresh)


def pool_ui(pool: str):
    """The add-on's PoolUI for an add-on pool; None for the Codex pool."""
    return mb.POOL_UI.get(pool)


def pool_noun(pool: str) -> str:
    ui = pool_ui(pool)
    return ui.noun if ui else 'seat'


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 2. Running codexpool: in the background, never on the main thread
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

NEWER = 'This needs a newer codexpool. Run the installer again to update it, then try again.'


@dataclass
class Result:
    args: list
    code: int
    out: str
    err: str

    @property
    def ok(self) -> bool:
        return self.code == 0

    @property
    def unknown(self) -> bool:
        """argparse's answer to a command or flag this codexpool doesn't have yet (the NEW ones in the spec)."""
        text = self.err + self.out
        return (self.code == 2 and ('invalid choice' in text or 'unrecognized arguments' in text)) or \
            (self.code != 0 and 'is not one of the settings it changes' in text)   # `set <an add-on setting>`

    def message(self) -> str:
        if self.unknown:
            return NEWER
        lines = [ln.strip() for ln in (self.err or self.out).splitlines() if ln.strip()]
        text = lines[-1] if lines else f'exit {self.code}'
        return text[len('codexpool: '):] if text.startswith('codexpool: ') else text


class Job:
    """`codexpool <args>` in the background. on_line(line) gets each output line (stderr merged into stdout) when
    given; on_done(Result) runs once at the end. Both run on the main thread. stdin: bytes for the command's
    standard input (a provider key for `lane key NAME -`: it never goes into argv or a file); env: extra
    environment variables."""

    def __init__(self, args: list[str], on_done, on_line=None, stdin: bytes | None = None, env: dict | None = None):
        if SNAPSHOT:
            raise RuntimeError('snapshot mode never runs codexpool')
        self.args = list(args)
        self.proc = None
        self.stopped = False
        stream = on_line is not None
        script = mb.CODEXPOOL_SCRIPT
        if not script.exists():
            AppHelper.callAfter(on_done, Result(self.args, 127, '', f'{tilde(script)} is missing: codexpool is not '
                                                                    'installed. Run the installer.'))
            return
        try:
            self.proc = subprocess.Popen(
                [*mb.codexpool_argv(), *self.args], cwd=str(POOL_DIR),
                stdin=subprocess.PIPE if stdin is not None else subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT if stream else subprocess.PIPE,
                start_new_session=True, env={**os.environ, 'PYTHONUNBUFFERED': '1', **(env or {})})
        except OSError as e:
            AppHelper.callAfter(on_done, Result(self.args, 127, '', e.strerror or str(e)))
            return

        def work():
            if stream:
                lines = []
                for raw in self.proc.stdout:
                    line = raw.decode('utf-8', 'replace').rstrip('\n')
                    lines.append(line)
                    AppHelper.callAfter(on_line, line)
                self.proc.wait()
                out, err = '\n'.join(lines), ''
            else:
                try:
                    o, e = self.proc.communicate(stdin)
                except OSError:   # it exited before reading its stdin (a broken pipe): its output says why
                    o, e = self.proc.stdout.read(), self.proc.stderr.read()
                    self.proc.wait()
                out, err = o.decode('utf-8', 'replace'), e.decode('utf-8', 'replace')
            AppHelper.callAfter(on_done, Result(self.args, self.proc.returncode, out, err))
        threading.Thread(target=work, daemon=True).start()

    def stop(self):
        """Stops the command and everything it started (its own process group)."""
        self.stopped = True
        if self.proc is not None and self.proc.poll() is None:
            try:
                os.killpg(self.proc.pid, signal.SIGTERM)
            except OSError:
                pass


def open_thing(target: str, *extra: str):
    """`open` a URL, a folder or an app, without blocking."""
    if SNAPSHOT:
        return
    mb.spawn(['/usr/bin/open', *extra, target])


def open_private_chrome(url: str):
    """A Chrome incognito window, for signing in to an account other than the browser's usual one."""
    if not SNAPSHOT:
        mb.spawn(['/usr/bin/open', '-na', 'Google Chrome', '--args', '--incognito', url])


def app_installed(bundle_id: str) -> bool:
    return NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(bundle_id) is not None


def copy_text(text: str) -> int:
    """Puts text on the clipboard. Returns the pasteboard's change count after, which moves on when anything else is
    copied; 0 when the pasteboard did not take it, and in snapshots, which never touch the clipboard. It never
    raises, so a sign-in link shows whether or not it could be copied."""
    if SNAPSHOT:
        return 0
    try:
        pb = NSPasteboard.generalPasteboard()
        pb.clearContents()
        if not pb.setString_forType_(S(text), NSPasteboardTypeString):
            return 0
        return int(pb.changeCount())
    except Exception:   # PyObjC raises its own errors too; a copy that failed is just not copied
        return 0


def clipboard_count() -> int:
    if SNAPSHOT:
        return 0
    try:
        return int(NSPasteboard.generalPasteboard().changeCount())
    except Exception:
        return 0


def ordinal(n: int) -> str:
    """1 -> '1st', 2 -> '2nd', 11 -> '11th'."""
    suffix = 'th' if 10 <= n % 100 <= 20 else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')
    return f'{n}{suffix}'


def tilde(path) -> str:
    p = str(path)
    home = str(Path.home())
    return '~' + p[len(home):] if p == home or p.startswith(home + '/') else p


class CodexReopener:
    """Quits the Codex app, waits for it to go, then opens it again (bundle id com.openai.codex)."""

    def __init__(self, done):
        self.done = done
        self.apps = list(NSRunningApplication.runningApplicationsWithBundleIdentifier_(CODEX_BUNDLE) or [])
        self.deadline = time.monotonic() + REOPEN_TIMEOUT_S
        for a in self.apps:
            a.terminate()
        self.tick()

    def tick(self):
        if all(a.isTerminated() for a in self.apps):
            open_thing(CODEX_BUNDLE, '-b')
            self.done(True, 'Codex reopened')
        elif time.monotonic() > self.deadline:
            self.done(False, 'Codex did not quit. Quit it yourself, then open it again.')
        else:
            AppHelper.callLater(0.4, self.tick)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 3. Data: status.json (the menu bar app's model), doctor --json, lane list --json, version
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class Check:
    status: str          # ok | warn | fail
    text: str
    fix: str


@dataclass
class Doctor:
    ok: bool
    problems: int
    warnings: int
    sections: list = field(default_factory=list)   # [(title, [Check])]


def parse_doctor(d) -> Doctor | None:
    d = mb.as_dict(d)
    if 'sections' not in d:
        return None
    sections = []
    for s in mb.as_list(d.get('sections')):
        s = mb.as_dict(s)
        checks = []
        for c in mb.as_list(s.get('checks')):
            c = mb.as_dict(c)
            status = mb.as_str(c.get('status')).lower()
            checks.append(Check(status if status in ('ok', 'warn', 'fail') else 'warn', mb.as_str(c.get('text')),
                                mb.as_str(c.get('fix'))))
        sections.append((mb.as_str(s.get('title'), 'Checks'), checks))
    fails = sum(1 for _, cs in sections for c in cs if c.status == 'fail')
    warns = sum(1 for _, cs in sections for c in cs if c.status == 'warn')
    problems = int(mb.as_num(d.get('problems'), fails) or 0)
    warnings = int(mb.as_num(d.get('warnings'), warns) or 0)
    ok = d.get('ok') is True if isinstance(d.get('ok'), bool) else problems == 0
    return Doctor(ok, problems, warnings, sections)


@dataclass
class LaneTest:
    ok: bool
    when: dt.datetime | None
    reason: str


@dataclass
class Member:
    id: str
    provider: str
    model: str
    name: str
    state: str
    test: LaneTest | None


@dataclass
class Lane:
    name: str
    effort: str
    role: str
    members: list
    display: str = ''              # its entry in the Codex model picker ('' from a codexpool that doesn't say)
    test: LaneTest | None = None   # the last lane-level test


def default_display(name: str) -> str:
    """A lane's picker entry when lanes.json sets none: its name with a capital first letter, as codexpool does."""
    return name[:1].upper() + name[1:]


def parse_test(t) -> LaneTest | None:
    return LaneTest(t.get('ok') is True, mb.parse_time(t.get('when')), mb.as_str(t.get('reason'))) \
        if isinstance(t, dict) else None


def parse_lanes(d) -> list | None:
    d = mb.as_dict(d)
    if 'lanes' not in d:
        return None
    lanes = []
    for x in mb.as_list(d.get('lanes')):
        x = mb.as_dict(x)
        members = []
        for m in mb.as_list(x.get('members')):
            m = mb.as_dict(m)
            members.append(Member(mb.as_str(m.get('id')), mb.as_str(m.get('provider')), mb.as_str(m.get('model')),
                                  mb.as_str(m.get('name')) or mb.as_str(m.get('model')) or mb.as_str(m.get('id')),
                                  mb.as_str(m.get('state'), 'unknown'), parse_test(m.get('last_test'))))
        lanes.append(Lane(mb.as_str(x.get('name'), 'lane'), mb.as_str(x.get('effort')), mb.as_str(x.get('role')),
                          members, mb.as_str(x.get('display')), parse_test(x.get('last_test'))))
    return lanes


@dataclass
class Provider:
    """A lane provider, from `codexpool lane providers --json`."""
    id: str
    title: str
    needs: str                 # login (xai) | key | engine (an add-on's pool as a read-only lane, accepted once)
    key_name: str | None       # the key file's name; None for xai, an engine and responses (key: <lane>-<id>)
    ready: bool | None         # signed in / key saved / engine accepted; None: not known (no `lane providers`)
    detail: str = ''
    kind: str = ''             # native | bridge | engine


def parse_providers(d) -> list | None:
    d = mb.as_dict(d)
    if 'providers' not in d:
        return None
    out = []
    for p in mb.as_list(d.get('providers')):
        p = mb.as_dict(p)
        pid = mb.as_str(p.get('id'))
        if not pid:
            continue
        fallback = next((f for f in PROVIDERS if f[0] == pid), (pid, pid, 'key', None))
        key = p.get('key_name')
        title = re.sub(r'\s*\(.*\)$', '', mb.as_str(p.get('title')))   # 'OpenAI Responses API (your own ...)'
        out.append(Provider(pid, title or fallback[1],
                            mb.as_str(p.get('needs')) or fallback[2],
                            key if isinstance(key, str) and key else None,
                            p.get('ready') if isinstance(p.get('ready'), bool) else None,
                            mb.as_str(p.get('detail')), mb.as_str(p.get('kind'))))
    return out


def fallback_providers() -> list:
    return [Provider(pid, title, needs, key, None) for pid, title, needs, key in PROVIDERS]


@dataclass
class LaneModel:
    """A model `codexpool lane models PROVIDER --json` lists."""
    id: str
    name: str
    context: int | None


def parse_models(d) -> list | None:
    d = mb.as_dict(d)
    if 'models' not in d:
        return None
    out = []
    for x in mb.as_list(d.get('models')):
        x = mb.as_dict(x)
        mid = mb.as_str(x.get('id'))
        if mid:
            ctx = mb.as_num(x.get('context'))
            out.append(LaneModel(mid, mb.as_str(x.get('name')), int(ctx) if ctx else None))
    return out


def parse_json_output(text: str):
    """The JSON a codexpool command printed (tolerating a line of preamble before it)."""
    try:
        return json.loads(text)
    except ValueError:
        i = text.find('{')
        if i > 0:
            try:
                return json.loads(text[i:])
            except ValueError:
                pass
    return None


class Store:
    """What the window shows. status.json and history.jsonl (the pace line) come through the menu bar app's
    DataSource (same parsing, same stale/down handling), as does an add-on pool's status file; doctor, lanes and
    version come from codexpool commands, or from fixture files in snapshot mode. Listeners get called with what
    changed: 'status', 'doctor', 'lanes' or 'version'."""

    def __init__(self, status_path: Path = mb.STATUS_FILE, now: dt.datetime | None = None,
                 history_path: Path | None = mb.HISTORY_FILE, pool_status: Path | None = 'live',
                 pool_history: Path | None = 'live'):
        """pool_status, pool_history: the add-on pool's files; 'live' means its own (PoolUI.status_file), None (a
        snapshot without --pool-status) no file, so that pool reads as not installed."""
        self.source = mb.DataSource(status_path, history_path)
        self.sources = {'codex': self.source}   # + the add-on pool's, when it has a file
        for pool, ui in mb.POOL_UI.items():
            path = ui.status_file if pool_status == 'live' else pool_status
            hist = ui.history_file if pool_history == 'live' else pool_history
            if path:
                self.sources[pool] = mb.DataSource(path, hist, pool=pool)
        self.installed_hint: set = set()       # pools whose install succeeded in this session
        self.now = now
        self.doctor: Doctor | None = None
        self.doctor_note = ''
        self.doctor_at: dt.datetime | None = None
        self.doctor_busy = False
        self.lanes: list | None = None
        self.lanes_note = ''
        self.lanes_busy = False
        self.providers: list | None = None     # lane providers and their credentials
        self.providers_note = ''
        self.providers_busy = False
        self.models: dict = {}                  # provider -> [LaneModel], for this session
        self.demo_models: list | None = None    # snapshot: what every `lane models` answers
        self.version: str | None = None
        self.version_note = ''
        self.listeners: list = []

    def clock(self) -> dt.datetime:
        return self.now or mb.utcnow()

    def model(self, pool: str = 'codex') -> mb.Model:
        src = self.sources.get(pool)
        if src is None:
            return mb.build_model(None, mb.NO_FILE, [], self.clock(), pool_name=pool)
        return src.model(self.now)

    def raw_pool(self, pool: str = 'codex') -> dict:
        src = self.sources.get(pool)
        return mb.as_dict(mb.as_dict(src.raw if src is not None else None).get('pool'))

    # -- status.json fields the menu bar app's model doesn't carry ---------------------------------------
    def balancing(self, pool: str = 'codex') -> str:
        """pool.balancing: 'priority' (your order) or 'reset' (soonest reset first). An older guard: priority."""
        v = mb.as_str(self.raw_pool(pool).get('balancing'))
        return v if v in BALANCING else BALANCING[0]

    def installed(self, pool: str) -> bool:
        """The menu bar app's rule for an add-on's pool (PoolUI.installed over its status file). An install that
        just succeeded here counts before the guard has written the file. The Codex pool always is."""
        if pool == 'codex':
            return True
        src = self.sources.get(pool)
        return pool in self.installed_hint or (src is not None and src.installed)

    def provider_list(self) -> list:
        return self.providers if self.providers is not None else fallback_providers()

    def provider(self, pid: str) -> Provider:
        return next((p for p in self.provider_list() if p.id == pid), Provider(pid, pid, 'key', None, None))

    def notify(self, what: str):
        for fn in list(self.listeners):
            fn(what)

    def poll(self, force: bool = False):
        changed = False
        for src in self.sources.values():
            changed = src.poll(force=force) or changed
        if changed:
            self.notify('status')

    # -- command-backed data (live) --------------------------------------------------------------------
    def fetch_doctor(self):
        if self.doctor_busy:
            return
        self.doctor_busy = True
        self.notify('doctor')

        def done(r: Result):
            self.doctor_busy = False
            data = parse_doctor(parse_json_output(r.out)) if r.out.strip() else None
            if data is not None:   # doctor exits non-zero when it finds problems; the JSON is still the answer
                self.doctor, self.doctor_note, self.doctor_at = data, '', self.clock()
            else:
                self.doctor_note = r.message() if not r.ok or r.unknown else 'codexpool doctor printed no report.'
            self.notify('doctor')
        Job(['doctor', '--json'], done)

    def fetch_lanes(self):
        if self.lanes_busy:
            return
        self.lanes_busy = True
        self.notify('lanes')

        def done(r: Result):
            self.lanes_busy = False
            data = parse_lanes(parse_json_output(r.out)) if r.ok else None
            if data is not None:
                self.lanes, self.lanes_note = data, ''
            else:
                self.lanes_note = r.message() if not r.ok else 'codexpool lane list printed no lanes.'
            self.notify('lanes')
        Job(['lane', 'list', '--json'], done)

    def fetch_providers(self):
        if self.providers_busy or SNAPSHOT:
            return
        self.providers_busy = True

        def done(r: Result):
            self.providers_busy = False
            data = parse_providers(parse_json_output(r.out)) if r.ok else None
            if data is not None:
                self.providers, self.providers_note = data, ''
            else:
                self.providers_note = r.message() if not r.ok else 'codexpool lane providers printed nothing.'
            self.notify('providers')
        Job(['lane', 'providers', '--json'], done)

    def fetch_models(self, provider: str, done, base_url: str = '', key_name: str = ''):
        """done(models or None, message): the models `codexpool lane models PROVIDER --json` lists."""
        if SNAPSHOT:
            done(self.demo_models, '' if self.demo_models is not None else 'No model list in this snapshot.')
            return
        args = ['lane', 'models', provider, '--json'] + (['--base-url', base_url] if base_url else []) + \
            (['--key-name', key_name] if key_name else [])

        def finished(r: Result):
            data = parse_json_output(r.out)
            models = parse_models(data) if r.ok else None
            if models is not None and not base_url:
                self.models[provider] = models
            err = mb.as_str(mb.as_dict(data).get('error'))
            done(models, '' if models is not None else (NEWER if r.unknown else err or r.message()))
        Job(args, finished)

    def fetch_version(self):
        def done(r: Result):
            m = re.search(r'codexpool\s+v?(\S+)', r.out) if r.ok else None
            self.version, self.version_note = (m.group(1), '') if m else (None, r.message())
            self.notify('version')
        Job(['version'], done)

    # -- fixtures (snapshot) ---------------------------------------------------------------------------
    def load_fixtures(self, doctor: Path | None, lanes: Path | None, providers: Path | None = None,
                      models: Path | None = None):
        for src in self.sources.values():
            src.poll(force=True)
        if doctor:
            self.doctor = parse_doctor(json.loads(doctor.read_text()))
            self.doctor_at = self.clock()
        if lanes:
            self.lanes = parse_lanes(json.loads(lanes.read_text()))
        if providers:
            self.providers = parse_providers(json.loads(providers.read_text()))
        if models:
            self.demo_models = parse_models(json.loads(models.read_text()))
        self.version = DEMO_VERSION


def doctor_checklist(doc: Doctor | None, m: mb.Model):
    """The Setup assistant's three checks: (title, status ok|warn|fail|unknown, detail)."""
    def find(pred):
        return [c for title, cs in (doc.sections if doc else []) for c in cs if pred(title.lower(), c)]

    def verdict(checks, fallback):
        if not checks:
            return fallback
        worst = 'fail' if any(c.status == 'fail' for c in checks) else \
            'warn' if any(c.status == 'warn' for c in checks) else 'ok'
        bad = next((c for c in checks if c.status != 'ok'), None)
        return worst, (bad.fix or bad.text) if bad else checks[0].text

    running = m.status not in ('down', 'stale', 'missing')
    pool = verdict(find(lambda t, c: t.startswith('pool')),
                   ('ok', 'The pool is serving') if running else ('unknown', 'codexpool doctor checks this'))
    codex = verdict(find(lambda t, c: t.startswith('codex app')), ('unknown', 'codexpool doctor checks this'))
    menubar = verdict(find(lambda t, c: c.text.lower().startswith('menu bar')), ('unknown', 'codexpool doctor checks this'))
    return [('Pool running', *pool), ('Codex app pointed at the pool', *codex), ('Menu bar running', *menubar)]


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 4. Look: colours beyond the menu bar app's, and the app icon (the capsule mark)
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

_DYN: dict = {}


def dyn(name: str, light: tuple, dark: tuple):
    """A dynamic sRGB colour with alpha: (r, g, b, a) in 0-255 / 0-1 for each appearance."""
    if name not in _DYN:
        def make(c):
            return NSColor.colorWithSRGBRed_green_blue_alpha_(c[0] / 255, c[1] / 255, c[2] / 255, c[3])
        lc, dc = make(light), make(dark)

        def provider(appearance):
            best = appearance.bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
            return dc if best == NSAppearanceNameDarkAqua else lc
        _DYN[name] = (NSColor.colorWithName_dynamicProvider_(name, provider), provider)
    return _DYN[name][0]


class K:
    """Colour roles for the window (the menu bar app's mb.C covers text, bars and pills)."""
    group = staticmethod(lambda: dyn('settings.group', (0, 0, 0, 0.032), (255, 255, 255, 0.045)))
    group_line = staticmethod(lambda: dyn('settings.groupline', (0, 0, 0, 0.075), (255, 255, 255, 0.085)))
    rule = staticmethod(lambda: dyn('settings.rule', (0, 0, 0, 0.085), (255, 255, 255, 0.09)))
    select = staticmethod(lambda: NSColor.controlAccentColor().colorWithAlphaComponent_(0.14))
    sidebar_line = staticmethod(lambda: dyn('settings.sideline', (0, 0, 0, 0.07), (0, 0, 0, 0.45)))


ICON_TINTS = {   # sidebar icon squares: (top, bottom) of the gradient
    'green': (0x4CD964, 0x28B14A), 'blue': (0x3D9BFF, 0x0A6CFF), 'purple': (0xC77DFF, 0x9B4DDB),
    'grey': (0xA2A2A8, 0x7C7C82), 'teal': (0x4FC3D9, 0x1E9DB5), 'indigo': (0x7A78F0, 0x4F4CD1),
    'orange': (0xFFB340, 0xFF8A00), 'red': (0xFF6B61, 0xE8392E), 'coral': (0xEE9270, 0xD4623F),
}


def draw_capsule_mark(x: float, y: float, w: float, h: float, top_pct: float = 64.0, bottom_pct: float = 44.0):
    """The logo: two stacked rounded bars (the menu bar meter), in a w x h box (flipped coordinates)."""
    th, bh, gap = h * 0.56, h * 0.30, h * 0.14
    track = mb.srgb(0xFFFFFF, 0.20)
    mb.fill_rounded(((x, y), (w, th)), th / 2, track)
    mb.fill_rounded(((x, y + th + gap), (w, bh)), bh / 2, track)
    NSGradient.alloc().initWithStartingColor_endingColor_(mb.srgb(0x5BE37D), mb.srgb(0x2FBF57)).drawInBezierPath_angle_(
        mb.rounded(((x, y), (max(th, w * top_pct / 100), th)), th / 2), 90)
    NSGradient.alloc().initWithStartingColor_endingColor_(mb.srgb(0x5BE37D), mb.srgb(0x2FBF57)).drawInBezierPath_angle_(
        mb.rounded(((x, y + th + gap), (max(bh, w * bottom_pct / 100), bh)), bh / 2), 90)


def draw_app_icon(s: float, shadow: bool = True):
    """The app icon at s x s pt (flipped): a dark squircle on the macOS icon grid holding the capsule mark."""
    inset = s * 0.098
    box = ((inset, inset * 0.9), (s - 2 * inset, s - 2 * inset))
    r = (s - 2 * inset) * 0.225
    if shadow:
        NSGraphicsContext.saveGraphicsState()
        sh = NSShadow.alloc().init()
        sh.setShadowBlurRadius_(s * 0.03)
        sh.setShadowOffset_((0, -s * 0.012))
        sh.setShadowColor_(mb.srgb(0x000000, 0.35))
        sh.set()
        mb.fill_rounded(box, r, mb.srgb(0x1B1E26))
        NSGraphicsContext.restoreGraphicsState()
    NSGradient.alloc().initWithStartingColor_endingColor_(mb.srgb(0x3A4050), mb.srgb(0x14161D)).drawInBezierPath_angle_(
        mb.rounded(box, r), 90)
    mb.stroke_rounded(box, r, mb.srgb(0xFFFFFF, 0.10), max(0.5, s / 256))
    (bx, by), (bw, bhh) = box
    mw, mh = bw * 0.60, bhh * 0.34
    draw_capsule_mark(bx + (bw - mw) / 2, by + (bhh - mh) / 2, mw, mh)


def app_icon_image(size: float = 512.0):
    def handler(rect):
        draw_app_icon(size)
        return True
    return NSImage.imageWithSize_flipped_drawingHandler_((size, size), True, handler)


def draw_icon_square(x: float, y: float, s: float, symbol: str, tint: str):
    """A System Settings sidebar icon: a white glyph on a coloured rounded square."""
    top, bottom = ICON_TINTS.get(tint, ICON_TINTS['grey'])
    path = mb.rounded(((x, y), (s, s)), s * 0.24)
    NSGradient.alloc().initWithStartingColor_endingColor_(mb.srgb(top), mb.srgb(bottom)).drawInBezierPath_angle_(path, 90)
    mb.draw_symbol(symbol, x + s / 2, y + s / 2, s * 0.56, mb.srgb(0xFFFFFF), NSFontWeightSemibold,
                   fit=(s * 0.72, s * 0.66))


def draw_pool_glyph(pool: str, s: float, color):
    """A pool's plain drawn mark, Codex a rounded hexagon outline and an add-on's pool its own shape
    (PoolUI.draw_settings_glyph; s x s, flipped): what the switcher shows when the pool's app is not on this Mac
    (pool_glyph_image), and in snapshots."""
    color.set()
    c, path = s / 2, NSBezierPath.bezierPath()
    path.setLineCapStyle_(1)    # round
    path.setLineJoinStyle_(1)
    ui = pool_ui(pool)
    if ui is not None:
        ui.draw_settings_glyph(path, s)
    else:
        path.setLineWidth_(s * 0.12)
        for k in range(6):
            a = math.radians(k * 60 - 90)
            pt = (c + math.cos(a) * s * 0.42, c + math.sin(a) * s * 0.42)
            path.moveToPoint_(pt) if k == 0 else path.lineToPoint_(pt)
        path.closePath()
    path.stroke()


def pool_glyph_image(pool: str, size: float = 12.0):
    """The pool's mark as a template image (the segmented control tints it): the real logo from the app on this
    Mac (the menu bar app's mb.app_mark, when mb.MARKS is 'app'), else the drawn glyph. The logo gets the menu
    bar's box, size * mb.MARK_SCALE[pool], so the cloud and a thin spark carry the same weight here too."""
    mark = mb.app_mark(pool) if mb.MARKS == 'app' else None
    box = size * mb.MARK_SCALE.get(pool, 1.0) if mark is not None else size

    def handler(rect):
        if mark is not None:
            mb.draw_mark(mark, pool, box / 2, box / 2, NSColor.blackColor(), size)
        else:
            draw_pool_glyph(pool, size, NSColor.blackColor())
        return True
    img = NSImage.imageWithSize_flipped_drawingHandler_((box, box), True, handler)
    img.setTemplate_(True)
    return img


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 5. Building blocks: Auto Layout helpers, labels, groups and rows, custom-drawn bits
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

class ActionTarget(NSObject):
    """Wraps a Python callable as a control's target (action 'fire:'). Owners keep a reference."""
    fn = None

    def fire_(self, sender):
        if self.fn is not None:
            self.fn(sender)


def target(fn, keep: list):
    t = ActionTarget.alloc().init()
    t.fn = fn
    keep.append(t)
    return t


class FlippedView(NSView):
    def isFlipped(self):
        return True


class FlippedClipView(NSClipView):
    def isFlipped(self):
        return True


class PaintView(NSView):
    """Draws with self.paint(width, height) in flipped coordinates, under the view's appearance. ax (a string, or
    a callable that returns one) is what VoiceOver reads for the drawing; without it the drawing is decoration."""
    paint = None
    ax = None

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        if self.paint is not None:
            size = self.bounds().size
            self.paint(size.width, size.height)

    def isAccessibilityElement(self):
        return bool(self.ax)

    def accessibilityRole(self):
        return 'AXStaticText'

    def accessibilityValue(self):
        text = self.ax() if callable(self.ax) else self.ax
        return S(text) if text else None


def auto(v):
    v.setTranslatesAutoresizingMaskIntoConstraints_(False)
    return v


def activate(*constraints):
    NSLayoutConstraint.activateConstraints_(list(constraints))


def fix(v, w=None, h=None, min_h=None):
    if w is not None:
        v.widthAnchor().constraintEqualToConstant_(w).setActive_(True)
    if h is not None:
        v.heightAnchor().constraintEqualToConstant_(h).setActive_(True)
    if min_h is not None:
        v.heightAnchor().constraintGreaterThanOrEqualToConstant_(min_h).setActive_(True)
    return v


def pin(v, parent, t=0.0, l=0.0, b=0.0, r=0.0):
    activate(v.topAnchor().constraintEqualToAnchor_constant_(parent.topAnchor(), t),
             v.leadingAnchor().constraintEqualToAnchor_constant_(parent.leadingAnchor(), l),
             v.trailingAnchor().constraintEqualToAnchor_constant_(parent.trailingAnchor(), -r),
             v.bottomAnchor().constraintEqualToAnchor_constant_(parent.bottomAnchor(), -b))


def canvas(paint, w=None, h=None, tip: str | None = None, ax=None):
    v = auto(PaintView.alloc().initWithFrame_(((0, 0), (w or 10, h or 10))))
    v.paint = paint
    v.ax = ax
    fix(v, w, h)
    if tip:
        v.setToolTip_(S(tip))
    return v


def vstack(views, spacing: float = 6.0, full: bool = True, insets=(0, 0, 0, 0)):
    """Views top to bottom; with full, each spans the stack's width (minus insets)."""
    s = auto(NSStackView.stackViewWithViews_([v for v in views if v is not None]))
    s.setOrientation_(1)
    s.setSpacing_(spacing)
    s.setAlignment_(NSLayoutAttributeLeading)
    s.setEdgeInsets_(NSEdgeInsets(*insets))
    if full:
        for v in s.arrangedSubviews():
            v.widthAnchor().constraintEqualToAnchor_constant_(s.widthAnchor(), -(insets[1] + insets[3])).setActive_(True)
    return s


def hstack(leading, trailing=(), spacing: float = 8.0, insets=(0, 0, 0, 0), min_h=None, cluster: bool = False):
    """A row: `leading` views from the left edge, `trailing` views against the right edge, centred vertically.
    A full-width row lets the gap between the two groups take any extra width; a cluster (a row nested in another
    row) keeps its natural size."""
    s = auto(NSStackView.alloc().initWithFrame_(((0, 0), (100, 20))))
    s.setOrientation_(0)
    s.setSpacing_(spacing)
    s.setAlignment_(NSLayoutAttributeCenterY)
    s.setEdgeInsets_(NSEdgeInsets(*insets))
    leading = [v for v in leading if v is not None]
    for v in leading:
        s.addView_inGravity_(v, 1)
    if not cluster:   # the one flexible thing in a row: it wants no width, so it takes whatever is left over
        if leading:
            s.setCustomSpacing_afterView_(0, leading[-1])
        gap = auto(NSView.alloc().initWithFrame_(((0, 0), (0, 0))))
        gap.widthAnchor().constraintGreaterThanOrEqualToConstant_(0).setActive_(True)
        c = gap.widthAnchor().constraintEqualToConstant_(0)
        c.setPriority_(1)
        c.setActive_(True)
        s.addView_inGravity_(gap, 1)
    for v in trailing:
        if v is not None:
            s.addView_inGravity_(v, 3)
    for v in s.views():   # NSStackView treats the top and bottom insets as optional; make them hold
        activate(v.topAnchor().constraintGreaterThanOrEqualToAnchor_constant_(s.topAnchor(), insets[0]),
                 s.bottomAnchor().constraintGreaterThanOrEqualToAnchor_constant_(v.bottomAnchor(), insets[2]))
    s.setHuggingPriority_forOrientation_(750 if cluster else 1, 0)
    if min_h:
        fix(s, min_h=min_h)
    return s


def padded(v, t=0.0, l=0.0, b=0.0, r=0.0):
    box = auto(FlippedView.alloc().initWithFrame_(((0, 0), (10, 10))))
    box.addSubview_(v)
    pin(v, box, t, l, b, r)
    return box


class WrapLabel(NSTextField):
    """A wrapping label that measures its own text. NSTextField's own measurement ignores the 2 pt the cell
    insets on each side, so a line that nearly fills the width is measured as one line and drawn as two."""

    def intrinsicContentSize(self):
        w = self.preferredMaxLayoutWidth()
        if w <= 0:
            return objc.super(WrapLabel, self).intrinsicContentSize()
        r = self.attributedStringValue().boundingRectWithSize_options_((w - 4, 100000.0),
                                                                       NSStringDrawingUsesLineFragmentOrigin)
        return (min(w, math.ceil(r.size.width) + 4), math.ceil(r.size.height) + 1)

    def layout(self):
        objc.super(WrapLabel, self).layout()
        w = self.frame().size.width
        if 0 < w < self.preferredMaxLayoutWidth() - 0.5:   # squeezed narrower than planned: wrap to the real width
            self.setPreferredMaxLayoutWidth_(w)
            self.invalidateIntrinsicContentSize()


def label(text: str, size: float = 13.0, weight=NSFontWeightRegular, color=None, mono: bool = False,
          wrap: float | None = None, align=None, select: bool = False, middle: bool = False):
    if wrap:
        t = WrapLabel.wrappingLabelWithString_(S(text))
        t.setPreferredMaxLayoutWidth_(wrap)
    else:
        t = NSTextField.labelWithString_(S(text))
        t.setLineBreakMode_(NSLineBreakByTruncatingMiddle if middle else NSLineBreakByTruncatingTail)
        t.setContentCompressionResistancePriority_forOrientation_(250, 0)
    t.setFont_(mb.font(size, weight, mono))
    t.setTextColor_(color if color is not None else NSColor.labelColor())
    if align is not None:
        t.setAlignment_(align)
    t.setSelectable_(select)
    return auto(t)


def secondary(text: str, size: float = 11.0, wrap: float | None = None, **kw):
    return label(text, size, color=NSColor.secondaryLabelColor(), wrap=wrap, **kw)


def text_field(text: str, width: float, placeholder: str | None = None, right: bool = False):
    """An editable field with the rounded bezel System Settings uses; it commits on Return and on leaving it."""
    f = auto(NSTextField.textFieldWithString_(S(text)))
    f.setBezelStyle_(NSTextFieldRoundedBezel)
    f.cell().setSendsActionOnEndEditing_(True)
    if placeholder:
        f.setPlaceholderString_(S(placeholder))
    if right:
        f.setAlignment_(NSTextAlignmentRight)
    fix(f, width)
    return f


def button(title: str, fn, keep: list, primary: bool = False, enabled: bool = True, small: bool = False):
    b = auto(NSButton.buttonWithTitle_target_action_(S(title), target(fn, keep), 'fire:'))
    if small:
        b.setControlSize_(NSControlSizeSmall)
        b.setFont_(NSFont.systemFontOfSize_(NSFont.smallSystemFontSize()))
    if primary:
        make_primary(b)
    b.setEnabled_(enabled)
    return b


def link_button(title: str, fn, keep: list, size: float = 13.0):
    """A borderless accent-coloured text button (like a link in System Settings)."""
    b = auto(NSButton.buttonWithTitle_target_action_(S(title), target(fn, keep), 'fire:'))
    b.setBordered_(False)
    b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
        S(title), {NSFontAttributeName: mb.font(size, NSFontWeightMedium if size < 13 else NSFontWeightRegular),
                   NSForegroundColorAttributeName: NSColor.linkColor()}))
    return b


def make_primary(b):
    """The default button (Return). A window that is not key draws it grey, and snapshots have no key window,
    so there it is tinted by hand to look the way it does in use."""
    b.setKeyEquivalent_('\r')
    if SNAPSHOT:
        b.setBezelColor_(NSColor.controlAccentColor())
        b.setAttributedTitle_(NSAttributedString.alloc().initWithString_attributes_(
            b.title(), {NSFontAttributeName: b.font(), NSForegroundColorAttributeName: NSColor.whiteColor()}))


def spinner(small: bool = True):
    size = 16.0 if small else 32.0
    p = auto(NSProgressIndicator.alloc().initWithFrame_(((0, 0), (size, size))))
    p.setStyle_(NSProgressIndicatorStyleSpinning)
    p.setIndeterminate_(True)
    if small:
        p.setControlSize_(NSControlSizeSmall)
    p.setDisplayedWhenStopped_(SNAPSHOT)   # snapshots can't animate; they show the spinner's still frame
    if not SNAPSHOT:
        p.startAnimation_(None)
    fix(p, size, size)
    return p


def symbol_view(name: str, size: float, color, weight=NSFontWeightRegular, box: float | None = None):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if img is None:
        img = NSImage.imageWithSystemSymbolName_accessibilityDescription_('circle', None)
    img = img.imageWithSymbolConfiguration_(NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight))
    v = auto(NSImageView.imageViewWithImage_(img))
    v.setContentTintColor_(color)
    w, h = img.size()
    fix(v, box or w, box or h)
    return v


def pill(text: str, fg, bg):
    """A capsule like the popover's state pills; fg and bg are colour functions (resolved when drawn)."""
    return canvas(lambda w, h: mb.draw_pill(text, 0, 0, fg(), bg()), mb.pill_width(text), mb.PILL_H, ax=text)


def code_chip(text: str, size: float = 12.0):
    """A command to type, in monospace on a faint rounded tile (selectable, so it can be copied by hand too)."""
    f = NSFont.monospacedSystemFontOfSize_weight_(size, NSFontWeightRegular)
    t = label(text, size, select=True)
    t.setFont_(f)
    box = auto(GroupView.alloc().initWithFrame_(((0, 0), (80, 22))))
    box.rows, box.radius = (), 5.0
    box.addSubview_(t)
    pin(t, box, 3, 7, 3, 7)
    return box


def pool_switcher(current: str, fn, keep: list, enabled: bool = True, titles=None):
    """Codex | <the add-on's pool>, centred: a segmented control with each pool's glyph. fn(pool) on a change.
    None with one pool (the Codex pool alone has nothing to switch to)."""
    if len(POOLS) < 2:
        return None
    titles = titles or POOL_TITLES
    seg = auto(NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
        [S(titles[p]) for p in POOLS], 0, target(lambda s: fn(POOLS[s.selectedSegment()]), keep), 'fire:'))
    for i, p in enumerate(POOLS):
        seg.setImage_forSegment_(pool_glyph_image(p), i)
        seg.setWidth_forSegment_(174, i)
    seg.setSelectedSegment_(POOLS.index(current) if current in POOLS else 0)
    seg.setEnabled_(enabled)
    seg.setAccessibilityLabel_(S('Pool'))
    seg.setToolTip_(S('Which pool this pane shows: the Codex pool (ChatGPT accounts) or ' +
                      pool_ui(POOLS[1]).switcher_blurb))
    row = auto(NSStackView.alloc().initWithFrame_(((0, 0), (100, 24))))
    row.setOrientation_(0)
    row.addView_inGravity_(seg, 2)   # centre
    return row


def plan_pill(text: str):
    return pill(text, mb.C.secondary, lambda: mb.C.wash(0.07))


def dot(color_fn, d: float = 8.0, ring: bool = False):
    def paint(w, h):
        if ring:
            mb.stroke_rounded(((w / 2 - d / 2, h / 2 - d / 2), (d, d)), d / 2, color_fn(), 1.5)
        else:
            mb.fill_circle(w / 2, h / 2, d / 2, color_fn())
    return canvas(paint, d + 2, d + 2)


def number_badge(i: int, dim: bool = False):
    """A position in an order (a lane's members, the seats' fill order): a digit in a faint circle."""
    f = mb.font(11, NSFontWeightSemibold)
    return canvas(lambda w, h: (mb.fill_circle(w / 2, h / 2, 9, mb.C.wash(0.05 if dim else 0.08)),
                                mb.draw_text(str(i), 0, (h - mb.line_height(f)) / 2, f,
                                             mb.C.tertiary() if dim else mb.C.secondary(), width=w, align='center')),
                  20, 20, ax=f'{i}.')


def radio(title: str, on: bool, fn, keep: list, enabled: bool = True):
    b = auto(NSButton.radioButtonWithTitle_target_action_(S(title), target(fn, keep), 'fire:'))
    b.setState_(1 if on else 0)
    b.setEnabled_(enabled)
    return b


def checkbox(title: str, on: bool, fn, keep: list, enabled: bool = True):
    b = auto(NSButton.checkboxWithTitle_target_action_(S(title), target(fn, keep), 'fire:'))
    b.setState_(1 if on else 0)
    b.setEnabled_(enabled)
    return b


def symbol_image(name: str, size: float = 11.0, weight=NSFontWeightSemibold, ax: str | None = None):
    img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, S(ax) if ax else None) or \
        NSImage.imageWithSystemSymbolName_accessibilityDescription_('circle', None)
    return img.imageWithSymbolConfiguration_(NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight))


def up_down(fn, keep: list, can_up: bool, can_down: bool, enabled: bool = True, what: str = ''):
    """A small two-segment control, ▲ ▼, that moves a row in an order: fn(-1) or fn(+1)."""
    seg = auto(NSSegmentedControl.segmentedControlWithImages_trackingMode_target_action_(
        [symbol_image('chevron.up', 10, ax=f'Move {what} up'.replace('  ', ' ')),
         symbol_image('chevron.down', 10, ax=f'Move {what} down'.replace('  ', ' '))],
        2, target(lambda s: fn(-1 if s.selectedSegment() == 0 else 1), keep), 'fire:'))   # 2: momentary
    seg.setControlSize_(NSControlSizeSmall)
    seg.setEnabled_forSegment_(can_up and enabled, 0)
    seg.setEnabled_forSegment_(can_down and enabled, 1)
    for i in (0, 1):
        seg.setWidth_forSegment_(22, i)
    seg.setToolTip_(S('Move up or down in the order'))
    return seg


def icon_button(symbol: str, fn, keep: list, ax: str, enabled: bool = True):
    """A small bordered button that shows only an SF Symbol (the − that removes a row)."""
    b = auto(NSButton.buttonWithImage_target_action_(symbol_image(symbol, 10, ax=ax), target(fn, keep), 'fire:'))
    b.setControlSize_(NSControlSizeSmall)
    b.setEnabled_(enabled)
    b.setToolTip_(S(ax))
    b.setAccessibilityLabel_(S(ax))
    return b


class GroupView(NSView):
    """A System Settings group: a rounded box whose rows are separated by inset hairlines."""
    rows = ()
    rules = True     # hairlines between rows
    radius = RADIUS

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        b = self.bounds()
        mb.fill_rounded(b, self.radius, K.group())
        mb.stroke_rounded(b, self.radius, K.group_line(), 1.0)
        for v in self.rows[1:] if self.rules else ():
            if v.isHidden():
                continue
            r = v.convertRect_toView_(v.bounds(), self)
            y = round(r.origin.y)
            mb.fill_rect(((ROW_X, y - 0.5), (b.size.width - 2 * ROW_X, 1.0)), K.rule())


def group(rows, width: float = GROUP_W, rules: bool = True):
    g = auto(GroupView.alloc().initWithFrame_(((0, 0), (width, 40))))
    rows = [r for r in rows if r is not None]
    g.rows = rows
    g.rules = rules
    s = vstack(rows, spacing=0)
    g.addSubview_(s)
    pin(s, g)
    return g


def row_text(title: str, subtitle: str | None, width: float, title_color=None, title_weight=NSFontWeightRegular):
    views = [label(title, 13, title_weight, color=title_color)]
    if subtitle:
        views.append(secondary(subtitle, 11, wrap=width))
    return vstack(views, spacing=2, full=False)


def form_row(title: str, subtitle: str | None = None, accessory=(), leading=None, width: float = GROUP_W,
             title_color=None, min_h: float = 40.0):
    """label (+ subtitle) on the left, controls on the right."""
    accessory = [a for a in (accessory if isinstance(accessory, (list, tuple)) else [accessory]) if a is not None]
    used = sum(a.fittingSize().width + 8 for a in accessory) + (leading.fittingSize().width + 10 if leading else 0)
    text = row_text(title, subtitle, max(120.0, width - 2 * ROW_X - used - 12), title_color)
    return hstack([leading, text] if leading else [text], accessory, spacing=10,
                  insets=(8, ROW_X, 8, ROW_X), min_h=min_h)


def section(body, header: str | None = None, footer: str | None = None, header_right=None, width: float = GROUP_W):
    parts = []
    if header or header_right is not None:
        parts.append(hstack([label(header or '', 13, NSFontWeightSemibold)],
                            [header_right] if header_right is not None else [], insets=(0, 4, 0, 2)))
    parts.append(body)
    if footer:
        parts.append(padded(secondary(footer, 11, wrap=width - 8), 0, 4, 0, 4))
    return vstack(parts, spacing=7)


def note_row(note, width: float = GROUP_W):
    """Feedback for the last action: ('busy'|'ok'|'error', text)."""
    if not note:
        return None
    kind, text = note
    icon = spinner() if kind == 'busy' else symbol_view(
        'checkmark.circle.fill' if kind == 'ok' else 'exclamationmark.triangle.fill', 13,
        mb.C.green_text() if kind == 'ok' else mb.C.orange_text(), NSFontWeightMedium)
    color = NSColor.secondaryLabelColor() if kind == 'busy' else (
        NSColor.labelColor() if kind == 'ok' else mb.C.orange_text())
    return hstack([icon, label(text, 12, color=color, wrap=width - 2 * ROW_X - 30)], spacing=8,
                  insets=(9, ROW_X, 9, ROW_X), min_h=36)


def fact_row(text: str, warn: bool = False, symbol: str | None = None, color=None, width: float = GROUP_W):
    """One line of fact in a group: a small symbol and secondary text (orange for a warning)."""
    if symbol is None:
        symbol = 'exclamationmark.triangle.fill' if warn else 'exclamationmark.circle'
    if color is None:
        color = mb.C.orange() if warn else NSColor.secondaryLabelColor()
    return hstack([symbol_view(symbol, 12, color, NSFontWeightMedium, box=16),
                   label(text, 12, color=mb.C.orange_text() if warn else NSColor.secondaryLabelColor(),
                         wrap=width - 2 * ROW_X - 30)], spacing=8, insets=(8, ROW_X, 8, ROW_X), min_h=32)


def empty_state(symbol: str, tint: str, title: str, body: str, actions=(), width: float = GROUP_W):
    icon = canvas(lambda w, h: draw_icon_square(0, 0, 44, symbol, tint), 44, 44)
    parts = [icon, label(title, 15, NSFontWeightSemibold, align=NSTextAlignmentCenter),
             secondary(body, 12, wrap=min(420.0, width - 60), align=NSTextAlignmentCenter)]
    if actions:
        parts.append(hstack(list(actions), spacing=8, cluster=True))
    s = auto(NSStackView.stackViewWithViews_(parts))
    s.setOrientation_(1)
    s.setAlignment_(9)   # centre X
    s.setSpacing_(8)
    s.setCustomSpacing_afterView_(12, icon)
    if actions:
        s.setCustomSpacing_afterView_(14, parts[2])
    s.setEdgeInsets_(NSEdgeInsets(26, 20, 24, 20))
    return group([s], width)


# -- seat bits shared by Overview, Seats and the Setup assistant -----------------------------------------

def state_style(seat: mb.Seat, m: mb.Model):
    """(text, fg, bg or None) for a seat's state, as the popover shows it: capsules for Serving, Out, Parked and
    Blocked; plain text for Ready and Off."""
    text = mb.STATE_PILL.get(seat.state, seat.state.title() or 'Unknown')
    if seat.state in (mb.READY, 'disabled') or text not in ('Serving', 'Out', 'Parked', 'Blocked'):
        return text, (mb.C.tertiary if seat.state == 'disabled' else mb.C.secondary), None
    if not m.reporting or text == 'Out' or (seat.serving and not m.serving_now):
        return text, mb.C.secondary, lambda: mb.C.wash(0.07)
    if seat.serving:
        if seat.reserve or seat.spending:
            return text, mb.C.red_text, lambda: mb.C.soft(mb.C.red())
        return text, mb.C.green_text, lambda: mb.C.soft(mb.C.green())
    if seat.state == 'parked':
        return text, mb.C.orange_text, lambda: mb.C.soft(mb.C.orange())
    return text, mb.C.red_text, lambda: mb.C.soft(mb.C.red())


def state_view(seat: mb.Seat, m: mb.Model):
    text, fg, bg = state_style(seat, m)
    if bg is None:
        return label(text, 12, NSFontWeightMedium, color=fg())
    return pill(text, fg, bg)


def seat_dot_color(seat: mb.Seat, m: mb.Model):
    if not m.reporting:
        return mb.C.grey
    if seat.serving:
        return mb.C.red if seat.reserve or seat.spending else mb.C.green
    return {'ready': mb.C.green, 'parked': mb.C.orange, 'blocked': mb.C.red}.get(seat.state, mb.C.grey)


def seat_title_row(seat: mb.Seat, m: mb.Model, name_weight=NSFontWeightSemibold, resets: bool = True):
    """dot · name · plan · Reserve ...................... ↺ n · state"""
    dim = not m.reporting or seat.unavailable
    left = [dot(seat_dot_color(seat, m), ring=seat.state == 'disabled' and m.reporting),
            label(seat.label, 13, name_weight, color=NSColor.secondaryLabelColor() if dim else None)]
    if seat.plan:
        left.append(plan_pill(seat.plan))
    if seat.reserve:
        hot = seat.serving and m.serving_now
        left.append(pill('Reserve', mb.C.red_text if hot else mb.C.secondary,
                         (lambda: mb.C.soft(mb.C.red())) if hot else (lambda: mb.C.wash(0.07))))
    right = []
    if resets and seat.resets and m.reporting:
        blue = seat.unavailable and seat.state != 'disabled'
        color = mb.C.blue_text() if blue else NSColor.secondaryLabelColor()
        right.append(hstack([symbol_view('arrow.counterclockwise', 10, color, NSFontWeightSemibold),
                             label(str(seat.resets), 12, NSFontWeightMedium, color=color)], spacing=3,
                            cluster=True))
        right[-1].setToolTip_(S(f'{seat.resets} banked free reset{"s" if seat.resets != 1 else ""}'
                                + (f', soonest expires {mb.fmt_day(seat.reset_expiry)}' if seat.reset_expiry else '')))
    right.append(state_view(seat, m))
    return left, right


def window_line(m: mb.Model, seat: mb.Seat, win: mb.Window | None, name: str) -> str:
    used = win.used if win else None
    parts = [f'{mb.fmt_pct(m.shown(used))} {m.word}']
    reset = win.reset_at if win else None
    out = seat.state in mb.OUT_STATES + ('parked',) and (used or 0) >= 99.5
    back = seat.until or reset
    if out and back and back > m.now:
        parts.append(f'back in {mb.fmt_span((back - m.now).total_seconds())}')
    elif reset and reset > m.now:
        parts.append(f'resets in {mb.fmt_span((reset - m.now).total_seconds())}')
    return ' · '.join(parts)


def seat_meters(seat: mb.Seat, m: mb.Model, width: float, scoped=()):
    """'Week ▬▬▬▬▬▬▬▬▬▬▬░░░░ 56% left · resets in 6d 13h' and, for seats with one, the 5-hour window; a seat
    with scoped weekly caps also gets one bar per cap (scoped: [(name, mb.Window)], e.g. one model family's)."""
    wins = [('Week', seat.week)] + ([('5h', seat.short)] if seat.short else []) + list(scoped)
    line_h, gap = 15.0, 5.0
    dim = not m.reporting or seat.unavailable
    cap_f, val_f = mb.font(11), mb.font(11, mono=False)
    lines = [(name, win, window_line(m, seat, win, name)) for name, win in wins]
    text_w = max(mb.text_width(t, val_f) for _, _, t in lines) + 4
    text_w = min(max(text_w, 150.0), width * 0.45)
    cap_w = min(72.0, max([40.0] + [mb.text_width(name, cap_f) + 8 for name, _ in scoped]))

    def paint(w, h):
        y = 0.0
        for name, win, text in lines:
            used = win.used if win else None
            mb.draw_text(name, 0, y, cap_f, mb.C.secondary(), width=cap_w - 4)
            bx, bw = cap_w, w - cap_w - text_w - 12
            mb.draw_bar(bx, y + (line_h - 6) / 2, bw, 6.0, m.shown(used), mb.bar_fill(used, dim))
            mb.draw_text(text, w - text_w, y, val_f, mb.C.secondary() if dim else mb.C.label(), width=text_w,
                         align='right')
            y += line_h + gap
    ax = '. '.join(f'{ {"Week": "Weekly", "5h": "5-hour"}.get(name, name + " weekly")}: {text.replace(" · ", ", ")}'
                   for name, _, text in lines)
    return canvas(paint, width, len(lines) * line_h + (len(lines) - 1) * gap, ax=ax)


def scoped_windows(seat: mb.Seat) -> list:
    """A seat's scoped weekly caps as (name, mb.Window), for seat_meters."""
    return [(x.name, mb.Window(x.used, x.reset_at, None)) for x in (getattr(seat, 'scoped', None) or [])]


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 6. Panes. Each builds its sections from the Store; the window rebuilds the pane when the data changes.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

class Pane:
    key = ''
    title = ''
    symbol = 'circle'
    tint = 'grey'

    def __init__(self, app):
        self.app = app              # the controller: store, run(), ask(), open_setup(), rebuild()
        self.keep: list = []        # targets of this build's controls
        self.note = None            # ('busy'|'ok'|'error', text) of the last action, shown inline
        self.note_at = 0.0
        self.ext: dict = {}         # an add-on's own state for its pool's side (cleared when the switcher moves)

    @property
    def store(self) -> Store:
        return self.app.store

    @property
    def pool(self) -> str:
        """The pool the switcher shows (Overview, Seats and Balancing): codex, or the add-on's pool."""
        return self.app.pool

    @property
    def ui(self):
        """The add-on's PoolUI on its pool's side; None on the Codex side."""
        return pool_ui(self.pool)

    @property
    def unit(self) -> str:
        return pool_noun(self.pool)

    def cmd(self, *args) -> list:
        """A seat command for this pool: with the add-on's command prefix on its side."""
        return (list(self.ui.command_prefix) if self.ui else []) + list(args)

    def switcher(self):
        return pool_switcher(self.pool, self.app.set_pool, self.keep)

    def pool_changed(self):
        """The switcher moved to the other pool: what was said about the last one goes."""
        self.note = None
        self.ext = {}

    def shown(self):
        """The pane became visible (fetch what it needs)."""

    def say(self, kind: str, text: str):
        self.note, self.note_at = (kind, text), time.monotonic()
        self.app.rebuild(self)

    def note_now(self):
        if self.note and self.note[0] == 'ok' and time.monotonic() - self.note_at > NOTE_S:
            self.note = None
        return self.note

    def build(self) -> list:
        raise NotImplementedError

    def run(self, args, doing: str, done: str, then=None, settled=None):
        """Runs `codexpool <args>`, notes progress and the outcome, and refreshes status.json after a change.
        then(result) runs when the command ends; settled() when the guard pass after a change has been read."""
        self.say('busy', doing)

        def finished(r: Result):
            if r.ok:
                self.say('ok', done)
                self.app.after_change(settled)
            else:
                self.say('error', r.message())
            if then:
                then(r)
        self.app.run(args, finished)


# -- Overview ---------------------------------------------------------------------------------------------

class OverviewPane(Pane):
    key, title, symbol, tint = 'overview', 'Overview', 'gauge.with.dots.needle.67percent', 'green'

    def build(self):
        out = [self.switcher()]
        if self.ui is not None:
            return out + self.ui.overview_sections(self)
        m = self.store.model()
        problem = self.problem(m)
        if problem is not None:
            out.append(problem)
        if m.seats and m.headline is not None:
            out.append(section(self.hero(m)))
        if m.seats:
            order = 'Soonest reset first' if self.store.balancing() == 'reset' else 'Your order'
            out.append(section(group([self.seat_row(s, m) for s in m.seats]), 'Seats',
                               footer=self.footer(m), header_right=secondary(order, 11)))
        return out

    def problem(self, m: mb.Model):
        k = self.keep
        if m.status == 'down':
            return empty_state('exclamationmark.triangle.fill', 'orange', 'The pool is down',
                               'Codex requests fail until the pool is running again.',
                               [button('Restart Pool…', lambda _: self.app.restart_pool(self), k)])
        if m.status in ('stale', 'missing'):
            body = ('The guard has stopped writing status.json, so these numbers may be out of date.'
                    if m.status == 'stale' else 'There is no status report yet. The guard writes one every minute.')
            return empty_state('exclamationmark.triangle.fill', 'grey', 'Pool not reporting', body,
                               [button('Check Health', lambda _: self.app.show_pane('health'), k)])
        if m.status == 'empty':
            return empty_state('person.crop.circle.badge.plus', 'blue', 'No seats yet',
                               'Add your ChatGPT accounts. Each one becomes a seat, and Codex moves to the next '
                               'when one runs out.',
                               [button('Add a ChatGPT Account…', lambda _: self.app.open_setup('setup-accounts'), k,
                                       primary=True)])
        return None

    def hero(self, m: mb.Model, pool: str = 'codex'):
        ui = pool_ui(pool)
        color = mb.headline_text
        number = f'{round(m.shown(m.headline)):d}'
        caption = f'{m.word} this week · {m.scope}'
        big, unit, cap_f = mb.font(40, NSFontWeightSemibold, mono=True), mb.font(22, NSFontWeightSemibold), mb.font(13)
        pill_text, pill_fg, pill_bg = self.status_pill(m, pool)
        inner = GROUP_W - 2 * 16

        def paint(w, h):
            base = round(big.ascender())
            nw = mb.text_width(number, big)
            mb.draw_text(number, 0, 0, big, color(m))
            mb.draw_text('%', nw, base - unit.ascender(), unit, color(m))
            mb.draw_text(caption, nw + mb.text_width('%', unit) + 8, base - cap_f.ascender(), cap_f, mb.C.secondary())
            pw = mb.pill_width(pill_text)
            mb.draw_pill(pill_text, w - pw, base - cap_f.ascender() - 1, pill_fg(), pill_bg())
            mb.draw_bar(0, base + 12, w, 8.0, m.shown(m.headline), mb.headline_fill(m))
        big_h = round(big.ascender()) + 12 + 8
        top = canvas(paint, inner, big_h, tip=mb.headline_breakdown(m),
                     ax=f'{number}% {m.word} this week, {m.scope}. {pill_text}')

        facts = ui.hero_facts(m) if ui else self.facts(m)
        col_w = inner / len(facts)
        grid = auto(NSGridView.gridViewWithViews_([
            [secondary(c, 11) for c, _, _ in facts],
            [label(v, 13, NSFontWeightMedium, color=vc) for _, v, vc in facts]]))
        grid.setRowSpacing_(3)
        grid.setColumnSpacing_(0)
        for i in range(len(facts)):
            grid.columnAtIndex_(i).setWidth_(col_w)
        lines = [top, grid]
        extra = ui.hero_extra(m) if ui else []
        resettable = [s for s in m.seats if s.resets and s.unavailable and s.state != 'disabled'] \
            if m.reporting and not ui else []
        if resettable:   # a link to the seat, where Redeem Reset… lives
            first = resettable[0]
            extra.append(link_button(f'Reset available for {", ".join(s.label for s in resettable)} ›',
                                     lambda _: self.app.show_seat(first.name or first.label), self.keep, size=12))
        pace = mb.pace_text(m)
        if pace:
            extra.append(secondary(pace, 12))
        if extra:
            lines.append(vstack(extra, spacing=3, full=False))
        box = vstack(lines, spacing=16, full=False, insets=(18, 16, 16, 16))
        return group([box])

    @staticmethod
    def status_pill(m: mb.Model, pool: str = 'codex'):
        """The popover's pill (mb.pool_pill): the pool's colour for Regular, red for Reserve, All out and Credits."""
        word, _fg, _bg = mb.pool_pill(m)
        return word, (lambda: mb.pool_pill(m)[1]), (lambda: mb.pool_pill(m)[2])   # resolved when drawn

    @staticmethod
    def facts(m: mb.Model):
        """(caption, value, colour) for the four columns under the headline."""
        label_c = NSColor.labelColor()
        if not m.reporting:   # the numbers are old: say nothing about who serves or comes back
            return [('New threads', '—', NSColor.secondaryLabelColor()),
                    ('Regular seats', f'{m.regular_ready} of {m.regular_total} ready' if m.regular_total else '—',
                     label_c),
                    ('Reserve', '—' if not m.reserve_seats else f'{len(m.reserve_seats)} seat'
                     f'{"s" if len(m.reserve_seats) != 1 else ""}', label_c),
                    ('Next back', '—', NSColor.secondaryLabelColor())]
        serving = m.serving.label if m.serving and m.serving_now else 'Nothing available'
        serving_c = (mb.C.red_text() if m.hot else label_c) if m.serving_now else NSColor.secondaryLabelColor()
        ready = f'{m.regular_ready} of {m.regular_total} ready' if m.regular_total else 'None'
        if not m.reserve_seats:
            reserve = 'None'
        elif len(m.reserve_seats) == 1:
            r = m.reserve_seats[0]
            reserve = (mb.STATE_PILL.get(r.state, r.state) if r.unavailable else
                       f'{mb.fmt_pct(m.shown(r.week.used if r.week else None))} {m.word}')
        else:
            reserve = f'{len(m.reserve_seats)} seats'
        if m.next_back and m.reporting:
            who, at = m.next_back
            nxt = f'{who} · {mb.fmt_span((at - m.now).total_seconds())}'
        else:
            nxt = 'Nothing out'
        return [('New threads', f'→ {serving}', serving_c), ('Regular seats', ready, label_c),
                ('Reserve', reserve, label_c), ('Next back', nxt, label_c)]

    def seat_row(self, seat: mb.Seat, m: mb.Model):
        """One seat: its bars (with its scoped caps) and, on an add-on pool's side, its own lines (why it is parked,
        its credits: PoolUI.seat_detail_views)."""
        inner = GROUP_W - 2 * ROW_X
        ui = pool_ui(m.pool)
        left, right = seat_title_row(seat, m)
        parts = [hstack(left, right, spacing=7),
                 padded(seat_meters(seat, m, inner - 20, scoped_windows(seat)), 0, 20, 0, 0)]
        if ui is not None:
            parts += ui.seat_detail_views(seat, m)
        ended = ui.sign_in_ended_text if ui else mb.SIGN_IN_ENDED
        if seat.state == 'blocked':
            parts.append(padded(label(f'Re-login needed · {seat.detail or "needs attention"}', 11,
                                      color=mb.C.red_text(), middle=True), 0, 20, 0, 0))
        elif seat.sign_in_soon:   # still served on its access token, for up to a day
            parts.append(padded(label(f'Re-login soon · {ended}', 11,
                                      color=mb.C.orange_text(), middle=True), 0, 20, 0, 0))
        v = vstack(parts, spacing=7, insets=(11, ROW_X, 12, ROW_X))
        if seat.serving and m.serving_now:
            v.setToolTip_(S(ui.serving_tip if ui else 'New threads land on this seat'))
        return v

    @staticmethod
    def footer(m: mb.Model, pool: str = 'codex') -> str:
        updated = f'Updated {mb.fmt_age(m.age)}' if m.age is not None else 'Not updated yet'
        total = mb.total_text(m)   # the pool's size, as the popover's summary line starts ('31× total')
        weighs = f'{total}; the headline weighs' if total else 'The headline weighs'
        ui = pool_ui(pool)
        if ui is not None:
            return ui.overview_footer(updated, weighs)
        return f'{updated}. {weighs} each seat by its size: Plus and Team 1×, Business 5×, Pro 20×.'


# -- Seats ------------------------------------------------------------------------------------------------

SIZES = (1, 2, 5, 10, 20)


class SeatListRow(NSView):
    """A selectable row inside a group: highlights when selected, calls on_click on mouse down. For VoiceOver it
    is one radio button (the seat list picks one seat), labelled with ax_title."""
    selected = False
    on_click = None
    ax_title = ''

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    def drawRect_(self, rect):
        if self.selected:
            b = self.bounds()
            mb.fill_rounded(((4, 3), (b.size.width - 8, b.size.height - 6)), 7, K.select())

    def mouseDown_(self, event):
        if self.on_click is not None:
            self.on_click()

    def isAccessibilityElement(self):
        return True

    def accessibilityRole(self):
        return 'AXRadioButton'

    def accessibilityLabel(self):
        return S(self.ax_title)

    def accessibilityValue(self):
        return 1 if self.selected else 0

    def accessibilityChildren(self):
        return []   # its labels and pills are in ax_title

    def accessibilityPerformPress(self):
        if self.on_click is not None:
            self.on_click()
        return True


class SeatsPane(Pane):
    key, title, symbol, tint = 'seats', 'Seats', 'person.2.fill', 'blue'

    def __init__(self, app):
        super().__init__(app)
        self.picked = {p: None for p in POOLS}   # pool -> the selected seat's file name

    @property
    def selected(self) -> str | None:
        return self.picked[self.pool]

    @selected.setter
    def selected(self, value: str | None):
        self.picked[self.pool] = value

    def build(self):
        ui = self.ui
        m = self.store.model(self.pool)
        k = self.keep
        out = [self.switcher()]
        if ui and hasattr(ui, 'settings_page'):
            return out + ui.settings_page(self, 'accounts')
        if ui is not None and not self.store.installed(self.pool):
            return out + [ui.setup_state(self.app, k)]
        if not m.seats:
            if ui is not None:
                return out + [ui.empty_state(self.app, k, 'seats')]
            return out + [empty_state('person.crop.circle.badge.plus', 'blue', 'No seats yet',
                                      'Each ChatGPT account you add becomes a seat. When one hits its usage limit, '
                                      'the next one picks up the same thread.',
                                      [button('Add a ChatGPT Account…',
                                              lambda _: self.app.open_setup('setup-accounts'), k, primary=True)])]
        names = [s.name or s.label for s in m.seats]
        if self.selected not in names:
            self.selected = names[0]
        seat = m.seats[names.index(self.selected)]
        rows = [self.list_row(s, m) for s in m.seats]
        add = hstack([secondary(ui.seats_hint if ui else 'In fill order. Balancing sets the order and the reserve.',
                                11, wrap=GROUP_W - 2 * ROW_X - 130)],
                     [button('Add Account…', lambda _: self.app.open_setup('setup-accounts', pool=self.pool), k)],
                     insets=(8, ROW_X, 8, ROW_X - 2), min_h=40)
        details = ui.seat_details(self, seat, m) if ui else self.details(seat, m)
        return out + [section(group(rows + [add])),
                      section(details, seat.label, footer='Changes run codexpool commands and take effect at once.')]

    def list_row(self, seat: mb.Seat, m: mb.Model):
        key = seat.name or seat.label
        left, right = seat_title_row(seat, m, name_weight=NSFontWeightMedium)
        usage = seat.week.used if seat.week else None
        right.insert(0, secondary(f'{mb.fmt_pct(m.shown(usage))} {m.word}', 11))
        inner = hstack(left, right, spacing=7, insets=(0, ROW_X, 0, ROW_X))
        r = auto(SeatListRow.alloc().initWithFrame_(((0, 0), (GROUP_W, 38))))
        r.addSubview_(inner)
        pin(inner, r)
        fix(r, h=38)
        r.selected = key == self.selected

        def pick():
            self.selected = key
            self.note = None
            self.app.rebuild(self)
        r.on_click = pick
        r.setToolTip_(S(f'{seat.label} ({seat.name})' if seat.name else seat.label))
        r.ax_title = ', '.join(t for t in (seat.label, seat.plan, 'reserve' if seat.reserve else '',
                                           f'{mb.fmt_pct(m.shown(usage))} {m.word} this week',
                                           state_style(seat, m)[0]) if t)
        return r

    def details(self, seat: mb.Seat, m: mb.Model):
        k = self.keep
        busy = bool(self.note and self.note[0] == 'busy')
        name = seat.name or seat.label

        # Name: commits on Return or when the field loses focus
        field = text_field(seat.label, 190)

        def rename(sender):
            new = str(sender.stringValue()).strip()
            if new.startswith('-'):
                sender.setStringValue_(S(seat.label))
                self.say('error', 'A seat name can\u2019t start with a dash.')
            elif new and new != seat.label:
                self.run(['label', name, new], f'Renaming {seat.label}…', f'{seat.label} is now {new}')
        field.setTarget_(target(rename, k))
        field.setAction_('fire:')

        # Size (weight)
        size = auto(NSPopUpButton.alloc().initWithFrame_pullsDown_(((0, 0), (90, 26)), False))
        current = (seat.weight or 1.0) if seat.capacity_known else None
        values = sorted(set(SIZES) | ({current} if current is not None else set()))
        if current is None:
            values.insert(0, None)
        size.addItemsWithTitles_([S('Unknown' if v is None else f'{v:g}×') for v in values])
        size.selectItemAtIndex_(values.index(current))

        def resize(sender):
            v = values[sender.indexOfSelectedItem()]
            if v is not None and v != current:
                self.run(['weight', name, f'{v:g}'], f'Resizing {seat.label}…', f'{seat.label} now counts {v:g}×')
        size.setTarget_(target(resize, k))
        size.setAction_('fire:')

        rotation = auto(NSSwitch.alloc().init())
        rotation.setControlSize_(NSControlSizeSmall)
        rotation.setState_(0 if seat.state in ('disabled', 'parked') else 1)

        def toggle_rotation(sender):
            if sender.state() == 1:
                if seat.state == 'parked':
                    sender.setState_(0)
                    self.app.ask(f'Enable {seat.label} and spend credits?',
                                 f'The credit guard parked {seat.label} because its plan limit is used up and more '
                                 'requests would spend credits. Enabling it overrides the guard until the limit '
                                 'resets.', 'Enable',
                                 lambda: self.run(['enable', name], f'Enabling {seat.label}…',
                                                  f'{seat.label} enabled; it may spend credits'))
                else:
                    self.run(['enable', name], f'Enabling {seat.label}…', f'{seat.label} is back in rotation')
            else:
                self.run(['disable', name], f'Disabling {seat.label}…', f'{seat.label} is out of rotation')
        rotation.setTarget_(target(toggle_rotation, k))
        rotation.setAction_('fire:')

        for c in (field, size, rotation):
            c.setEnabled_(not busy)

        exp = f', soonest expires {mb.fmt_day(seat.reset_expiry)}' if seat.reset_expiry else ''
        reset_sub = (f'{seat.resets} banked free reset{"s" if seat.resets != 1 else ""}{exp}.' if seat.resets
                     else 'None banked. ChatGPT sometimes grants free resets; they show up here.')
        state_sub = {'parked': 'Parked by the credit guard: its plan limit is used up.',
                     'blocked': f'Needs a new sign-in: {seat.detail or "the login stopped working"}.',
                     'disabled': 'Out of rotation. Threads on it moved to the next seat.'}.get(
            seat.state, 'Takes new threads in its turn in the fill order.')
        rows = [
            form_row('Name', f'Seat file {seat.name}' if seat.name else None, field),
            form_row('Size', 'Its share of the headline. Plus and Team 1×, Business 5×, Pro 20×.', size),
            self.order_row(seat, m),
            form_row('In rotation', state_sub, rotation),
            form_row('Banked resets', reset_sub,
                     button('Redeem Reset…', lambda _: self.confirm_reset(seat), k,
                            enabled=bool(seat.resets) and not busy)),
            form_row('ChatGPT sign-in', f'{mb.SIGN_IN_ENDED}. The seat serves until its access runs out, within a '
                     'day: sign in again before then.' if seat.sign_in_soon else
                     'Sign in again if the seat is blocked or you changed its password.',
                     button('Sign In Again…', lambda _: self.app.open_setup(
                         'setup-accounts', label=seat.label,
                         priority=None if seat.priority is None else int(seat.priority)), k, enabled=not busy)),
            form_row('Remove from pool', 'Deletes the seat file. The ChatGPT login itself is not revoked.',
                     button('Remove…', lambda _: self.confirm_remove(seat), k, enabled=not busy)),
        ]
        note = note_row(self.note_now())
        if note is not None:
            rows.append(note)
        return group(rows)

    def order_row(self, seat: mb.Seat, m: mb.Model):
        """Where the seat is in the fill order, as a row that opens Balancing (where the order and the reserve are
        set)."""
        regular = [s for s in m.seats if not s.reserve]
        unit = self.unit
        if seat.reserve:
            sub = f'Reserve: used only when every other {unit} is out.'
        else:
            pos = next((i for i, s in enumerate(regular, 1) if (s.name or s.label) == (seat.name or seat.label)), 0)
            how = 'soonest reset first' if self.store.balancing(self.pool) == 'reset' else 'in your order'
            sub = f'{ordinal(pos)} of {len(regular)} regular {unit}{"s" if len(regular) != 1 else ""}, {how}.'
        return self.balancing_link('Fill order', sub)

    def balancing_link(self, title: str, sub: str):
        chevron = symbol_view('chevron.right', 11, NSColor.tertiaryLabelColor(), NSFontWeightSemibold)
        r = form_row(title, sub, [secondary('Balancing', 12), chevron])
        return clickable(r, lambda: self.app.show_pane('balancing'), f'{title}: open Balancing')

    def confirm_reset(self, seat: mb.Seat):
        n = seat.resets
        exp = f' The soonest expires {mb.fmt_day(seat.reset_expiry)}.' if seat.reset_expiry else ''
        self.app.ask(f'Use a reset on {seat.label}?',
                     f'{seat.label}’s weekly and 5-hour limits go back to full right away, and the pool can use '
                     f'it again. This spends 1 of {n} banked free reset{"s" if n != 1 else ""}.{exp} It never buys '
                     'a reset.', 'Use Reset',
                     lambda: self.run(['reset', seat.name or seat.label, '--yes'], f'Resetting {seat.label}…',
                                      f'{seat.label} is back to full usage'))

    def confirm_remove(self, seat: mb.Seat):
        ui = self.ui
        text = ui.remove_text(seat) if ui else (
            f'This deletes the seat file {seat.name}. The ChatGPT login itself is not revoked, and you can '
            'add the account again later.')
        self.app.ask(f'Remove {seat.label} from the {ui.title + " " if ui else ""}pool?', text,
                     'Remove', lambda: self.run(self.cmd('remove', seat.name or seat.label, '--yes'),
                                                f'Removing {seat.label}…', f'{seat.label} removed'),
                     destructive=True)


# -- Balancing --------------------------------------------------------------------------------------------

MODES = (   # pool.balancing: (value, title, explanation)
    ('priority', 'Your order', 'Uses the first seat until it runs out, then the next. Best for prompt caching.'),
    ('reset', 'Soonest reset first', 'Uses the seat whose weekly quota resets soonest, so none of it goes to waste. '
              'New threads follow; running threads stay on their seat.'),
)


def seat_key(seat: mb.Seat) -> str:
    """What codexpool commands take for a seat: its file name (its label for an older status.json)."""
    return seat.name or seat.label


class BalancingPane(Pane):
    """How the pool picks a seat (your order, or soonest reset first), the fill order, and the reserve."""
    key, title, symbol, tint = 'balancing', 'Balancing', 'slider.horizontal.3', 'orange'

    def __init__(self, app):
        super().__init__(app)
        self.where = 'mode'        # the group that shows self.note: mode | order | reserve
        self.pending = None        # a fill order (seat keys) set here that status.json doesn't show yet
        self.want_mode = None      # the mode clicked here that status.json doesn't show yet
        self.want_reserve = {}     # seat key -> the reserve checkbox clicked here that status.json doesn't show yet
        self.commit_timer = None
        self.pending_pool = 'codex'   # the pool self.pending belongs to

    def pool_changed(self):
        super().pool_changed()
        if self.commit_timer is not None:   # an order still being clicked together: save it for its own pool
            self.commit_timer.invalidate()
            self.commit_order()
        self.want_mode, self.want_reserve = None, {}

    def build(self):
        ui = self.ui
        m = self.store.model(self.pool)
        out = [self.switcher()]
        if ui and hasattr(ui, 'settings_page'):
            return out + ui.settings_page(self, 'switching')
        if ui is not None and not self.store.installed(self.pool):
            return out + [ui.setup_state(self.app, self.keep)]
        if not m.seats:
            if ui is not None:
                return out + [ui.empty_state(self.app, self.keep, 'balancing')]
            return out + [empty_state('person.crop.circle.badge.plus', 'blue', 'No seats yet',
                                      'Add your ChatGPT accounts first. Balancing decides which seat new threads go '
                                      'to.', [button('Add a ChatGPT Account…',
                                                     lambda _: self.app.open_setup('setup-accounts'), self.keep,
                                                     primary=True)])]
        mode = self.store.balancing(self.pool)
        if self.want_mode == mode:
            self.want_mode = None
        mode = self.want_mode or mode   # show the choice just made until status.json agrees
        busy = bool(self.note and self.note[0] == 'busy')
        out += [self.mode_section(mode, busy), self.order_section(m, mode, busy), self.reserve_section(m, busy)]
        if ui is not None:   # the add-on pool's own sections (its usage credits)
            out += ui.balancing_sections(self, m, busy)
        return out

    def note_for(self, where: str):
        return note_row(self.note_now()) if self.where == where else None

    # -- how the pool picks a seat ---------------------------------------------------------------------
    def mode_section(self, mode: str, busy: bool):
        k = self.keep
        rows = []
        for value, title, text in (self.ui.modes() if self.ui else MODES):
            r = radio(title, mode == value, lambda _s, v=value: self.set_mode(v), k, enabled=not busy)
            desc = secondary(text, 11, wrap=GROUP_W - 2 * ROW_X - 21)
            rows.append(vstack([r, padded(desc, 0, 21, 0, 0)], spacing=2, full=False, insets=(10, ROW_X, 10, ROW_X)))
        rows.append(self.note_for('mode'))
        return section(group(rows), f'How the pool picks {"an" if self.unit[0] in "aeiou" else "a"} {self.unit}')

    def set_mode(self, value: str):
        if value == (self.want_mode or self.store.balancing(self.pool)):
            self.app.rebuild(self)   # the radio clicked again: keep it on
            return
        self.where, self.pending, self.want_mode = 'mode', None, value
        if self.ui is not None:
            done, key = self.ui.mode_done(value), self.ui.balancing_key
        else:
            done = ('New threads now go to the seat that resets soonest' if value == 'reset' else
                    'New threads now follow your order')
            key = 'balancing'

        def then(r: Result):
            if r.ok:
                self.store.poll(force=True)   # `set` rewrote status.json already
            else:
                self.forget_mode()

        self.run(['set', key, value], 'Saving…', done, then=then, settled=self.forget_mode)

    def forget_mode(self):
        """The command failed, or the guard pass after it was read: status.json is the truth again."""
        if self.want_mode is not None:
            self.want_mode = None
            self.app.rebuild(self)

    # -- the fill order --------------------------------------------------------------------------------
    def regular_order(self, m: mb.Model) -> list:
        """The regular seats in fill order: status.json's, or the one set here that it doesn't show yet."""
        regular = [s for s in m.seats if not s.reserve]
        if self.pending and self.pending_pool == self.pool:
            rank = {key: i for i, key in enumerate(self.pending)}
            regular.sort(key=lambda s: rank.get(seat_key(s), len(rank)))
        return regular

    def order_section(self, m: mb.Model, mode: str, busy: bool):
        reset = mode == 'reset'
        regular = self.regular_order(m)
        rows = [self.order_row(i, s, m, len(regular), reset, busy) for i, s in enumerate(regular)]
        rows += [self.reserve_order_row(s, m) for s in m.reserve_seats]
        rows.append(self.note_for('order'))
        if self.ui is not None:
            footer = self.ui.order_footer(reset)
        elif reset:
            footer = ('Re-sorted every minute by weekly reset. Seats without usage data follow in your order, '
                      'which comes back when you switch to “Your order”.')
        else:
            footer = ('New threads go to the first seat that has quota left. Threads already running stay on their '
                      'seat until it runs out.')
        right = secondary('Updates itself' if reset else 'First to last', 11)
        return section(group(rows), f'{self.unit.title()} order', footer=footer, header_right=right)

    def seat_left(self, seat: mb.Seat, m: mb.Model, badge):
        dim = not m.reporting or seat.unavailable
        left = [badge, label(seat.label, 13, NSFontWeightMedium, color=NSColor.secondaryLabelColor() if dim else None)]
        if seat.plan:
            left.append(plan_pill(seat.plan))
        if seat.serving and m.serving_now:
            hot = seat.reserve or seat.spending
            left.append(pill('Serving', mb.C.red_text if hot else mb.C.green_text,
                             (lambda: mb.C.soft(mb.C.red())) if hot else (lambda: mb.C.soft(mb.C.green()))))
        elif seat.state not in (mb.READY, mb.SERVING):
            text, fg, bg = state_style(seat, m)
            left.append(pill(text, fg, bg) if bg is not None else label(text, 11, NSFontWeightMedium, color=fg()))
        return left

    def when_text(self, seat: mb.Seat, m: mb.Model) -> str:
        """'56% left · resets in 6d 13h' (the window that wastes quota first: the week, else the 5 hours)."""
        win = seat.week if seat.week and (seat.week.used is not None or seat.week.reset_at) else seat.short
        if win is None or (win.used is None and win.reset_at is None):
            return 'No usage data yet'
        return window_line(m, seat, win, 'Week' if win is seat.week else '5h')

    def order_row(self, i: int, seat: mb.Seat, m: mb.Model, n: int, reset: bool, busy: bool):
        left = self.seat_left(seat, m, number_badge(i + 1, dim=seat.unavailable))
        text = self.when_text(seat, m)
        if reset and text == 'No usage data yet':
            text = 'No usage data yet · kept in your order'
        right = [secondary(text, 11)]
        if not reset:
            right.append(up_down(lambda d, i=i: self.move(i, d), self.keep, i > 0, i < n - 1, enabled=not busy,
                                 what=seat.label))
        return hstack(left, right, spacing=8, insets=(7, ROW_X, 7, ROW_X), min_h=40)

    def reserve_order_row(self, seat: mb.Seat, m: mb.Model):
        badge = symbol_view('arrow.down.to.line', 11, NSColor.tertiaryLabelColor(), NSFontWeightSemibold, box=20)
        left = self.seat_left(seat, m, badge)
        left.insert(3 if seat.plan else 2, pill('Reserve', mb.C.secondary, lambda: mb.C.wash(0.07)))
        return hstack(left, [secondary(f'Last, when every other {self.unit} is out', 11)], spacing=8,
                      insets=(7, ROW_X, 7, ROW_X), min_h=40)

    def move(self, i: int, d: int):
        m = self.store.model(self.pool)
        keys = [seat_key(s) for s in self.regular_order(m)]
        j = i + d
        if not 0 <= j < len(keys):
            return
        keys[i], keys[j] = keys[j], keys[i]
        self.pending, self.pending_pool, self.where = keys, self.pool, 'order'
        if self.note and self.note[0] != 'busy':
            self.note = None
        self.app.rebuild(self)
        if self.commit_timer is not None:   # a burst of clicks commits once
            self.commit_timer.invalidate()
        self.commit_timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            0.9, target(lambda _t: self.commit_order(), self.keep), 'fire:', None, False)

    def commit_order(self):
        self.commit_timer = None
        keys, pool = self.pending, self.pending_pool
        m = self.store.model(pool)
        if not keys or keys == [seat_key(s) for s in m.seats if not s.reserve]:
            self.pending = None
            self.app.rebuild(self)
            return
        first = next((s.label for s in m.seats if seat_key(s) == keys[0]), keys[0])
        self.where = 'order'
        self.say('busy', 'Saving the order…')

        def finished(r: Result):
            if r.ok:
                self.say('ok', f'New {getattr(pool_ui(pool), "session_word", "threads")} now go to {first} first')
                self.app.after_change(lambda: self.settled(keys))
            else:
                self.pending = None
                self.say('error', r.message())
        ui = pool_ui(pool)
        self.app.run((list(ui.command_prefix) if ui else []) + ['order', *keys], finished)

    def settled(self, keys: list):
        """The guard pass after `codexpool order`: status.json has the order now."""
        if self.pending == keys:
            self.pending = None
            self.app.rebuild(self)

    # -- the reserve -----------------------------------------------------------------------------------
    def reserve_section(self, m: mb.Model, busy: bool):
        k = self.keep
        rows = []
        for seat in sorted(m.seats, key=lambda s: s.reserve):   # regular seats in fill order, then the reserve
            left = [dot(seat_dot_color(seat, m), ring=seat.state == 'disabled' and m.reporting),
                    label(seat.label, 13, NSFontWeightMedium)]
            if seat.plan:
                left.append(plan_pill(seat.plan))
            key = seat_key(seat)
            if self.want_reserve.get(key) == seat.reserve:
                self.want_reserve.pop(key)
            box = checkbox('Use last (reserve)', self.want_reserve.get(key, seat.reserve),
                           lambda sender, s=seat: self.set_reserve(s, sender.state() == 1), k, enabled=not busy)
            rows.append(hstack(left, [box], spacing=8, insets=(7, ROW_X, 7, ROW_X), min_h=38))
        rows.append(self.note_for('reserve'))
        if self.ui is not None:
            footer = self.ui.reserve_footer
        else:
            footer = ('The reserve is used only when every other seat is out; the menu bar turns red while it serves. '
                      'Your biggest seat, such as a Pro 20× plan, makes a good reserve.')
        return section(group(rows), 'Reserve', footer=footer)

    def set_reserve(self, seat: mb.Seat, on: bool):
        key = seat_key(seat)
        self.where, self.pending = 'reserve', None
        self.want_reserve[key] = on   # the checkbox shows the click until status.json agrees

        def forget(_r=None):
            if self.want_reserve.pop(key, None) is not None:
                self.app.rebuild(self)

        self.run(self.cmd('reserve', key, *([] if on else ['--off'])), f'Updating {seat.label}…',
                 f'{seat.label} is {"now the reserve: used last" if on else f"a regular {self.unit} again"}',
                 then=lambda r: None if r.ok else forget(), settled=forget)


# -- Lanes ------------------------------------------------------------------------------------------------

LANE_STATE = {   # member state from `lane list` -> (label, colour role)
    'ready': ('Ready', 'green'), 'bridge ok': ('Bridge OK', 'green'), 'active': ('Serving', 'green'),
    'cooldown': ('Cooling down', 'orange'), 'exhausted': ('Out', 'orange'), 'disabled': ('Off', 'grey'),
    'no key': ('No key', 'red'), 'bridge down': ('Bridge down', 'red'), 'not in bridge': ('Not in bridge', 'red'),
    'missing': ('Not signed in', 'red'), 'blocked': ('Blocked', 'red'), 'unknown': ('Unknown', 'grey'),
    # an engine member (an add-on's pool as a read-only lane): its states come from the add-on (PoolUI.settings_loaded)
}

ENGINE_STATE = {}   # `lane providers` detail of an engine provider (before its ': codexpool …' hint) -> plain words
ENGINE_HINT = {}    # ... and what to do, for a member row's tooltip (both filled by the add-on)
ENGINE_COPY = {     # the Lanes pane's words for an engine provider the add-on doesn't name (PoolUI.lane_copy)
    'provider_line': 'A read-only engine. ', 'provider_ready': 'Engine accepted.',
    'provider_unready': 'Accept the engine once the lane is saved.', 'model_line': 'Type the model id the engine serves.',
    'credential_sub': 'Save the lane, then Credentials → Accept Engine… runs one read-only probe turn and records the '
                      'engine version.',
    'no_member_tip': 'Add a member on this engine to a lane first; accepting probes that lane’s engine.',
    'row_state': 'read-only engine', 'accept_title': 'Accept the engine?',
    'accept_body': 'codexpool runs one read-only probe turn through the engine and records its exact version. Do it '
                   'again after an update.',
    'accept_sheet': 'Accepting the engine', 'accept_sheet_sub': 'Output from codexpool lane apply --accept-engine.',
    'accepted': 'Engine accepted. Start a new Codex thread to use the lane.',
    'demo_model': ('model-id', ''),   # the lanes-model-engine snapshot's Add Model sheet
}


def lane_copy(pid: str) -> dict:
    """The Lanes pane's words for an engine provider: the add-on's for its own provider ids, else ENGINE_COPY."""
    for ui in mb.POOL_UI.values():
        if pid in getattr(ui, 'lane_provider_ids', ()):
            return {**ENGINE_COPY, **ui.lane_copy(pid)}
    return ENGINE_COPY


def engine_state_text(detail: str) -> str:
    """'untested engine: codexpool lane apply --accept-engine' -> 'Not accepted yet'."""
    state = (detail or '').split(':', 1)[0].strip()
    return ENGINE_STATE.get(state) or plain_detail(detail) or 'Not ready'


def member_line(p: Provider, model: str) -> str:
    """'xAI · grok-4.7-build-fast'; an engine member says so: '<engine> · <model> · read-only'."""
    return f'{p.title} · {model}' + (' · read-only' if p.needs == 'engine' else '')


def state_pill(state: str):
    text, role = LANE_STATE.get(state, (state.title() or 'Unknown', 'grey'))
    fg = {'green': mb.C.green_text, 'orange': mb.C.orange_text, 'red': mb.C.red_text}.get(role, mb.C.secondary)
    bg = {'green': lambda: mb.C.soft(mb.C.green()), 'orange': lambda: mb.C.soft(mb.C.orange()),
          'red': lambda: mb.C.soft(mb.C.red())}.get(role, lambda: mb.C.wash(0.07))
    return pill(text, fg, bg)


def test_view(test: LaneTest | None, now: dt.datetime, never: str = 'Never tested', prefix: str = '',
              short: bool = False, wrap: float | None = None):
    """✓ Passed 2h ago / ✗ Failed: reason / – Never tested, for a member or a whole lane. short: a failure says
    just Failed (the reason is in its tooltip and on the lane's own line)."""
    if test is None:
        icon, text, color = 'minus.circle', never, NSColor.secondaryLabelColor()
    elif test.ok:
        ago = mb.fmt_age((now - test.when).total_seconds()) if test.when else ''
        icon, text, color = 'checkmark.circle.fill', f'{prefix}{"passed" if prefix else "Passed"} {ago}'.strip(), \
            mb.C.green_text()
    elif short:
        icon, text, color = 'xmark.circle.fill', f'{prefix}{"failed" if prefix else "Failed"}', mb.C.red_text()
    else:
        icon, text, color = 'xmark.circle.fill', \
            f'{prefix}{"failed" if prefix else "Failed"}: {test.reason or "see lane test"}', mb.C.red_text()
    v = hstack([symbol_view(icon, 11, color, NSFontWeightMedium),
                label(text, 11, color=NSColor.secondaryLabelColor() if test is None or prefix else None,
                      wrap=wrap if test is not None and not test.ok else None)],
               spacing=4, cluster=True)
    if test is not None and not test.ok and test.reason:
        v.setToolTip_(S(test.reason))
    v.setContentCompressionResistancePriority_forOrientation_(751, 0)   # the model id gives way first, not this
    return v


@dataclass
class Credential:
    """A row in the Lanes pane's Credentials: a provider's sign-in or key, or a responses member's own key."""
    title: str
    needs: str                 # login | key | engine
    key_name: str | None       # for needs == key
    ready: bool | None
    uses: list                 # the lanes that need it
    detail: str = ''
    key_title: str = ''        # what the Add Key sheet calls the key ('' = title)
    provider: str = ''         # the provider's id (an engine's words come from its add-on: lane_copy)


CLI_HINT = re.compile(r'\s*\((?:codexpool|check the key)[^)]*\)|:\s*codexpool\s.*$')


def plain_detail(text: str) -> str:
    """A CLI detail without the terminal command it suggests, capitalised: 'the xAI credential is turned off:
    codexpool enable x' -> 'The xAI credential is turned off' (the window has a button for it)."""
    d = CLI_HINT.sub('', text or '').strip().rstrip(':;,').strip()
    return d[:1].upper() + d[1:]


def infer_ready(pid: str, lanes: list, needs: str = '') -> bool | None:
    """Whether a provider is signed in / has its key / has its engine accepted, from its members' states in
    `lane list` (for a codexpool without `lane providers`)."""
    states = {m.state for lane in lanes for m in lane.members if m.provider == pid}
    if not states:
        return None
    if pid == 'xai':
        if states & {'ready', 'active', 'cooldown', 'exhausted'}:
            return True
        return False if states & {'missing', 'blocked', 'disabled'} else None
    if needs == 'engine':
        return 'engine ok' in states
    return False if 'no key' in states else True


def credentials(store: Store) -> list:
    lanes = store.lanes or []
    used: dict = {}
    for lane in lanes:
        for mem in lane.members:
            names = used.setdefault(mem.provider, [])
            if lane.name not in names:
                names.append(lane.name)
    out = []
    for p in store.provider_list():
        if p.id == 'responses':
            continue
        uses = used.get(p.id, [])
        ready = p.ready if p.ready is not None else infer_ready(p.id, lanes, p.needs)
        if ready or uses:
            out.append(Credential(p.title, p.needs if p.needs in ('login', 'engine') else 'key', p.key_name, ready,
                                  uses, p.detail, provider=p.id))
    title = store.provider('responses').title
    for lane in lanes:
        for mem in lane.members:
            if mem.provider == 'responses':
                out.append(Credential(f'{mem.name} ({title})', 'key', f'{lane.name}-{mem.id}',
                                      mem.state != 'no key' if mem.state != 'unknown' else None,
                                      [lane.name], key_title=mem.name))
    return out


class LanesPane(Pane):
    key, title, symbol, tint = 'lanes', 'Codex lanes', 'arrow.triangle.branch', 'purple'

    def __init__(self, app):
        super().__init__(app)
        self.where = 'apply'       # the group that shows self.note: apply | credentials

    def shown(self):
        if SNAPSHOT:
            return
        if self.store.lanes is None and not self.store.lanes_busy:
            self.store.fetch_lanes()
        if self.store.providers is None:
            self.store.fetch_providers()

    def busy(self) -> bool:
        return bool(self.note and self.note[0] == 'busy')

    def build(self):
        st, k = self.store, self.keep
        busy = self.busy()
        docs = button('Open Docs', lambda _: open_thing(LANES_DOCS_URL), k)
        if st.lanes is None:
            if st.lanes_busy:
                return [group([hstack([spinner(), secondary('Reading lanes…', 12)], spacing=8,
                                      insets=(14, ROW_X, 14, ROW_X))])]
            return [empty_state('arrow.triangle.branch', 'purple', 'Lanes aren’t available',
                                st.lanes_note or NEWER,
                                [button('Try Again', lambda _: st.fetch_lanes(), k), docs])]
        if not st.lanes:
            return [empty_state('arrow.triangle.branch', 'purple', 'No lanes yet',
                                'A lane hands token-heavy work, like sweeps, bulk edits and second opinions, to a '
                                'model from another provider, as a native Codex subagent. Its tokens count against '
                                'that provider, not your seats. Lanes are optional.',
                                [button('New Lane…', lambda _: self.app.edit_lane(None), k, primary=True),
                                 button('Read About Lanes', lambda _: open_thing(LANES_DOCS_URL), k)])]
        new = button('New Lane…', lambda _: self.app.edit_lane(None), k, enabled=not busy)
        intro = hstack([secondary('Lanes hand token-heavy work to models from other providers, as native Codex '
                                  'subagents. The pool serves each lane from its members in order.', 12,
                                  wrap=GROUP_W - new.fittingSize().width - 24)], [new], spacing=12,
                       insets=(0, 4, 0, 0))
        out = [intro] + [self.lane_card(lane, busy) for lane in st.lanes]
        apply_row = form_row('Apply lanes', 'Regenerates the lane config and Codex role files from lanes.json.',
                             button('Apply…', lambda _: self.confirm_apply(), k, enabled=not busy))
        rows = [apply_row, form_row('Documentation', 'How lanes work, providers, and lanes.json.', docs)]
        if self.where == 'apply':
            rows.append(note_row(self.note_now()))
        out.append(section(group(rows)))
        out.append(self.credentials_section(busy))
        return out

    def lane_card(self, lane: Lane, busy: bool):
        k = self.keep
        picker = lane.display or default_display(lane.name)
        sub = ' · '.join(x for x in (f'“{picker}” in the model picker', f'effort {lane.effort}' if lane.effort else '')
                         if x)
        title = hstack([label(lane.name, 13, NSFontWeightSemibold), secondary(sub, 11)], spacing=8, cluster=True)
        delete = button('Delete…', lambda _: self.confirm_delete(lane), k, small=True, enabled=not busy)
        actions = [button('Test…', lambda _: self.confirm_test(lane), k, small=True, enabled=not busy),
                   button('Edit…', lambda _: self.app.edit_lane(lane), k, small=True, enabled=not busy)]
        head = hstack([title], [delete] + actions, spacing=6, insets=(0, 4, 0, 2))
        head.setCustomSpacing_afterView_(18, delete)   # the destructive one stands apart from the everyday ones
        rows = []
        if lane.role:
            rows.append(padded(secondary(lane.role, 12, wrap=GROUP_W - 2 * ROW_X), 11, ROW_X, 11, ROW_X))
        for i, mem in enumerate(lane.members, 1):
            rows.append(self.member_row(i, mem))
        rows.append(hstack([test_view(lane.test, self.store.clock(), 'The lane as a whole hasn’t been tested yet',
                                      prefix='Lane test ', wrap=GROUP_W - 2 * ROW_X - 20)],   # the full reason
                           insets=(8, ROW_X, 9, ROW_X), min_h=32))
        return vstack([head, group(rows)], spacing=7)

    def member_row(self, i: int, mem: Member):
        p = self.store.provider(mem.provider)
        names = vstack([label(mem.name, 13, NSFontWeightMedium),
                        secondary(member_line(p, mem.model), 11)], spacing=2, full=False)
        row = hstack([number_badge(i), names], [test_view(mem.test, self.store.clock(), short=True),
                                                state_pill(mem.state)],
                     spacing=10, insets=(9, ROW_X, 9, ROW_X), min_h=48)
        if p.needs == 'engine' and mem.state in ENGINE_HINT:   # what the state pill asks for
            row.setToolTip_(S(ENGINE_HINT[mem.state]))
        return row

    # -- credentials -----------------------------------------------------------------------------------
    def credentials_section(self, busy: bool):
        k = self.keep
        rows = []
        for c in credentials(self.store):
            if c.needs == 'login':
                state = {True: 'Signed in', False: 'Not signed in', None: 'Not checked yet'}[c.ready]
                act = button('Sign In Again…' if c.ready else 'Sign In…',
                             lambda _: self.app.sign_in_xai(done=self.signed_in), k, enabled=not busy)
            elif c.needs == 'engine':   # an add-on's pool as a read-only engine: accepted once, by version
                copy = lane_copy(c.provider)
                state = {True: 'Engine accepted', False: engine_state_text(c.detail), None: 'Not checked yet'}[c.ready]
                act = button('Accept Again…' if c.ready else 'Accept Engine…',
                             lambda _, pid=c.provider: self.confirm_accept_engine(pid), k,
                             enabled=not busy and bool(c.uses))
                if not c.uses:
                    act.setToolTip_(S(copy['no_member_tip']))
                uses = f'used by {", ".join(c.uses)}' if c.uses else 'not used by a lane yet'
                rows.append(form_row(c.title, f'{state} · {copy["row_state"]} · {uses}', act,
                                     leading=dot(mb.C.green if c.ready else mb.C.orange if c.uses else mb.C.grey)))
                continue
            else:
                state = {True: 'Key saved', False: 'No key yet', None: 'Not checked yet'}[c.ready]
                act = button('Replace Key…' if c.ready else 'Add Key…',
                             lambda _, c=c: self.app.add_key(c.key_name, c.key_title or c.title,
                                                             replace=bool(c.ready), done=self.key_saved), k,
                             enabled=not busy and bool(c.key_name))
            uses = f'used by {", ".join(c.uses)}' if c.uses else 'not used by a lane yet'
            sub = f'{state} · {uses}'
            if c.ready is False and c.needs == 'login' and c.detail and not c.detail.startswith('not signed in') and \
                    plain_detail(c.detail):
                sub = f'{plain_detail(c.detail)} · {uses}'
            color = (mb.C.green if c.ready else mb.C.orange if c.uses else mb.C.grey) if c.ready is not None else \
                mb.C.grey
            rows.append(form_row(c.title, sub, act, leading=dot(color)))
        if not rows:
            rows.append(hstack([secondary('No provider is signed in or has a key yet.', 12)],
                               insets=(12, ROW_X, 12, ROW_X), min_h=40))
        if self.where == 'credentials':
            rows.append(note_row(self.note_now()))
        return section(group(rows), 'Credentials',
                       footer='They stay on this Mac, readable only by you. codexpool never shows a key.')

    def signed_in(self, ok: bool):
        if ok:
            self.where = 'credentials'
            self.say('ok', 'Signed in to xAI')
            self.store.fetch_providers()
            self.store.fetch_lanes()

    def key_saved(self, title: str):
        self.where = 'credentials'
        self.say('ok', f'Saved the {title} key. Apply lanes so the bridge reads it.')
        self.store.fetch_providers()
        self.store.fetch_lanes()

    def confirm_accept_engine(self, pid: str = ''):
        """`codexpool lane apply --accept-engine`: one read-only probe turn through the engine's pool, then its exact
        version is accepted (docs/LANES.md, Engine members). It streams into a sheet like a lane test."""
        copy = lane_copy(pid)
        self.app.ask(copy['accept_title'], copy['accept_body'], 'Accept',
                     lambda: self.app.stream_sheet(copy['accept_sheet'], ['lane', 'apply', '--accept-engine'],
                                                   lambda r: self.engine_accepted(r, pid),
                                                   sub=copy['accept_sheet_sub']))

    def engine_accepted(self, r: Result, pid: str = ''):
        self.where = 'credentials'
        if r.ok:
            self.say('ok', lane_copy(pid)['accepted'])
        self.store.fetch_providers()
        self.store.fetch_lanes()

    # -- lane actions ----------------------------------------------------------------------------------
    def lane_saved(self, name: str, new: bool):
        self.where = 'apply'
        self.say('ok', f'{"Added" if new else "Saved"} the {name} lane and applied it. Start a new Codex thread to '
                       'use it.')
        self.store.fetch_lanes()
        self.store.fetch_providers()

    def confirm_delete(self, lane: Lane):
        self.app.ask(f'Delete the {lane.name} lane?',
                     'codexpool takes it out of lanes.json and removes everything generated for it: its lines in the '
                     'pool config and the bridge, its Codex role file and its part of ~/.codex/AGENTS.md. Sign-ins and '
                     'keys stay.', 'Delete',
                     lambda: self.delete(lane), destructive=True)

    def delete(self, lane: Lane):
        self.where = 'apply'
        self.run(['lane', 'remove', lane.name], f'Deleting {lane.name}…', f'Deleted the {lane.name} lane',
                 then=lambda r: self.store.fetch_lanes())

    def confirm_test(self, lane: Lane):
        self.app.ask(f'Test the {lane.name} lane?',
                     'This spawns real subagents through Codex, one for each member and then the lane. It spends '
                     'lane-provider quota and a little seat quota, and it can take a few minutes.', 'Run Test',
                     lambda: self.app.stream_sheet(f'Testing {lane.name}', ['lane', 'test', lane.name],
                                                   lambda r: self.store.fetch_lanes()))

    def confirm_apply(self):
        self.app.ask('Apply lanes?',
                     'codexpool writes the lanes block in the pool config, the bridge config, the Codex role files in '
                     '~/.codex/agents and the lanes block in ~/.codex/AGENTS.md from lanes.json.', 'Apply',
                     lambda: self.apply())

    def apply(self):
        self.where = 'apply'
        self.run(['lane', 'apply'], 'Applying lanes…', 'Lanes applied', then=lambda r: self.store.fetch_lanes())


# -- General ----------------------------------------------------------------------------------------------

class GeneralPane(Pane):
    key, title, symbol, tint = 'general', 'General', 'gearshape.fill', 'grey'

    def build(self):
        m, k = self.store.model(), self.keep
        busy = bool(self.note and self.note[0] == 'busy')
        display = auto(NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
            ['Left', 'Used'], 0, target(lambda s: self.set_value('display', ('left', 'used')[s.selectedSegment()]),
                                        k), 'fire:'))
        display.setSelectedSegment_(0 if m.left else 1)
        headline = auto(NSPopUpButton.alloc().initWithFrame_pullsDown_(((0, 0), (140, 26)), False))
        headline.addItemsWithTitles_(['All seats', 'Regular seats'])
        headline.selectItemAtIndex_(0 if m.headline_mode == 'all' else 1)
        headline.setTarget_(target(lambda s: self.set_value('headline', ('all', 'regular')[s.indexOfSelectedItem()]),
                                   k))
        headline.setAction_('fire:')
        for c in (display, headline):
            c.setEnabled_(not busy)
        sample = f'{mb.fmt_pct(m.shown(m.headline))} {m.word}' if m.headline is not None else '54% left'
        menubar = [
            form_row('Numbers show', f'Left counts down from 100%, used counts up. Now: {sample}.', display),
            form_row('Headline covers', 'Regular seats leaves the reserve out of the number.', headline),
        ]
        note = note_row(self.note_now())
        if note is not None:
            menubar.append(note)
        pool = [
            form_row('Restart the pool', 'Codex requests fail for a few seconds while it restarts.',
                     button('Restart…', lambda _: self.app.restart_pool(self), k, enabled=not busy)),
            form_row('Logs', tilde(LOGS_DIR), button('Open Logs', lambda _: open_thing(str(LOGS_DIR)), k)),
            form_row('Codex app', 'Quit and reopen it after adding seats or changing lanes.',
                     button('Reopen Codex…', lambda _: self.app.reopen_codex(self), k, enabled=not busy)),
        ]
        setup = [form_row('Setup assistant', 'Add ChatGPT accounts and check the install.',
                          button('Open…', lambda _: self.app.open_setup('setup-welcome'), k))]
        return [section(group(menubar), 'Menu bar'), section(group(pool), 'Pool'), section(group(setup), 'Setup')]

    def set_value(self, key: str, value: str):
        m = self.store.model()
        if value == {'display': m.display, 'headline': m.headline_mode}.get(key):
            return   # the current choice clicked again
        words = {'left': 'what is left', 'used': 'what is used', 'all': 'all seats', 'regular': 'the regular seats'}
        self.run(['set', key, value], 'Saving…', f'The menu bar now shows {words.get(value, value)}')


# -- Health -----------------------------------------------------------------------------------------------

CHECK_ICON = {'ok': ('checkmark.circle.fill', 'green'), 'warn': ('exclamationmark.triangle.fill', 'orange'),
              'fail': ('xmark.octagon.fill', 'red'), 'unknown': ('questionmark.circle', 'grey')}


def check_color(status: str):
    """Status symbols are fills, so they take the system colours (text uses the darker *_text ones)."""
    return {'green': mb.C.green, 'orange': mb.C.orange, 'red': mb.C.red}.get(
        CHECK_ICON.get(status, CHECK_ICON['unknown'])[1], mb.C.grey)()


class HealthPane(Pane):
    key, title, symbol, tint = 'health', 'Health', 'stethoscope', 'teal'

    def shown(self):
        if not SNAPSHOT and self.store.doctor is None and not self.store.doctor_busy:
            self.store.fetch_doctor()

    def build(self):
        st, k = self.store, self.keep
        doc = st.doctor
        again = button('Run Again', lambda _: st.fetch_doctor(), k, enabled=not st.doctor_busy)
        copy = button('Copy Report', lambda _: self.copy_report(), k, enabled=doc is not None)
        if doc is None and not st.doctor_busy:
            return [empty_state('stethoscope', 'teal', 'No health report',
                                st.doctor_note or 'codexpool doctor has not run yet.',
                                [again, button('Run in Terminal', lambda _: mb.open_in_terminal(mb.cp_command('doctor')),
                                               k)])]
        if doc is None:
            head_icon = spinner(small=False)
            title, sub = 'Checking…', 'codexpool doctor is looking at the pool, Codex, seats and the guard.'
        else:
            status = 'fail' if doc.problems else 'warn' if doc.warnings else 'ok'
            head_icon = symbol_view({'ok': 'checkmark.seal.fill', 'warn': 'exclamationmark.triangle.fill',
                                     'fail': 'xmark.octagon.fill'}[status], 26, check_color(status), NSFontWeightMedium,
                                    box=32)
            if status == 'ok':
                title = 'Everything looks good'
            else:
                bits = []
                if doc.problems:
                    bits.append(f'{doc.problems} problem{"s" if doc.problems != 1 else ""}')
                if doc.warnings:
                    bits.append(f'{doc.warnings} warning{"s" if doc.warnings != 1 else ""}')
                title = ', '.join(bits)
            when = mb.fmt_age((st.clock() - st.doctor_at).total_seconds()) if st.doctor_at else ''
            sub = f'Checked {when} by codexpool doctor.'
            if st.doctor_busy:
                sub = 'Checking again…'
        head = hstack([head_icon, vstack([label(title, 15, NSFontWeightSemibold), secondary(sub, 11, wrap=300)],
                                         spacing=2, full=False)],
                      [copy, again], spacing=12, insets=(14, 14, 14, ROW_X), min_h=60)
        out = [group([head, note_row(self.note_now())])]   # note: 'Report copied'
        if st.doctor_note and doc is not None:
            out.append(group([note_row(('error', st.doctor_note))]))
        for title, checks in (doc.sections if doc else []):
            if checks:
                out.append(section(group([self.check_row(c) for c in checks]), title))
        return out

    @staticmethod
    def check_row(c: Check):
        name, _ = CHECK_ICON.get(c.status, CHECK_ICON['unknown'])
        icon = symbol_view(name, 13, check_color(c.status), NSFontWeightMedium, box=16)
        width = GROUP_W - 2 * ROW_X - 26
        text = [label(c.text, 13, wrap=width)]
        if c.fix and c.status != 'ok':
            text.append(label(f'Fix: {c.fix}', 11, color=NSColor.secondaryLabelColor(), wrap=width, select=True))
        col = vstack(text, spacing=3, full=False)
        r = hstack([icon, col], spacing=10, insets=(8, ROW_X, 8, ROW_X), min_h=34)
        r.setAlignment_(3)   # top: the icon sits on the first line
        return r

    def copy_report(self):
        doc = self.store.doctor
        if doc is None:
            return
        mark = {'ok': '✓', 'warn': '!', 'fail': '✗'}
        lines = [f'codexpool doctor ({self.store.clock().astimezone().strftime("%Y-%m-%d %H:%M")})']
        for title, checks in doc.sections:
            lines += ['', title] + [f' {mark.get(c.status, "?")} {c.text}' + (
                f'\n     → {c.fix}' if c.fix and c.status != 'ok' else '') for c in checks]
        lines += ['', 'OK' if not doc.problems else f'{doc.problems} problem(s)']
        if copy_text('\n'.join(lines) + '\n'):
            self.say('ok', 'Report copied')
        else:
            self.say('error', 'Couldn’t copy the report')


# -- About ------------------------------------------------------------------------------------------------

class AboutPane(Pane):
    key, title, symbol, tint = 'about', 'About', 'info.circle.fill', 'indigo'

    def build(self):
        st = self.store
        m = st.model()
        icon = canvas(lambda w, h: draw_app_icon(w), 96, 96)
        version = f'Version {st.version}' if st.version else ('Version unknown' if st.version_note else 'Version …')
        cpa = m.version.split('-gate')[0].split('+gate')[0]
        head = [icon, label('codexpool', 26, NSFontWeightSemibold, align=NSTextAlignmentCenter),
                secondary(version + (f'  ·  CLIProxyAPI {cpa}' if cpa else ''), 12, align=NSTextAlignmentCenter),
                label('Every ChatGPT seat you have, behind one Codex.', 13, align=NSTextAlignmentCenter)]
        hs = auto(NSStackView.stackViewWithViews_(head))
        hs.setOrientation_(1)
        hs.setAlignment_(9)
        hs.setSpacing_(4)
        hs.setCustomSpacing_afterView_(12, icon)
        hs.setCustomSpacing_afterView_(10, head[2])
        hs.setEdgeInsets_(NSEdgeInsets(6, 0, 4, 0))

        def link(title, sub, url, symbol):
            r = form_row(title, sub, symbol_view('arrow.up.right', 11, NSColor.tertiaryLabelColor(), NSFontWeightSemibold),
                         leading=canvas(lambda w, h: draw_icon_square(0, 0, 22, symbol, 'grey'), 22, 22), min_h=38)
            click = clickable(r, lambda: open_thing(url), title)
            click.setToolTip_(S(url))
            return click
        links = group([link('Website', None, SITE_URL, 'globe'),
                       link('Source code', 'github.com/memfactorduke/codex-load-balancer', REPO_URL,
                            'chevron.left.forwardslash.chevron.right'),
                       link('Documentation', None, DOCS_URL, 'book.fill'),
                       link('Report an issue', None, ISSUES_URL, 'exclamationmark.bubble.fill')])
        legal = [
            'Free and source-available under the PolyForm Noncommercial License 1.0.0: free for personal and '
            'other noncommercial use; commercial use needs permission.',
            'codexpool is an independent project, not affiliated with or endorsed by OpenAI. Codex and ChatGPT '
            'are trademarks of OpenAI. Use it only with accounts you own, and follow the terms that apply to them.',
        ]
        fine = vstack([secondary(t, 11, wrap=GROUP_W - 40, align=NSTextAlignmentCenter) for t in legal], spacing=8,
                      full=False)
        fs = padded(fine, 0, 20, 0, 20)
        return [hs, links, fs]


class LinkRowView(NSView):
    """Makes a whole row clickable (a link row), with a pointing-hand cursor. VoiceOver sees one link, ax_title."""
    on_click = None
    ax_title = ''

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    def hitTest_(self, point):
        return self if NSPointInRect(point, self.frame()) else None   # the row's controls are decoration

    def resetCursorRects(self):
        self.addCursorRect_cursor_(self.bounds(), NSCursor.pointingHandCursor())

    def mouseUp_(self, event):
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        b = self.bounds()
        if self.on_click is not None and 0 <= p.x <= b.size.width and 0 <= p.y <= b.size.height:
            self.on_click()

    def isAccessibilityElement(self):
        return True

    def accessibilityRole(self):
        return 'AXLink'

    def accessibilityLabel(self):
        return S(self.ax_title)

    def accessibilityChildren(self):
        return []   # the row's text and icons are decoration

    def accessibilityPerformPress(self):
        if self.on_click is not None:
            self.on_click()
        return True


def clickable(view, fn, ax_title: str = ''):
    v = auto(LinkRowView.alloc().initWithFrame_(((0, 0), (10, 10))))
    v.addSubview_(view)
    pin(view, v)
    v.on_click = fn
    v.ax_title = ax_title
    return v


PANE_CLASSES = (OverviewPane, SeatsPane, BalancingPane, LanesPane, GeneralPane, HealthPane, AboutPane)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 7. The Settings window: sidebar (icon squares, like System Settings) + content (toolbar title, scrolling
#    sections)
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

class SettingsWindow(NSWindow):
    pass


class KeyLookWindow(SettingsWindow):
    """Snapshots only: draws as the key, main window (coloured traffic lights, accent controls), which an
    offscreen window never is."""

    def isKeyWindow(self):
        return True

    def isMainWindow(self):
        return True

    def _hasActiveAppearance(self):
        return True

    def _hasActiveAppearanceIgnoringKeyFocus(self):
        return True

    def _hasKeyAppearance(self):
        return True

    def _hasMainAppearance(self):
        return True


def make_window(w: float, h: float, title: str, toolbar: bool, resizable: bool):
    style = NSWindowStyleMaskTitled | NSWindowStyleMaskClosable | NSWindowStyleMaskMiniaturizable | \
        NSWindowStyleMaskFullSizeContentView | (NSWindowStyleMaskResizable if resizable else 0)
    cls = KeyLookWindow if SNAPSHOT else SettingsWindow
    origin = (-20000, -20000) if SNAPSHOT else (0, 0)
    win = cls.alloc().initWithContentRect_styleMask_backing_defer_((origin, (w, h)), style, NSBackingStoreBuffered,
                                                                   False)
    win.setReleasedWhenClosed_(False)
    win.setTitle_(S(title))
    win.setTitlebarAppearsTransparent_(True)
    win.setTitleVisibility_(NSWindowTitleHidden)
    if toolbar:   # an empty unified toolbar: a 52 pt band with the traffic lights centred in it
        tb = NSToolbar.alloc().initWithIdentifier_(S(f'codexpool.{title}'))
        tb.setShowsBaselineSeparator_(False)
        win.setToolbar_(tb)
        win.setToolbarStyle_(NSWindowToolbarStyleUnified)
    root = FlippedView.alloc().initWithFrame_(((0, 0), (w, h)))
    win.setContentView_(root)
    return win, root


class SidebarItem(NSView):
    """One sidebar row: icon square + title; accent highlight when selected (grey when the window isn't key)."""
    key = ''
    title = ''
    symbol = ''
    tint = 'grey'
    selected = False
    on_click = None

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    def drawRect_(self, rect):
        b = self.bounds()
        key_window = self.window() is not None and self.window().isKeyWindow()
        if self.selected:
            color = NSColor.selectedContentBackgroundColor() if key_window else \
                NSColor.unemphasizedSelectedContentBackgroundColor()
            mb.fill_rounded(((10, 1), (b.size.width - 20, b.size.height - 2)), 6, color)
        draw_icon_square(18, (b.size.height - 20) / 2, 20, self.symbol, self.tint)
        f = mb.font(13)
        fg = NSColor.whiteColor() if self.selected and key_window else NSColor.labelColor()
        mb.draw_text(self.title, 46, (b.size.height - mb.line_height(f)) / 2, f, fg, width=b.size.width - 56)

    def mouseDown_(self, event):
        if self.on_click is not None:
            self.on_click(self.key)

    def accessibilityRole(self):
        return 'AXButton'

    def accessibilityLabel(self):
        return self.title

    def isAccessibilityElement(self):
        return True

    def accessibilityPerformPress(self):
        if self.on_click is not None:
            self.on_click(self.key)
        return True


class SidebarHeader(NSView):
    """The top of the sidebar: the app icon, the wordmark and one line of status (clicks open Overview)."""
    line = ''
    line_color = None
    line2 = ''           # the add-on pool's line, when it is installed
    line2_color = None
    on_click = None

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    def drawRect_(self, rect):
        b = self.bounds()
        NSGraphicsContext.saveGraphicsState()
        t = NSAffineTransform.transform()
        t.translateXBy_yBy_(12, (b.size.height - 38) / 2)
        t.concat()
        draw_app_icon(38, shadow=False)
        NSGraphicsContext.restoreGraphicsState()
        tf, sf = mb.font(13, NSFontWeightSemibold), mb.font(11)
        lines = [(self.line, self.line_color)] + ([(self.line2, self.line2_color)] if self.line2 else [])
        y = (b.size.height - mb.line_height(tf) - len(lines) * mb.line_height(sf)) / 2
        mb.draw_text('codexpool', 54, y, tf, mb.C.label(), width=b.size.width - 64)
        y += mb.line_height(tf)
        for text, color in lines:
            mb.draw_text(text, 54, y, sf, (color or mb.C.secondary)(), width=b.size.width - 64)
            y += mb.line_height(sf)

    def mouseDown_(self, event):
        if self.on_click is not None:
            self.on_click('overview')


def sidebar_status(m: mb.Model, prefix: str = '', unit: str = 'seat'):
    """(line, colour function) under the wordmark, coloured like the menu bar number; prefix names the pool when
    both are installed."""
    color = lambda: mb.headline_text(m)   # noqa: E731 (resolved when drawn, for the view's appearance)
    if m.status in ('regular', 'reserve') and m.headline is not None:
        who = m.serving.label if m.serving else ''
        return f'{prefix}{mb.fmt_pct(m.shown(m.headline))} {m.word} · {who}', color
    if prefix:   # '<pool> pool is down'
        return (f'{prefix}pool ' + {'allout': 'is all out', 'down': 'is down', 'stale': 'isn’t reporting',
                                    'missing': 'isn’t reporting', 'empty': 'is empty'}.get(m.status, m.status), color)
    return ({'allout': f'Every {unit} is out', 'down': 'Pool is down', 'stale': 'Not reporting',
             'missing': 'Not reporting', 'empty': f'No {unit}s yet'}.get(m.status, m.status), color)


def sidebar_lines(store: Store) -> tuple:
    """(line, colour, line2, colour2): the Codex pool, and the add-on's pool once it is installed."""
    second = POOLS[1] if len(POOLS) > 1 and store.installed(POOLS[1]) else None
    if second is None:
        return (*sidebar_status(store.model()), '', None)
    return (*sidebar_status(store.model(), 'Codex '),
            *sidebar_status(store.model(second), f'{POOL_TITLES[second]} ', pool_noun(second)))


class SettingsView:
    """Builds and updates the Settings window."""

    GROUPS = (('overview', 'seats', 'balancing', 'lanes'), ('general', 'health', 'about'))

    def __init__(self, app, panes: dict, height: float = WIN_H):
        self.app = app
        self.panes = panes
        self.current = 'overview'
        self.win, root = make_window(WIN_W, height, 'codexpool Settings', toolbar=True, resizable=True)
        self.win.setContentMinSize_((WIN_W, MIN_H))
        self.win.setContentMaxSize_((WIN_W, 4000))
        if not SNAPSHOT:
            self.win.setFrameAutosaveName_('codexpool.settings')
        # sidebar
        side = NSVisualEffectView.alloc().initWithFrame_(((0, 0), (SIDEBAR_W, height)))
        side.setMaterial_(NSVisualEffectMaterialSidebar)
        side.setBlendingMode_(NSVisualEffectBlendingModeBehindWindow)
        side.setState_(NSVisualEffectStateFollowsWindowActiveState)
        side.setAutoresizingMask_(NSViewHeightSizable)
        root.addSubview_(side)
        line = PaintView.alloc().initWithFrame_(((SIDEBAR_W - 1, 0), (1, height)))
        line.paint = lambda w, h: mb.fill_rect(((0, 0), (w, h)), K.sidebar_line())
        line.setAutoresizingMask_(NSViewHeightSizable)
        root.addSubview_(line)
        self.header = SidebarHeader.alloc().initWithFrame_(((0, BAR_H), (SIDEBAR_W, 50)))
        self.header.on_click = self.select
        root.addSubview_(self.header)
        self.items = {}
        y = BAR_H + 50 + 14
        for keys in self.GROUPS:
            for key in keys:
                pane = panes[key]
                item = SidebarItem.alloc().initWithFrame_(((0, y), (SIDEBAR_W, 30)))
                item.key, item.title, item.symbol, item.tint = key, pane.title, pane.symbol, pane.tint
                item.on_click = self.select
                root.addSubview_(item)
                self.items[key] = item
                y += 30
            y += 14
        # content: the toolbar title, then the scrolling sections
        self.title = label('', 15, NSFontWeightBold)
        self.title.setTranslatesAutoresizingMaskIntoConstraints_(True)
        self.title.setFrame_(((SIDEBAR_W + MARGIN, (BAR_H - 20) / 2), (CONTENT_W - 2 * MARGIN, 20)))
        root.addSubview_(self.title)
        sv = self.scroll = NSScrollView.alloc().initWithFrame_(((SIDEBAR_W, BAR_H), (CONTENT_W, height - BAR_H)))
        sv.setContentView_(FlippedClipView.alloc().initWithFrame_(((0, 0), (CONTENT_W, height - BAR_H))))
        sv.setDrawsBackground_(False)
        sv.contentView().setDrawsBackground_(False)
        sv.setBorderType_(NSNoBorder)
        sv.setHasVerticalScroller_(not SNAPSHOT)   # an overlay scroller would show up in snapshots
        sv.setAutohidesScrollers_(True)
        sv.setAutoresizingMask_(NSViewHeightSizable)
        root.addSubview_(sv)
        self.doc = auto(FlippedView.alloc().initWithFrame_(((0, 0), (CONTENT_W, 100))))
        sv.setDocumentView_(self.doc)
        clip = sv.contentView()
        activate(self.doc.leadingAnchor().constraintEqualToAnchor_(clip.leadingAnchor()),
                 self.doc.topAnchor().constraintEqualToAnchor_(clip.topAnchor()),
                 self.doc.widthAnchor().constraintEqualToConstant_(CONTENT_W))
        self.body = None

    def select(self, key: str):
        if key not in self.panes:
            return
        changed = key != self.current
        self.current = key
        pane = self.panes[key]
        pane.shown()
        self.render(reset_scroll=changed)

    def render(self, reset_scroll: bool = False):
        pane = self.panes[self.current]
        for key, item in self.items.items():
            item.selected = key == self.current
            item.setNeedsDisplay_(True)
        h = self.header
        h.line, h.line_color, h.line2, h.line2_color = sidebar_lines(self.app.store)
        h.setNeedsDisplay_(True)
        self.title.setStringValue_(S(pane.title))
        self.win.setTitle_(S(f'{pane.title} · codexpool'))
        y = 0.0 if reset_scroll else self.scroll.contentView().bounds().origin.y
        pane.keep = []
        sections = [s for s in pane.build() if s is not None]
        body = vstack(sections, spacing=22, insets=(6, MARGIN, 28, MARGIN))
        if self.body is not None:
            self.body.removeFromSuperview()
        self.body = body
        self.doc.addSubview_(body)
        pin(body, self.doc)
        self.doc.layoutSubtreeIfNeeded()
        self.doc.scrollPoint_((0, y))

    def content_height(self) -> float:
        self.doc.layoutSubtreeIfNeeded()
        return self.doc.fittingSize().height


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 8. Sheets on the Settings window: the lane editor, Add Model, Add Key, the xAI sign-in
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

SHEET_PAD = 20.0
SHEET_RADIUS = 12.0            # snapshots: the corners of a sheet drawn over the window


def make_sheet(w: float):
    """A window to show as a sheet (drawn as the key window in snapshots, like the Settings window)."""
    cls = KeyLookWindow if SNAPSHOT else NSWindow
    origin = (-20000, -20000) if SNAPSHOT else (0, 0)
    win = cls.alloc().initWithContentRect_styleMask_backing_defer_((origin, (w, 200)), NSWindowStyleMaskTitled,
                                                                   NSBackingStoreBuffered, False)
    win.setReleasedWhenClosed_(False)
    win.setContentView_(FlippedView.alloc().initWithFrame_(((0, 0), (w, 200))))
    return win


def cancel_button(fn, keep: list, enabled: bool = True):
    b = button('Cancel', fn, keep, enabled=enabled)
    b.setKeyEquivalent_('\x1b')   # Escape
    return b


def popup(titles, selected: int, fn, keep: list, width: float = 140.0):
    p = auto(NSPopUpButton.alloc().initWithFrame_pullsDown_(((0, 0), (width, 26)), False))
    p.addItemsWithTitles_([S(t) for t in titles])
    p.selectItemAtIndex_(selected)
    p.setTarget_(target(fn, keep))
    p.setAction_('fire:')
    fix(p, width)
    return p


class Sheet:
    """A sheet: a title, sections built by build(), and a row of buttons, rebuilt by render(). It hangs on the
    Settings window, or on another sheet (parent). harvest() reads what was typed before a rebuild."""
    width = 460.0

    def __init__(self, app, parent: Sheet | None = None):
        self.app = app
        self.parent = parent
        self.keep: list = []
        self.body = None
        self.open = False
        self.child: Sheet | None = None
        self.win = make_sheet(self.width)

    @property
    def inner(self) -> float:
        return self.width - 2 * SHEET_PAD

    def host(self):
        return self.parent.win if self.parent is not None else self.app.ensure_view().win

    def heading(self) -> tuple:
        return '', None

    def build(self) -> list:
        return []

    def buttons(self) -> tuple:
        return [], []

    def harvest(self):
        """Reads what the user typed into the controls, before they are rebuilt."""

    def ended(self):
        """The sheet closed: stop whatever it started (a sign-in)."""

    def render(self):
        self.harvest()
        self.keep = []
        root = self.win.contentView()
        if self.body is not None:
            self.body.removeFromSuperview()
        title, sub = self.heading()
        head = [label(title, 15, NSFontWeightSemibold)] + ([secondary(sub, 11, wrap=self.inner)] if sub else [])
        left, right = self.buttons()
        parts = [vstack(head, spacing=3, full=False)] + [v for v in self.build() if v is not None] + \
            [hstack(left, right, spacing=10, insets=(4, 0, 0, 0), min_h=28)]
        body = vstack(parts, spacing=16, insets=(SHEET_PAD, SHEET_PAD, 18, SHEET_PAD))
        root.addSubview_(body)
        activate(body.topAnchor().constraintEqualToAnchor_(root.topAnchor()),
                 body.leadingAnchor().constraintEqualToAnchor_(root.leadingAnchor()),
                 body.widthAnchor().constraintEqualToConstant_(self.width))
        self.body = body
        body.layoutSubtreeIfNeeded()
        h = math.ceil(body.fittingSize().height)
        content = self.win.contentRectForFrameRect_(self.win.frame())
        if abs(content.size.height - h) > 0.5:   # grow or shrink with the top edge where it is
            top = content.origin.y + content.size.height
            frame = self.win.frameRectForContentRect_(((content.origin.x, top - h), (self.width, h)))
            self.win.setFrame_display_animate_(frame, True, self.open and not SNAPSHOT)
        root.layoutSubtreeIfNeeded()

    def present(self):
        self.render()
        self.open = True
        if not SNAPSHOT:
            self.host().beginSheet_completionHandler_(self.win, None)

    def close(self):
        if self.child is not None:
            self.child.close()
        if not self.open:
            return
        self.open = False
        self.ended()
        self.host().endSheet_(self.win)
        self.win.orderOut_(None)
        if self.parent is not None and self.parent.child is self:
            self.parent.child = None
        self.app.sheet_closed(self)


# -- the lane editor -----------------------------------------------------------------------------------

@dataclass(eq=False)
class Draft:
    """A member in the lane editor: one of the lane's (id set) or one added in this edit (id '')."""
    id: str
    provider: str
    model: str
    name: str = ''             # its display name; '' = the model id
    state: str = ''            # from lane list, for the lane's own members
    base_url: str = ''         # a responses member's endpoint

    def spec(self) -> str:
        return f'{self.provider}:{self.model}' + (f':{self.name}' if self.name else '')


def lane_args(lane: Lane | None, name: str, display: str, effort: str, role: str, members: list) -> list | None:
    """The one codexpool command that saves the lane editor: `lane add` for a new lane, else `lane edit` with the
    changes (None when nothing changed). lane edit applies its flags in the order given: the removals first, then the
    additions (they go to the end, so they are added in the order the list has them), then the moves, from the last
    place to the first, which puts every member where the list has it."""
    url = next((d.base_url for d in members if d.provider == 'responses' and not d.id and d.base_url), '')
    role = ' '.join(role.split())
    if lane is None:
        args = ['lane', 'add', name]
        for d in members:
            args.append(f'--member={d.spec()}')
        args += ['--effort', effort]
        if role:
            args.append(f'--role={role}')          # the = form: a text that starts with - stays a value
        if display:
            args.append(f'--display={display}')
        return args + ([f'--base-url={url}'] if url else [])
    ops = []
    if role and role != ' '.join(lane.role.split()):
        ops.append(f'--role={role}')
    if effort != lane.effort:
        ops += ['--effort', effort]
    shown = lane.display if lane.display and lane.display != default_display(lane.name) else ''
    if display != shown:
        ops.append(f'--display={display}' if display else '--no-display')
    keep = {d.id for d in members if d.id}
    order = []   # the lane's members as lane edit will have them after each step: ids, and Drafts for new ones
    for m in lane.members:
        if m.id in keep:
            order.append(m.id)
        else:
            ops += ['--remove-member', m.id]
    for d in members:
        if not d.id:
            ops.append(f'--add-member={d.spec()}')
            order.append(d)
    want = [d.id or d for d in members]
    for pos in range(len(want), 0, -1):
        item = want[pos - 1]
        if isinstance(item, str) and order.index(item) != pos - 1:
            order.remove(item)
            order.insert(pos - 1, item)
            ops += ['--move-member', item, '--to', str(pos)]
    if not ops:
        return None
    return ['lane', 'edit', lane.name] + ops + ([f'--base-url={url}'] if url else [])


class LaneEditor(Sheet):
    """New Lane… and Edit…: the lane's name (new lanes), picker label, effort, role and members. Save runs one
    `codexpool lane add` or `lane edit`, which also applies the lanes."""
    width = SHEET_W

    def __init__(self, app, lane: Lane | None, on_saved):
        super().__init__(app)
        self.lane, self.new, self.on_saved = lane, lane is None, on_saved
        self.name = ''
        self.display = '' if lane is None or not lane.display or lane.display == default_display(lane.name) else \
            lane.display
        self.effort = lane.effort if lane is not None and lane.effort in EFFORTS else 'xhigh'
        self.role = lane.role if lane is not None else ''
        self.members = [Draft(m.id, m.provider, m.model, '' if m.name == m.model else m.name, m.state)
                        for m in (lane.members if lane is not None else [])]
        self.problem = ''          # why Save didn't go ahead
        self.output = ''           # ... and what the command printed
        self.missing_keys: list = []   # the API keys lane apply asked for, each with an Add Key… here
        self.job = None
        self.fields: dict = {}

    def harvest(self):
        f = self.fields
        if 'name' in f:
            self.name = str(f['name'].stringValue()).strip()
        if 'display' in f:
            self.display = str(f['display'].stringValue()).strip()
        if 'role' in f:
            self.role = str(f['role'].string())
        if 'effort' in f:
            self.effort = EFFORTS[max(0, f['effort'].indexOfSelectedItem())]

    def heading(self):
        if self.new:
            return 'New Lane', 'Codex spawns a lane as a subagent, and the pool serves it from its models in order.'
        return f'Edit “{self.lane.name}”', 'Saving updates the lane in Codex; new threads use it.'

    def build(self):
        k, W = self.keep, self.inner
        busy = self.job is not None
        self.fields = {}
        rows = []
        if self.new:
            f = self.fields['name'] = text_field(self.name, 220, 'e.g. review')
            rows.append(form_row('Name', 'Lowercase letters, digits and -.', f, width=W))
        base = self.name if self.new else self.lane.name
        disp = self.fields['display'] = text_field(self.display, 220, default_display(base) if base else 'Review')
        rows.append(form_row('Picker label', 'Its name in the Codex model picker.', disp, width=W))
        eff = self.fields['effort'] = popup(EFFORTS, EFFORTS.index(self.effort), lambda _s: None, k, width=110)
        rows.append(form_row('Effort', 'How hard the lane’s models think.', eff, width=W))
        role, self.fields['role'] = role_field(self.role, W - 2 * ROW_X)
        rows.append(vstack([row_text('Role', 'What the lane is for. The main agent reads it to decide when to use the '
                                             'lane.', W - 2 * ROW_X), role], spacing=8, full=False,
                           insets=(8, ROW_X, 12, ROW_X)))
        for key, c in self.fields.items():
            if key == 'role':
                c.setEditable_(not busy)
                c.setSelectable_(not busy)
            else:
                c.setEnabled_(not busy)
        out = [group(rows, W)]
        members = [self.member_row(i, d, busy) for i, d in enumerate(self.members)]
        if not members:
            members.append(hstack([secondary('No models yet. Add at least one.', 12)],
                                  insets=(12, ROW_X, 12, ROW_X), min_h=40))
        add = button('Add Model…', lambda _: self.add_model(), k, enabled=not busy)
        members.append(hstack([secondary('The pool serves the lane from the first member that is available.', 11,
                                         wrap=W - 2 * ROW_X - add.fittingSize().width - 16)], [add],
                              insets=(8, ROW_X, 8, ROW_X - 2), min_h=40))
        out.append(section(group(members, W), 'Members', header_right=secondary('In fallback order', 11), width=W))
        if self.problem:
            out.append(self.problem_box(W))
        return out

    def member_row(self, i: int, d: Draft, busy: bool):
        k = self.keep
        title = d.name or d.model
        names = vstack([label(title, 13, NSFontWeightMedium),
                        secondary(member_line(self.app.store.provider(d.provider), d.model), 11)],
                       spacing=2, full=False)
        right = [state_pill(d.state) if d.state else pill('New', mb.C.blue_text,
                                                          lambda: mb.C.soft(NSColor.systemBlueColor()))]
        right.append(up_down(lambda step, i=i: self.move(i, step), k, i > 0, i < len(self.members) - 1,
                             enabled=not busy, what=title))
        right.append(icon_button('minus', lambda _, i=i: self.remove(i), k, f'Remove {title}', enabled=not busy))
        return hstack([number_badge(i + 1), names], right, spacing=8, insets=(8, ROW_X, 8, ROW_X), min_h=46)

    def problem_box(self, W: float):
        rows = [note_row(('error', self.problem), W)]
        busy = self.job is not None
        for key in self.missing_keys:
            title = self.key_title(key)
            rows.append(form_row(f'{title} key', 'It stays on this Mac and is never shown.',
                                 button('Add Key…', lambda _, key=key, title=title: self.app.add_key(
                                     key, title, parent=self, done=lambda _t, key=key: self.key_added(key)),
                                     self.keep, enabled=not busy), width=W))
        if self.output:
            rows.append(padded(label(self.output, 11, color=NSColor.secondaryLabelColor(), mono=True,
                                     wrap=W - 2 * ROW_X - 30, select=True), 0, ROW_X + 30, 10, ROW_X))
        return group(rows, W, rules=False)

    def key_title(self, key: str) -> str:
        """What the Add Key sheet calls a key lane apply asked for: a provider's, or a new responses member's."""
        p = next((q for q in self.app.store.provider_list() if q.key_name == key), None)
        if p is not None:
            return p.title
        d = next((d for d in self.members if d.provider == 'responses' and not d.id), None)
        if d is not None:
            host = re.sub(r'^https://([^/:]+).*$', r'\1', d.base_url)
            return d.name or host or d.model
        return key

    def key_added(self, key: str):
        """A key lane apply asked for is saved: save again once none is missing (nothing was changed before)."""
        if key in self.missing_keys:
            self.missing_keys.remove(key)
        self.app.store.fetch_providers()
        if not self.missing_keys:
            self.save()
        else:
            self.render()

    def buttons(self):
        k = self.keep
        busy = self.job is not None
        left = [hstack([spinner(), secondary('Saving and applying…', 11)], spacing=6, cluster=True)] if busy else []
        save = button('Add Lane' if self.new else 'Save', lambda _: self.save(), k, primary=True,
                      enabled=not busy and bool(self.members))
        return left, [cancel_button(lambda _: self.close(), k, enabled=not busy), save]

    def move(self, i: int, step: int):
        j = i + step
        if 0 <= j < len(self.members):
            self.members[i], self.members[j] = self.members[j], self.members[i]
            self.render()

    def remove(self, i: int):
        del self.members[i]
        self.render()

    def add_model(self):
        self.harvest()
        self.app.present_sheet(AddModelSheet(self.app, self, any(d.provider == 'xai' for d in self.members),
                                             self.added), self)

    def added(self, d: Draft):
        self.members.append(d)
        self.problem = self.output = ''
        self.render()

    def check(self) -> str:
        if self.new:
            if not self.name:
                return 'Give the lane a name, e.g. review.'
            if not LANE_NAME_OK.match(self.name) or self.name == 'default':
                return ('A lane name is lowercase letters, digits and -, starts with a letter and has at most 31 '
                        'characters.')
            if any(lane.name == self.name for lane in (self.app.store.lanes or [])):
                return f'There is already a lane called {self.name}.'
        if len(self.display) > DISPLAY_MAX:
            return f'The picker label has {len(self.display)} characters; the picker shows at most {DISPLAY_MAX}.'
        if not self.new and not self.role.strip():
            return 'Say what the lane is for: the main agent reads the role to decide when to use it.'
        if not self.members:
            return 'Add at least one model.'
        if len({d.base_url for d in self.members if d.provider == 'responses' and not d.id}) > 1:
            return 'Add one Responses endpoint at a time: save the lane, then add the next one.'
        if sum(d.provider == 'xai' for d in self.members) > 1:
            return 'A lane can have one xAI member: the pool has one xAI sign-in to fall back from.'
        return ''

    def save(self):
        self.harvest()
        problem = self.check()
        if problem:
            self.problem, self.output, self.missing_keys = problem, '', []
            self.render()
            return
        args = lane_args(self.lane, self.name, self.display, self.effort, self.role, self.members)
        if args is None:   # nothing changed
            self.close()
            return
        self.problem = self.output = ''
        self.missing_keys = []
        name = self.name if self.new else self.lane.name

        def done(r: Result):
            self.job = None
            if r.ok:
                self.close()
                self.on_saved(name, self.new)
                return
            if r.unknown:
                self.problem, self.output = NEWER, ''
            else:
                text = r.err.strip() or r.out
                missing = list(dict.fromkeys(re.findall(r'no API key for ([a-z0-9][a-z0-9-]{0,63})', text)))
                if missing:   # lane apply stopped before changing anything: add the keys here, then it saves
                    self.missing_keys = missing
                    self.problem = ('Add the API key it needs, then the lane is saved.' if len(missing) == 1 else
                                    'Add the API keys it needs, then the lane is saved.')
                    self.output = ''
                else:
                    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
                    lines = [ln[len('codexpool: '):] if ln.startswith('codexpool: ') else ln for ln in lines]
                    self.problem = 'Couldn’t save the lane.'
                    self.output = '\n'.join(lines[-8:])
            self.render()
        self.job = Job(args, done)
        self.render()


ROLE_MIN_H, ROLE_MAX_H = 58.0, 150.0
ROLE_INSET = (4.0, 5.0)   # the text view's container inset (x, y)


def role_field(text: str, width: float):
    """A lane's role: a wrapping text view in a rounded box, as tall as its text (58 to 150 pt), scrolling past
    that. Return and Tab end editing, like a field (a role is one paragraph: role_text flattens line breaks).
    Returns (box, text view); read it with textView.string()."""
    font = mb.font(12)
    tv_w = width - 2
    scroll = NSTextView.scrollableTextView()
    scroll.setBorderType_(NSNoBorder)
    scroll.setDrawsBackground_(False)
    scroll.setAutohidesScrollers_(True)
    scroll.setHasHorizontalScroller_(False)
    tv = scroll.documentView()
    tv.setRichText_(False)
    tv.setImportsGraphics_(False)
    tv.setAllowsUndo_(True)
    tv.setFieldEditor_(True)
    tv.setFont_(font)
    tv.setTextColor_(NSColor.labelColor())
    tv.setDrawsBackground_(False)
    tv.setTextContainerInset_(ROLE_INSET)
    tv.setString_(S(text))
    try:   # NSTextView's placeholder (a documented property since macOS 10.x, set through KVC)
        tv.setValue_forKey_(NSAttributedString.alloc().initWithString_attributes_(
            S('What the lane is for, e.g. codebase sweeps, bulk edits and second opinions'),
            {NSFontAttributeName: font, NSForegroundColorAttributeName: NSColor.placeholderTextColor()}),
            'placeholderAttributedString')
    except Exception:  # noqa: BLE001 - only the hint is lost
        pass
    pad = tv.textContainer().lineFragmentPadding()
    need = NSAttributedString.alloc().initWithString_attributes_(S(text or ' '), {NSFontAttributeName: font}) \
        .boundingRectWithSize_options_((tv_w - 2 * ROLE_INSET[0] - 2 * pad, 1e4),
                                       NSStringDrawingUsesLineFragmentOrigin).size.height
    h = max(ROLE_MIN_H, min(math.ceil(need) + 2 * ROLE_INSET[1] + 4, ROLE_MAX_H))
    box = auto(NSBox.alloc().initWithFrame_(((0, 0), (width, h))))
    box.setBoxType_(4)          # NSBoxCustom: drawn with the colours below, which follow light and dark
    box.setTitlePosition_(0)    # NSNoTitle
    box.setBorderColor_(NSColor.separatorColor())
    box.setFillColor_(NSColor.textBackgroundColor())
    box.setBorderWidth_(1.0)
    box.setCornerRadius_(6.0)
    box.setContentViewMargins_((0, 0))
    scroll.setFrame_(((0, 0), (width - 2, h - 2)))
    box.setContentView_(scroll)
    fix(box, width, h)
    return box, tv


# -- Add Model -----------------------------------------------------------------------------------------

class AddModelSheet(Sheet):
    """A provider, a model (listed by `codexpool lane models`, or typed), a display name; a provider that isn't ready
    gets its Sign In or Add Key right here."""
    width = 520.0

    def __init__(self, app, parent: Sheet, has_xai: bool, on_add, provider: str | None = None):
        super().__init__(app, parent)
        self.has_xai, self.on_add = has_xai, on_add
        usable = [p for p in app.store.provider_list() if not (p.id == 'xai' and has_xai)]
        first = next((p for p in usable if p.ready), usable[0] if usable else app.store.provider_list()[0])
        self.provider = provider or first.id
        self.model = self.name = self.auto_name = self.base_url = ''
        self.models: list | None = None
        self.models_note = ''
        self.loading = False
        self.problem = ''
        self.fields: dict = {}
        self.load_models()

    def harvest(self):
        f = self.fields
        for key in ('model', 'name', 'base_url'):
            if key in f:
                setattr(self, key, str(f[key].stringValue()).strip())

    def load_models(self):
        pid = self.provider
        p = self.app.store.provider(pid)
        self.models, self.models_note, self.loading = self.app.store.models.get(pid), '', False
        if self.models is not None or pid == 'responses' or p.needs == 'engine' or (p.needs == 'key' and p.ready is False):
            return   # listed already; typed by hand (responses, and the engine has no catalog); or it needs its key
        self.loading = True

        def done(models, note):
            if pid != self.provider:
                return
            self.loading, self.models, self.models_note = False, models, note
            if self.open:
                self.render()
        self.app.store.fetch_models(pid, done)

    def heading(self):
        return 'Add a Model', 'It joins the lane last; move it up in the list afterwards.'

    def build(self):
        k, W = self.keep, self.inner
        st = self.app.store
        p = st.provider(self.provider)
        provs = st.provider_list()
        self.fields = {}
        pop = popup([q.title + (' (one per lane)' if q.id == 'xai' and self.has_xai else '') for q in provs],
                    max(0, next((i for i, q in enumerate(provs) if q.id == p.id), 0)),
                    lambda s: self.pick_provider(provs[s.indexOfSelectedItem()].id), k, width=240)
        pop.setAutoenablesItems_(False)
        for i, q in enumerate(provs):
            if q.id == 'xai' and self.has_xai:
                pop.itemAtIndex_(i).setEnabled_(False)
        rows = [form_row('Provider', self.provider_line(p), pop, width=W)]
        if p.id == 'responses':
            url = self.fields['base_url'] = text_field(self.base_url, 240, 'https://api.example.com/v1')
            rows.append(form_row('Base URL', 'The bridge posts to <base URL>/responses.', url, width=W))
        combo = auto(NSComboBox.alloc().initWithFrame_(((0, 0), (240, 26))))
        combo.setCompletes_(True)
        combo.setNumberOfVisibleItems_(10)
        combo.addItemsWithObjectValues_([S(m.id) for m in self.models or []])
        combo.setStringValue_(S(self.model))
        combo.setPlaceholderString_(S('Model id'))
        combo.setTarget_(target(lambda s: self.picked(), k))
        combo.setAction_('fire:')
        fix(combo, 240)
        self.fields['model'] = combo
        model = hstack([spinner(), combo], spacing=6, cluster=True) if self.loading else combo
        model_row = form_row('Model', self.model_line(p), model, width=W)
        if self.models_note and self.models_note != NEWER and not self.loading and self.models is None:
            model_row.setToolTip_(S(plain_detail(self.models_note)))   # why the list failed, without the CLI hint
        rows.append(model_row)
        name = self.fields['name'] = text_field(self.name, 240, self.default_name() or 'The model id')
        rows.append(form_row('Display name', 'Its name here and in the lane description Codex reads.', name, width=W))
        if self.problem:
            rows.append(note_row(('error', self.problem), W))
        out = [group(rows, W)]
        if p.ready is False and p.id != 'responses':
            out.append(self.credential_box(p, W))
        return out

    def provider_line(self, p: Provider) -> str:
        if p.id == 'responses':
            return 'Your own endpoint. Saving the lane asks for its API key.'
        if p.needs == 'login':
            return {True: 'Signed in.', False: 'Not signed in yet.'}.get(p.ready, 'Signs in with your account.')
        if p.needs == 'engine':
            copy = lane_copy(p.id)
            return copy['provider_line'] + \
                {True: copy['provider_ready'], False: copy['provider_unready']}.get(p.ready, '')
        return {True: 'Key saved.', False: 'Needs an API key.'}.get(p.ready, 'Uses an API key.')

    def model_line(self, p: Provider) -> str:
        if p.id == 'responses':
            return 'Type the model id the endpoint expects.'
        if p.needs == 'engine':
            return lane_copy(p.id)['model_line']
        if self.loading:
            return 'Loading the models…'
        if self.models is not None:
            n = len(self.models)
            return f'{n} model{"s" if n != 1 else ""} listed, or type an id.' if n else 'None listed; type an id.'
        if p.ready is False:
            return 'Add its key to list them.' if p.needs == 'key' else 'Sign in to list them.'
        if self.models_note == NEWER:
            return 'This codexpool can’t list models yet; type an id.'
        return 'Couldn’t list the models; type an id.' if self.models_note else 'Type an id.'

    def default_name(self) -> str:
        m = next((m for m in self.models or [] if m.id == self.model), None)
        return m.name if m and m.name else ''

    def credential_box(self, p: Provider, W: float):
        k = self.keep
        if p.needs == 'engine':   # accepting needs a lane with the member: after Save, in Credentials
            icon = symbol_view('checkmark.seal', 13, mb.C.orange_text(), NSFontWeightMedium, box=18)
            return group([form_row(engine_state_text(p.detail) if p.detail else 'Engine not accepted yet',
                                   lane_copy(p.id)['credential_sub'], (), leading=icon, width=W)], W)
        if p.needs == 'login':
            title, sub = 'Not signed in', f'Sign in to {p.title} so the pool can serve its models.'
            act = button(f'Sign In to {p.title}…', lambda _: self.app.sign_in_xai(parent=self, done=self.signed_in), k)
        else:
            title, sub = 'No key yet', f'{p.title} needs an API key. It stays on this Mac and is never shown.'
            act = button('Add Key…', lambda _: self.app.add_key(p.key_name, p.title, parent=self, done=self.key_saved),
                         k, enabled=bool(p.key_name))
        icon = symbol_view('key.fill', 13, mb.C.orange_text(), NSFontWeightMedium, box=18)
        return group([form_row(title, sub, act, leading=icon, width=W)], W)

    def ready_now(self, pid: str):
        """A sign-in or key just worked: the provider is ready (until `lane providers` says otherwise)."""
        p = next((q for q in self.app.store.providers or [] if q.id == pid), None)
        if p is not None:
            p.ready = True
        self.app.store.models.pop(pid, None)
        self.app.store.fetch_providers()
        if pid == self.provider:
            self.harvest()
            self.load_models()
            self.render()

    def signed_in(self, ok: bool):
        if ok:
            self.ready_now('xai')

    def key_saved(self, title: str):
        self.ready_now(self.provider)

    def pick_provider(self, pid: str):
        if pid == self.provider:
            return
        self.harvest()
        self.provider, self.problem = pid, ''
        if self.name == self.auto_name:
            self.name = self.auto_name = ''
        self.load_models()
        self.render()

    def picked(self):
        """A model picked from the list (or typed and Return): its listed name becomes the display name, unless one
        was typed."""
        self.harvest()
        name = self.default_name()
        if name and self.name in ('', self.auto_name):
            self.name = self.auto_name = name
            if 'name' in self.fields:
                self.fields['name'].setStringValue_(S(name))

    def buttons(self):
        k = self.keep
        return [], [cancel_button(lambda _: self.close(), k), button('Add', lambda _: self.add(), k, primary=True)]

    def add(self):
        self.harvest()
        model = self.model
        if not model:
            self.problem = 'Pick a model or type its id.'
        elif not re.match(r'^[A-Za-z0-9._/-]+$', model):
            self.problem = 'A model id is letters, digits and . _ / -.'
        elif self.provider == 'responses' and not re.match(r'^https://[A-Za-z0-9.-]+', self.base_url):
            self.problem = 'Give the https:// base URL of its Responses API.'
        elif any(d.provider == self.provider and d.model == model for d in self.parent.members):
            same = next(d for d in self.parent.members if d.provider == self.provider and d.model == model)
            self.problem = f'{same.name or model} is already in this lane.'
        elif self.provider == 'responses' and any(d.provider == 'responses' and not d.id and d.base_url != self.base_url
                                                  for d in self.parent.members):
            self.problem = 'Add one Responses endpoint at a time: save the lane, then add the next one.'
        else:
            self.problem = ''
        if self.problem:
            self.render()
            return
        name = self.name or self.default_name()
        d = Draft('', self.provider, model, '' if name == model else name, '', self.base_url)
        self.close()
        self.on_add(d)


# -- Add Key -------------------------------------------------------------------------------------------

class KeySheet(Sheet):
    """A provider key in a secure field, piped to `codexpool lane key NAME -`; the field is cleared as it goes."""
    width = 460.0

    def __init__(self, app, key_name: str, title: str, replace: bool = False, parent: Sheet | None = None, done=None):
        super().__init__(app, parent)
        self.key_name, self.title, self.replace, self.done = key_name, title, replace, done
        self.problem = ''
        self.job = None
        self.field = None

    def heading(self):
        return (f'{"Replace" if self.replace else "Add"} the {self.title} Key',
                f'Paste the API key from your {self.title} account. It stays on this Mac and is never shown.')

    def build(self):
        W = self.inner
        f = auto(NSSecureTextField.alloc().initWithFrame_(((0, 0), (290, 24))))
        f.setBezelStyle_(NSTextFieldRoundedBezel)
        f.setPlaceholderString_(S('API key'))
        f.setEnabled_(self.job is None)
        f.setTarget_(target(lambda _s: self.save(), self.keep))
        f.setAction_('fire:')
        fix(f, 290)
        self.field = f
        rows = [form_row('API key', None, f, width=W)]
        if self.problem:
            rows.append(note_row(('error', self.problem), W))
        return [group(rows, W)]

    def buttons(self):
        k = self.keep
        busy = self.job is not None
        left = [hstack([spinner(), secondary('Saving…', 11)], spacing=6, cluster=True)] if busy else []
        return left, [cancel_button(lambda _: self.close(), k, enabled=not busy),
                      button('Save Key', lambda _: self.save(), k, primary=True, enabled=not busy)]

    def ended(self):
        if self.field is not None:   # Cancel: a pasted key that wasn't saved doesn't stay in the field
            self.field.setStringValue_('')

    def save(self):
        if self.job is not None or self.field is None:
            return
        data = str(self.field.stringValue()).strip().encode()
        self.field.setStringValue_('')   # the key lives on only in the pipe to codexpool
        if not data:
            self.problem = 'Paste the key first.'
            self.render()
            return

        def done(r: Result):
            self.job = None
            if r.ok:
                self.close()
                if self.done:
                    self.done(self.title)
            else:
                self.problem = r.message()
                self.render()
        self.problem = ''
        self.job = Job(['lane', 'key', self.key_name, '-'], done, stdin=data + b'\n')
        del data
        self.render()


# -- xAI sign-in ---------------------------------------------------------------------------------------

class XaiLoginSheet(Sheet):
    """`codexpool lane login xai --no-open`: the link it prints, with Open and Copy like the Setup assistant (the
    sheet puts it on the clipboard itself, so CODEXPOOL_NO_CLIPBOARD keeps the CLI's copy out)."""
    width = 580.0

    def __init__(self, app, parent: Sheet | None = None, done=None):
        super().__init__(app, parent)
        self.done = done
        self.phase = 'starting'    # starting | waiting | finishing | done | failed
        self.url = ''
        self.copied = 0            # the pasteboard's change count after the copy (0: not, or no longer, there)
        self.message = ''
        self.job = None
        self.timer = None
        self.chrome = None

    def present(self):
        super().present()
        if not SNAPSHOT:
            self.start()

    def start(self):
        self.phase, self.url, self.copied, self.message = 'starting', '', 0, ''
        run = {}

        def line(text: str):
            if run.get('job') is not self.job:
                return
            if 'Authentication saved to ' in text and self.phase in ('starting', 'waiting'):
                self.phase = 'finishing'   # signed in: codexpool is saving the credential; never stop it now
                self.stop_timer()
                self.render()
            elif not self.url and first_url(text):
                self.url, self.phase = first_url(text), 'waiting'
                self.copied = copy_text(self.url)
                self.render()
                self.start_timer()

        def finished(r: Result):
            if run.get('job') is not self.job:
                return
            self.job = None
            self.stop_timer()
            if run['job'].stopped:
                return
            if r.ok:
                self.phase = 'done'
                if self.done:
                    self.done(True)
            else:
                self.phase, self.message = 'failed', r.message()
            self.render()
        self.job = run['job'] = Job(['lane', 'login', 'xai', '--no-open'], finished, line,
                                    env={'CODEXPOOL_NO_CLIPBOARD': '1'})
        self.render()

    def start_timer(self):
        self.stop_timer()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, target(lambda _t: self.tick(), []), 'fire:', None, True)

    def stop_timer(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None

    def tick(self):
        if self.phase == 'waiting' and self.copied and clipboard_count() != self.copied:
            self.copied = 0   # something else was copied since: the link is gone from the clipboard
            self.render()

    def ended(self):
        self.stop_timer()
        if self.job is not None and self.phase in ('starting', 'waiting'):
            self.job.stop()
            self.job = None

    def heading(self):
        return 'Sign In to xAI', ('The pool signs in to xAI once, with an account whose quota your lanes may use. '
                                  'That sign-in serves lane models only.')

    def build(self):
        k, W = self.keep, self.inner
        if self.phase == 'starting':
            return [group([hstack([spinner(), label('Getting a sign-in link…', 13)], spacing=8,
                                  insets=(12, ROW_X, 12, ROW_X), min_h=44)], W)]
        if self.phase == 'finishing':
            return [group([hstack([spinner(), label('Signed in. Saving the sign-in…', 13, NSFontWeightSemibold)],
                                  spacing=8, insets=(12, ROW_X, 12, ROW_X), min_h=44)], W)]
        if self.phase == 'done':
            return [group([hstack([symbol_view('checkmark.circle.fill', 18, mb.C.green_text(), NSFontWeightMedium),
                                   vstack([label('Signed in to xAI', 13, NSFontWeightSemibold),
                                           secondary('Lane members on xAI can serve now.', 11)], spacing=2,
                                          full=False)], spacing=10, insets=(12, ROW_X, 12, ROW_X), min_h=52)], W)]
        if self.phase == 'failed':
            return [group([note_row(('error', self.message or 'The sign-in did not complete.'), W)], W)]
        title = hstack([spinner(), label('Waiting for you to sign in', 13, NSFontWeightSemibold)], spacing=8,
                       cluster=True)
        buttons = [button('Open in Browser', lambda _: open_thing(self.url), k)]
        if self.chrome is None:
            self.chrome = True if SNAPSHOT else app_installed(CHROME_BUNDLE)
        if self.chrome:
            buttons.append(button('Open in Private Chrome Window', lambda _: open_private_chrome(self.url), k))
        buttons.append(button('Copy Link', lambda _: self.copy(), k))
        copied = None
        if self.copied:
            copied = hstack([symbol_view('checkmark.circle.fill', 12, mb.C.green_text(), NSFontWeightMedium),
                             secondary('Copied', 12)], spacing=4, cluster=True)
            copied.setAccessibilityLabel_(S('The sign-in link is on the clipboard'))
        url = label(self.url, 11, color=NSColor.secondaryLabelColor(), mono=True, middle=True, select=True)
        url.setToolTip_(S(self.url))
        rows = [hstack([title], insets=(12, ROW_X, 4, ROW_X)),
                padded(secondary('To sign in to a different xAI account than the one your browser uses, open the '
                                 'link in a private window.', 12, wrap=W - 2 * ROW_X), 0, ROW_X, 6, ROW_X),
                hstack(buttons, [copied], spacing=8, insets=(4, ROW_X, 8, ROW_X)),
                hstack([url], insets=(4, ROW_X, 12, ROW_X))]
        return [group(rows, W, rules=False)]

    def copy(self):
        self.copied = copy_text(self.url)
        self.render()

    def buttons(self):
        k = self.keep
        if self.phase == 'done':
            return [], [button('Done', lambda _: self.close(), k, primary=True)]
        if self.phase == 'failed':
            return [], [cancel_button(lambda _: self.close(), k), button('Try Again', lambda _: self.start(), k,
                                                                         primary=True)]
        return [], [cancel_button(lambda _: self.close(), k, enabled=self.phase != 'finishing')]


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 9. Setup assistant: Welcome (checklist) → Add accounts (sign-in links) → Done
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

STEPS = ('welcome', 'accounts', 'done')


@dataclass
class Login:
    """One `codexpool login LABEL --no-open` run and what the assistant shows for it."""
    phase: str = 'idle'        # idle | starting | waiting | finishing | added | again | failed | expired
    label: str = ''
    priority: int | None = None
    relogin: bool = False      # Sign In Again on a seat (Settings › Seats), not a new account
    before: dict = field(default_factory=dict)   # seat file -> label, when the sign-in started
    url: str = ''
    deadline: float = 0.0      # time.monotonic() when the link expires
    copied: int = 0            # the link is on the clipboard: its change count then (0: it isn't, or no longer)
    job: Job | None = None
    seat_file: str = ''
    plan: str = ''             # 'Business 5×', from status.json once the guard has seen the seat
    message: str = ''
    switch_note: str = ''      # the sign-in could not point Codex at the pool (switch_problem)
    reserve_done: bool = False
    lines: list = field(default_factory=list)
    pool: str = 'codex'        # codex: `codexpool login`; an add-on's pool: its own (PoolUI.login_args)


@dataclass
class Install:
    """An add-on pool's install command (PoolUI.install_command), run from the assistant with its output streaming
    into the add-on's card (PoolUI.setup_install_card)."""
    phase: str = 'idle'        # idle | running | done | failed
    lines: list = field(default_factory=list)
    job: Job | None = None
    message: str = ''


def first_url(text: str) -> str:
    m = re.search(r'https://[^\s<>"\']+', text)
    return m.group(0).rstrip('.,)') if m else ''


def switch_problem(lines) -> str:
    """What to show when `codexpool login` could not point Codex at the pool after the first seat: it printed that it
    could not write the Codex config, or that openai_base_url was changed by hand and left as it is. '' otherwise."""
    for ln in lines:
        text = ln.strip()
        m = re.match(r'warning: could not point Codex at the pool \((.*?)\)', text)
        if m:
            return (f'Codex isn’t pointed at the pool: its config couldn’t be changed ({m.group(1)}). Run '
                    'codexpool install in Terminal, then quit and reopen Codex.')
        if text.startswith('openai_base_url was ') and 'by hand' in text:
            return ('Codex isn’t pointed at the pool: its openai_base_url was changed by hand, so it was left as it '
                    'is. To use the pool, run codexpool install in Terminal, then quit and reopen Codex.')
    return ''


class SetupAssistant:
    def __init__(self, app, step: str = 'welcome'):
        self.app = app
        self.step = step if step in STEPS else 'welcome'
        self.login = Login()
        self.keep: list = []
        self.countdown = None      # the label that ticks while a link is waiting
        self.timer = None
        self.tick_target = target(lambda _t: self.tick(), [])
        self.default_label = ''
        self.pool = 'codex'        # the Add accounts step's pool: ChatGPT accounts (codex) or the add-on pool's
        self.install = Install()
        self.install_text = None   # the text view the install streams into, while it shows
        self.chrome = None         # Google Chrome installed (asked once, when a link first shows)
        self.win, root = make_window(SETUP_W, SETUP_H, 'Set Up codexpool', toolbar=False, resizable=False)
        self.root = root
        self.dots = PaintView.alloc().initWithFrame_((((SETUP_W - 120) / 2, 7), (120, 16)))
        self.dots.paint = self.paint_dots
        self.dots.ax = lambda: f'Step {STEPS.index(self.step) + 1} of {len(STEPS)}'
        root.addSubview_(self.dots)
        self.body = None
        self.bottom = None

    # -- chrome -----------------------------------------------------------------------------------------
    def paint_dots(self, w, h):
        i = STEPS.index(self.step)
        d, gap = 8.0, 10.0
        x = (w - (3 * d + 2 * gap)) / 2
        for k in range(3):
            color = NSColor.controlAccentColor() if k == i else (
                NSColor.controlAccentColor().colorWithAlphaComponent_(0.45) if k < i else mb.C.wash(0.18))
            mb.fill_circle(x + d / 2 + k * (d + gap), h / 2, d / 2, color)

    def show(self, step: str | None = None, label: str | None = None, priority: int | None = None):
        if step in STEPS:
            self.step = step
        if label is not None and self.login.phase in ('idle', 'added', 'again', 'failed', 'expired'):
            self.login = Login(label=label, priority=priority, relogin=True, pool=self.pool)
            self.default_label = label
        self.render()

    def go(self, step: str):
        self.step = step
        if step == 'welcome' and not SNAPSHOT:
            self.app.store.fetch_doctor()
        self.render()

    def render(self):
        self.keep = []
        self.countdown = None
        self.install_text = None   # install_card() sets it when the install's output shows
        for v in (self.body, self.bottom):
            if v is not None:
                v.removeFromSuperview()
        self.dots.setNeedsDisplay_(True)
        body = {'welcome': self.welcome, 'accounts': self.accounts, 'done': self.done}[self.step]()
        self.body = vstack([v for v in body if v is not None], spacing=16, insets=(0, 0, 0, 0))
        self.root.addSubview_(self.body)
        self.bottom = self.bottom_bar()
        self.root.addSubview_(self.bottom)
        activate(self.bottom.leadingAnchor().constraintEqualToAnchor_(self.root.leadingAnchor()),
                 self.bottom.trailingAnchor().constraintEqualToAnchor_(self.root.trailingAnchor()),
                 self.bottom.bottomAnchor().constraintEqualToAnchor_(self.root.bottomAnchor()),
                 self.body.topAnchor().constraintGreaterThanOrEqualToAnchor_constant_(self.root.topAnchor(), 50),
                 self.body.leadingAnchor().constraintEqualToAnchor_constant_(self.root.leadingAnchor(), 56),
                 self.body.widthAnchor().constraintEqualToConstant_(SETUP_BODY_W))
        # Welcome and Done sit in the middle of the space; Accounts stays at the top, since it changes height
        self.body.setHuggingPriority_forOrientation_(750, 1)
        if self.step == 'accounts':
            self.body.topAnchor().constraintEqualToAnchor_constant_(self.root.topAnchor(), 50).setActive_(True)
        else:
            centre = self.body.centerYAnchor().constraintEqualToAnchor_constant_(self.root.centerYAnchor(), -18)
            centre.setPriority_(500)
            centre.setActive_(True)
        self.fit_height()
        self.root.layoutSubtreeIfNeeded()

    def fit_height(self):
        """Grows the window (top edge fixed, never shrinking while open) when a step needs more room than it has,
        e.g. a long seat list above a sign-in link."""
        need = 50 + self.body.fittingSize().height + 20 + self.bottom.fittingSize().height
        have = self.root.frame().size.height
        screen = self.win.screen()
        limit = screen.visibleFrame().size.height - 40 if screen is not None else 900.0
        if need > have + 0.5:
            grow = min(need, max(have, limit)) - have
            f = self.win.frame()
            self.win.setFrame_display_(((f.origin.x, f.origin.y - grow), (f.size.width, f.size.height + grow)), True)

    def heading(self, title: str, body: str, icon=None):
        parts = ([icon] if icon is not None else []) + [
            label(title, 22, NSFontWeightBold, align=NSTextAlignmentCenter),
            secondary(body, 13, wrap=SETUP_BODY_W - 40, align=NSTextAlignmentCenter)]
        s = auto(NSStackView.stackViewWithViews_(parts))
        s.setOrientation_(1)
        s.setAlignment_(9)
        s.setSpacing_(6)
        if icon is not None:
            s.setCustomSpacing_afterView_(14, icon)
        return s

    def bottom_bar(self):
        k = self.keep
        left, right = [], []
        busy = self.login.phase in ('starting', 'waiting', 'finishing')
        if self.step == 'welcome':
            left.append(button('Open Settings', lambda _: self.app.show_settings(), k))
            right.append(button('Continue', lambda _: self.go('accounts'), k, primary=True))
        elif self.step == 'accounts':
            right.append(button('Back', lambda _: self.go('welcome'), k, enabled=not busy))
            retry = self.login.phase in ('failed', 'expired') and bool(self.login.label)   # Try Again is the default
            install = not self.app.store.installed(self.pool)                          # so is Install the … Pool
            right.append(button('Continue', lambda _: self.go('done'), k,
                                primary=self.has_seats() and not busy and not retry and not install,
                                enabled=not busy))
        else:
            left.append(button('Open Settings', lambda _: self.app.show_settings(close_setup=True), k))
            right.append(button('Back', lambda _: self.go('accounts'), k))
            right.append(button('Done', lambda _: self.win.close(), k, primary=True))
        bar = hstack(left, right, spacing=10, insets=(14, 20, 16, 20), min_h=62)
        rule = canvas(lambda w, h: mb.fill_rect(((0, 0), (w, 1)), K.rule()), None, 1)
        return vstack([rule, bar], spacing=0)

    def has_seats(self) -> bool:
        st = self.app.store
        return any(st.installed(p) and st.model(p).seats for p in POOLS) or self.login.phase in ('added', 'again')

    @property
    def ui(self):
        """The add-on's PoolUI while the step is on its pool; None for ChatGPT accounts."""
        return pool_ui(self.pool)

    def set_pool(self, pool: str):
        if pool in POOLS and pool != self.pool:
            self.pool = pool
            if self.login.phase in ('idle', 'failed', 'expired', 'added', 'again'):
                self.login = Login(pool=pool)
                self.default_label = ''
            self.render()

    # -- step 1 -----------------------------------------------------------------------------------------
    def welcome(self):
        st = self.app.store
        icon = canvas(lambda w, h: draw_app_icon(w), 72, 72)
        head = self.heading('Welcome to codexpool',
                            'codexpool pools your ChatGPT accounts behind the Codex app and CLI. When one seat runs '
                            'out, the next one picks up the same thread.' +
                            ''.join(getattr(u, 'welcome_suffix', '') for u in mb.POOL_UI.values()), icon)
        rows = []
        for title, status, detail in doctor_checklist(st.doctor, st.model()):
            name, _ = CHECK_ICON.get(status, CHECK_ICON['unknown'])
            icon_v = spinner() if st.doctor_busy and st.doctor is None else \
                symbol_view(name, 14, check_color(status), NSFontWeightMedium, box=18)
            rows.append(form_row(title, None if status == 'ok' else detail, (), leading=icon_v, width=SETUP_BODY_W,
                                 min_h=40))
        k = self.keep
        again = link_button('Check again', lambda _: st.fetch_doctor(), k)
        foot = hstack([secondary('From codexpool doctor.', 11)], [again], insets=(0, 4, 0, 0))
        return [head, padded(vstack([group(rows, SETUP_BODY_W), foot], spacing=6), 8, 0, 0, 0)]

    # -- step 2 -----------------------------------------------------------------------------------------
    def accounts(self):
        st = self.app.store
        ui = self.ui
        if ui and hasattr(ui, 'setup_accounts'):
            return ui.setup_accounts(self)
        m = st.model(self.pool)
        if ui is not None:
            head = self.heading(*ui.setup_heading)
        else:
            head = self.heading('Add your ChatGPT accounts',
                                'Each account becomes a seat. Add every account you want Codex to use: personal, '
                                'work, team or Pro.')
        busy = self.login.phase in ('starting', 'waiting', 'finishing')
        switch = pool_switcher(self.pool, self.set_pool, self.keep, enabled=not busy,
                               titles={'codex': 'ChatGPT', **{p: u.account_title for p, u in mb.POOL_UI.items()}})
        if ui is not None and not st.installed(self.pool):
            return [head, switch, ui.setup_install_card(self)]
        unit = self.unit
        seats = list(m.seats)
        if seats:
            rows = []
            shown = seats if len(seats) <= 6 else seats[:5]
            for s in shown:
                left, right = seat_title_row(s, m, name_weight=NSFontWeightMedium, resets=False)
                rows.append(hstack(left, right, spacing=7, insets=(0, ROW_X, 0, ROW_X), min_h=36))
            if len(shown) < len(seats):
                rows.append(hstack([secondary(f'and {len(seats) - len(shown)} more (see Settings › Seats)', 12)],
                                   insets=(0, ROW_X + 17, 0, ROW_X), min_h=34))
            listing = section(group(rows, SETUP_BODY_W),
                              f'In the {ui.title + " " if ui else ""}pool ({len(seats)} {unit}'
                              f'{"s" if len(seats) != 1 else ""})', width=SETUP_BODY_W)
        else:
            listing = group([hstack([symbol_view('person.crop.circle.badge.questionmark', 16,
                                                 NSColor.secondaryLabelColor()),
                                     secondary(f'No {unit}s yet. Add your first account below.', 12)], spacing=10,
                                    insets=(12, ROW_X, 12, ROW_X))], SETUP_BODY_W)
        out = [head, switch, listing, self.login_card()]
        if ui is not None and self.install.phase == 'done':
            out.insert(2, group([note_row(('ok', ui.installed_note), SETUP_BODY_W)], SETUP_BODY_W))
        return out

    @property
    def unit(self) -> str:
        return pool_noun(self.pool)

    # -- installing the add-on's pool (its card: PoolUI.setup_install_card; the plumbing is here) -----------
    def install_output(self, width: float, height: float = 150.0):
        """The install's output so far, in a scrolling monospace box that later lines are appended to."""
        scroll = auto(NSTextView.scrollableTextView())
        text = scroll.documentView()
        text.setEditable_(False)
        text.setDrawsBackground_(False)
        scroll.setDrawsBackground_(False)
        text.setTextContainerInset_((6, 6))
        scroll.setHasVerticalScroller_(not SNAPSHOT)
        box = auto(GroupView.alloc().initWithFrame_(((0, 0), (width, height))))
        box.rows, box.radius = (), 7.0
        box.addSubview_(scroll)
        pin(scroll, box, 1, 1, 1, 1)
        fix(box, width, height)
        self.install_text = text
        for ln in self.install.lines:
            self.append_install(ln)
        return box

    def append_install(self, line: str):
        t = self.install_text
        if t is None:
            return
        f = NSFont.monospacedSystemFontOfSize_weight_(10.5, NSFontWeightRegular)
        t.textStorage().appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
            S(line + '\n'), {NSFontAttributeName: f, NSForegroundColorAttributeName: NSColor.secondaryLabelColor()}))
        t.scrollToEndOfDocument_(None)

    def start_install(self):
        if self.install.phase == 'running' or self.ui is None:
            return
        pool = self.pool
        ins = self.install = Install(phase='running')

        def line(text: str):
            if ins is self.install:
                ins.lines.append(text)
                self.append_install(text)

        def done(r: Result):
            ins.job = None
            if ins is not self.install or ins.phase != 'running':
                return   # stopped: already shown
            if r.ok:
                ins.phase = 'done'
                self.app.store.installed_hint.add(pool)
                self.app.after_change()   # a guard pass writes the pool's status file
            else:
                ins.phase, ins.message = 'failed', r.message()
            self.render()
        ins.job = Job(list(self.ui.install_command), done, line)
        self.render()

    def stop_install(self):
        ins = self.install
        if ins.job is not None:
            ins.job.stop()
        ins.phase, ins.message = 'failed', 'Stopped. Installing again starts over and keeps what is already done.'
        self.render()

    def login_card(self):
        lg, k = self.login, self.keep
        W = SETUP_BODY_W
        ui = self.ui
        title = f'Add a {ui.account_title} account' if ui else 'Add a ChatGPT account'
        if lg.phase in ('idle', 'failed', 'expired'):   # the name field
            field = text_field(lg.label or self.default_label, 230, 'e.g. Work or Personal')   # the row says Name
            field.cell().setSendsActionOnEndEditing_(False)   # Return starts the sign-in; leaving the field doesn't

            def start(_sender):
                self.start_login(str(field.stringValue()).strip())
            field.setTarget_(target(start, k))
            field.setAction_('fire:')
            again = lg.phase in ('failed', 'expired') and bool(lg.label)
            go = button('Try Again' if again else 'Get Sign-In Link', start, k, primary=again or not self.has_seats())
            rows = [hstack([label('Name', 13), field], [go], spacing=10, insets=(10, ROW_X, 10, ROW_X), min_h=44)]
            if lg.phase in ('failed', 'expired'):
                rows.append(note_row(('error', lg.message)))
            site = ui.sign_in_site if ui else 'chatgpt.com'
            return section(group(rows, W), title, width=W,
                           footer=f'You sign in on {site} in your browser; codexpool never sees your password.')
        if lg.phase == 'again':
            rows = [hstack([symbol_view('arrow.triangle.2.circlepath', 16, NSColor.secondaryLabelColor(),
                                        NSFontWeightMedium, box=18),
                            vstack([label(f'That was {lg.label} again', 13, NSFontWeightSemibold),
                                    secondary((ui.same_account_text if ui else
                                               'The same ChatGPT account and workspace') +
                                              ': its sign-in is refreshed and nothing was added. For another account, '
                                              'close every private window, then open the link in a new one.', 11,
                                              wrap=W - 200)],
                                   spacing=2, full=False)],
                           [button('Add Another…', lambda _: self.reset_login(), k)], spacing=10,
                           insets=(12, ROW_X, 12, ROW_X), min_h=56)]
            if lg.switch_note:
                rows.append(note_row(('error', lg.switch_note)))
            return section(group(rows, W), title, width=W)
        if lg.phase == 'added':
            verb = 'Signed in again:' if lg.relogin else 'Added'
            text = f'{verb} {lg.label or lg.seat_file}' + (f' · {lg.plan}' if lg.plan else '')
            reserve = None if lg.relogin else button('Mark as Reserve', lambda _: self.mark_reserve(), k,
                                                     enabled=not lg.reserve_done)
            another = button('Add Another…', lambda _: self.reset_login(), k)
            rows = [hstack([symbol_view('checkmark.circle.fill', 18, mb.C.green_text(), NSFontWeightMedium),
                            vstack([label(text, 13, NSFontWeightSemibold),
                                    secondary(f'A reserve {self.unit} is used last.'
                                              if not lg.reserve_done else
                                              f'{lg.label} is now the reserve.', 11, wrap=W - 300)],
                                   spacing=2, full=False)],
                           [reserve, another], spacing=10, insets=(12, ROW_X, 12, ROW_X), min_h=56)]
            if lg.switch_note:
                rows.append(note_row(('error', lg.switch_note)))
            if lg.message:
                rows.append(note_row(('error', lg.message)))
            return section(group(rows, W), title, width=W)
        if lg.phase == 'finishing':
            rows = [hstack([spinner(), label(f'Signed in. Adding {lg.label or "the account"}…', 13,
                                             NSFontWeightSemibold)], spacing=8, insets=(12, ROW_X, 12, ROW_X),
                           min_h=48)]
            return section(group(rows, W), title, width=W)
        # starting / waiting
        who = f'the account for “{lg.label}”' if lg.label else 'the account to add'
        heading = hstack([spinner(), label(f'Sign in as {who}', 13, NSFontWeightSemibold)], spacing=8, cluster=True)
        if lg.phase == 'starting':
            rows = [hstack([heading], [button('Cancel', lambda _: self.cancel_login(), k)], spacing=10,
                           insets=(12, ROW_X, 12, ROW_X), min_h=48)]
            return section(group(rows, W), title, width=W)
        self.countdown = secondary(self.countdown_text(), 11)
        url = label(lg.url, 11, color=NSColor.secondaryLabelColor(), mono=True, middle=True, select=True)
        url.setToolTip_(S(lg.url))
        buttons = [button('Open in Browser', lambda _: open_thing(lg.url), k)]
        if self.chrome is None:
            self.chrome = True if SNAPSHOT else app_installed(CHROME_BUNDLE)
        if self.chrome:
            buttons.append(button('Open in Private Chrome Window', lambda _: open_private_chrome(lg.url), k))
        buttons.append(button('Copy Link', lambda _: self.copy_link(), k))
        copied = None
        if lg.copied:
            copied = hstack([symbol_view('checkmark.circle.fill', 12, mb.C.green_text(), NSFontWeightMedium),
                             secondary('Copied', 12)], spacing=4, cluster=True)
            copied.setAccessibilityLabel_(S('The sign-in link is on the clipboard'))
        rows = [
            hstack([heading], [self.countdown], spacing=10, insets=(12, ROW_X, 4, ROW_X)),
            padded(secondary('To add a different account than the one your browser is signed in to, use a private '
                             'window.', 12, wrap=W - 2 * ROW_X), 0, ROW_X, 6, ROW_X),
            hstack(buttons, [copied], spacing=8, insets=(4, ROW_X, 8, ROW_X)),
            hstack([url], [link_button('Cancel', lambda _: self.cancel_login(), k)], spacing=10,
                   insets=(4, ROW_X, 10, ROW_X)),
        ]
        return section(group(rows, W, rules=False), title, width=W)

    def countdown_text(self) -> str:
        left = max(0, int(self.login.deadline - self.clock()))
        return f'Link expires in {left // 60}:{left % 60:02d}'

    def clock(self) -> float:
        return self.snapshot_clock if SNAPSHOT else time.monotonic()

    snapshot_clock = 0.0

    def copy_link(self):
        """Copy Link: the link on the clipboard once more (anything copied since took its place)."""
        lg = self.login
        lg.copied = copy_text(lg.url)
        self.render()
        self.flash_copied('Link copied' if lg.copied else 'Couldn’t copy the link')

    def flash_copied(self, text: str):
        if self.countdown is not None:
            self.countdown.setStringValue_(S(text))
            AppHelper.callLater(1.5, self.tick)

    # -- login flow ------------------------------------------------------------------------------------
    def start_login(self, name: str):
        noun = self.unit
        if not name:
            self.login = Login(phase='failed', message=f'Give the {noun} a name first, e.g. Work or Personal.')
            self.render()
            return
        if name.startswith('-'):
            an = 'An' if noun[0] in 'aeiou' else 'A'
            self.login = Login(phase='failed', label=name, message=f'{an} {noun} name can’t start with a dash.')
            self.render()
            return
        same = self.login.label == name
        pr = self.login.priority if same else None
        pool = self.pool
        lg = self.login = Login(phase='starting', label=name, priority=pr, relogin=self.login.relogin and same,
                                before={s.name: s.label for s in self.app.store.model(pool).seats if s.name},
                                pool=pool)
        # The assistant puts the link on the clipboard itself, and shows that it did: --no-copy keeps codexpool's
        # own copy out of it (an add-on's pool says how in PoolUI.login_args, e.g. an environment variable)
        ui = pool_ui(pool)
        if ui is not None:
            args, env = ui.login_args(name, pr)
        else:
            args = ['login', name, '--no-open', '--no-copy'] + (['--priority', str(pr)] if pr is not None else [])
            env = None

        def line(text: str):
            if lg is not self.login:
                return
            lg.lines.append(text)
            if 'Authentication saved to ' in text and lg.phase in ('starting', 'waiting'):
                # Signed in: codexpool is writing the seat's name (and priority). Never stop it now.
                lg.phase = 'finishing'
                self.stop_timer()
                self.render()
                return
            if not lg.url:
                url = first_url(text)
                if url:
                    lg.url, lg.phase, lg.deadline = url, 'waiting', time.monotonic() + LOGIN_TTL_S
                    lg.copied = copy_text(url)
                    self.render()
                    self.start_timer()

        def done(r: Result):
            if lg is not self.login:
                return
            self.stop_timer()
            seat = next((ln.strip() for ln in lg.lines if ln.strip().startswith('seat ')), '')
            if lg.phase == 'expired' or (lg.job is not None and lg.job.stopped):
                return   # an attempt that was cancelled or ran out of time: already shown
            if r.ok and seat:
                m = re.match(r'seat\s+(\S+?):', seat)
                lg.seat_file = m.group(1) if m else ''
                plan = re.search(r'\bplan=(\S+)', seat)
                names = ui.plan_names if ui else mb.PLAN_NAMES
                lg.phase, lg.plan = 'added', mb.plan_badge(plan.group(1), None, names) if plan else ''
                old = lg.before.get(lg.seat_file)
                if old is not None and not lg.relogin:
                    # The browser signed in to an account that is already a seat: its login was refreshed, nothing
                    # was added, and `codexpool login` left its name as it was.
                    lg.phase, lg.label = 'again', old
                lg.switch_note = switch_problem(lg.lines) if lg.pool == 'codex' else ''
                if lg.switch_note:
                    self.switch_note = lg.switch_note
                elif lg.pool == 'codex' and any('Codex now uses the pool' in ln for ln in lg.lines):
                    self.switch_note = ''
                self.render()
                self.app.after_change(lambda: self.fill_plan(lg))
            else:
                lg.phase, lg.message = 'failed', r.message() if not r.ok else 'The sign-in did not complete.'
                self.render()
        def ended(r: Result):
            done(r)
            self.app.login_ended()   # a quit that was waiting for this sign-in can go ahead
        lg.job = Job(args, ended, line, env=env)
        self.render()

    def fill_plan(self, lg: Login):
        """After the guard has seen the new seat: its plan and the size the pool gave it."""
        seat = next((s for s in self.app.store.model(lg.pool).seats if s.name == lg.seat_file), None)
        if seat is not None and lg is self.login:
            lg.plan = seat.plan
            self.render()

    def start_timer(self):
        self.stop_timer()
        self.timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(
            1.0, self.tick_target, 'fire:', None, True)

    def stop_timer(self):
        if self.timer is not None:
            self.timer.invalidate()
            self.timer = None

    def tick(self):
        lg = self.login
        if lg.phase != 'waiting':
            return
        if time.monotonic() >= lg.deadline:
            lg.phase, lg.message = 'expired', 'The sign-in link expired. Links last 5 minutes; get a new one.'
            if lg.job:
                lg.job.stop()
            self.stop_timer()
            self.render()
        elif lg.copied and clipboard_count() != lg.copied:   # something else was copied since: the link is gone
            lg.copied = 0
            self.render()
        elif self.countdown is not None:
            self.countdown.setStringValue_(S(self.countdown_text()))

    def cancel_login(self):
        lg = self.login
        if lg.job:
            lg.job.stop()
        self.stop_timer()
        self.login = Login(label=lg.label, priority=lg.priority, pool=lg.pool)
        self.render()

    def reset_login(self):
        self.login = Login(pool=self.pool)
        self.default_label = ''
        self.render()

    def mark_reserve(self):
        lg = self.login

        def done(r: Result):
            if r.ok:
                lg.reserve_done, lg.message = True, ''
                self.app.after_change()
            else:
                lg.message = r.message()
            self.render()
        ui = pool_ui(lg.pool)
        self.app.run((list(ui.command_prefix) if ui else []) + ['reserve', lg.seat_file or lg.label], done)

    # -- step 3 -----------------------------------------------------------------------------------------
    def done(self):
        k = self.keep
        if self.switch_note:   # a sign-in could not point Codex at the pool: reopening it would not help yet
            icon = symbol_view('exclamationmark.triangle.fill', 54, mb.C.orange_text(), NSFontWeightMedium, box=64)
            head = self.heading('One more step', self.switch_note, icon)
        else:
            icon = symbol_view('checkmark.circle.fill', 54, mb.C.green_text(), NSFontWeightMedium, box=64)
            head = self.heading('You’re all set',
                                'Quit and reopen the Codex app so it uses the pool. Your threads stay where they are.',
                                icon)
        reopen = button('Quit and Reopen Codex…', lambda _: self.app.reopen_codex(None, self), k)
        note = note_row(self.done_note) if self.done_note else None
        rows = [form_row('Codex app', 'Picks up the pool when it starts.', reopen, width=SETUP_BODY_W)]
        if note is not None:
            rows.append(note)
        for u in mb.POOL_UI.values():   # the add-on pool's own rows (its launcher, its desktop app)
            rows += u.setup_done_rows(self)
        tips = group([
            form_row('Settings', 'Seats, lanes, the menu bar display and health checks. Click the menu bar meter, '
                     'then Settings… (⌘,).', (), leading=canvas(lambda w, h: draw_icon_square(0, 0, 22,
                                                                                               'gearshape.fill', 'grey'),
                                                               22, 22), width=SETUP_BODY_W),
            form_row('Menu bar meter', 'The number is what is left this week across every seat, in the pool’s own '
                     'colour while a regular seat serves, red once the reserve takes over, grey when the pool needs '
                     'attention.' + ''.join(getattr(u, 'meter_tip_suffix', '') for u in mb.POOL_UI.values()), (),
                     leading=canvas(lambda w, h: draw_icon_square(0, 0, 22, 'gauge.with.dots.needle.67percent',
                                                                  'green'), 22, 22), width=SETUP_BODY_W),
        ], SETUP_BODY_W)
        return [head, group(rows, SETUP_BODY_W), tips]

    def desktop_feedback(self, kind, text):
        """Progress of an add-on's Done-step action (Set Up Pooled Desktop…), in the step's note (None: cancelled or
        refused)."""
        self.done_note = (kind, text) if kind else None
        self.render()

    done_note = None
    switch_note = ''   # the last sign-in that tried to point Codex at the pool could not (switch_problem)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 10. App controller: single instance, windows, menus, the Dock icon, confirmations, command plumbing
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

_LOCK_FD = None


def claim_single_instance(pane: str | None) -> bool:
    """True if this process is the one Settings process. Otherwise the running one is asked to show `pane` (a
    distributed notification) and brought forward, and this one should exit."""
    global _LOCK_FD
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    fd = os.open(PID_FILE, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        try:
            pid = int(os.read(fd, 32).decode().strip() or 0)
        except ValueError:
            pid = 0
        os.close(fd)
        try:   # for a first instance still starting up, which isn't listening yet
            tmp = REQUEST_FILE.with_suffix('.tmp')
            tmp.write_text(f'{pane or "front"}\n')
            os.replace(tmp, REQUEST_FILE)
        except OSError:
            pass
        NSDistributedNotificationCenter.defaultCenter().postNotificationName_object_userInfo_deliverImmediately_(
            SHOW_NOTE, S(pane or 'front'), None, True)
        other = NSRunningApplication.runningApplicationWithProcessIdentifier_(pid) if pid else None
        if other is not None:
            other.activateWithOptions_(0)
        return False
    os.ftruncate(fd, 0)
    os.write(fd, f'{os.getpid()}\n'.encode())
    _LOCK_FD = fd
    return True


def take_request() -> str | None:
    """The pane a second launch asked for in state/settings-request, if any (and removes the file)."""
    try:
        fresh = time.time() - REQUEST_FILE.stat().st_mtime < 30   # older: left behind by an instance that quit
        pane = REQUEST_FILE.read_text().strip()
        REQUEST_FILE.unlink()
    except OSError:
        return None
    return pane if fresh and split_request(pane)[0] in PANES + SETUP_PANES + ('front',) else None


def split_request(text: str) -> tuple:
    """'seats@<pool>' -> ('seats', '<pool>'); 'seats' -> ('seats', None). A launch's --pool travels this way."""
    pane, _, pool = text.partition('@')
    return pane, (pool if pool in POOLS else None)


def release_single_instance():
    global _LOCK_FD
    if _LOCK_FD is not None:
        try:
            os.ftruncate(_LOCK_FD, 0)
            os.close(_LOCK_FD)
        except OSError:
            pass
        _LOCK_FD = None


class SettingsController(NSObject):
    def init(self):
        self = objc.super(SettingsController, self).init()  # noqa: PLW0642 (the PyObjC init idiom)
        if self is None:
            return None
        self.store = None
        self.panes = {}
        self.view = None
        self.setup = None
        self.keep = []
        self.start_pane = 'overview'
        self.sheet = None
        self.stream_job = None     # the lane test streaming into a sheet, stopped on quit
        self.top_sheet = None      # a Lanes sheet on the Settings window (its own sheets hang on it)
        self.quitting = False      # a quit is waiting for a sign-in to finish (NSTerminateLater)
        self.last_status = None
        self.pool = 'codex'        # the switcher's pool on Overview, Seats and Balancing (remembered)
        return self

    # -- construction ----------------------------------------------------------------------------------
    @objc.python_method
    def configure(self, store: Store, pane: str, height: float = WIN_H, pool: str | None = None):
        self.store = store
        self.start_pane = pane
        if pool not in POOLS and not SNAPSHOT:
            pool = mb.as_str(NSUserDefaults.standardUserDefaults().stringForKey_(POOL_DEFAULT))
        self.pool = pool if pool in POOLS else 'codex'
        self.panes = {cls.key: cls(self) for cls in PANE_CLASSES}
        store.listeners.append(self.data_changed)
        self.height = height

    @objc.python_method
    def ensure_view(self):
        if self.view is None:
            self.view = SettingsView(self, self.panes, self.height)
            self.view.win.setDelegate_(self)
        return self.view

    @objc.python_method
    def ensure_setup(self):
        if self.setup is None:
            self.setup = SetupAssistant(self)
            self.setup.win.setDelegate_(self)
        return self.setup

    # -- lifecycle (live) ------------------------------------------------------------------------------
    def applicationDidFinishLaunching_(self, note):
        app = NSApplication.sharedApplication()
        app.setApplicationIconImage_(app_icon_image())
        self.build_menus()
        NSDistributedNotificationCenter.defaultCenter().addObserver_selector_name_object_(
            self, 'showRequested:', SHOW_NOTE, None)
        self.store.poll(force=True)
        self.last_status = tuple(self.store.model(p).status for p in POOLS)
        self.store.fetch_version()
        # The default run loop mode only: no rebuild while a pop-up menu is open or the window is being resized.
        timer = NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(POLL_EVERY_S, self, 'tick:',
                                                                                         None, True)
        timer.setTolerance_(2.0)
        self.handle_request(self.start_pane)
        pending = take_request()   # a second launch that came before the observer above was registered
        if pending and pending != self.start_pane:
            self.handle_request(pending)
        mb.activate_app()

    def applicationShouldTerminateAfterLastWindowClosed_(self, app):
        return True

    def applicationShouldTerminate_(self, app):
        """Quitting (or closing the last window) while a sign-in is finishing waits for it: codexpool is naming
        the seat and, for Sign In Again, waiting up to 20 s for the pool to load it to restore its priority.
        Stopping it then, or closing the pipe it prints to, would lose the name or the priority."""
        if not self.login_finishing():
            return NSTerminateNow
        self.quitting = True
        for w in (self.view.win if self.view else None, self.setup.win):
            if w is not None:
                w.orderOut_(None)
        # the run loop is in the modal panel mode until the reply, so the timer goes in the common modes
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(
            QUIT_WAIT_S, target(lambda _t: self.finish_quit(), self.keep), 'fire:', None, False)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        return NSTerminateLater

    @objc.python_method
    def login_finishing(self) -> bool:
        return self.setup is not None and self.setup.login.phase == 'finishing'

    @objc.python_method
    def login_ended(self):
        if self.quitting:
            AppHelper.callAfter(self.finish_quit)   # after the rest of the login's own callback

    @objc.python_method
    def finish_quit(self):
        if self.quitting:
            self.quitting = False
            NSApplication.sharedApplication().replyToApplicationShouldTerminate_(True)

    def applicationWillTerminate_(self, note):
        lg = self.setup.login if self.setup is not None else None
        if lg is not None and lg.job is not None and lg.phase in ('starting', 'waiting'):
            lg.job.stop()   # a sign-in still waiting for the browser (once signed in, it is never stopped)
        ins = self.setup.install if self.setup is not None else None
        if ins is not None and ins.job is not None:
            ins.job.stop()   # the pool's install would run on with no reader; running it again finishes it
        if self.stream_job is not None:
            self.stream_job.stop()   # a lane test, and the subagents it started, would run on with no reader
        s = self.top_sheet
        while s is not None:         # an xAI sign-in still waiting for the browser
            s.ended()
            s = s.child
        release_single_instance()

    def showRequested_(self, note):
        take_request()
        self.handle_request(str(note.object() or 'front'))
        mb.activate_app()

    @objc.python_method
    def handle_request(self, request: str):
        pane, pool = split_request(request)
        if pool is not None:
            if pane.startswith('setup-'):
                setup = self.ensure_setup()
                if setup.login.phase in ('idle', 'added', 'again', 'failed', 'expired'):
                    setup.pool = pool   # never away from a sign-in in progress
            else:
                self.set_pool(pool, render=False)
        self.show_request(pane)

    @objc.python_method
    def show_request(self, pane: str):
        """A pane another launch asked for (the menu bar, the installer, `codexpool gui`). An open Setup assistant
        is only brought forward when the request would move it back to Welcome or away from a sign-in in
        progress: the installer and the menu bar's first run can both ask for it within a minute."""
        s = self.setup
        if pane.startswith('setup-') and s is not None and s.win.isVisible() and (
                pane == 'setup-welcome' or s.login.phase in ('starting', 'waiting', 'finishing')):
            s.win.makeKeyAndOrderFront_(None)
            s.win.orderFrontRegardless()
            return
        self.open_pane(pane if pane != 'front' else None)

    def tick_(self, timer):
        self.store.poll()
        # it can change with no new file: the guard stopped writing (stale), for either pool
        status = tuple(self.store.model(p).status for p in POOLS)
        if status != self.last_status:
            self.last_status = status
            self.store.notify('status')
        if self.view is not None and self.view.current in ('overview', 'seats', 'balancing', 'general', 'health',
                                                          'lanes'):
            pane = self.panes[self.view.current]
            if pane.note and pane.note[0] == 'ok' and time.monotonic() - pane.note_at > NOTE_S:
                pane.note = None
                self.rebuild(pane)

    def windowDidBecomeKey_(self, note):
        if self.view is not None and note.object() == self.view.win:
            for item in self.view.items.values():
                item.setNeedsDisplay_(True)

    def windowDidResignKey_(self, note):
        self.windowDidBecomeKey_(note)

    # -- windows ---------------------------------------------------------------------------------------
    @objc.python_method
    def open_pane(self, pane: str | None):
        if pane and pane.startswith('setup-'):
            step = {'setup-welcome': 'welcome', 'setup-done': 'done'}.get(pane, 'accounts')
            self.open_setup(pane, step=step)
            return
        if pane in LANE_SHOTS:
            pane = 'lanes'
        view = self.ensure_view()
        view.select(pane if pane in self.panes else view.current)
        if not view.win.isVisible():
            view.win.center()
        view.win.makeKeyAndOrderFront_(None)
        view.win.orderFrontRegardless()

    @objc.python_method
    def show_pane(self, key: str):
        self.open_pane(key)

    @objc.python_method
    def set_pool(self, pool: str, render: bool = True):
        """The pool switcher: Overview, Seats and Balancing show this pool now, and next time too."""
        if pool not in POOLS or pool == self.pool:
            return
        self.pool = pool
        if not SNAPSHOT:
            NSUserDefaults.standardUserDefaults().setObject_forKey_(pool, POOL_DEFAULT)
        for key in ('overview', 'seats', 'balancing'):
            self.panes[key].pool_changed()
        if render and self.view is not None:
            self.view.render(reset_scroll=True)

    @objc.python_method
    def show_seat(self, name: str):
        self.panes['seats'].selected = name
        self.open_pane('seats')

    @objc.python_method
    def show_settings(self, close_setup: bool = False):
        self.open_pane(self.view.current if self.view else 'overview')
        if close_setup and self.setup is not None:
            self.setup.win.close()

    @objc.python_method
    def open_setup(self, pane: str = 'setup-welcome', step: str | None = None, label: str | None = None,
                   priority: int | None = None, pool: str | None = None):
        target_pool = pool or self.pool
        target_ui = pool_ui(target_pool)
        if target_ui and getattr(target_ui, 'setup_pane', None):
            self.set_pool(target_pool)
            self.open_pane(target_ui.setup_pane)
            return
        setup = self.ensure_setup()
        if pool in POOLS and setup.login.phase in ('idle', 'added', 'again', 'failed', 'expired'):
            setup.pool = pool
        step = step or {'setup-welcome': 'welcome', 'setup-accounts': 'accounts', 'setup-done': 'done'}.get(pane,
                                                                                                         'accounts')
        if step == 'welcome' and not SNAPSHOT and self.store.doctor is None:
            self.store.fetch_doctor()
        was_visible = setup.win.isVisible()
        setup.show(step, label, priority)
        if not was_visible:
            if self.view is not None and self.view.win.isVisible():
                f = self.view.win.frame()
                setup.win.setFrameTopLeftPoint_((f.origin.x + (f.size.width - SETUP_W) / 2,
                                                 f.origin.y + f.size.height - 40))
            else:
                setup.win.center()
        setup.win.makeKeyAndOrderFront_(None)
        setup.win.orderFrontRegardless()

    def windowWillClose_(self, note):
        # A sign-in still waiting for the browser stops, and starts over next time (its callbacks are ignored).
        # One that is finishing keeps running: its result shows when the assistant opens again, and a quit
        # waits for it (applicationShouldTerminate_).
        if self.setup is not None and note.object() == self.setup.win and \
                self.setup.login.phase in ('starting', 'waiting'):
            self.setup.cancel_login()

    # -- data ------------------------------------------------------------------------------------------
    @objc.python_method
    def data_changed(self, what: str):
        if self.view is not None:
            relevant = {'status': ('overview', 'seats', 'balancing', 'general', 'about'), 'doctor': ('health',),
                        'lanes': ('lanes',), 'providers': ('lanes',), 'version': ('about',)}.get(what, ())
            if self.view.current in relevant and not self.editing():
                self.view.render()
            elif what == 'status':   # the sidebar's status lines
                h = self.view.header
                h.line, h.line_color, h.line2, h.line2_color = sidebar_lines(self.store)
                h.setNeedsDisplay_(True)
        if self.setup is not None and self.setup.win.isVisible() and what in ('status', 'doctor') and \
                self.setup.login.phase not in ('waiting', 'starting') and not self.editing(self.setup.win):
            self.setup.render()

    @objc.python_method
    def editing(self, win=None) -> bool:
        """A text field is being edited: don't rebuild under the user's cursor."""
        win = win or (self.view.win if self.view else None)
        if win is None:
            return False
        fr = win.firstResponder()
        return fr is not None and fr.isKindOfClass_(NSTextView) and fr.isFieldEditor()

    @objc.python_method
    def rebuild(self, pane: Pane):
        if self.view is not None and self.view.current == pane.key:
            self.view.render()

    @objc.python_method
    def run(self, args, done):
        Job(args, done)

    @objc.python_method
    def after_change(self, then=None):
        """One guard pass so status.json shows the change at once (like the menu bar app after an action)."""
        if self.quitting:
            return   # the guard runs every minute anyway; a pass started now would outlive its reader

        def finished(r: Result):
            self.store.poll(force=True)
            if then:
                then()
        Job(['guard'], finished)

    # -- the Lanes pane's sheets ---------------------------------------------------------------------------
    @objc.python_method
    def edit_lane(self, lane: Lane | None):
        """New Lane… (lane None) and Edit…."""
        if self.top_sheet is None:
            self.present_sheet(LaneEditor(self, lane, self.panes['lanes'].lane_saved))

    @objc.python_method
    def sign_in_xai(self, parent: Sheet | None = None, done=None):
        if parent is not None or self.top_sheet is None:
            self.present_sheet(XaiLoginSheet(self, parent, done), parent)

    @objc.python_method
    def add_key(self, key_name: str, title: str, replace: bool = False, parent: Sheet | None = None, done=None):
        if parent is not None or self.top_sheet is None:
            self.present_sheet(KeySheet(self, key_name, title, replace, parent, done), parent)

    @objc.python_method
    def present_sheet(self, sheet: Sheet, parent: Sheet | None = None, pane: str | None = 'lanes'):
        """A sheet on the Settings window (on pane, when given) or on another sheet."""
        if parent is not None:
            parent.child = sheet
        else:
            if self.top_sheet is not None:
                return
            if pane:
                self.open_pane(pane)
            self.top_sheet = sheet
        sheet.present()

    @objc.python_method
    def sheet_closed(self, sheet: Sheet):
        if self.top_sheet is sheet:
            self.top_sheet = None

    # -- confirmations and long commands ---------------------------------------------------------------
    @objc.python_method
    def front_window(self):
        for w in (self.setup.win if self.setup else None, self.view.win if self.view else None):
            if w is not None and w.isKeyWindow():
                return w
        return self.view.win if self.view and self.view.win.isVisible() else (self.setup.win if self.setup else None)

    @objc.python_method
    def ask(self, title: str, text: str, button_title: str, then, destructive: bool = False, cancelled=None):
        alert = NSAlert.alloc().init()
        alert.setMessageText_(S(title))
        alert.setInformativeText_(S(text))
        alert.setAlertStyle_(NSAlertStyleWarning)
        b = alert.addButtonWithTitle_(S(button_title))
        if destructive:
            b.setHasDestructiveAction_(True)
        alert.addButtonWithTitle_('Cancel')
        alert.setIcon_(app_icon_image(128))

        def answered(code):
            if code == NSAlertFirstButtonReturn:
                then()
            elif cancelled is not None:
                cancelled()
        win = self.front_window()
        if win is not None:
            alert.beginSheetModalForWindow_completionHandler_(win, answered)
        else:
            answered(alert.runModal())

    @objc.python_method
    def stream_sheet(self, title: str, args: list, finished=None, sub: str | None = None):
        """Runs a long command (lane test, accepting an engine) with its output streaming into a sheet; Stop ends
        it. sub: the line under the title (default: the lane test's)."""
        win = self.front_window()
        w, h = 620.0, 420.0
        sheet = NSWindow.alloc().initWithContentRect_styleMask_backing_defer_(((0, 0), (w, h)), NSWindowStyleMaskTitled,
                                                                              NSBackingStoreBuffered, False)
        root = FlippedView.alloc().initWithFrame_(((0, 0), (w, h)))
        sheet.setContentView_(root)
        keep: list = []
        head_spin = spinner()
        head = label(S(f'{title}…'), 15, NSFontWeightSemibold)
        sub = secondary(sub or 'Output from codexpool lane test. This can take a few minutes.', 11)
        scroll = NSTextView.scrollableTextView()
        text = scroll.documentView()
        text.setEditable_(False)
        text.setFont_(mb.font(11, mono=True))
        text.setTextContainerInset_((6, 8))
        auto(scroll)
        state = {'job': None, 'done': False}
        stop = button('Stop', None, keep)

        def append(line: str):
            text.textStorage().appendAttributedString_(NSAttributedString.alloc().initWithString_attributes_(
                S(line + '\n'), {NSFontAttributeName: mb.font(11, mono=True),
                                 NSForegroundColorAttributeName: NSColor.labelColor()}))
            text.scrollToEndOfDocument_(None)

        def close(_sender):
            if not state['done'] and state['job'] is not None:
                state['job'].stop()
                return
            win.endSheet_(sheet)
            self.sheet = None

        def done(r: Result):
            state['done'] = True
            self.stream_job = None
            head_spin.stopAnimation_(None)
            head.setStringValue_(S(f'{title}: passed' if r.ok else (f'{title}: stopped' if state['job'].stopped else
                                                                    f'{title}: failed ({r.message()})')))
            stop.setTitle_('Close')
            make_primary(stop)
            if finished:
                finished(r)
        stop.setTarget_(target(close, keep))
        top = hstack([head_spin, vstack([head, sub], spacing=2, full=False)], spacing=10, cluster=True)
        body = vstack([top, scroll, hstack([], [stop])], spacing=12, insets=(18, 20, 16, 20))
        fix(scroll, h=h - 130)
        root.addSubview_(body)
        pin(body, root)
        self.sheet = (sheet, keep)
        win.beginSheet_completionHandler_(sheet, None)
        state['job'] = self.stream_job = Job(args, done, append)

    # -- pool and Codex actions used by several panes --------------------------------------------------
    @objc.python_method
    def restart_pool(self, pane: Pane):
        self.ask('Restart the pool?', 'Codex requests fail for a few seconds while the pool restarts.', 'Restart',
                 lambda: pane.run(['restart'], 'Restarting the pool…', 'Pool restarted'))

    @objc.python_method
    def reopen_codex(self, pane: Pane | None, setup: SetupAssistant | None = None):
        if not app_installed(CODEX_BUNDLE):
            msg = ('error', 'The Codex app isn’t installed.')
            if pane is not None:
                pane.say(*msg)
            if setup is not None:
                setup.done_note = msg
                setup.render()
            return

        def go():
            def done(ok: bool, text: str):
                if pane is not None:
                    pane.say('ok' if ok else 'error', text)
                if setup is not None:
                    setup.done_note = ('ok' if ok else 'error', text)
                    setup.render()
            if pane is not None:
                pane.say('busy', 'Reopening Codex…')
            if setup is not None:
                setup.done_note = ('busy', 'Reopening Codex…')
                setup.render()
            CodexReopener(done)
        self.ask('Quit and reopen the Codex app?',
                 'Codex quits and opens again, so it picks up the pool. Finish or save what you are typing in Codex '
                 'first.', 'Reopen', go)

    # -- menus -----------------------------------------------------------------------------------------
    @objc.python_method
    def build_menus(self):
        main = NSMenu.alloc().init()

        def submenu(title):
            item = main.addItemWithTitle_action_keyEquivalent_(S(title), None, '')
            menu = NSMenu.alloc().initWithTitle_(S(title))
            item.setSubmenu_(menu)
            return menu

        def add(menu, title, action, key='', target_=None, mask=None, tag=0):
            item = menu.addItemWithTitle_action_keyEquivalent_(S(title), action, key)
            if target_ is not None:
                item.setTarget_(target_)
            if mask is not None:
                item.setKeyEquivalentModifierMask_(mask)
            item.setTag_(tag)
            return item
        app_menu = submenu('codexpool')
        add(app_menu, 'About codexpool', 'showPaneItem:', '', self, tag=PANES.index('about'))
        app_menu.addItem_(NSMenuItem.separatorItem())
        add(app_menu, 'Settings…', 'showPaneItem:', ',', self, tag=PANES.index('overview'))
        add(app_menu, 'Setup Assistant…', 'openSetupItem:', '', self)
        app_menu.addItem_(NSMenuItem.separatorItem())
        add(app_menu, 'Hide codexpool', 'hide:', 'h')
        add(app_menu, 'Hide Others', 'hideOtherApplications:', 'h', mask=(1 << 19) | (1 << 20))
        add(app_menu, 'Show All', 'unhideAllApplications:')
        app_menu.addItem_(NSMenuItem.separatorItem())
        add(app_menu, 'Quit codexpool', 'terminate:', 'q')
        edit = submenu('Edit')
        add(edit, 'Undo', 'undo:', 'z')
        add(edit, 'Redo', 'redo:', 'Z')
        edit.addItem_(NSMenuItem.separatorItem())
        for title, action, key in (('Cut', 'cut:', 'x'), ('Copy', 'copy:', 'c'), ('Paste', 'paste:', 'v'),
                                   ('Select All', 'selectAll:', 'a')):
            add(edit, title, action, key)
        view = submenu('View')
        for i, cls in enumerate(PANE_CLASSES):
            add(view, cls.title, 'showPaneItem:', str(i + 1), self, tag=i)
        window = submenu('Window')
        add(window, 'Minimize', 'performMiniaturize:', 'm')
        add(window, 'Close', 'performClose:', 'w')
        help_menu = submenu('Help')
        add(help_menu, 'codexpool Documentation', 'openDocsItem:', '?', self)
        add(help_menu, 'Report an Issue', 'openIssuesItem:', '', self)
        app = NSApplication.sharedApplication()
        app.setMainMenu_(main)
        app.setWindowsMenu_(window)
        app.setHelpMenu_(help_menu)

    def showPaneItem_(self, item):
        self.open_pane(PANES[item.tag()])

    def openSetupItem_(self, item):
        self.open_setup('setup-welcome')

    def openDocsItem_(self, item):
        open_thing(DOCS_URL)

    def openIssuesItem_(self, item):
        open_thing(ISSUES_URL)


def patch_identity():
    """The process's name in the menu bar and Dock, and its defaults domain, before AppKit starts."""
    info = NSBundle.mainBundle().infoDictionary()
    try:
        info['CFBundleIdentifier'] = BUNDLE_ID
        info['CFBundleName'] = 'codexpool'
    except (TypeError, AttributeError):
        pass
    NSProcessInfo.processInfo().setProcessName_('codexpool')


def run_app(pane: str, pool: str | None = None):
    request = f'{pane}@{pool}' if pool else pane   # a second launch forwards the pool with the pane
    if not claim_single_instance(request):
        return
    patch_identity()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyRegular)
    controller = SettingsController.alloc().init()
    controller.configure(Store(), request)
    app.setDelegate_(controller)
    AppHelper.runEventLoop(unexpectedErrorAlert=lambda: True)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 11. Snapshot mode and main()
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def render_window(win) -> tuple:
    """The window's frame view (title bar, traffic lights, content) cached offscreen at 2x."""
    win.contentView().layoutSubtreeIfNeeded()
    win.displayIfNeeded()
    frame = win.contentView().superview()
    size = frame.bounds().size
    rep = mb.new_bitmap(size.width, size.height)
    frame.cacheDisplayInRect_toBitmapImageRep_(frame.bounds(), rep)
    return rep, size.width, size.height


def compose_snapshot(rep, w: float, h: float, dark: bool, appearance, out: str, sheets=()):
    """The window on a soft backdrop with rounded corners, a hairline and a shadow, like the popover shots; sheets
    ((rep, w, h), bottom first) hang below its toolbar, centred, each on the one before."""
    margin, radius = 34.0, 12.0
    W, H = w + 2 * margin, h + 2 * margin
    card = ((margin, margin), (w, h))

    def compose():
        top, bottom = ((0x2A, 0x2E, 0x3A), (0x14, 0x16, 0x1D)) if dark else ((0xCD, 0xD6, 0xE6), (0xB6, 0xC1, 0xD8))
        NSGradient.alloc().initWithStartingColor_endingColor_(
            mb.rgb(*(c / 255 for c in top)), mb.rgb(*(c / 255 for c in bottom))).drawInRect_angle_(((0, 0), (W, H)), 90)
        NSGraphicsContext.saveGraphicsState()
        sh = NSShadow.alloc().init()
        sh.setShadowBlurRadius_(30)
        sh.setShadowOffset_((0, -12))
        sh.setShadowColor_(mb.rgb(0, 0, 0, 0.5 if dark else 0.25))
        sh.set()
        mb.fill_rounded(card, radius, mb.rgb(0.16, 0.16, 0.17) if dark else mb.rgb(0.96, 0.96, 0.96))
        NSGraphicsContext.restoreGraphicsState()
        NSGraphicsContext.saveGraphicsState()
        mb.rounded(card, radius).addClip()
        rep.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            card, ((0, 0), (0, 0)), NSCompositingOperationSourceOver, 1.0, True, None)
        NSGraphicsContext.restoreGraphicsState()
        mb.stroke_rounded(card, radius, mb.rgb(1, 1, 1, 0.14) if dark else mb.rgb(0, 0, 0, 0.16), 1.0)
        if dark:   # the inner light edge dark windows have
            mb.stroke_rounded(((margin + 1, margin + 1), (w - 2, h - 2)), radius - 1, mb.rgb(1, 1, 1, 0.05), 1.0)
        for srep, sw, sh_ in sheets:
            box = ((margin + round((w - sw) / 2), margin + BAR_H - 4), (sw, sh_))
            NSGraphicsContext.saveGraphicsState()
            shadow = NSShadow.alloc().init()
            shadow.setShadowBlurRadius_(24)
            shadow.setShadowOffset_((0, -8))
            shadow.setShadowColor_(mb.rgb(0, 0, 0, 0.55 if dark else 0.28))
            shadow.set()
            mb.fill_rounded(box, SHEET_RADIUS, NSColor.windowBackgroundColor())
            NSGraphicsContext.restoreGraphicsState()
            NSGraphicsContext.saveGraphicsState()
            mb.rounded(box, SHEET_RADIUS).addClip()
            srep.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                box, ((0, 0), (0, 0)), NSCompositingOperationSourceOver, 1.0, True, None)
            NSGraphicsContext.restoreGraphicsState()
            mb.stroke_rounded(box, SHEET_RADIUS, mb.rgb(1, 1, 1, 0.12) if dark else mb.rgb(0, 0, 0, 0.14), 1.0)

    mb.write_png(mb.render_offscreen(compose, W, H, appearance), out)


def snapshot(args):
    global SNAPSHOT
    SNAPSHOT = True
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyProhibited)
    dark = args.appearance == 'dark'
    appearance = NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua if dark else NSAppearanceNameAqua)
    app.setAppearance_(appearance)
    raw, _ = mb.load_status(args.status)
    now = mb.parse_time(args.now) if args.now else None
    if now is None and raw is not None:
        generated = mb.parse_time(raw.get('generated_at'))
        now = generated + dt.timedelta(seconds=12) if generated else None
    # no --history / --pool-history: no pace line; no --pool-status: no add-on pool (deterministic)
    store = Store(args.status, now or mb.utcnow(), args.history, args.pool_status, args.pool_history)
    store.load_fixtures(args.doctor, args.lanes, args.providers, args.models)
    controller = SettingsController.alloc().init()
    pool = pool_id(args.pool) or 'codex'
    controller.configure(store, args.pane, args.height or WIN_H, pool)
    ext_ui = next((u for u in mb.POOL_UI.values() if args.pane in u.snapshot_panes), None)   # an add-on's state
    pane, ext_pool = ext_ui.snapshot_pane(args.pane) if ext_ui else (args.pane, None)

    sheets = []
    if pane.startswith('setup-'):
        setup = controller.ensure_setup()
        setup.pool = ext_pool or pool
        setup.win.setAppearance_(appearance)
        step = {'setup-welcome': 'welcome', 'setup-done': 'done'}.get(pane, 'accounts')
        ui = pool_ui(setup.pool)
        login = ui.snapshot_login(args.pane, setup) if ui else None   # the add-on's made-up sign-in states
        if args.pane == 'setup-signin':
            setup.snapshot_clock = 1000.0
            setup.login = login or Login(phase='waiting', label='Work C', url=DEMO_SIGNIN_URL,
                                         deadline=1000.0 + 252, copied=1)
        elif args.pane == 'setup-added':
            setup.login = login or Login(phase='added', label='Work C', seat_file='codex-work-c.json',
                                         plan='Business 5×')
        elif args.pane == 'setup-again':
            setup.login = login or Login(phase='again', label='Personal', seat_file='codex-personal.json', plan='Plus')
        setup.show(step)
        rep, w, h = render_window(setup.win)
    else:
        if args.seat:
            controller.panes['seats'].picked[pool] = args.seat
        view = controller.ensure_view()
        view.win.setAppearance_(appearance)
        chain = ext_ui.snapshot_prepare(controller, args.pane) if ext_ui else []
        view.select('lanes' if pane in LANE_SHOTS else pane)
        if pane in LANE_SHOTS:
            chain = demo_sheets(controller, pane, args.lane)
        for sheet in chain:
            sheet.win.setAppearance_(appearance)
            sheet.render()
        need = max([0.0] + [BAR_H + sheet.win.contentView().frame().size.height + 40 for sheet in chain])
        if not args.height:   # fit the content, like a window the user sized to it
            want = max(BAR_H + view.content_height(), need)
            h = max(MIN_H, min(900.0, want))
            view.win.setContentSize_((WIN_W, h))
            view.render()
        rep, w, h = render_window(view.win)
        sheets = [render_view(sheet.win.contentView()) for sheet in chain]
    compose_snapshot(rep, w, h, dark, appearance, args.snapshot, sheets)
    print(args.snapshot)


def demo_sheets(controller, pane: str, lane: str | None = None) -> list:
    """The sheets a lanes-* snapshot shows, from the fixtures, bottom first. lane: the lane the editor opens on
    (default: the first)."""
    st = controller.store
    lanes = st.lanes or []
    if lane:
        lanes = [x for x in lanes if x.name == lane] or lanes
    providers = st.provider_list()
    if pane == 'lanes-key':
        p = next((q for q in providers if q.needs == 'key' and q.key_name and not q.ready), None) or \
            next((q for q in providers if q.key_name), providers[0])
        return [KeySheet(controller, p.key_name or 'opencode-go', p.title)]
    if pane == 'lanes-signin':
        sheet = XaiLoginSheet(controller)
        sheet.phase, sheet.url, sheet.copied = 'waiting', DEMO_XAI_URL, 1
        return [sheet]
    if pane == 'lanes-new' or not lanes:
        editor = LaneEditor(controller, None, lambda *a: None)
        editor.name, editor.effort = 'review', 'high'
        editor.role = 'Reviews diffs before they land and gives a second opinion on risky changes.'
        p = next((q for q in providers if q.ready and q.id not in ('xai', 'responses')), providers[0])
        m = (st.demo_models or [LaneModel('model-id', '', None)])[0]
        editor.members = [Draft('', p.id, m.id, m.name)]
    else:
        editor = LaneEditor(controller, lanes[0], lambda *a: None)
    chain = [editor]
    if pane in ('lanes-model', 'lanes-model-key', 'lanes-model-engine'):
        has_xai = any(d.provider == 'xai' for d in editor.members)
        if pane == 'lanes-model-engine':
            want = [q for q in providers if q.needs == 'engine']
        else:
            want = [q for q in providers if q.id not in ('xai', 'responses') and q.needs != 'engine' and
                    bool(q.ready) == (pane == 'lanes-model')]
        sheet = AddModelSheet(controller, editor, has_xai, lambda d: None, (want or providers)[0].id)
        if pane == 'lanes-model-engine':
            sheet.model, sheet.name = lane_copy(sheet.provider)['demo_model']
        if pane == 'lanes-model' and sheet.models:
            taken = {d.model for d in editor.members if d.provider == sheet.provider}
            m = next((x for x in sheet.models if x.id not in taken), sheet.models[0])
            sheet.model, sheet.name = m.id, m.name
        chain.append(sheet)
    return chain


def render_view(view) -> tuple:
    """A view (a sheet's content) cached offscreen at 2x: (rep, width, height)."""
    view.layoutSubtreeIfNeeded()
    size = view.bounds().size
    rep = mb.new_bitmap(size.width, size.height)
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
    return rep, size.width, size.height


def ext_shots() -> tuple:
    """The add-on's snapshot-only states (PoolUI.snapshot_panes)."""
    return tuple(pane for u in mb.POOL_UI.values() for pane in getattr(u, 'snapshot_panes', ()))


def main(argv=None):
    p = argparse.ArgumentParser(prog='codexpool-settings', description='codexpool Settings window and Setup assistant')
    p.add_argument('--pane', default='overview', choices=PANES + SETUP_PANES + LANE_SHOTS + ext_shots(),
                   help="the pane or Setup assistant step to open (lanes-* and an add-on's states: snapshots only)")
    p.add_argument('--pool', choices=POOLS + mb.pool_aliases(),
                   help='the pool Overview, Seats, Balancing or the Setup assistant show (default: the last one '
                        'chosen)')
    p.add_argument('--snapshot', metavar='OUT.png', help='render the window to a PNG and exit (runs no command)')
    p.add_argument('--appearance', choices=('light', 'dark'), default='light', help='(snapshot)')
    p.add_argument('--status', type=Path, default=mb.STATUS_FILE, help='status.json to render (snapshot)')
    p.add_argument('--doctor', type=Path, help='codexpool doctor --json output to render (snapshot)')
    p.add_argument('--lanes', type=Path, help='codexpool lane list --json output to render (snapshot)')
    p.add_argument('--providers', type=Path, help='codexpool lane providers --json output to render (snapshot)')
    p.add_argument('--models', type=Path, help='codexpool lane models --json output, for every provider (snapshot)')
    p.add_argument('--history', type=Path, help='history.jsonl for the pace line (snapshot; default: none)')
    p.add_argument('--pool-status', type=Path, help="an add-on pool's status file to render (snapshot; default: none, "
                                                     'so that pool reads as not installed)')
    p.add_argument('--pool-history', type=Path, help="its history file, for its pace line (snapshot)")
    p.add_argument('--now', help='pretend the time is this ISO-8601 instant (snapshot)')
    p.add_argument('--height', type=float, help='window height in pt (snapshot; default: fit the content)')
    p.add_argument('--seat', metavar='FILE', help='the seat file name the Seats pane selects (snapshot; default: the '
                                                  'first)')
    p.add_argument('--lane', metavar='NAME', help='the lane the lanes-edit / lanes-model snapshots open (default: '
                                                  'the first)')
    p.add_argument('--marks', choices=('drawn', 'app'),
                   help="the pool switcher's marks: plain drawn shapes, or the logos from the pools' apps on this "
                        "Mac (default: drawn for a snapshot, app for the live window)")
    args = p.parse_args(argv)
    args.pool = pool_id(args.pool)
    # the menu bar module's own default is drawn (so importing it reads nothing from /Applications); the live window
    # wants the logos, a snapshot the reproducible shapes unless asked
    mb.MARKS = args.marks or ('drawn' if args.snapshot else 'app')
    if args.snapshot:
        snapshot(args)
        return
    ext_ui = next((u for u in mb.POOL_UI.values() if args.pane in u.snapshot_panes), None)
    if ext_ui is not None:   # snapshot states: live, the pane or step they are drawn on
        args.pane, args.pool = ext_ui.snapshot_pane(args.pane)
    run_app(args.pane, args.pool)


def pool_id(name):
    return mb.pool_id(name)


# The add-on's PoolUI gets this module once it is defined: its Settings members are built on the helpers above.
for _ui in mb.POOL_UI.values():
    _ui.settings_loaded(sys.modules[__name__])


if __name__ == '__main__':
    main()
