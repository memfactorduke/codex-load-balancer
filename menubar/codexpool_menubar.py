#!/usr/bin/env python3
"""Codex Pool menu bar app.

A native macOS menu bar item (Python + PyObjC/AppKit) for codexpool, the pool of ChatGPT seats behind the
Codex desktop app. The spec is ~/.codexpool/menubar/SPEC.md; the pool itself is ~/.codexpool/README.md.

It reads only ~/.codexpool/state/status.json and ~/.codexpool/state/history.jsonl (plus settings.json, for the
Python that runs codexpool): no Keychain, no network, no management API. Actions shell out to codexpool
(non-blocking) or open Terminal. Settings… and "Add a ChatGPT account…" start the Settings window
(codexpool_settings.py, its own process); on first run, with no Codex seats yet, the app opens its Setup assistant.

Run (the LaunchAgent that `codexpool install` sets up does this, with "menubar_python" from settings.json):
    ~/.codexpool/.venv/bin/python ~/.codexpool/menubar/codexpool_menubar.py

Snapshot (no UI; for humans and agents checking the design):
    codexpool_menubar.py --snapshot OUT.png [--appearance light|dark] [--status PATH] [--history PATH]
                         [--now ISO-8601] [--range 24h|7d] [--max-height PT] [--hover KIND:VALUE]
                         [--marks drawn|app]
    writes OUT.png (the popover) and OUT-menubar.png (the menu bar item), both at 2x.
    --history defaults to history.jsonl beside --status when that exists, else the live history.
    --max-height caps the popover the way a short screen does (the seat list then scrolls).
    --hover highlights one region, e.g. seat:<seat file name> or action:doctor, and prints its tooltip if it has one
    (tip:headline prints the hero's breakdown).
    --marks app draws the pools' marks as the live app does, from the pools' apps on this Mac; the default,
    drawn, uses plain drawn shapes and reads nothing from /Applications, so snapshots are reproducible.
    --pool-status makes an add-on's pool installed (both numbers in the item, the switcher in the popover).

A second pool comes from an add-on (addons/<id>/menubar_ext.py, docs/ADDONS.md): its `load(mb)` returns a PoolUI
whose members this file reads by name (section 1, load_pool_extensions). Without one the app is the Codex pool's.

Layout of this file:
    1. Paths and constants          6. Drawing primitives
    2. Parsing and formatting       7. Menu bar item (meter image + title)
    3. Status model                 8. Popover layout (what goes where) and the views that draw it
    4. History, pace and chart      9. Actions (codexpool, Terminal) and the app controller
    5. Theme (colours, fonts)      10. Snapshot mode and main()
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import math
import os
import plistlib
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path
from xml.parsers.expat import ExpatError

import objc
from AppKit import (
    NSAlert,
    NSAlertFirstButtonReturn,
    NSAppearance,
    NSAppearanceNameAqua,
    NSAppearanceNameDarkAqua,
    NSApplication,
    NSApplicationActivationPolicyAccessory,
    NSApplicationActivationPolicyProhibited,
    NSAttributedString,
    NSBezierPath,
    NSBitmapImageFileTypePNG,
    NSBitmapImageRep,
    NSColor,
    NSCompositingOperationCopy,
    NSCompositingOperationSourceIn,
    NSCompositingOperationSourceOver,
    NSDeviceRGBColorSpace,
    NSEvent,
    NSEventTypeLeftMouseDown,
    NSEventTypeLeftMouseUp,
    NSEventTypeRightMouseDown,
    NSEventTypeRightMouseUp,
    NSEventMaskKeyDown,
    NSEventMaskLeftMouseDown,
    NSEventMaskOtherMouseDown,
    NSEventMaskRightMouseDown,
    NSEventModifierFlagCommand,
    NSEventModifierFlagControl,
    NSEventModifierFlagDeviceIndependentFlagsMask,
    NSEventModifierFlagOption,
    NSEventModifierFlagShift,
    NSFont,
    NSFontAttributeName,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSImage,
    NSImageInterpolationHigh,
    NSImageLeft,
    NSImageOnly,
    NSImageSymbolConfiguration,
    NSLineBreakByTruncatingMiddle,
    NSLineBreakByTruncatingTail,
    NSLineBreakByWordWrapping,
    NSLineCapStyleRound,
    NSLineJoinStyleRound,
    NSMenu,
    NSMenuItem,
    NSMutableParagraphStyle,
    NSNoBorder,
    NSParagraphStyleAttributeName,
    NSPopover,
    NSPopoverBehaviorTransient,
    NSRectEdgeMinY,
    NSRectFillUsingOperation,
    NSScreen,
    NSScrollerStyleOverlay,
    NSScrollView,
    NSShadow,
    NSStatusBar,
    NSStringDrawingTruncatesLastVisibleLine,
    NSStringDrawingUsesLineFragmentOrigin,
    NSTextAlignmentCenter,
    NSTextAlignmentLeft,
    NSTextAlignmentRight,
    NSTrackingActiveAlways,
    NSTrackingArea,
    NSTrackingInVisibleRect,
    NSTrackingMouseEnteredAndExited,
    NSTrackingMouseMoved,
    NSView,
    NSViewController,
    NSWorkspace,
    NSWorkspaceDidWakeNotification,
)
from Foundation import (NSAffineTransform, NSBundle, NSObject, NSPointInRect, NSRunLoop, NSRunLoopCommonModes, NSTimer,
                        NSUserDefaults)
from PyObjCTools import AppHelper

# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 1. Paths and constants
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

HOME = Path.home()
POOL_DIR = HOME / '.codexpool'
STATUS_FILE = POOL_DIR / 'state' / 'status.json'
HISTORY_FILE = POOL_DIR / 'state' / 'history.jsonl'
SETTINGS_FILE = Path(os.environ.get('CODEXPOOL_SETTINGS') or POOL_DIR / 'settings.json').expanduser()
CODEXPOOL = HOME / '.local' / 'bin' / 'codexpool'   # the wrapper `codexpool install` writes (Terminal commands only)
CODEXPOOL_SCRIPT = POOL_DIR / 'bin' / 'codexpool'   # what background actions run, with codexpool_python()
WRAPPER_MARK = '# codexpool wrapper'                # first comment line of that wrapper
DOCS = POOL_DIR / 'README.md'                      # the local copy of the repo's README
DOCS_URL = 'https://github.com/memfactorduke/codex-load-balancer#readme'   # when there is no local copy
SETTINGS_SCRIPT = Path(__file__).resolve().with_name('codexpool_settings.py')   # the Settings window (own process)
SETUP_SHOWN = POOL_DIR / 'state' / 'setup-shown'   # the first-run Setup assistant has been opened (or wasn't needed)
ADDONS_DIR = Path(os.environ.get('CODEXPOOL_ADDONS') or Path(__file__).resolve().parent.parent / 'addons')

# The pools: the Codex pool, and at most one more from an add-on (a second CLIProxyAPI instance with its own status
# file). An add-on's pool is installed when its PoolUI says so from its status file; without that the item and the
# popover are the Codex pool's alone. POOLS, POOL_NAME, POOL_UI, POOL_COLORS, MARK_APPS and MARK_SCALE are extended
# by register_pool_ui() when the module has loaded (load_pool_extensions, at the end of this file).
POOLS = ('codex',)
POOL_NAME = {'codex': 'Codex'}
POOL_UI: dict = {}    # pool id -> the add-on's PoolUI (never the Codex pool)

BUNDLE_ID = 'com.codexpool.menubar'
AUTOSAVE_NAME = 'CodexPool'

STALE_AFTER_S = 180          # status.json older than this means "pool not reporting"
WAKE_GRACE_S = 120           # ... except this soon after the Mac wakes: the guard needs a pass to catch up
WAKE_REPOLL_S = 15           # re-read the files this long after a wake
POLL_EVERY_S = 10            # how often we stat the status file
COUNTDOWN_EVERY_S = 30       # how often countdown labels refresh while the popover is open
TOAST_S = 8                  # how long an action's feedback replaces the header subtitle
REOPEN_GUARD_S = 0.35        # a click this soon after the popover closed is the click that closed it
KEY_ESCAPE = 53              # kVK_Escape: closes the popover
HISTORY_TAIL_BYTES = 1 << 20 # read at most the last 1 MiB of history.jsonl
PACE_WINDOW_S = 6 * 3600     # the pace slope looks at the last 6 h of history
PACE_MIN_RISING_S = 30 * 60  # ... and needs at least 30 min of non-dropping samples
MIN_CHART_SAMPLES = 3        # fewer than this in the range shows "Collecting history…"

WIDTH = 340.0                # popover width, pt
PAD = 16.0                   # popover side padding
INNER = WIDTH - 2 * PAD
SCREEN_MARGIN = 40.0         # the popover is at most the screen's visible height minus this; the seats scroll
MIN_LIST_H = 110.0           # ... but always shows at least this much of the seat list

RANGES = {'24h': (24 * 3600, '24h', 30 * 60, '30 min'),      # (span, label, usage bucket, its words)
          '7d': (7 * 24 * 3600, '7d', 4 * 3600, '4 h')}
RESET_MIN_PCT = 0.5          # a drop of at least this much in the headline's used % between samples is a reset

# What the headline covers and how numbers read: pool.headline and pool.display in status.json, which the guard
# copies from settings.json. A file without them (an older guard) gets the defaults, the first of each.
HEADLINES = ('all', 'regular')     # every seat that is not off, reserve included / the regular seats only
DISPLAYS = ('left', 'used')        # count down from 100 % / count up from 0 %
SCOPE = {'all': 'all seats', 'regular': 'regular seats'}   # an add-on's pool has its own words (PoolUI.scope_words)
# How the pool picks a seat: pool.balancing, also copied from settings.json. `priority`: the fill order you set;
# `reset`: the guard reorders the regular seats so the one whose weekly quota resets soonest goes first.
BALANCINGS = ('priority', 'reset')
ORDER_TITLE = {'priority': 'Your order', 'reset': 'Soonest reset first'}
ORDER_TIP = {
    'priority': 'The pool uses the first seat until it runs out, then the next. Change the order in Settings.',
    'reset': 'The pool uses the seat whose weekly quota resets soonest, so none of it goes to waste, and '
             'reorders the seats by itself. New threads follow; running threads stay on their seat. '
             'The reserve stays last.',
}

# Seat states written by the guard, and how the popover names them.
SERVING, READY = 'active', 'ready'
OUT_STATES = ('exhausted', 'cooldown')
UNAVAILABLE = OUT_STATES + ('parked', 'disabled', 'blocked')   # rows that cannot serve are dimmed
STATE_PILL = {'active': 'Serving', 'ready': 'Ready', 'exhausted': 'Out', 'cooldown': 'Out',
              'parked': 'Parked', 'blocked': 'Blocked', 'disabled': 'Off'}
SIGN_IN_ENDED = 'OpenAI ended this sign-in'   # a seat that still serves on its access token: Re-login soon

# Plan families, matched in order against the plan string ("self_serve_business_prolite" is Business).
PLAN_NAMES = (('enterprise', 'Enterprise'), ('business', 'Business'), ('team', 'Team'), ('edu', 'Edu'),
              ('promax', 'Pro $500'), ('pro', 'Pro'), ('plus', 'Plus'), ('free', 'Free'))   # an add-on's pool: PoolUI.plan_names

NO_FILE = 'No status file yet'
INCOMPLETE = 'The status file is incomplete'


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 2. Parsing and formatting (defensive: status.json may be partial or from an older guard)
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

UTC = dt.timezone.utc


def utcnow() -> dt.datetime:
    return dt.datetime.now(UTC)


def as_dict(x) -> dict:
    return x if isinstance(x, dict) else {}


def as_list(x) -> list:
    return x if isinstance(x, list) else []


def as_str(x, default: str = '') -> str:
    return x if isinstance(x, str) else default


def as_num(x, default=None):
    if isinstance(x, bool) or x is None:
        return default
    try:
        v = float(x)
    except (TypeError, ValueError):
        return default
    return v if math.isfinite(v) else default


def clamp_pct(v):
    return None if v is None else max(0.0, min(100.0, v))


def parse_time(value) -> dt.datetime | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        try:
            return dt.datetime.fromtimestamp(value, UTC)
        except (OverflowError, OSError, ValueError):
            return None
    if not isinstance(value, str) or not value.strip():
        return None
    s = value.strip()
    if s.endswith('Z'):
        s = s[:-1] + '+00:00'
    try:
        t = dt.datetime.fromisoformat(s)
    except ValueError:
        return None
    return t if t.tzinfo else t.replace(tzinfo=UTC)


def fmt_pct(v) -> str:
    return '—' if v is None or not math.isfinite(v) else f'{round(v):d}%'


def fmt_span(seconds: float) -> str:
    """A countdown, written like CodexBar's: '45m', '3h 56m', '3d 20h'. Rounds down, never less than '1m'."""
    m = max(1, int(seconds // 60))
    if m < 60:
        return f'{m}m'
    h, m = divmod(m, 60)
    if h < 24:
        return f'{h}h {m}m' if m else f'{h}h'
    d, h = divmod(h, 24)
    return f'{d}d {h}h' if h else f'{d}d'


def fmt_age(seconds) -> str:
    """'just now' under a minute, then '2m ago', '3h 5m ago'."""
    if seconds is None:
        return 'a while ago'
    if seconds < 60:
        return 'just now'
    return f'{fmt_span(seconds)} ago'


def fmt_approx_hours(hours: float) -> str:
    if hours < 1:
        return f'~{max(5, round(hours * 60 / 5) * 5)}m'
    if hours < 48:
        return f'~{round(hours)}h'
    return f'~{round(hours / 24)}d'


def money(v, currency: str = 'USD') -> str:
    """'$31', '$112.40' (cents only when there are any); other currencies as 'EUR 31'."""
    if v is None:
        return '—'
    text = f'{v:,.0f}' if abs(v - round(v)) < 0.005 else f'{v:,.2f}'
    return f'${text}' if currency.upper() == 'USD' else f'{currency.upper()} {text}'


def plan_badge(plan: str, weight, names=PLAN_NAMES) -> str:
    """'team', 1 -> 'Team 1×'; 'self_serve_business_prolite', 5 -> 'Business 5×'; 'pro', 20 -> 'Pro 20×'.
    An add-on's pool passes its own names, e.g. 'max_20x', 20 -> 'Max 20×'."""
    p = plan.lower()
    name = next((n for key, n in names if key in p), plan.replace('_', ' ').title() if plan else '')
    mult = f'{weight:g}×' if weight is not None else ''
    return ' '.join(x for x in (name, mult) if x)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 3. Status model: status.json + history -> one Model the UI draws from
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class Window:
    used: float | None           # percent, 0..100
    reset_at: dt.datetime | None
    minutes: float | None        # 300 = the 5-hour window, 10080 = the week


@dataclass
class Scoped:
    """One more named limit an add-on's pool has (a weekly cap that covers one model family): drawn as a thin bar
    under the week's, and counted among the seat's windows."""
    name: str
    used: float | None
    reset_at: dt.datetime | None


@dataclass
class Seat:
    label: str
    name: str                    # seat file name; what we pass to codexpool
    email: str
    plan: str                    # 'Team 1×'
    state: str                   # active | ready | exhausted | cooldown | parked | blocked | disabled | ...
    detail: str
    until: dt.datetime | None    # when an out/parked seat comes back
    priority: float | None
    weight: float | None
    reserve: bool
    week: Window | None
    short: Window | None         # the 5-hour window (Team seats)
    capacity_known: bool = True
    resets: int = 0              # banked free resets (codexpool reset uses one)
    reset_expiry: dt.datetime | None = None   # when the soonest banked reset expires
    sign_in_ended: bool = False  # the provider ended its sign-in: only a new one helps
    provider: str = 'codex'      # the pool the seat is in (its id)
    scoped: list[Scoped] = field(default_factory=list)   # an add-on's pool: its extra named limits
    extra: object = None         # an add-on's pool: what else its PoolUI.parse_seat read (its credits, say)
    spending: bool = False       # the normalised alarm: serving on paid use right now (PoolUI.parse_seat sets it)

    @property
    def serving(self) -> bool:
        return self.state == SERVING

    def at_limit(self, now: dt.datetime) -> bool:  # a plan window is used up and still in force at now
        return any(w is not None and w.used is not None and w.used >= 100 and (w.reset_at is None or w.reset_at > now)
                   for w in (self.week, self.short))

    @property
    def sign_in_soon(self) -> bool:  # still served on an access token that has not run out yet (up to a day)
        return self.sign_in_ended and self.available

    @property
    def available(self) -> bool:
        return self.state in (SERVING, READY)

    @property
    def unavailable(self) -> bool:  # dimmed row, grey bars
        return self.state in UNAVAILABLE


@dataclass
class Sample:
    t: float                     # epoch seconds
    used: float | None           # weekly use of the regular seats (history.jsonl 'used')
    reserve: bool                # a reserve seat was serving
    all: float | None = None     # weekly use of every seat, reserve included (history.jsonl 'all')
    spending: bool = False       # the historical alarm: a seat served on paid use (PoolUI.history_alarm_key)

    @property
    def alarm(self) -> bool:     # red on the chart, as the headline is: the reserve, paid use, or every seat out
        return self.reserve or self.spending or (self.all is not None and self.all >= 100)


def sample_value(s: Sample, mode: str) -> float | None:
    """What the headline was at this sample, for headline mode `mode` (all | regular), as used %."""
    return s.all if mode == 'all' else s.used


@dataclass
class Model:
    now: dt.datetime
    status: str                  # regular | reserve | allout | down | stale | empty | missing
    problem: str = ''            # why there is no usable status file (missing)
    age: float | None = None     # seconds since the guard last wrote status.json
    headline: float | None = None    # weekly use in %, of the seats `headline_mode` covers
    headline_mode: str = 'all'   # all | regular (pool.headline)
    display: str = 'left'        # left | used (pool.display): how every number and bar reads
    balancing: str = 'priority'  # priority | reset (pool.balancing): how the pool orders the regular seats
    serving: Seat | None = None
    seats: list[Seat] = field(default_factory=list)
    regular_ready: int = 0
    regular_total: int = 0
    reserve_seats: list[Seat] = field(default_factory=list)
    next_back: tuple[str, dt.datetime] | None = None
    version: str = ''
    history: list[Sample] = field(default_factory=list)
    pool: str = 'codex'          # which pool this is (status.json, or an add-on's status file)
    extra: object = None         # an add-on's pool: what else its PoolUI.parse_pool read from the pool block

    @property
    def ui(self):                   # the add-on's PoolUI; None for the Codex pool
        return POOL_UI.get(self.pool)

    @property
    def reporting(self) -> bool:  # the numbers are current
        return self.status not in ('stale', 'missing')

    @property
    def spending(self) -> Seat | None:   # the seat serving on paid use (an add-on's last resort)
        return next((s for s in self.seats if s.spending), None) if self.serving_now else None

    @property
    def hot(self) -> bool:          # red: the reserve serves, every seat is out, or paid use is being spent
        return self.status in ('reserve', 'allout') or self.spending is not None

    @property
    def warn(self) -> bool:         # grey with a warning glyph: down, not reporting, or nothing in it
        return self.status in ('down', 'stale', 'missing', 'empty')

    @property
    def name(self) -> str:          # 'Codex', or the add-on pool's title
        return POOL_NAME.get(self.pool, 'Codex')

    @property
    def noun(self) -> str:          # what the pool calls a seat
        return self.ui.noun if self.ui else 'seat'

    @property
    def serving_now(self) -> bool:  # a seat is actually taking requests
        return self.status in ('regular', 'reserve')

    @property
    def degraded(self) -> bool:     # grey headline, warning glyph in the menu bar
        return not self.serving_now

    @property
    def left(self) -> bool:         # numbers count down from 100 % and bars drain
        return self.display == 'left'

    @property
    def word(self) -> str:          # '52% left' / '48% used'
        return 'left' if self.left else 'used'

    @property
    def scope(self) -> str:         # 'all seats' / 'regular seats' (an add-on's pool: its own words)
        scope = self.ui.scope_words if self.ui else SCOPE
        return scope.get(self.headline_mode, scope['all'])

    def shown(self, used):
        """A used % as this model shows it: unchanged, or what is left of it. Also the length of its bar; the
        bar's colour always comes from the used % (usage_color), so the two modes colour alike."""
        return None if used is None else (100.0 - used if self.left else used)


def load_status(path: Path) -> tuple[dict | None, str]:
    """Returns (status dict, '') or (None, reason)."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return None, NO_FILE
    except OSError as e:
        return None, f'Cannot read the status file ({e.strerror or e})'
    try:
        data = json.loads(raw)
    except ValueError:
        return None, INCOMPLETE
    if not isinstance(data, dict):
        return None, 'The status file is not a JSON object'
    return data, ''


def parse_window(d) -> Window | None:
    d = as_dict(d)
    if not d:
        return None
    return Window(clamp_pct(as_num(d.get('used'))), parse_time(d.get('reset_at')), as_num(d.get('window_min')))


def parse_seat(d, pool: str = 'codex', now: dt.datetime | None = None) -> Seat | None:
    """One row of status.json's seats[], or of an add-on pool's status file (pool=its id): the same shape, with the
    add-on's names for the week and the short window (PoolUI.week_key, short_key), and whatever else its
    PoolUI.parse_seat reads (the extra limits, the credits, the spending flag; `now` for its rules)."""
    d = as_dict(d)
    if not d:
        return None
    ui = POOL_UI.get(pool)
    week_key = getattr(ui, 'week_key', None) if ui else None
    short_key = getattr(ui, 'short_key', None) if ui else None
    week = parse_window(d.get('week') or (d.get(week_key) if week_key else None))
    week_used = clamp_pct(as_num(d.get('week_used')))
    if week_used is not None and (week is None or week.used is None):
        week = Window(week_used, week.reset_at if week else None, 10080)
    short = parse_window(d.get(short_key) if short_key and d.get(short_key) else d.get('short'))
    if short is not None and short.used is None:
        short = None
    if short is not None and short.minutes is None and short_key:
        short.minutes = 300
    capacity_known = d.get('capacity_known') is not False
    weight = as_num(d.get('weight')) if capacity_known else None
    plan = as_str(d.get('plan')) or as_str(as_dict(d.get('usage')).get('plan'))
    noun = ui.noun if ui else 'seat'
    label = as_str(d.get('label')) or as_str(d.get('email')) or as_str(d.get('name')) or noun.title()
    seat = Seat(label=label, name=as_str(d.get('name')), email=as_str(d.get('email')),
                plan=plan_badge(plan, weight, ui.plan_names if ui else PLAN_NAMES),
                state=as_str(d.get('state')).lower() or 'unknown',
                detail=as_str(d.get('detail')).strip(), until=parse_time(d.get('until')),
                priority=as_num(d.get('priority')), weight=weight, capacity_known=capacity_known, reserve=d.get('reserve') is True,
                week=week, short=short, resets=int(as_num(as_dict(d.get('resets')).get('available'), 0) or 0),
                reset_expiry=parse_time(as_dict(d.get('resets')).get('next_expiry')),
                sign_in_ended=d.get('sign_in_ended') is True, provider=pool)
    if ui:
        ui.parse_seat(d, seat, now)
    return seat


def weighted_used(seats: list[Seat]) -> float | None:
    # Weights are relative sizes (Plus = 1, Pro = 20); cap absurd values so the maths stays finite.
    if any(not s.capacity_known for s in seats):
        return None
    rows = [(min(max(s.weight or 1.0, 0.0), 1e6), s.week.used) for s in seats if s.week and s.week.used is not None]
    total = sum(w for w, _ in rows)
    v = sum(w * u for w, u in rows) / total if total else None
    return v if v is None or math.isfinite(v) else None


def build_model(raw: dict | None, problem: str, history: list[Sample], now: dt.datetime,
                mtime: dt.datetime | None = None, wake_grace: bool = False, pool_name: str = 'codex') -> Model:
    """mtime: when status.json was last written (None in snapshots, whose --now is made up). The age is the
    older of that and generated_at, so a clock step cannot hide a guard that stopped. wake_grace: the Mac
    woke up moments ago, so an old file is not (yet) a guard that stopped. pool_name: an add-on pool's id for
    its status file (same shape, its own seat fields)."""
    pool_name = pool_name if pool_name in POOLS else 'codex'
    ui = POOL_UI.get(pool_name)
    written = (now - mtime).total_seconds() if mtime else None
    if raw is None:
        return Model(now=now, status='missing', problem=problem or NO_FILE, age=written, history=history,
                     pool=pool_name)

    pool = as_dict(raw.get('pool'))
    generated = parse_time(raw.get('generated_at'))
    ages = [a for a in ((now - generated).total_seconds() if generated else None, written) if a is not None]
    age = max(ages) if ages else None

    rows = as_list(raw.get('seats'))
    if pool_name == 'codex':  # status.json also lists lane logins (e.g. xAI): they aren't seats, and have no usage
        rows = [x for x in rows if as_str(as_dict(x).get('provider'), 'codex') == 'codex']
    seats = [s for s in (parse_seat(x, pool_name, now) for x in rows) if s]
    seats.sort(key=lambda s: -(s.priority if s.priority is not None else -1e9))  # fill order; stable
    regular = [s for s in seats if not s.reserve]
    reserve = [s for s in seats if s.reserve]

    active_label = as_str(raw.get('active'))
    serving = next((s for s in seats if s.serving), None) or \
        next((s for s in seats if active_label and s.label == active_label and s.available), None)

    mode, display = as_str(pool.get('headline')), as_str(pool.get('display'))
    headline_mode = mode if mode in HEADLINES else HEADLINES[0]
    display = display if display in DISPLAYS else DISPLAYS[0]
    balancing = as_str(pool.get('balancing'))
    balancing = balancing if balancing in BALANCINGS else BALANCINGS[0]   # absent (an older guard): your order
    if mode in HEADLINES:   # the guard picked the figure: used_pct is the one `headline` names
        headline = clamp_pct(as_num(pool.get('used_pct')))
        if headline is None:
            headline = clamp_pct(as_num(pool.get(f'used_pct_{mode}')))
    else:                   # an older guard: used_pct is the regular seats, used_pct_all every seat
        headline = clamp_pct(as_num(pool.get('used_pct_all')))
    if headline is None:
        headline = weighted_used([s for s in seats if s.state != 'disabled'] if headline_mode == 'all' else regular)

    selected = [s for s in seats if s.state != 'disabled' and (headline_mode == 'all' or not s.reserve)]
    if any(not s.capacity_known for s in selected):
        headline = None

    nb = as_dict(raw.get('next_back'))
    nb_at = parse_time(nb.get('at'))
    next_back = (as_str(nb.get('label'), '?'), nb_at) if nb_at and nb_at > now else None
    if next_back is None:  # derive it from the seats when the guard did not say
        waiting = [(s.until, s.label) for s in seats if s.until and s.until > now and not s.available]
        if waiting:
            at, label = min(waiting)
            next_back = (label, at)

    regular_ready = sum(1 for s in regular if s.available and not s.spending)  # on plan quota, not on paid use
    if not seats:
        regular_ready = int(as_num(pool.get('regular_available'), 0))
    reserve_in_use = pool.get('reserve_in_use') is True or bool(serving and serving.reserve)
    if not reserve_in_use and regular and regular_ready == 0 and (serving is None or serving.reserve) and \
            any(s.available for s in reserve):
        reserve_in_use = True  # nothing regular left: the next request goes to the reserve seat

    if not pool or 'seats' not in raw:
        status, problem = 'missing', INCOMPLETE
    elif age is None or (age > STALE_AFTER_S and not wake_grace):
        status = 'stale'
    elif pool.get('running') is not True or pool.get('error'):
        status = 'down'
    elif not seats:
        status = 'empty'
    elif not any(s.available for s in seats):
        status = 'allout'
    elif reserve_in_use:
        status = 'reserve'
    else:
        status = 'regular'

    regular_total = len(regular) if seats else max(0, int(as_num(pool.get('seats'), 0)))
    headline = clamp_pct(as_num(headline))
    return Model(now=now, status=status, problem=problem, age=age, headline=headline, headline_mode=headline_mode,
                 display=display, balancing=balancing, serving=serving, seats=seats, regular_ready=regular_ready,
                 regular_total=regular_total, reserve_seats=reserve, next_back=next_back,
                 version=as_str(pool.get('version')), history=history, pool=pool_name,
                 extra=ui.parse_pool(pool) if ui else None)


class DataSource:
    """Caches status.json and history.jsonl by mtime. poll() is cheap enough to run every few seconds."""

    def __init__(self, status_path: Path = STATUS_FILE, history_path: Path | None = HISTORY_FILE,
                 pool: str = 'codex'):
        self.status_path = Path(status_path)
        self.history_path = Path(history_path) if history_path else None
        self.pool = pool                 # codex (status.json), or an add-on pool's id (its own status file)
        self.raw: dict | None = None
        self.problem = NO_FILE
        self.status_mtime: dt.datetime | None = None   # when the copy in self.raw was written
        self.history: list[Sample] = []
        self._status_ns = None
        self._history_ns = None

    @staticmethod
    def _mtime_ns(path: Path | None):
        try:
            return path.stat().st_mtime_ns if path else None
        except OSError:
            return None

    def poll(self, force: bool = False) -> bool:
        """Re-reads whichever file changed. Returns True if anything did."""
        changed = False
        m = self._mtime_ns(self.status_path)
        if force or m != self._status_ns:
            raw, problem = load_status(self.status_path)
            if raw is not None or problem != INCOMPLETE or self.raw is None:
                self.raw, self.problem = raw, problem  # a half-written file keeps the last good copy
                self._status_ns = m
                self.status_mtime = None if m is None else dt.datetime.fromtimestamp(m / 1e9, UTC)
            changed = True
        m = self._mtime_ns(self.history_path)
        if force or m != self._history_ns:
            ui = POOL_UI.get(self.pool)
            self.history = load_history(self.history_path, getattr(ui, 'history_alarm_key', None) if ui else None)
            self._history_ns = m
            changed = True
        return changed

    def model(self, now: dt.datetime | None = None, wake_grace: bool = False) -> Model:
        """now: pretend time (snapshots); the file's mtime is only meaningful against the real clock."""
        return build_model(self.raw, self.problem, self.history, now or utcnow(),
                           mtime=self.status_mtime if now is None else None, wake_grace=wake_grace,
                           pool_name=self.pool)

    def is_installed(self, now: dt.datetime | None = None) -> bool:
        """For an add-on's pool: is it installed at all, by its PoolUI's rule over the status file (now: pretend
        time, as in model()). The Codex pool always is."""
        ui = POOL_UI.get(self.pool)
        if ui is None:
            return True
        return ui.installed(self.raw, self.problem, self.model(now).age if self.raw is not None else None)

    @property
    def installed(self) -> bool:
        return self.is_installed()


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 4. History, pace and the chart series
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def load_history(path: Path | None, alarm_key: str | None = None) -> list[Sample]:
    """history.jsonl lines: {t, used, all, reserve, seats{label: week_used}}. Bad lines are skipped. alarm_key: an
    add-on pool's per-sample flag for paid use (PoolUI.history_alarm_key)."""
    if not path:
        return []
    try:
        with open(path, 'rb') as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - HISTORY_TAIL_BYTES))
            blob = f.read()
    except OSError:
        return []
    lines = blob.decode('utf-8', 'replace').splitlines()
    if size > HISTORY_TAIL_BYTES and lines:
        lines = lines[1:]  # the first line is probably cut in half
    out = []
    for line in lines:
        try:
            d = json.loads(line)
        except ValueError:
            continue
        d = as_dict(d)
        t = parse_time(d.get('t'))
        if t is None:
            continue
        out.append(Sample(t.timestamp(), clamp_pct(as_num(d.get('used'))), d.get('reserve') is True,
                          clamp_pct(as_num(d.get('all'))),
                          spending=bool(alarm_key) and d.get(alarm_key) is True))
    out.sort(key=lambda s: s.t)
    return out


def total_text(m: Model) -> str:
    """'36× total': the pool's size, every seat's × added up as the headline weighs them (seats turned off aside;
    as `codexpool status` has it); '' with no seats."""
    total = sum(min(max(s.weight or 1.0, 0.0), 1e6) for s in m.seats if s.state != 'disabled' and s.capacity_known)
    if any(not s.capacity_known for s in m.seats if s.state != 'disabled'):
        return f'{round(total, 2):g}× known + unknown capacity' if total else 'Unknown capacity'
    return f'{round(total, 2):g}× total' if total else ''


def pace_text(m: Model) -> str | None:
    """'At this pace the pool lasts ~27h' (headline over all seats) or 'At this pace the regular seats last ~9h',
    'Steady', or None (not enough history, or nothing to say). Only while a seat the headline covers is serving:
    a regular seat, or with the all-seats headline the reserve too.

    The slope is the total rise of the headline's used % over the time it did not drop, in the last
    PACE_WINDOW_S. Drops are weekly resets and are ignored, both their size and their interval."""
    covered = ('regular', 'reserve') if m.headline_mode == 'all' else ('regular',)
    if m.status not in covered or m.headline is None or m.headline >= 99.5:
        return None
    now = m.now.timestamp()
    pts = [(s.t, v) for s in m.history
           if (v := sample_value(s, m.headline_mode)) is not None and now - PACE_WINDOW_S <= s.t <= now + 300]
    if len(pts) < 2:
        return None
    rise = rising_s = 0.0
    for (ta, va), (tb, vb) in pairwise(pts):
        span = tb - ta
        if span <= 0 or vb < va:
            continue
        rise += vb - va
        rising_s += span
    if rising_s < PACE_MIN_RISING_S:
        return None
    per_hour = rise / (rising_s / 3600)
    if per_hour < 0.25:
        return 'Steady'
    who = 'the pool lasts' if m.headline_mode == 'all' else 'the regular seats last'
    return f'At this pace {who} {fmt_approx_hours((100 - m.headline) / per_hour)}'


@dataclass
class ChartData:
    pts: list[tuple[float, float, bool]]  # (t, value shown, red: Sample.alarm); starts with the last sample before t0
    in_range: int                         # samples inside [t0, t1]
    t0: float                             # the x axis always spans the whole range: now - 24 h (or 7 d) ...
    t1: float                             # ... to now
    bars: list[tuple[float, float, float, bool]] = field(default_factory=list)
    # (start, end, used %, red) per usage bucket with any use: how much of the headline's quota went in it
    resets: list[float] = field(default_factory=list)   # when a seat's week reset (the headline's used % dropped)
    bucket_s: float = 1800.0              # the bucket width
    peak: float = 0.0                     # the biggest bucket's use, %; 0 with no use in range


def chart_data(m: Model, range_key: str) -> ChartData:
    """The headline's history as the hero shows it: the same seats, as used or as left (falling, then jumping up
    at a weekly reset), plus the use per bucket: the headline's used % rising between two samples is use, spread
    over the buckets the two samples span (a sleeping Mac skips samples, but the seats were still used); a drop of
    RESET_MIN_PCT or more is a reset, which never counts as use but is marked. Bars end at `now`, so the buckets
    are anchored there."""
    span_s, _, bucket_s, _ = RANGES.get(range_key, RANGES['24h'])
    t1 = m.now.timestamp()
    t0 = t1 - span_s
    series = [(s.t, v, s.alarm) for s in m.history
              if (v := sample_value(s, m.headline_mode)) is not None and s.t <= t1 + 300]
    rows = [(t, m.shown(v), a) for t, v, a in series]
    inside = [r for r in rows if r[0] >= t0]
    before = [r for r in rows if r[0] < t0][-1:]   # lets the line enter from the left edge
    pts = before + inside
    end = max([t1] + [p[0] for p in pts])

    n = int(math.ceil(span_s / bucket_s))
    use = [0.0] * n
    red = [False] * n
    resets: list[float] = []
    for (ta, va, alarm), (tb, vb, _) in pairwise(series):
        if tb <= t0 or tb <= ta:
            continue
        d = vb - va
        if d <= 0:
            if -d >= RESET_MIN_PCT and tb <= end:
                resets.append(tb)
            continue
        lo, hi = max(ta, t0), min(tb, end)
        if hi <= lo:
            continue
        rate = d / (tb - ta)
        k0, k1 = int((lo - t0) // bucket_s), min(n - 1, int((hi - t0 - 1e-9) // bucket_s))
        for k in range(k0, k1 + 1):
            ba, bb = t0 + k * bucket_s, t0 + (k + 1) * bucket_s
            overlap = min(hi, bb) - max(lo, ba)
            if overlap > 0:
                use[k] += rate * overlap
                red[k] = red[k] or alarm
    bars = [(t0 + k * bucket_s, min(end, t0 + (k + 1) * bucket_s), u, red[k]) for k, u in enumerate(use) if u > 0]
    return ChartData(pts, len(inside), t0, end, bars, resets, bucket_s, max([0.0] + [b[2] for b in bars]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 5. Theme: semantic colours and fonts
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def srgb(hex_rgb: int, alpha: float = 1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(((hex_rgb >> 16) & 255) / 255.0,
                                                      ((hex_rgb >> 8) & 255) / 255.0, (hex_rgb & 255) / 255.0,
                                                      alpha)


_DYNAMIC: dict = {}

# The pools' colours: (text on light, text on dark, fill on light, fill on dark).
CODEX_BLUE = (0x0A6FB5, 0x6CC4FF, 0x1A7BC7, 0x6CC4FF)   # text light, text dark, fill light, fill dark
POOL_COLORS = {'codex': CODEX_BLUE}                        # an add-on's pool adds its own (PoolUI.colors)


def dynamic(name: str, light: int, dark: int):
    """A colour that picks its light or dark value when drawn, for whatever appearance is drawing it."""
    if name not in _DYNAMIC:
        lc, dc = srgb(light), srgb(dark)

        def provider(appearance):
            best = appearance.bestMatchFromAppearancesWithNames_([NSAppearanceNameAqua, NSAppearanceNameDarkAqua])
            return dc if best == NSAppearanceNameDarkAqua else lc
        _DYNAMIC[name] = (NSColor.colorWithName_dynamicProvider_(name, provider), provider)  # keep the block alive
    return _DYNAMIC[name][0]


class C:
    """Colour roles. Functions, not constants: colours must be made after AppKit is up.

    The system greens, oranges and reds are for fills (bars, chart, pill backgrounds). Text uses the *_text
    colours, which are darker in light mode so coloured words stay readable (>= 4:1) on the light material."""
    label = staticmethod(lambda: NSColor.labelColor())
    secondary = staticmethod(lambda: NSColor.secondaryLabelColor())
    tertiary = staticmethod(lambda: NSColor.tertiaryLabelColor())
    separator = staticmethod(lambda: NSColor.separatorColor())
    green = staticmethod(lambda: NSColor.systemGreenColor())
    orange = staticmethod(lambda: NSColor.systemOrangeColor())
    red = staticmethod(lambda: NSColor.systemRedColor())
    grey = staticmethod(lambda: NSColor.systemGrayColor())
    green_text = staticmethod(lambda: dynamic('codexpool.green', 0x248A3D, 0x30D158))
    red_text = staticmethod(lambda: dynamic('codexpool.red', 0xD70015, 0xFF453A))
    orange_text = staticmethod(lambda: dynamic('codexpool.orange', 0xC93400, 0xFF9F0A))
    blue_text = staticmethod(lambda: dynamic('codexpool.blue', 0x0066CC, 0x409CFF))   # actionable (resets)
    # Each pool's own colour: its number while it serves normally (menu bar, tiles, hero), its glyph, its chart.
    # Text shades are >= 4.5:1 on the light and dark menu bar and popover; fills are for glyphs and lines.
    codex_text = staticmethod(lambda: C.pool_text('codex'))
    codex = staticmethod(lambda: C.pool_fill('codex'))

    @staticmethod
    def pool_text(pool: str):
        c = POOL_COLORS.get(pool, CODEX_BLUE)
        return dynamic(f'codexpool.{pool}', c[0], c[1])

    @staticmethod
    def pool_fill(pool: str):
        c = POOL_COLORS.get(pool, CODEX_BLUE)
        return dynamic(f'codexpool.{pool}.fill', c[2], c[3])

    @staticmethod
    def wash(alpha: float):                      # neutral fills: tracks, tints, quiet pills, hover
        return NSColor.labelColor().colorWithAlphaComponent_(alpha)

    @staticmethod
    def soft(color, alpha=0.14):                 # coloured pill backgrounds
        return color.colorWithAlphaComponent_(alpha)

    track = staticmethod(lambda: C.wash(0.09))     # empty part of a bar
    dim_fill = staticmethod(lambda: C.wash(0.25))  # bars of seats that cannot serve, and of a degraded pool
    row_tint = staticmethod(lambda: C.wash(0.05))  # the serving seat's row
    hover = staticmethod(lambda: C.wash(0.08))


def headline_text(m: Model):
    """The headline number's colour: the pool's own colour while a regular seat serves; red while the reserve
    serves, every seat is out or a seat spends paid use; else grey."""
    if m.hot:
        return C.red_text()
    return C.pool_text(m.pool) if m.status == 'regular' else C.secondary()


def pool_pill(m: Model):
    """(word, text colour, background) for the pool's state: the header pill and the switcher tiles."""
    if m.spending is not None:
        return getattr(m.ui, 'alarm_word', 'Paid use'), C.red_text(), C.soft(C.red(), 0.16)
    if m.status == 'regular':
        return 'Regular', C.pool_text(m.pool), C.soft(C.pool_fill(m.pool), 0.16)
    if m.status == 'reserve':
        return 'Reserve', C.red_text(), C.soft(C.red(), 0.16)
    if m.status == 'allout':
        return 'All out', C.red_text(), C.soft(C.red(), 0.16)
    word = {'down': 'Down', 'stale': 'Stale', 'empty': f'No {m.noun}s'}.get(m.status, 'No data')
    return word, C.secondary(), C.wash(0.08)


def usage_color(pct):
    """Every usage bar, from its used %: green < 70 %, orange 70-90 %, red >= 90 %. In left mode the same
    thresholds read green above 30 % left, orange at 30 % or less, red at 10 % or less."""
    if pct is None or pct < 70:
        return C.green()
    return C.orange() if pct < 90 else C.red()


def bar_fill(pct, dim: bool):
    return C.dim_fill() if dim else usage_color(pct)


def headline_fill(m: Model):
    """The headline's own bars (the hero bar, the meter's top bar): red while the reserve serves, like the
    number above them (and while every seat is out or credits are spent), else by threshold (grey when down or
    not reporting). Seat bars keep their threshold colours."""
    return C.red() if m.hot else bar_fill(m.headline, m.degraded)


_FONTS: dict = {}


def font(size: float, weight=NSFontWeightRegular, mono: bool = False):
    key = (size, weight, mono)
    if key not in _FONTS:
        make = NSFont.monospacedDigitSystemFontOfSize_weight_ if mono else NSFont.systemFontOfSize_weight_
        _FONTS[key] = make(size, weight)
    return _FONTS[key]


def line_height(f) -> float:
    return math.ceil(f.ascender() - f.descender() + f.leading())


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 6. Drawing primitives (current NSGraphicsContext, flipped coordinates: y grows downwards)
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def fresh(s: str) -> str:
    """The same text as a new str object. PyObjC 12.2 leaks a str (and its proxy) that crosses into
    Objective-C twice, and the layout measures text before it draws it, so every string handed to AppKit
    goes through here. Re-check whether this is still needed when PyObjC is upgraded."""
    return ''.join(s)


_ALIGN = {'left': NSTextAlignmentLeft, 'right': NSTextAlignmentRight, 'center': NSTextAlignmentCenter}
_TRUNCATE = {'tail': NSLineBreakByTruncatingTail, 'middle': NSLineBreakByTruncatingMiddle}


def attributed(text: str, f, color, align: str | None = None, truncate: str | None = None):
    attrs = {NSFontAttributeName: f, NSForegroundColorAttributeName: color}
    if align or truncate:
        ps = NSMutableParagraphStyle.alloc().init()
        if truncate:
            ps.setLineBreakMode_(_TRUNCATE[truncate])
        ps.setAlignment_(_ALIGN[align or 'left'])
        attrs[NSParagraphStyleAttributeName] = ps
    return NSAttributedString.alloc().initWithString_attributes_(fresh(text), attrs)


def text_width(text: str, f) -> float:
    return math.ceil(attributed(text, f, C.label()).size().width)


def draw_text(text: str, x: float, y: float, f, color, width: float | None = None, align: str = 'left',
              truncate: str = 'tail'):
    """One line; (x, y) is the top-left of its line box. With width, truncates with … and aligns."""
    if width is None:
        attributed(text, f, color).drawAtPoint_((x, y))
        return
    a = attributed(text, f, color, align=align, truncate=truncate)
    a.drawWithRect_options_context_(((x, y), (width, line_height(f) + 2)),
                                    NSStringDrawingUsesLineFragmentOrigin | NSStringDrawingTruncatesLastVisibleLine,
                                    None)


def fragment_height(f) -> float:
    """Height the text system gives one laid-out line (a little more than the font metrics say)."""
    return math.ceil(attributed('Ag', f, C.label()).boundingRectWithSize_options_(
        (10000.0, 10000.0), NSStringDrawingUsesLineFragmentOrigin).size.height)


def text_block_height(text: str, f, width: float, max_lines: int = 2) -> float:
    r = attributed(text, f, C.label()).boundingRectWithSize_options_((width, 10000.0),
                                                                       NSStringDrawingUsesLineFragmentOrigin)
    lines = max(1, min(max_lines, round(r.size.height / fragment_height(f))))
    return lines * fragment_height(f)


def draw_text_block(text: str, x: float, y: float, width: float, f, color, max_lines: int = 2):
    """Word-wrapped text, at most max_lines, the last one truncated with …"""
    ps = NSMutableParagraphStyle.alloc().init()
    ps.setLineBreakMode_(NSLineBreakByWordWrapping)
    a = NSAttributedString.alloc().initWithString_attributes_(
        fresh(text), {NSFontAttributeName: f, NSForegroundColorAttributeName: color, NSParagraphStyleAttributeName: ps})
    a.drawWithRect_options_context_(((x, y), (width, fragment_height(f) * max_lines + 1)),
                                    NSStringDrawingUsesLineFragmentOrigin | NSStringDrawingTruncatesLastVisibleLine,
                                    None)


def wrap_words(text: str, f, width: float, max_lines: int = 3) -> list:
    """The text broken into lines at spaces so each fits `width` (the last one may not, when max_lines is hit;
    draw_text truncates it). Used where a line's length must be known, e.g. to fit a link after it."""
    lines, line = [], ''
    for word in text.split(' '):
        trial = f'{line} {word}' if line else word
        if line and text_width(trial, f) > width and len(lines) < max_lines - 1:
            lines.append(line)
            line = word
        else:
            line = trial
    lines.append(line)
    return lines


def rounded(rect, radius):
    return NSBezierPath.bezierPathWithRoundedRect_xRadius_yRadius_(rect, radius, radius)


def fill_rounded(rect, radius, color):
    color.setFill()
    rounded(rect, radius).fill()


def fill_rect(rect, color):
    color.setFill()
    NSBezierPath.fillRect_(rect)


def fill_circle(cx, cy, r, color):
    color.setFill()
    NSBezierPath.bezierPathWithOvalInRect_(((cx - r, cy - r), (2 * r, 2 * r))).fill()


def stroke_rounded(rect, radius, color, width=1.0):
    (x, y), (w, h) = rect
    p = rounded(((x + width / 2, y + width / 2), (w - width, h - width)), radius)
    p.setLineWidth_(width)
    color.setStroke()
    p.stroke()


def draw_bar(x, y, w, h, pct, fill, track=None):
    """A capsule track with a capsule fill; any usage above zero shows at least a dot."""
    fill_rounded(((x, y), (w, h)), h / 2, track or C.track())
    if pct is not None and pct > 0:
        fill_rounded(((x, y), (max(h, w * min(pct, 100.0) / 100.0), h)), h / 2, fill)


PILL_H = 17.0


def pill_font():
    return font(10, NSFontWeightSemibold)


def pill_width(text: str) -> float:
    return text_width(text, pill_font()) + 14


def draw_pill(text: str, x: float, y: float, fg, bg):
    w = pill_width(text)
    fill_rounded(((x, y), (w, PILL_H)), PILL_H / 2, bg)
    f = pill_font()
    draw_text(text, x, y + (PILL_H - line_height(f)) / 2, f, fg, width=w, align='center')


BUTTON_H = 22.0


def button_font():
    return font(12, NSFontWeightMedium)


def button_width(text: str) -> float:
    return text_width(text, button_font()) + 24


def symbol_image(name: str, size: float, color, weight=NSFontWeightRegular):
    """An SF Symbol tinted with `color` at draw time (so dynamic colours follow the appearance)."""
    base = NSImage.imageWithSystemSymbolName_accessibilityDescription_(name, None)
    if base is None:
        return None
    img = base.imageWithSymbolConfiguration_(
        NSImageSymbolConfiguration.configurationWithPointSize_weight_(size, weight)) or base

    def handler(rect):
        img.drawInRect_(rect)
        color.set()   # SourceIn keeps the symbol's shape and takes the colour (and its alpha) wholesale
        NSRectFillUsingOperation(rect, NSCompositingOperationSourceIn)
        return True
    return NSImage.imageWithSize_flipped_drawingHandler_(img.size(), False, handler)


def draw_symbol(name: str, cx: float, cy: float, size: float, color, weight=NSFontWeightRegular, fit=None):
    """Draws a symbol centred on (cx, cy); fit=(w, h) scales it down to fit that box."""
    img = symbol_image(name, size, color, weight)
    if img is None:
        return
    w, h = img.size()
    if fit:
        k = min(1.0, fit[0] / w, fit[1] / h)
        w, h = w * k, h * k
    img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
        ((cx - w / 2, cy - h / 2), (w, h)), ((0, 0), (0, 0)), NSCompositingOperationSourceOver, 1.0, True, None)


def draw_chart(rect, data: ChartData, grey: bool, regular=None):
    """Use per bucket as bars from the baseline, scaled so the busiest bucket fills the chart, under the headline
    (as used or as left) as a 1.5 pt line on a fixed 0-100 % scale across [t0, t1]. Both take `regular` (the
    pool's colour; green if not given) while a regular seat served and red while the reserve did, credits were
    spent or every seat was out (Sample.alarm; all grey when nothing is serving now). A reset (the headline's used
    % dropped) is a dashed hairline with a small triangle on top. A dashed stub carries the last value on to 'now'
    (t1); a dot marks the latest sample."""
    (x, y), (w, h) = rect
    top, bottom = y + 2.5, y + h - 0.5
    t0, t1, pts = data.t0, data.t1, data.pts
    span = max(1.0, t1 - t0)

    def px(t):
        return x + (t - t0) / span * w

    def py(v):
        return bottom - (v / 100.0) * (bottom - top)

    def colour(reserve):
        return C.grey() if grey else (C.red() if reserve else (regular or C.green()))

    fill_rect(((x, y + h - 0.5), (w, 0.5)), C.separator())               # 0 %
    guide = NSBezierPath.bezierPath()                                     # 50 %
    guide.moveToPoint_((x, py(50)))
    guide.lineToPoint_((x + w, py(50)))
    guide.setLineWidth_(0.5)
    guide.setLineDash_count_phase_([2.0, 3.0], 2, 0)
    C.separator().setStroke()
    guide.stroke()

    # Bars: each bucket's use, the busiest one reaching the top; a bucket with any use is at least 1 pt tall.
    if data.peak > 0:
        gap = 1.5 if w / (span / data.bucket_s) >= 5 else 1.0
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.clipRect_(((x, top - 1), (w, bottom - top + 1)))
        for ba, bb, u, reserve in data.bars:
            xa, xb = px(ba) + gap / 2, px(bb) - gap / 2
            if xb - xa < 1.0:
                xa, xb = (xa + xb) / 2 - 0.5, (xa + xb) / 2 + 0.5
            bh = max(1.0, u / data.peak * (bottom - top))
            r = min(1.5, (xb - xa) / 2)
            fill_rounded(((xa, bottom - bh), (xb - xa, bh + r)), r, colour(reserve).colorWithAlphaComponent_(0.42))
        NSGraphicsContext.restoreGraphicsState()

    # Resets: a dashed hairline the chart's height, with a small triangle at the top.
    for t in data.resets:
        rx = px(t)
        if not x <= rx <= x + w:
            continue
        mark = NSBezierPath.bezierPath()
        mark.moveToPoint_((rx, top + 4))
        mark.lineToPoint_((rx, bottom))
        mark.setLineWidth_(1.0)
        mark.setLineDash_count_phase_([2.0, 2.0], 2, 0)
        C.secondary().setStroke()
        mark.stroke()
        tri = NSBezierPath.bezierPath()
        tri.moveToPoint_((rx, y))
        tri.lineToPoint_((rx + 3, y + 4.5))
        tri.lineToPoint_((rx - 3, y + 4.5))
        tri.closePath()
        C.secondary().setFill()
        tri.fill()

    line = NSBezierPath.bezierPath()
    for i, (t, v, _) in enumerate(pts):
        (line.moveToPoint_ if i == 0 else line.lineToPoint_)((px(t), py(v)))
    line.setLineWidth_(1.5)
    line.setLineJoinStyle_(NSLineJoinStyleRound)
    line.setLineCapStyle_(NSLineCapStyleRound)

    # Colour runs: the segment from sample i to i+1 takes sample i's colour. Each run is drawn clipped to
    # its own x range (and everything to the chart's), so the colour changes exactly where serving changed.
    runs: list[list] = []
    for (ta, _, ra), (tb, _, _) in pairwise(pts):
        if runs and runs[-1][2] == ra:
            runs[-1][1] = px(tb)
        else:
            runs.append([px(ta), px(tb), ra])
    if not runs:
        runs = [[x, x + w, pts[0][2]]]
    runs[0][0], runs[-1][1] = x - 1, x + w + 1
    for xa, xb, reserve in runs:
        lo, hi = max(x, xa), min(x + w, xb)
        if hi <= lo:
            continue
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.clipRect_(((lo, y - 2), (hi - lo, h + 3)))
        colour(reserve).setStroke()
        line.stroke()
        NSGraphicsContext.restoreGraphicsState()

    lt, lv, lr = pts[-1]
    c = colour(lr)
    ex, ey = px(lt), py(lv)
    if px(t1) - ex > 3:
        stub = NSBezierPath.bezierPath()
        stub.moveToPoint_((ex, ey))
        stub.lineToPoint_((px(t1), ey))
        stub.setLineWidth_(1.0)
        stub.setLineDash_count_phase_([2.0, 2.5], 2, 0)
        c.setStroke()
        stub.stroke()
    fill_circle(ex, ey, 4.5, c.colorWithAlphaComponent_(0.22))
    fill_circle(ex, ey, 2.25, c)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 7. Menu bar item: a two-bar capsule meter (headline on top, serving seat below) and the headline %
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

METER_W, METER_H = 18.0, 12.0


def menubar_font():
    """The menu bar's size in regular weight (like the clock), with monospaced digits."""
    return font(NSFont.menuBarFontOfSize_(0).pointSize(), NSFontWeightRegular, mono=True)


def menubar_length() -> float:
    """A fixed item width that fits the meter and ' 100%', so the items to the left never shift."""
    return float(math.ceil(METER_W + 4 + text_width(' 100%', menubar_font()) + 2))


def draw_meter(x: float, y: float, m: Model):
    """Top: the headline (red while the reserve serves); bottom: the serving seat's week. Both fill as used, or
    drain as left (m.display)."""
    top = m.headline
    bottom = m.serving.week.used if m.serving and m.serving.week else None
    track = NSColor.labelColor().colorWithAlphaComponent_(0.22)
    draw_bar(x, y, METER_W, 6.0, m.shown(top), headline_fill(m), track)
    draw_bar(x, y + 8.0, METER_W, 4.0, m.shown(bottom), usage_color(bottom), track)


def draw_warning_glyph(x: float, y: float):
    draw_symbol('exclamationmark.triangle.fill', x + METER_W / 2, y + METER_H / 2, 12, C.secondary(),
                fit=(METER_W, METER_H))


def menubar_image(m: Model):
    """The meter, or a warning glyph in its place when the pool is down, not reporting or empty. Drawn lazily, so
    its colours follow the menu bar's own light/dark appearance (NSImage re-draws per appearance)."""
    warn = m.warn

    def handler(rect):
        if warn:
            draw_warning_glyph(0, 0)
        else:
            draw_meter(0, 0, m)
        return True
    img = NSImage.imageWithSize_flipped_drawingHandler_((METER_W, METER_H), True, handler)
    img.setTemplate_(False)
    return img


def menubar_title(m: Model):
    """' 53%' (left, or used) in the pool's blue, or red (reserve, all out); the last known % in grey when down or
    not reporting; '—' only without a usable status file."""
    color = C.secondary() if m.warn else headline_text(m)
    return attributed(' ' + fmt_pct(m.shown(m.headline)), menubar_font(), color)


# -- the pools' marks: the logos, from the pools' apps on this Mac ----------------------------------------------
#
# The repo ships no logo files. Each mark is an alpha mask made at runtime from the app's own icon, found by
# bundle id: Codex from the ChatGPT app's icon-codex-light.png (the cloud is the only saturated thing in it, so
# alpha comes from chroma and the `>_` prompt, the white squircle and the shadow stay cut out; the glossy
# highlight on the top lobe is low-chroma lavender, so inside the cloud's outline a softer key fills it in); an
# add-on's pool names its app and files (PoolUI.mark) and turns the icon into a mask itself (PoolUI.mark_alpha,
# e.g. a menu bar template's own alpha, thickened by a pixel with dilate_alpha). A mask is trimmed to its bounds,
# checked (not tiny, and plausibly covered), cached per (app path, version) and drawn flat in the pool's colour, on
# the pixel grid. A missing app or file, a mask that fails the check, or anything that throws falls back to the
# drawn glyph, with one line of stderr per cause, and is tried again at the next re-check. The pixel maths are pure
# functions over bytes (chroma_alpha, flood_exterior, solid_inside, dilate_alpha, mask_bounds, valid_mask,
# crop_alpha).

MARKS = 'drawn'   # 'app': the logos (the live app); 'drawn': plain shapes, nothing read from /Applications
                  # (imports, and --snapshot unless --marks app)
MARK_APPS = {'codex': ('com.openai.codex', ('icon-codex-light.png',))}   # + an add-on pool's (PoolUI.mark)
MARK_PX = 256                   # the Codex icon (1024^2) is rasterised this big before masking
MARK_KEY = (32, 112)            # the chroma key for the cloud's outline: 0 alpha up to 32, full from 112
MARK_INNER_KEY = (32, 64)       # inside the outline: the highlight starts at chroma 43, the `>_` prompt ends at 42
MARK_RECHECK_S = 600.0          # an app's path and version are looked up again at most this often
MARK_SCALE = {'codex': 1.0}     # optical size, as a fraction of the GLYPH box: the solid cloud fills it; a thin
                                # mark (an add-on's PoolUI.mark_scale) overhangs it a little, so the two weigh the
                                # same next to the 13 pt numbers (tuned by eye at 2x)


def chroma_alpha(rgba: bytes, lo: int = 32, hi: int = 112) -> bytes:
    """One alpha byte per pixel of `rgba` (w*h*4 bytes, premultiplied or not) from its chroma, max(r, g, b) -
    min(r, g, b): 0 up to `lo`, 255 from `hi`, a straight ramp between, and never above the pixel's own alpha.
    Saturated pixels stay, near-neutral ones (white, grey, black, a shadow) go."""
    out = bytearray(len(rgba) >> 2)
    span = max(1, hi - lo)
    for i in range(0, len(rgba) - 3, 4):
        r, g, b, a = rgba[i], rgba[i + 1], rgba[i + 2], rgba[i + 3]
        c = max(r, g, b) - min(r, g, b)
        v = 0 if c <= lo else 255 if c >= hi else (c - lo) * 255 // span
        out[i >> 2] = v if v < a else a
    return bytes(out)


def flood_exterior(alpha: bytes, w: int, h: int, threshold: int = 32) -> bytes:
    """One byte per pixel of a w*h mask: 1 where the pixel is outside the mark (below `threshold` and joined to the
    bitmap's border by such pixels, four-connected), 0 inside it, in a hole or on its edge. The pure function under
    solid_inside: the `>_` prompt is a hole, the highlight is inside, the white squircle is outside."""
    n = w * h
    out = bytearray(n)
    if n == 0 or n != len(alpha):
        return bytes(out)
    stack = [i for i in range(w) if alpha[i] < threshold]
    stack += [i for i in range(n - w, n) if alpha[i] < threshold]
    stack += [y * w for y in range(1, h - 1) if alpha[y * w] < threshold]
    stack += [y * w + w - 1 for y in range(1, h - 1) if alpha[y * w + w - 1] < threshold]
    for i in stack:
        out[i] = 1
    while stack:
        i = stack.pop()
        x = i % w
        for j in ((i - 1) if x > 0 else -1, (i + 1) if x < w - 1 else -1, i - w, i + w):
            if 0 <= j < n and not out[j] and alpha[j] < threshold:
                out[j] = 1
                stack.append(j)
    return bytes(out)


def solid_inside(alpha: bytes, inner: bytes, exterior: bytes) -> bytes:
    """`alpha` with every pixel that is not `exterior` (flood_exterior) raised to `inner`, the same pixels through a
    softer key: the mark's outline keeps its edge, the inside loses its soft spots. A hole stays a hole as long as
    the softer key leaves it alone."""
    return bytes(a if e else (a if a >= b else b) for a, b, e in zip(alpha, inner, exterior))


def dilate_alpha(alpha: bytes, w: int, h: int, radius: int = 1) -> bytes:
    """The w*h mask thickened by `radius` pixels each way: each pixel becomes the largest alpha in its
    (2 * radius + 1)-square (clipped at the edges). radius 0 is the mask itself."""
    if radius <= 0 or w <= 0 or h <= 0:
        return bytes(alpha)
    # separable: the running maximum along each row, then along each column
    rows = bytearray(alpha)
    for y in range(h):
        row = alpha[y * w:(y + 1) * w]
        for x in range(w):
            rows[y * w + x] = max(row[max(0, x - radius):x + radius + 1])
    out = bytearray(len(alpha))
    for x in range(w):
        col = rows[x::w]
        for y in range(h):
            out[y * w + x] = max(col[max(0, y - radius):y + radius + 1])
    return bytes(out)


def spark_dilation(filename: str) -> int:
    """How many pixels a thin menu bar template is thickened by (an add-on's mark_alpha): one at @2x and @3x (a
    third or a half of a point: rays 1.3 pt wide then cover a pixel at menu bar size), none for the 1x file, where
    a pixel is a whole point."""
    return 1 if '@' in filename else 0


def mask_bounds(alpha: bytes, w: int, h: int, threshold: int = 8):
    """The box (x0, y0, x1, y1; x1 and y1 exclusive) around the pixels of a w*h mask above `threshold`, or None
    when there are none."""
    rows = [y for y in range(h) if max(alpha[y * w:(y + 1) * w], default=0) > threshold]
    if not rows:
        return None
    cols = [x for x in range(w) if max(alpha[x::w], default=0) > threshold]
    return cols[0], rows[0], cols[-1] + 1, rows[-1] + 1


def crop_alpha(alpha: bytes, w: int, bounds) -> tuple[bytes, int, int]:
    """(mask, width, height): the w-wide mask cut down to `bounds`."""
    x0, y0, x1, y1 = bounds
    return b''.join(alpha[y * w + x0:y * w + x1] for y in range(y0, y1)), x1 - x0, y1 - y0


def valid_mask(alpha: bytes, w: int, h: int, bounds, min_side: float = 0.1, coverage=(0.15, 0.85)) -> bool:
    """Whether a w*h mask trimmed to `bounds` is plausibly a mark: the box is at least `min_side` of the bitmap on
    each side (a speck is not), and the mean alpha inside it is within `coverage` (an empty or a solid box is
    not). The Codex cloud sits near 0.65, a thin spark near 0.4."""
    if bounds is None:
        return False
    cut, cw, ch = crop_alpha(alpha, w, bounds)
    if cw < min_side * w or ch < min_side * h or not cut:
        return False
    return coverage[0] <= sum(cut) / (255.0 * len(cut)) <= coverage[1]


def render_rgba(path: str, px: int | None = None):
    """The image file drawn into an RGBA bitmap: (bytes, w, h), row-packed; None when it cannot be read. px: the
    longer side, in pixels, to draw it at (aspect kept); default, the file's own pixels (a template is not
    resampled)."""
    img = NSImage.alloc().initWithContentsOfFile_(path)
    if img is None or not img.representations():
        return None
    rep0 = img.representations()[0]
    sw, sh = max(1, int(rep0.pixelsWide())), max(1, int(rep0.pixelsHigh()))
    k = 1.0 if px is None else px / max(sw, sh)
    w, h = max(1, round(sw * k)), max(1, round(sh * k))
    rep = new_bitmap(w, h, 1.0)
    ctx = NSGraphicsContext.graphicsContextWithBitmapImageRep_(rep)
    NSGraphicsContext.saveGraphicsState()
    try:
        NSGraphicsContext.setCurrentContext_(ctx)
        ctx.setImageInterpolation_(NSImageInterpolationHigh)
        NSColor.clearColor().set()
        NSRectFillUsingOperation(((0, 0), (w, h)), NSCompositingOperationCopy)
        img.drawInRect_fromRect_operation_fraction_(((0, 0), (w, h)), ((0, 0), (0, 0)),
                                                    NSCompositingOperationSourceOver, 1.0)
        ctx.flushGraphics()
    finally:
        NSGraphicsContext.restoreGraphicsState()
    stride = rep.bytesPerRow()
    raw = bytes(rep.bitmapData()[:stride * h])
    if stride != w * 4:
        raw = b''.join(raw[y * stride:y * stride + w * 4] for y in range(h))
    return raw, w, h


def mask_image(alpha: bytes, w: int, h: int):
    """The mask as a w*h NSImage: white, with the mask for alpha. Drawn tinted by draw_mark; as a template it is
    what a segmented control tints (the Settings switcher)."""
    rep = new_bitmap(w, h, 1.0)
    stride, buf = rep.bytesPerRow(), rep.bitmapData()
    for y in range(h):
        row = alpha[y * w:(y + 1) * w]
        buf[y * stride:y * stride + 4 * w] = bytes(v for a in row for v in (a, a, a, a))   # premultiplied white
    img = NSImage.alloc().initWithSize_((w, h))
    img.addRepresentation_(rep)
    return img


def app_bundle(bundle_id: str):
    """(app path, CFBundleShortVersionString) of the app installed for `bundle_id`, or None. Info.plist is read
    fresh (NSBundle caches it), so an update in place changes the key; one that cannot be read (missing, or half
    written while the app is being replaced) gives an empty version, so the next re-check sees a new key."""
    url = NSWorkspace.sharedWorkspace().URLForApplicationWithBundleIdentifier_(bundle_id)
    if url is None:
        return None
    path = str(url.path())
    try:
        with open(os.path.join(path, 'Contents', 'Info.plist'), 'rb') as f:
            version = plistlib.load(f).get('CFBundleShortVersionString')
    except (OSError, ValueError, TypeError, ExpatError, plistlib.InvalidFileException):
        version = None
    return path, str(version or '')


_MARKS: dict = {}          # pool -> [looked up at (monotonic), app_bundle() key (None: no mark), NSImage or None]
_MARK_NOTED: set = set()   # (pool, cause) already reported to stderr


def mark_note(pool: str, cause: str, detail: str | None = None):
    """One line of stderr per cause, ever: the mark is decoration, and this runs on every redraw. `detail` (an
    exception's text) goes in the line, not in what makes a cause new, so a message that varies is still one."""
    if (pool, cause) not in _MARK_NOTED:
        _MARK_NOTED.add((pool, cause))
        what = f'{cause} ({detail})' if detail else cause
        print(f'codexpool-menubar: {POOL_NAME[pool]} mark: {what}; drawing the plain glyph instead', file=sys.stderr)


def build_mark(pool: str, app_path: str):
    """The pool's mask from the app at app_path, as mask_image(); None, with the cause noted, when it cannot be."""
    names = MARK_APPS[pool][1]
    res = os.path.join(app_path, 'Contents', 'Resources')
    path = next((os.path.join(res, n) for n in names if os.path.isfile(os.path.join(res, n))), None)
    if path is None:
        mark_note(pool, f'no {names[0]} in {app_path}')
        return None
    ui = POOL_UI.get(pool)
    rendered = render_rgba(path, MARK_PX if ui is None else getattr(ui, 'mark_px', None))
    if rendered is None:
        mark_note(pool, f'could not read {path}')
        return None
    rgba, w, h = rendered
    if ui is None:
        outline = chroma_alpha(rgba, *MARK_KEY)
        alpha = solid_inside(outline, chroma_alpha(rgba, *MARK_INNER_KEY), flood_exterior(outline, w, h, MARK_KEY[0]))
    else:
        alpha = ui.mark_alpha(rgba, w, h, os.path.basename(path))
    bounds = mask_bounds(alpha, w, h)
    if not valid_mask(alpha, w, h, bounds):
        mark_note(pool, f'{os.path.basename(path)} does not look like a mark')
        return None
    return mask_image(*crop_alpha(alpha, w, bounds))


def app_mark(pool: str):
    """The pool's mark from its app (mask_image(), trimmed), or None: draw the plain glyph. Made once per (app
    path, version) and kept for the process; the app is looked up again at most every MARK_RECHECK_S, so an
    install or update shows up without a relaunch. A build that fails is remembered without its key, so the next
    re-check tries it again (an app half copied into /Applications is whole by then); a look-up that throws keeps
    whatever the pool had. Never raises."""
    now = time.monotonic()
    entry = _MARKS.get(pool)
    if entry is not None and now - entry[0] < MARK_RECHECK_S:
        return entry[2]
    try:
        key = app_bundle(MARK_APPS[pool][0])
    except Exception as e:  # noqa: BLE001 - any AppKit surprise
        mark_note(pool, 'looking the app up failed', str(e))
        if entry is None:
            entry = _MARKS[pool] = [now, None, None]
        entry[0] = now
        return entry[2]
    if key is None:
        mark_note(pool, f'{MARK_APPS[pool][0]} is not installed')
    if entry is not None and key == entry[1]:   # the same app, or still none
        entry[0] = now
        return entry[2]
    image = None
    if key is not None:
        try:
            image = build_mark(pool, key[0])
        except Exception as e:  # noqa: BLE001 - a broken icon file must not take the menu bar down
            mark_note(pool, f'could not make it from {key[0]}', str(e))
    _MARKS[pool] = [now, key if image is not None else None, image]
    return image


def mark_keys():
    """Which app builds each mark now, one key per pool in POOLS order, for the strip's signature: a new app or
    version makes the item redraw. None while the marks are drawn shapes."""
    if MARKS != 'app':
        return None
    return tuple(_MARKS[pool][1] if app_mark(pool) is not None else None for pool in POOLS)


def bind_ctm():
    """CGContextGetCTM from CoreGraphics, with CGContextRef registered as a CF type so NSGraphicsContext.CGContext()
    hands one over without a warning (the menu bar's venv has the AppKit and Foundation bindings, not Quartz). None
    when the binding fails: the marks are then drawn where they fall, not on the pixel grid."""
    try:
        bundle = objc.loadBundle('CoreGraphics', {}, bundle_path='/System/Library/Frameworks/CoreGraphics.framework',
                                 scan_classes=False)
        fns: dict = {}
        objc.loadBundleFunctions(bundle, fns, [('CGContextGetCTM', b'{CGAffineTransform=dddddd}^{CGContext=}'),
                                               ('CGContextGetTypeID', b'Q')])
        objc.registerCFSignature('CGContextRef', b'^{CGContext=}', fns['CGContextGetTypeID']())
        return fns['CGContextGetCTM']
    except Exception:  # noqa: BLE001 - a PyObjC without the pieces: no snapping, nothing else lost
        return None


CGContextGetCTM = bind_ctm()


def pixel_rect(rect):
    """`rect` ((x, y), (w, h)), in the current context's points, moved onto its device pixel grid: the origin on a
    pixel, the size a whole number of pixels (at least one), so an image drawn into it is rasterised once. The
    rect itself when the context's transform cannot be read or is not axis-aligned."""
    ctx = NSGraphicsContext.currentContext()
    if ctx is None or CGContextGetCTM is None:
        return rect
    try:
        m = CGContextGetCTM(ctx.CGContext())
    except Exception:  # noqa: BLE001
        return rect
    if m.b or m.c or not m.a or not m.d:
        return rect
    (x, y), (w, h) = rect
    sx, sy = abs(m.a), abs(m.d)
    px = math.floor(m.a * x + m.tx + 0.5)          # the origin's device pixel
    py = math.floor(m.d * y + m.ty + 0.5)
    pw, ph = max(1, math.floor(w * sx + 0.5)), max(1, math.floor(h * sy + 0.5))
    return ((px - m.tx) / m.a, (py - m.ty) / m.d), (pw / sx, ph / sy)


def draw_mark(mark, pool: str, cx: float, cy: float, color, size: float):
    """The mark centred on (cx, cy): fitted aspect-correct into a box of size * MARK_SCALE[pool], snapped to the
    pixel grid (pixel_rect: rasterised once, crisp at 1x too), drawn flat in `color` the way symbol_image does
    (SourceIn in its own image, so a dynamic colour follows the appearance)."""
    mw, mh = mark.size()
    box = size * MARK_SCALE[pool]
    k = min(box / mw, box / mh)
    w, h = mw * k, mh * k
    (x, y), (w, h) = pixel_rect(((cx - w / 2, cy - h / 2), (w, h)))

    def handler(rect):
        NSGraphicsContext.currentContext().setImageInterpolation_(NSImageInterpolationHigh)
        mark.drawInRect_(rect)
        color.set()
        NSRectFillUsingOperation(rect, NSCompositingOperationSourceIn)
        return True
    img = NSImage.imageWithSize_flipped_drawingHandler_((w, h), False, handler)
    img.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
        ((x, y), (w, h)), ((0, 0), (0, 0)), NSCompositingOperationSourceOver, 1.0, True, None)


# -- both pools: '⬡ 54%   ✳ 71%' (each pool's mark, then its number) -----------------------------------------

GLYPH = 11.0          # the pool marks' box, pt
GLYPH_GAP = 4.0       # mark to number
HALF_GAP = 10.0       # between the two halves
STRIP_PAD = 3.0       # inside the item, left and right
STRIP_H = 18.0        # the strip image's height (the menu bar centres it)


def draw_pool_glyph(pool: str, cx: float, cy: float, color, size: float = GLYPH, weight: float = 1.45):
    """The pool's mark, centred on (cx, cy), flat in `color`: the real logo from the app on this Mac (app_mark,
    when MARKS is 'app'), else a plain drawn shape, Codex a rounded hexagon outline and an add-on's pool whatever
    its PoolUI.draw_glyph strokes."""
    mark = app_mark(pool) if MARKS == 'app' else None
    if mark is not None:
        draw_mark(mark, pool, cx, cy, color, size)
        return
    color.setStroke()
    p = NSBezierPath.bezierPath()
    p.setLineWidth_(weight)
    p.setLineCapStyle_(NSLineCapStyleRound)
    p.setLineJoinStyle_(NSLineJoinStyleRound)
    ui = POOL_UI.get(pool)
    if ui is not None:
        ui.draw_glyph(p, cx, cy, size, weight)
    else:
        p.setLineWidth_(weight * 0.9)
        r = size / 2 - weight / 2 - 0.35
        pts = [(cx + r * math.sin(math.radians(60 * i)), cy - r * math.cos(math.radians(60 * i))) for i in range(6)]
        corner = 1.3   # rounded corners
        mid = ((pts[0][0] + pts[1][0]) / 2, (pts[0][1] + pts[1][1]) / 2)
        p.moveToPoint_(mid)
        for i in range(1, 7):
            a, b = pts[i % 6], pts[(i + 1) % 6]
            p.appendBezierPathWithArcFromPoint_toPoint_radius_(a, b, corner)
        p.closePath()
    p.stroke()


def strip_number(m: Model):
    """One pool's number in the strip: its colour (or red), grey when down or not reporting."""
    return attributed(fmt_pct(m.shown(m.headline)), menubar_font(), C.secondary() if m.warn else headline_text(m))


def strip_length() -> float:
    """The two-pool item's fixed width: room for '100%' in both halves, so the items to the left never shift."""
    w = text_width('100%', menubar_font())
    return float(math.ceil(2 * STRIP_PAD + 2 * (GLYPH + GLYPH_GAP + w) + HALF_GAP))


def strip_layout(codex: Model, second: Model) -> tuple[list, float]:
    """([(pool, model, glyph x, number x)], split x) for a strip image as wide as strip_length(). Both halves sit
    against the gap in the middle (Codex ends there, the second pool starts there), so each one moves only when
    its own number changes width, never when the other pool's does. split: where the Codex half ends for a click
    (the middle of the gap, the middle of the item)."""
    split = strip_length() / 2
    cw = math.ceil(strip_number(codex).size().width)
    cx = math.floor(split - HALF_GAP / 2 - cw - GLYPH_GAP - GLYPH)   # at '100%' this is STRIP_PAD
    kx = math.ceil(split + HALF_GAP / 2)
    return [('codex', codex, cx, cx + GLYPH + GLYPH_GAP), (second.pool, second, kx, kx + GLYPH + GLYPH_GAP)], split


def strip_image(codex: Model, second: Model):
    """The two-pool item: each half is the pool's glyph (a warning triangle when that pool is down or not
    reporting) and its number. Drawn lazily, so its colours follow the menu bar's appearance."""
    halves, _ = strip_layout(codex, second)
    w = strip_length()

    def handler(rect):
        for pool, m, gx, tx in halves:
            cy = STRIP_H / 2
            if m.warn:
                draw_symbol('exclamationmark.triangle.fill', gx + GLYPH / 2, cy, 11, C.secondary(),
                            fit=(GLYPH + 1, GLYPH))
            else:
                draw_pool_glyph(pool, gx + GLYPH / 2, cy, C.pool_fill(pool))
            s = strip_number(m)
            s.drawAtPoint_((tx, (STRIP_H - s.size().height) / 2))
        return True
    img = NSImage.imageWithSize_flipped_drawingHandler_((w, STRIP_H), True, handler)
    img.setTemplate_(False)
    return img


def pool_severity(m: Model) -> float:
    """How much a pool needs a look: down > all out > paid use > reserve > regular > empty. Keyboard and VoiceOver
    activation opens the popover on the worse one."""
    if m.status in ('down', 'stale', 'missing'):
        return 4
    if m.status == 'allout':
        return 3
    if m.spending is not None:
        return 2.5
    return {'reserve': 2, 'regular': 1}.get(m.status, 0)


def pool_line(m: Model) -> str:
    """One pool in the two-pool tooltip: 'Codex 54% left this week · serving Work B'."""
    who = m.serving.label if m.serving else ''
    figure = f'{fmt_pct(m.shown(m.headline))} {m.word} this week'
    spend = m.spending
    if spend is not None:
        text = f'{figure} · {m.ui.spending_line(spend) if m.ui else spend.label}'
    else:
        text = {
            'regular': f'{figure} · serving {who}',
            'reserve': f'{figure} · serving the reserve {m.noun} {who}',
            'allout': f'every {m.noun} is out',
            'down': 'the pool is down',
            'stale': 'not reporting',
            'empty': f'no {m.noun}s yet',
            'missing': 'not reporting',
        }.get(m.status, m.status)
    return f'{m.name} {text}{m.ui.tip_suffix(m) if m.ui else ""}'


def strip_tooltip(codex: Model, second: Model) -> str:
    return f'{pool_line(codex)}\n{pool_line(second)}'


def strip_accessibility(codex: Model, second: Model) -> str:
    """What VoiceOver reads for the two-pool item: 'Codex 54% left, <pool> 45% left, reserve'."""
    def one(m):
        if m.warn:
            return f'{m.name} ' + {'down': 'down', 'empty': f'no {m.noun}s'}.get(m.status, 'not reporting')
        state = getattr(m.ui, 'alarm_word', 'paid use').lower() if m.spending else \
            {'reserve': 'reserve', 'allout': 'all out'}.get(m.status, '')
        return ', '.join(t for t in (f'{m.name} {fmt_pct(m.shown(m.headline))} {m.word}', state) if t)
    return ', '.join(one(m) for m in (codex, second))


def menubar_tooltip(m: Model) -> str:
    """'Codex Pool: 54% left this week · all seats · serving Work B', worded like the hero's caption. Used mode
    with the regular headline keeps its original wording."""
    who = m.serving.label if m.serving else ''
    figure = f'{fmt_pct(m.shown(m.headline))} {m.word} this week · {m.scope}'
    if m.headline_mode == 'regular' and not m.left:
        regular = f'{fmt_pct(m.headline)} of the regular seats used this week, serving {who}'
    else:
        regular = f'{figure} · serving {who}'
    return 'Codex Pool: ' + {
        'regular': regular,
        'reserve': (f'{figure} · serving the reserve seat {who}' if m.headline_mode == 'all' else
                    f'regular seats used up, serving the reserve seat {who}'),
        'allout': 'every seat is out',
        'down': 'the pool is down',
        'stale': 'not reporting',
        'empty': 'no seats in the pool',
        'missing': 'not reporting',
    }.get(m.status, m.status)


def menubar_signature(m: Model):
    """Everything the menu bar item shows; the item is only redrawn when this changes."""
    serving = m.shown(m.serving.week.used if m.serving and m.serving.week else None)
    return (m.status, m.headline_mode, m.display, fmt_pct(m.shown(m.headline)),
            None if serving is None else round(serving), m.serving.label if m.serving else '', m.spending is not None,
            m.ui.tip_suffix(m) if m.ui else '')   # an add-on's word in the tooltip (its desktop app, say)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 8. Popover. PopoverLayout decides what goes where (a list of draw ops + click regions, top to bottom).
#    PopoverContent shows it as three PopoverViews: the part above the seat list, the seat list inside a
#    scroll view (it scrolls only when the popover would not fit on screen), and the footer.
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

@dataclass
class DrawState:
    hover: tuple | None = None


FOOTER_ROWS = (  # (SF Symbol, title, action)
    ('terminal', 'Status…', 'status'),
    ('stethoscope', 'Doctor', 'doctor'),
    ('doc.text', 'Pool log', 'log'),
    ('book', 'Docs', 'docs'),
    ('arrow.clockwise', 'Refresh', 'refresh'),
)
APP_ROWS = (     # above Quit: the Settings window and its Setup assistant (SF Symbol, title, action, shortcut hint)
    ('person.crop.circle.badge.plus', 'Add a ChatGPT account…', 'addaccount', ''),
    ('gearshape', 'Settings…', 'settings', '⌘,'),
)

BANNER_STATES = ('down', 'stale', 'missing', 'empty')
TILE_GAP, TILE_H = 10.0, 66.0    # the two-pool switcher at the top of the popover
ROW_PAD, ROW_GAP = 4.0, 2.0      # seat rows: inner top/bottom padding, space between rows
SMALL_LH = 13.0                  # seat rows set their 11 pt lines on a tight 13 pt line


def fmt_day(t: dt.datetime | None) -> str:
    return t.astimezone().strftime('%b %-d') if t else ''


def seat_windows(seat: Seat) -> list[tuple[str, float | None, dt.datetime | None]]:
    """(label, used %, reset) for each limit a seat has: the week, the 5-hour window, then the scoped caps."""
    out = [('Week', seat.week.used if seat.week else None, seat.week.reset_at if seat.week else None)]
    if seat.short:
        out.append(('5h', seat.short.used, seat.short.reset_at))
    out += [(sc.name, sc.used, sc.reset_at) for sc in seat.scoped]
    return out


def binding_window(seat: Seat) -> int:
    """Index in seat_windows() of the limit that binds: the most used one (the week on a tie)."""
    wins = seat_windows(seat)
    return max(range(len(wins)), key=lambda i: (wins[i][1] or 0, -i))


def seat_right_text(seat: Seat, now: dt.datetime) -> str:
    if seat.state in OUT_STATES + ('parked',):
        back = seat.until or (seat.week.reset_at if seat.week else None)
        return f'Back in {fmt_span((back - now).total_seconds())}' if back and back > now else 'Back soon'
    reset = seat.week.reset_at if seat.week else None
    ui = POOL_UI.get(seat.provider)
    if ui is not None:   # its own line, e.g. the limit that binds: '5h resets in 2h 10m'
        text = ui.seat_right_text(seat, now)
        if text is not None:
            return text
    return f'Resets in {fmt_span((reset - now).total_seconds())}' if reset and reset > now else ''


def headline_breakdown(m: Model) -> str:
    """The hero's tooltip: how the headline is made. One line per seat in fill order, then the weighted result:
        Work A · 5× · 0% left · back in 2d 7h
        Work B · 5× · 56% left · serving · resets in 6d 13h
        Pro 20x · 20× reserve · 66% left · resets in 3d 8h
        Weighted by size: 54% left · all seats
    With the regular headline a reserve line reads 'Pro 20x · reserve, not counted · 66% left'."""
    lines = []
    for s in m.seats:
        used = s.week.used if s.week else None
        if s.state == 'disabled' or used is None:
            lines.append(f'{s.label} · {"off" if s.state == "disabled" else "usage unknown"}, not counted')
            continue
        figure = f'{fmt_pct(m.shown(used))} {m.word}'
        serving = ['serving'] if s.serving and m.serving_now else []
        if s.reserve and m.headline_mode != 'all':
            lines.append(' · '.join([s.label, 'reserve, not counted', figure] + serving))
            continue
        size = 'capacity unknown' if not s.capacity_known else f'{min(max(s.weight or 1.0, 0.0), 1e6):g}×'   # the weight, as weighted_used() counts it
        parts = [s.label, f'{size} reserve' if s.reserve else size, figure] + serving
        when = seat_right_text(s, m.now)   # 'Back in 2d 7h' for out and parked seats, else 'Resets in 6d 13h'
        if when:
            parts.append(when[0].lower() + when[1:])
        lines.append(' · '.join(parts))
    lines.append(f'Weighted by size: {fmt_pct(m.shown(m.headline))} {m.word} · {m.scope}')
    return '\n'.join(lines)


class PopoverLayout:
    """Builds the popover top to bottom. Each section method takes y and returns the y below it."""

    def __init__(self, m: Model, range_key: str = '24h', toast: str | None = None, tiles: dict | None = None,
                 busy: str | None = None):
        """tiles: {pool: Model} for both pools when an add-on's pool is installed; the popover then opens with a
        two-tile switcher and shows m (one of the two) below it. busy: an add-on's command is running (its
        caption; its control is greyed meanwhile)."""
        self.m = m
        self.tiles = tiles if tiles and len(POOLS) > 1 and all(p in tiles for p in POOLS) else None
        self.range_key = range_key if range_key in RANGES else '24h'
        self.toast = toast
        self.busy = busy
        self.ops: list = []          # callables op(DrawState), run in order by PopoverView.drawRect_
        self.regions: list = []      # (rect, key) for clicks and hover
        self.tips: dict = {}         # key -> tooltip
        self.height = 0.0
        self.scroll_span: tuple[float, float] | None = None   # (top, bottom) of the seat rows
        self.samples = sum(1 for s in m.history if sample_value(s, m.headline_mode) is not None)

    # -- plumbing ------------------------------------------------------------------------------------
    def add(self, fn, *args, **kwargs):
        self.ops.append(lambda st: fn(*args, **kwargs))

    def text(self, *args, **kwargs):
        self.add(draw_text, *args, **kwargs)

    def region(self, rect, key, radius=7.0, tint=None, tip=None):
        """A clickable area with a hover highlight (and an optional permanent tint and tooltip)."""
        self.regions.append((rect, key))
        if tip:
            self.tips[key] = tip

        def op(st):
            if tint is not None:
                fill_rounded(rect, radius, tint)
            if st.hover == key:
                fill_rounded(rect, radius, C.hover())
        self.ops.append(op)

    def rule(self, y: float, above: float = 9.0, below: float = 10.0) -> float:
        """A hairline separator `above` pt below y; returns the (whole-point) y `below` pt under it."""
        y = math.ceil(y + above)
        self.add(fill_rect, ((PAD, y), (INNER, 1.0)), C.separator())
        return y + 1.0 + below

    def build(self) -> PopoverLayout:
        m = self.m
        y = self.switcher(11.0) if self.tiles else self.header(11.0)
        banner = m.status in BANNER_STATES or m.spending is not None
        hero = bool(m.seats) and m.headline is not None
        if banner or hero:
            y = self.rule(y)
            if banner:
                y = self.banner(y) + (16 if hero else 0)
            if hero:
                y = self.hero(y)
        if any(not s.capacity_known for s in m.seats if s.state != 'disabled'):
            y = self.rule(y)
            self.text(total_text(m), PAD, y, font(12), C.secondary(), width=INNER)
            y += line_height(font(12)) + 4
            if m.headline is None:
                self.text('Pool percentage unavailable; see each seat below.', PAD, y, font(11), C.secondary(), width=INNER)
                y += line_height(font(11))
        unknown_headline = any(not s.capacity_known for s in m.seats
                               if s.state != 'disabled' and (m.headline_mode == 'all' or not s.reserve))
        if not unknown_headline and (m.status not in ('missing', 'empty') or self.samples >= MIN_CHART_SAMPLES):
            y = self.chart(self.rule(y))
        if m.seats:
            y = self.seats(self.rule(y, below=8))
        y = self.footer(self.rule(y, above=6, below=5))
        self.height = math.ceil(y + 6)
        return self

    # -- 1. header -----------------------------------------------------------------------------------
    def subtitle(self) -> str:
        m = self.m
        if self.toast:
            return self.toast
        if m.status == 'missing':
            return 'No report yet' if m.age is None else f'Last written {fmt_age(m.age)}'
        if m.status == 'stale':
            return f'Last report {fmt_age(m.age)}'
        updated = f'Updated {fmt_age(m.age)}'
        return f'{updated} · Serving {m.serving.label}' if m.serving_now and m.serving else updated

    def header_pill(self):
        return pool_pill(self.m)

    def header(self, y: float) -> float:
        tf, sf = font(15, NSFontWeightSemibold), font(11)
        self.text(f'{self.m.name} Pool', PAD, y, tf, C.label())
        text, fg, bg = self.header_pill()
        pw = pill_width(text)
        self.add(draw_pill, text, WIDTH - PAD - pw, y + (line_height(tf) - PILL_H) / 2, fg, bg)
        y += line_height(tf) + 1
        self.text(self.subtitle(), PAD, y, sf, C.secondary(), width=INNER - pw - 8)
        return y + line_height(sf)

    def switcher(self, y: float) -> float:
        """Two tiles, one per pool: glyph, name and state; the number (coloured like the menu bar); a 4 pt bar.
        The selected one has a quiet fill and a hairline in its pool's colour. The subtitle follows it."""
        w = (INNER - TILE_GAP) / 2
        name_f, state_f, big, unit, cap_f = (font(12.5, NSFontWeightSemibold), font(10.5, NSFontWeightMedium),
                                             font(21, NSFontWeightSemibold, mono=True),
                                             font(13, NSFontWeightSemibold), font(11))
        for i, pool in enumerate(POOLS):
            tm = self.tiles[pool]
            x = PAD + i * (w + TILE_GAP)
            rect = ((x, y), (w, TILE_H))
            selected = pool == self.m.pool
            self.region(rect, ('pool', pool), radius=10, tint=C.wash(0.055) if selected else None)
            if selected:
                self.add(stroke_rounded, rect, 10, C.pool_fill(pool).colorWithAlphaComponent_(0.6), 1.0)
            else:
                self.add(stroke_rounded, rect, 10, C.separator(), 1.0)
            ix, top = x + 10, y + 9
            lh = line_height(name_f)
            if tm.warn:
                self.add(draw_symbol, 'exclamationmark.triangle.fill', ix + GLYPH / 2, top + lh / 2, 11,
                         C.secondary(), NSFontWeightRegular, (GLYPH + 1, GLYPH))
            else:
                self.add(draw_pool_glyph, pool, ix + GLYPH / 2, top + lh / 2, C.pool_fill(pool))
            self.text(tm.name, ix + GLYPH + 6, top, name_f, C.label())
            word, fg, _ = pool_pill(tm)
            ny = top + lh + 2
            baseline = ny + round(big.ascender())
            if tm.headline is None:   # no number (no accounts, no data): the state takes its place
                sf2 = font(13, NSFontWeightMedium)
                self.text(word, ix, baseline - sf2.ascender(), sf2, C.secondary(), width=w - 20)
            else:
                self.text(word, ix, top + name_f.ascender() - state_f.ascender(), state_f,
                          C.secondary() if word == 'Regular' else fg, width=w - 20, align='right')
                color = C.secondary() if tm.warn else headline_text(tm)
                number = fmt_pct(tm.shown(tm.headline)).rstrip('%')
                nw, uw = text_width(number, big), text_width('%', unit)
                self.text(number, ix, ny, big, color)
                self.text('%', ix + nw, baseline - unit.ascender(), unit, color)
                self.text(tm.word, ix + nw + uw + 5, baseline - cap_f.ascender(), cap_f, C.secondary(),
                          width=w - 20 - nw - uw - 5)
            by = y + TILE_H - 9 - 4
            self.add(draw_bar, ix, by, w - 20, 4.0, tm.shown(tm.headline), headline_fill(tm))
            self.tips[('pool', pool)] = pool_line(tm)
        y += TILE_H + 7
        sf = font(11)
        self.text(self.subtitle(), PAD, y, sf, C.secondary(), width=INNER)
        return y + line_height(sf)

    # -- 2. problem banner (down / not reporting / no seats) -----------------------------------------
    def banner_copy(self):
        """(title, body, (button title, action) or a list of them, or None)."""
        m = self.m
        if m.ui is not None:
            return m.ui.banner_copy(m)
        if m.status == 'down':
            return 'Pool is down', 'Codex requests fail until the pool is running again.', ('Restart Pool', 'restart')
        if m.status == 'stale':
            return ('Pool not reporting',
                    ('The guard has stopped writing status.json, so these numbers may be out of date.'),
                    ('Run Doctor', 'doctor'))
        if m.status == 'empty':
            return ('No seats in the pool', 'The pool is running but has no seats yet.',
                    ('Add Account…', 'addaccount'))
        body = {NO_FILE: 'There is no status file yet. The guard writes one every minute.',
                INCOMPLETE: 'The status file is incomplete.'}.get(m.problem, f'{m.problem}.')
        return 'Pool not reporting', body, ('Run Doctor', 'doctor')

    def banner(self, y: float) -> float:
        down = self.m.status == 'down' or (self.m.spending is not None and self.m.status not in BANNER_STATES)
        title, body, button = self.banner_copy()
        if self.tiles and title.startswith('Pool '):   # with the switcher up, say which pool
            title = f'{self.m.name} pool {title[5:]}'
        box = 32.0
        self.add(fill_rounded, ((PAD, y), (box, box)), 8, C.soft(C.orange(), 0.16) if down else C.wash(0.08))
        self.add(draw_symbol, 'exclamationmark.triangle.fill', PAD + box / 2, y + box / 2 - 0.5, 14,
                 C.orange_text() if down else C.secondary(), NSFontWeightSemibold)
        tf, bf = font(14, NSFontWeightSemibold), font(11)
        x = PAD + box + 10
        w = WIDTH - PAD - x
        self.text(title, x, y - 1, tf, C.label(), width=w)
        by = y - 1 + line_height(tf) + 1
        self.add(draw_text_block, body, x, by, w, bf, C.secondary(), 3)
        by += text_block_height(body, bf, w, 3) + 8
        if button is None:
            return max(y + box, by - 8)
        bfont = button_font()
        for title, action in (button if isinstance(button, list) else [button]):   # one button, or a row of them
            bw = button_width(title)
            self.region(((x, by), (bw, BUTTON_H)), action if isinstance(action, tuple) else ('action', action),
                        radius=BUTTON_H / 2, tint=C.soft(C.orange(), 0.16) if down else C.wash(0.08))
            self.text(title, x, by + (BUTTON_H - line_height(bfont)) / 2, bfont,
                      C.orange_text() if down else C.label(), width=bw, align='center')
            x += bw + 8
        return max(y + box, by + BUTTON_H)

    # -- 3. hero -------------------------------------------------------------------------------------
    def hero(self, y: float) -> float:
        m = self.m
        color = headline_text(m)
        big, unit, cap_f = font(28, NSFontWeightSemibold, mono=True), font(17, NSFontWeightSemibold), font(12)
        number = f'{round(m.shown(m.headline)):d}'
        baseline = y + round(big.ascender())
        nw, uw = text_width(number, big), text_width('%', unit)
        caption = f'{m.word} this week · {m.scope}'
        self.text(number, PAD, y, big, color)
        self.text('%', PAD + nw, baseline - unit.ascender(), unit, color)
        self.text(caption, PAD + nw + uw + 7, baseline - cap_f.ascender(), cap_f, C.secondary())
        top = y
        y = baseline + 9
        self.add(draw_bar, PAD, y, INNER, 6.0, m.shown(m.headline), headline_fill(m))
        y += 6
        # Hovering the number or its bar shows how it is made (a tooltip only: no highlight, no click).
        self.regions.append((((PAD, top), (INNER, y - top)), ('tip', 'headline')))
        self.tips[('tip', 'headline')] = headline_breakdown(m)
        y += 10

        lines = []
        parts = [f'{m.regular_ready} of {m.regular_total} regular {m.noun}s ready'] if m.regular_total else []
        if total := total_text(m):
            parts.insert(0, total)
        solo = len(m.reserve_seats) == 1
        for r in m.reserve_seats:
            name = 'reserve' if solo else f'{r.label} reserve'
            if r.unavailable:
                parts.append(f'{name} {STATE_PILL.get(r.state, r.state).lower()}')
            elif m.left:
                parts.append(f'{name} {fmt_pct(m.shown(r.week.used if r.week else None))} left')
            else:
                parts.append(f'{name} at {fmt_pct(r.week.used if r.week else None)}')
        if parts:
            text = ' · '.join(parts)
            if text_width(text, font(12)) > INNER:   # '… accounts ready' is longer than '… seats ready': the reserve
                text = text.replace(' regular ', ' ', 1)   # is named after it, so 'regular' is implied
            lines.append((text, font(12), C.label()))
        if m.ui is not None:   # what makes its seats change, e.g. the serving one's 5-hour window
            lines += m.ui.hero_lines(m)
        if m.next_back and m.reporting:
            label, at = m.next_back
            loud = m.status in ('allout', 'reserve')   # it is the thing to wait for
            lines.append((f'Next back: {label} in {fmt_span((at - m.now).total_seconds())}',
                          font(12, NSFontWeightMedium) if loud else font(11), C.label() if loud else C.secondary()))
        resettable = [s.label for s in m.seats if s.resets and s.unavailable and s.state != 'disabled'] \
            if m.reporting else []
        if resettable:
            lines.append((f'Reset available for {", ".join(resettable)} · click the seat', font(11, NSFontWeightMedium),
                          C.blue_text()))
        pace = pace_text(m)
        if pace:
            lines.append((pace, font(11), C.secondary()))
        for i, (text, f, c) in enumerate(lines):
            self.text(text, PAD, y, f, c, width=INNER)
            y += line_height(f) + (2 if i < len(lines) - 1 else 0)
        return y

    # -- 4. chart ------------------------------------------------------------------------------------
    def section_title(self, title: str, y: float, row_h: float):
        hf = font(11, NSFontWeightSemibold)
        self.text(title, PAD, y + (row_h - line_height(hf)) / 2, hf, C.secondary())

    def chart(self, y: float) -> float:
        m = self.m
        toggle = self.samples >= MIN_CHART_SAMPLES   # hidden while neither range could draw a line
        row_h = 18.0 if toggle else float(line_height(font(11, NSFontWeightSemibold)))
        self.section_title('Quota left' if m.left else 'Usage', y, row_h)
        if toggle:
            self.range_toggle(y)
        y += row_h + 5

        data = chart_data(m, self.range_key)
        chart_h, af = 60.0, font(10)
        span_word, _, bucket_word = RANGES[self.range_key][1:]
        if data.in_range < MIN_CHART_SAMPLES:   # an empty chart of the same size, so nothing jumps later
            self.add(fill_rect, ((PAD, y + chart_h - 0.5), (INNER, 0.5)), C.separator())
            f = font(11)
            self.text('Collecting history…', PAD, y + (chart_h - line_height(f)) / 2, f, C.secondary(),
                      width=INNER, align='center')
            legend = ''
        else:
            self.add(draw_chart, ((PAD, y), (INNER, chart_h)), data, not m.serving_now, C.pool_fill(m.pool))
            peak = f'{data.peak:.1f}%' if data.peak < 10 else f'{data.peak:.0f}%'
            legend = f'bars: used per {bucket_word} · peak {peak}' if data.peak > 0 else 'no use in this range'
        y += chart_h + 5
        self.text(f'{span_word} ago', PAD, y, af, C.secondary())
        if legend:
            self.text(legend, PAD, y, af, C.secondary(), width=INNER, align='center')
        self.text('now', PAD, y, af, C.secondary(), width=INNER, align='right')
        return y + line_height(af)

    def range_toggle(self, y: float):
        seg_f = font(10.5, NSFontWeightMedium)
        x = WIDTH - PAD
        for key in reversed(list(RANGES)):
            label = RANGES[key][1]
            w = text_width(label, seg_f) + 14
            x -= w
            rect = ((x, y), (w, 18.0))
            selected = key == self.range_key
            self.regions.append((rect, ('range', key)))
            if selected:
                self.add(fill_rounded, rect, 5, C.wash(0.09))
            self.text(label, x, y + (18 - line_height(seg_f)) / 2, seg_f, C.label() if selected else C.secondary(),
                      width=w, align='center')
            x -= 2

    # -- 5. seats ------------------------------------------------------------------------------------
    def seats(self, y: float) -> float:
        hf, nf = font(11, NSFontWeightSemibold), font(10.5)
        self.section_title(f'{self.m.noun.title()}s', y, line_height(hf))
        order = ORDER_TITLE[self.m.balancing]
        self.text(order, PAD, y + hf.ascender() - nf.ascender(), nf, C.secondary(), width=INNER, align='right')
        # Hovering it says how the pool picks a seat (a tooltip only: no highlight, no click).
        ow = math.ceil(text_width(order, nf))
        self.regions.append((((PAD + INNER - ow, y), (ow, line_height(hf))), ('tip', 'order')))
        self.tips[('tip', 'order')] = ORDER_TIP[self.m.balancing]
        y += line_height(hf) + 4
        top = y
        for seat in self.m.seats:
            y = self.seat_row(seat, y) + ROW_GAP
        self.scroll_span = (top, y - ROW_GAP)
        return y - ROW_GAP

    def state_badge(self, seat: Seat, mid: float, stale: bool) -> float:
        """Draws the seat's state at the right edge, centred on `mid`; returns its width.
        Capsules for the states that need a look (Serving, Out, Parked, Blocked); plain text for Ready/Off."""
        m = self.m
        text = STATE_PILL.get(seat.state, seat.state.title() or 'Unknown')
        if seat.state in (READY, 'disabled') or text not in ('Serving', 'Out', 'Parked', 'Blocked'):
            f = font(11, NSFontWeightMedium)
            w = text_width(text, f)
            color = C.tertiary() if seat.state == 'disabled' else C.secondary()
            self.text(text, WIDTH - PAD - w, mid - line_height(f) / 2, f, color)
            return w
        quiet = C.secondary(), C.wash(0.07)
        if stale or text == 'Out' or (seat.serving and not m.serving_now):
            fg, bg = quiet
        elif seat.serving:
            hot = seat.reserve or seat.spending
            fg, bg = (C.red_text(), C.soft(C.red())) if hot else (C.green_text(), C.soft(C.green()))
        elif seat.state == 'parked':
            fg, bg = C.orange_text(), C.soft(C.orange())
        else:
            fg, bg = C.red_text(), C.soft(C.red())
        w = pill_width(text)
        self.add(draw_pill, text, WIDTH - PAD - w, mid - PILL_H / 2, fg, bg)
        return w

    def seat_row(self, seat: Seat, y: float) -> float:
        """Line 1: name, plan (and reserve) in small text, state at the right. Then the weekly bar (seats with a
        5-hour window add a thin bar for it, and one per scoped cap), then '49% used' / 'Resets in 6d 12h'. Extra
        lines: the problem of a blocked seat or one whose sign-in ended ('Re-login soon'), then an add-on pool's
        own (PoolUI.seat_lines: why it parked the seat, its credits), with its tooltip (PoolUI.seat_tip). Returns
        the y below the row."""
        m = self.m
        stale = not m.reporting
        dim = stale or seat.unavailable
        ui = m.ui
        name_f, small_f = font(13, NSFontWeightSemibold), font(11)
        lh, sh = line_height(name_f), SMALL_LH
        wins = seat_windows(seat)
        labelled = len(wins) > 1      # each limit on its own line: 'Week ▬▬▬ 45%', the binding one emphasised
        bars_h = SMALL_LH * len(wins) if labelled else 5.0
        blocked, soon = seat.state == 'blocked', seat.sign_in_soon
        ended = ui.sign_in_ended_text if ui else SIGN_IN_ENDED
        detail = (seat.detail or 'Needs attention') if blocked else (ended if soon else '')
        extra = []   # (SF Symbol, its colour, text, text colour)
        if detail:
            extra.append(('exclamationmark.circle.fill' if blocked else 'exclamationmark.triangle.fill',
                          C.secondary() if stale else C.red() if blocked else C.orange(), detail, C.secondary()))
        if ui is not None:
            extra += ui.seat_lines(seat, m, stale, bool(detail))
        # line 3's right text: the sign-in problem, or when the seat resets / comes back
        if blocked or soon:
            rf = font(11, NSFontWeightMedium)
            right, rc = ('Re-login needed' if blocked else 'Re-login soon'), \
                C.secondary() if stale else C.red_text() if blocked else C.orange_text()
        else:
            right = seat_right_text(seat, m.now)
            if seat.resets and seat.unavailable and not stale and seat.state != 'disabled':
                right = f'{right} · reset available' if right else 'Reset available'
            rf, rc = small_f, C.secondary()
        line3 = not labelled or bool(right)   # a labelled row's figures sit on the bars; no line without a right text
        row_h = ROW_PAD + lh + 4 + bars_h + (4 + sh if line3 else 0) + (sh + 1) * len(extra) + ROW_PAD
        key = ('seat', seat.name or seat.label)
        reset_tip = ''
        if seat.resets:
            exp = f', soonest expires {fmt_day(seat.reset_expiry)}' if seat.reset_expiry else ''
            reset_tip = f'{seat.resets} banked reset{"s" if seat.resets != 1 else ""}{exp}. Click to use one.'
        self.region(((PAD - 8, y), (INNER + 16, row_h)), key, radius=9,
                    tint=C.row_tint() if seat.serving and m.serving_now else None,
                    tip='  '.join(t for t in (detail, reset_tip, ui.seat_tip(seat) if ui else '') if t) or None)

        # line 1
        top = y + ROW_PAD
        badge_w = self.state_badge(seat, top + lh / 2, stale)
        if seat.resets and not stale:   # ↺ n, blue when the seat is out (a reset brings it back now)
            rf = font(11, NSFontWeightMedium)
            count = str(seat.resets)
            rc = C.blue_text() if seat.unavailable else C.secondary()
            cw = text_width(count, rf)
            rx = WIDTH - PAD - badge_w - 8 - cw
            self.text(count, rx, top + name_f.ascender() - rf.ascender(), rf, rc, width=cw + 1)
            self.add(draw_symbol, 'arrow.counterclockwise', rx - 7, top + lh / 2, 10, rc, NSFontWeightSemibold, (10, 10))
            badge_w += cw + 22
        room = INNER - badge_w - 10
        plan = seat.plan
        tag = ' · Reserve' if seat.reserve else ''
        tail_w = (text_width(plan, small_f) if plan else 0) + (text_width(tag, small_f) if tag else 0)
        name_w = min(text_width(seat.label, name_f), max(room - tail_w - 6, room * 0.55))
        self.text(seat.label, PAD, top, name_f, C.secondary() if dim else C.label(), width=name_w)
        x = PAD + name_w + 6
        small_y = top + name_f.ascender() - small_f.ascender()   # same baseline as the name
        if plan:
            pw = min(text_width(plan, small_f), PAD + room - x)
            self.text(plan, x, small_y, small_f, C.secondary(), width=max(0.0, pw))
            x += pw
        if tag and PAD + room - x > 20:
            hot = seat.serving and m.serving_now   # red only while the reserve is actually serving
            self.text(tag, x, small_y, small_f, C.red_text() if hot else C.secondary(), width=PAD + room - x)

        # bars: one full-width weekly bar, or for a seat with more limits one labelled line per limit
        by = top + lh + 4
        if not labelled:
            used = wins[0][1]
            self.add(draw_bar, PAD, by, INNER, 5.0, m.shown(used), bar_fill(used, dim))
        else:
            self.window_bars(seat, wins, PAD, by, small_f, dim, stale)
        y3 = by + bars_h + 4

        # line 3: the figure ('56% left') left, the reset or return right; a labelled row has only the right
        if line3:
            rw = text_width(right, rf) if right else 0
            lw = self.usage_text(seat, PAD, y3, small_f, dim, stale, room=INNER - rw - 12) if not labelled else 0
            self.text(right, PAD + lw + 12, y3, rf, rc, width=INNER - lw - 12, align='right')
        dy = y3 + sh + 1 if line3 else by + bars_h
        for symbol, sc, text, tc in extra:
            self.add(draw_symbol, symbol, PAD + 5, dy + sh / 2, 10, sc, NSFontWeightRegular, (10.5, 10))
            self.text(text[:1].upper() + text[1:], PAD + 14, dy, small_f, tc, width=INNER - 14, truncate='middle')
            dy += sh + 1
        return y + row_h

    def usage_text(self, seat: Seat, x: float, y: float, f, dim: bool, stale: bool, room: float | None = None) -> float:
        """'49% used' / '51% left' for a seat with one limit (a seat with more has its figures on its bar lines,
        window_bars). Returns the width drawn."""
        m = self.m
        week = seat_windows(seat)[0][1]
        text = f'{fmt_pct(m.shown(week))} {m.word}'
        self.text(text, x, y, f, C.secondary() if dim else C.label())
        return text_width(text, f)

    def window_bars(self, seat: Seat, wins: list, x: float, y: float, f, dim: bool, stale: bool):
        """One SMALL_LH line per limit: its name at the left ('Week', '5h', a scoped cap's model such as 'Fable'),
        its bar, and its figure at the right ('45%', or '45% left' in left mode). The limit that binds is the
        primary one: a 5 pt bar with its name and figure in the label colour; the others are 3 pt and secondary.
        The names and figures sit in aligned columns, so the bars line up."""
        m = self.m
        binding = binding_window(seat)
        vals = [f'{fmt_pct(m.shown(used))}{" left" if m.left else ""}' for _, used, _ in wins]
        cap_w = max(text_width(label, f) for label, _, _ in wins) + 8
        val_w = max(text_width(v, f) for v in vals) + 1
        bx, bw = x + cap_w, INNER - cap_w - val_w - 8
        for i, ((label, used, _), val) in enumerate(zip(wins, vals)):
            strong = i == binding and not stale and (not dim or (used or 0) >= 99.5)
            h = 5.0 if i == binding else 3.0
            colour = C.label() if strong else C.secondary()
            self.text(label, x, y, f, colour, width=cap_w - 4)
            self.add(draw_bar, bx, y + (SMALL_LH - h) / 2, bw, h, m.shown(used), bar_fill(used, dim))
            self.text(val, x + INNER - val_w, y, f, colour, width=val_w, align='right')
            y += SMALL_LH

    # -- 6. footer -----------------------------------------------------------------------------------
    def footer(self, y: float) -> float:
        f = font(13)
        row_h = 22.0
        ui = self.m.ui
        for symbol, title, action in FOOTER_ROWS:
            y = self.footer_row(y, row_h, symbol, title, action, f)
        if ui is not None:   # the add-on pool's own rows (a route control, its desktop app)
            y = ui.footer_rows(self, y, row_h, f)
        y = self.rule(y, 5, 5)
        for symbol, title, action, hint in APP_ROWS:
            if ui is not None and action == 'addaccount':
                title = ui.add_account_title
            y = self.footer_row(y, row_h, symbol, title, action, f, hint)
        y = self.footer_row(y, row_h, None, 'Quit', 'quit', f)
        version = self.m.version.split('-gate')[0].split('+gate')[0]
        text = ' · '.join(t for t in (f'CLIProxyAPI {version}' if version else '',
                                      ui.version_text(self.m) if ui else '') if t)
        if text:
            vf = font(10.5)
            self.text(text, PAD, y - row_h + (row_h - line_height(vf)) / 2, vf, C.tertiary(),
                      width=INNER - 2, align='right')
        return y

    def segmented(self, y, row_h, kind, items, selected, tips: dict, enabled: bool = True) -> float:
        """A small two-segment control at the row's right edge: (label, value) items, the selected value's segment
        filled. Each segment is a click region (kind, value) with its tooltip. Greyed when not enabled (then the
        segments only carry their tooltips). Returns its left edge."""
        seg_f, seg_h = font(11, NSFontWeightMedium), 20.0
        widths = [text_width(t, seg_f) + 16 for t, _ in items]
        x = WIDTH - PAD - sum(widths) - 4
        left = x
        sy = y + (row_h - seg_h) / 2
        self.add(fill_rounded, ((x, sy), (sum(widths) + 4, seg_h)), 6, C.wash(0.06))
        x += 2
        for (label, value), w in zip(items, widths):
            rect = ((x, sy + 2), (w, seg_h - 4))
            on = enabled and selected == value
            self.regions.append((rect, (kind, value)))
            if tips.get(value):
                self.tips[(kind, value)] = tips[value]
            if on:
                self.add(fill_rounded, rect, 4.5, C.wash(0.13))
            self.text(label, x, sy + 2 + (seg_h - 4 - line_height(seg_f)) / 2, seg_f,
                      C.tertiary() if not enabled else C.label() if on else C.secondary(), width=w, align='center')
            x += w
        return left

    def footer_row(self, y, row_h, symbol, title, action, f, hint: str = '') -> float:
        self.region(((PAD - 8, y), (INNER + 16, row_h)), ('action', action), radius=6)
        x = PAD
        if symbol:
            self.add(draw_symbol, symbol, PAD + 8, y + row_h / 2, 13, C.label(), NSFontWeightRegular)
            x = PAD + 26
        self.text(title, x, y + (row_h - line_height(f)) / 2, f, C.label())
        if hint:   # a menu-style shortcut, right-aligned
            self.text(hint, PAD, y + (row_h - line_height(f)) / 2, f, C.tertiary(), width=INNER - 2, align='right')
        return y + row_h


class PopoverView(NSView):
    """Draws the slice [y0, y0 + height) of a PopoverLayout, highlights the region under the pointer and
    hands clicks to handler(key, view, point)."""

    def initWithFrame_(self, frame):
        self = objc.super(PopoverView, self).initWithFrame_(frame)  # noqa: PLW0642 (the PyObjC init idiom)
        if self is None:
            return None
        self._lay = None
        self._y0 = 0.0
        self._hover = None
        self._pressed = None
        self._tracking = None
        self.handler = None
        return self

    def isFlipped(self):
        return True

    def acceptsFirstMouse_(self, event):
        return True

    @objc.python_method
    def set_slice(self, lay: PopoverLayout, y0: float, y1: float):
        self._lay, self._y0 = lay, y0
        self.setFrameSize_((WIDTH, max(0.0, y1 - y0)))
        self.setNeedsDisplay_(True)

    def drawRect_(self, rect):
        if self._lay is None:
            return
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.clipRect_(self.bounds())   # views do not clip to their bounds by default since macOS 14
        shift = NSAffineTransform.transform()
        shift.translateXBy_yBy_(0.0, -self._y0)
        shift.concat()
        st = DrawState(hover=self._hover)
        for op in self._lay.ops:
            op(st)
        NSGraphicsContext.restoreGraphicsState()

    # -- pointer ---------------------------------------------------------------------------------------
    @objc.python_method
    def region_at(self, event):
        """(region key or None, point in this view's coordinates)."""
        p = self.convertPoint_fromView_(event.locationInWindow(), None)
        if self._lay is None:
            return None, p
        ly = p.y + self._y0
        for ((rx, ry), (rw, rh)), key in reversed(self._lay.regions):
            if rx <= p.x <= rx + rw and ry <= ly <= ry + rh:
                return key, p
        return None, p

    @objc.python_method
    def set_hover(self, key):
        if key != self._hover:
            self._hover = key
            tip = self._lay.tips.get(key) if self._lay is not None and key else None
            self.setToolTip_(fresh(tip) if tip else None)
            self.setNeedsDisplay_(True)

    def updateTrackingAreas(self):
        if self._tracking is not None:
            self.removeTrackingArea_(self._tracking)
        opts = NSTrackingMouseMoved | NSTrackingMouseEnteredAndExited | NSTrackingActiveAlways | \
            NSTrackingInVisibleRect
        self._tracking = NSTrackingArea.alloc().initWithRect_options_owner_userInfo_(
            self.bounds(), opts, self, None)
        self.addTrackingArea_(self._tracking)
        objc.super(PopoverView, self).updateTrackingAreas()

    def mouseMoved_(self, event):
        key, _ = self.region_at(event)
        self.set_hover(key if key and key[0] != 'range' else None)   # route segments: a tooltip, no highlight

    def mouseExited_(self, event):
        self.set_hover(None)

    def mouseDown_(self, event):
        key, p = self.region_at(event)
        self._pressed = key
        if key and key[0] == 'seat' and self.handler:   # menus open on mouse down, like NSMenu
            self._pressed = None
            self.handler(key, self, p)
            self.set_hover(None)

    def mouseUp_(self, event):
        key, p = self.region_at(event)
        if key is not None and key == self._pressed and self.handler:
            self.handler(key, self, p)
        self._pressed = None


class PopoverContent(NSView):
    """The popover's content view: [above the seats] [seat list in a scroll view] [footer].

    show() builds the layout under this view's appearance, because colours derived with
    colorWithAlphaComponent_ are resolved when they are created; it is rebuilt if the appearance changes."""

    def initWithFrame_(self, frame):
        self = objc.super(PopoverContent, self).initWithFrame_(frame)  # noqa: PLW0642 (the PyObjC init idiom)
        if self is None:
            return None
        self._build = None
        self._max_h = None
        self.lay = None
        self.head_view = PopoverView.alloc().initWithFrame_(((0, 0), (WIDTH, 0)))
        self.list_view = PopoverView.alloc().initWithFrame_(((0, 0), (WIDTH, 0)))
        self.foot_view = PopoverView.alloc().initWithFrame_(((0, 0), (WIDTH, 0)))
        sv = self.scroller_view = NSScrollView.alloc().initWithFrame_(((0, 0), (WIDTH, 0)))
        sv.setDrawsBackground_(False)
        sv.contentView().setDrawsBackground_(False)
        sv.setBorderType_(NSNoBorder)
        sv.setHasVerticalScroller_(True)
        sv.setHasHorizontalScroller_(False)
        sv.setAutohidesScrollers_(True)
        sv.setScrollerStyle_(NSScrollerStyleOverlay)
        sv.setDocumentView_(self.list_view)
        for v in (self.head_view, sv, self.foot_view):
            self.addSubview_(v)
        return self

    def isFlipped(self):
        return True

    @objc.python_method
    def views(self):
        return (self.head_view, self.list_view, self.foot_view)

    @objc.python_method
    def set_handler(self, handler):
        self._handler = handler
        for v in self.views():
            v.handler = handler

    def performKeyEquivalent_(self, event):
        """⌘, opens Settings, as in any Mac app."""
        mods = event.modifierFlags() & NSEventModifierFlagDeviceIndependentFlagsMask
        if mods == NSEventModifierFlagCommand and event.charactersIgnoringModifiers() == ',' and \
                getattr(self, '_handler', None) is not None:
            self._handler(('action', 'settings'), self, None)
            return True
        return objc.super(PopoverContent, self).performKeyEquivalent_(event)

    @objc.python_method
    def show(self, build, max_height: float | None = None, reset_scroll: bool = False):
        """build() -> PopoverLayout. max_height: cap for the whole popover; the seat list scrolls to fit."""
        self._build, self._max_h = build, max_height
        self.relayout(reset_scroll)

    @objc.python_method
    def relayout(self, reset_scroll: bool = False):
        made = []
        self.effectiveAppearance().performAsCurrentDrawingAppearance_(lambda: made.append(self._build()))
        lay = self.lay = made[0]
        total = float(lay.height)
        s0, s1 = lay.scroll_span or (total, total)
        over = max(0.0, total - self._max_h) if self._max_h else 0.0
        list_h = s1 - s0 if not over else max(min(s1 - s0, MIN_LIST_H), s1 - s0 - over)
        self.head_view.set_slice(lay, 0.0, s0)
        self.head_view.setFrameOrigin_((0, 0))
        self.list_view.set_slice(lay, s0, s1)
        self.scroller_view.setFrame_(((0, s0), (WIDTH, list_h)))
        self.scroller_view.setHidden_(s1 <= s0)
        self.foot_view.set_slice(lay, s1, total)
        self.foot_view.setFrameOrigin_((0, s0 + list_h))
        self.setFrameSize_((WIDTH, s0 + list_h + (total - s1)))
        if reset_scroll:
            self.list_view.scrollPoint_((0, 0))
        self.setNeedsDisplay_(True)

    @objc.python_method
    def height(self) -> float:
        return self.frame().size.height

    @objc.python_method
    def set_hover(self, key):
        """Highlights `key` in whichever slice holds it (None clears all three)."""
        rect = next((r for r, k in self.lay.regions if k == key), None) if self.lay and key else None
        for v in self.views():
            inside = rect is not None and v._y0 <= rect[0][1] < v._y0 + v.frame().size.height
            v.set_hover(key if inside else None)

    def viewDidChangeEffectiveAppearance(self):
        if self._build is not None:
            self.relayout()


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 9. Actions and the app controller
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

_TERMINAL_DIR: str | None = None


def spawn(argv: list[str]) -> bool:
    """Starts a helper (open, ...) without blocking, and reaps it so it never lingers as a zombie."""
    try:
        p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL, start_new_session=True)
    except OSError:
        return False
    threading.Thread(target=p.wait, daemon=True).start()
    return True


def open_settings(pane: str | None = None, pool: str | None = None) -> bool:
    """Starts the Settings window (codexpool_settings.py) on `pane` and, with `pool` (one of POOLS), on that
    pool's side of the panes that have both, without blocking. It runs with this app's own
    interpreter, which has PyObjC (codexpool's own Python may not), given explicitly as for every action: never
    through PATH. A Settings window that is already open comes forward and shows the pane (it is single instance).
    Its output goes to logs/settings.log, as with `codexpool gui`, so a traceback is never lost. The window draws
    the pools' marks as this app does (--marks), so `--marks drawn` here is drawn there too."""
    if not SETTINGS_SCRIPT.exists():
        return False
    argv = [sys.executable, str(SETTINGS_SCRIPT)] + (['--pane', pane] if pane else []) + \
        (['--pool', pool] if pool in POOLS else []) + ['--marks', MARKS]
    try:
        logs = POOL_DIR / 'logs'
        logs.mkdir(parents=True, exist_ok=True)
        with open(logs / 'settings.log', 'ab') as log:
            p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=log, stderr=log, start_new_session=True)
    except OSError:
        return spawn(argv)   # no log (a full disk, a read-only folder): still open the window
    threading.Thread(target=p.wait, daemon=True).start()
    return True


def codex_seat_count(raw: dict | None) -> int:
    """Codex seats in a status.json (lane credentials such as xAI are listed there too, with another provider)."""
    seats = as_list(as_dict(raw).get('seats'))
    return sum(1 for s in seats if as_str(as_dict(s).get('provider'), 'codex') == 'codex')


def codexpool_python() -> str:
    """The interpreter for bin/codexpool when the wrapper is missing: "python" in settings.json, else codexpool's
    own venv, else this one (codexpool needs only the standard library)."""
    try:
        value = json.loads(SETTINGS_FILE.read_text()).get('python')
    except (OSError, ValueError, AttributeError):
        value = None
    if isinstance(value, str) and value.strip():
        return os.path.expanduser(value)
    venv = POOL_DIR / '.venv' / 'bin' / 'python'
    return str(venv) if venv.exists() else sys.executable


def codexpool_argv() -> list[str]:
    """How background actions run codexpool: bin/codexpool with an explicit interpreter. Never the ~/.local/bin
    entry or the script's own `#!/usr/bin/env python3`: under launchd PATH is /usr/bin:/bin, where python3 is
    the system one (possibly a stub, or stopped by the Xcode license), and ~/.local/bin/codexpool may belong
    to another tool."""
    return [codexpool_python(), str(CODEXPOOL_SCRIPT)]


def wrapper_is_ours() -> bool:
    """~/.local/bin/codexpool is the sh wrapper `codexpool install` writes (it names its interpreter)."""
    try:
        return not CODEXPOOL.is_symlink() and WRAPPER_MARK in CODEXPOOL.read_text()[:400]
    except (OSError, UnicodeDecodeError):
        return False


def run_codexpool(args: list[str], on_done=None, want_out: bool = False):
    """Runs `codexpool <args>` without blocking. on_done(returncode, last stderr line) runs on the main thread
    when it exits; with want_out, on_done(returncode, last stderr line, stdout) (the desktop commands print their
    closing line there). codexpool never prints tokens; its errors go to stderr."""
    try:
        p = subprocess.Popen([*codexpool_argv(), *args], cwd=str(POOL_DIR), stdin=subprocess.DEVNULL,
                             stdout=subprocess.PIPE if want_out else subprocess.DEVNULL, stderr=subprocess.PIPE,
                             start_new_session=True)
    except OSError as e:
        if on_done:
            on_done(127, e.strerror or str(e), '') if want_out else on_done(127, e.strerror or str(e))
        return

    def wait():
        out, err = p.communicate()
        lines = [ln.strip() for ln in err.decode('utf-8', 'replace').splitlines() if ln.strip()]
        if on_done:
            last = lines[-1] if lines else ''
            if want_out:
                AppHelper.callAfter(on_done, p.returncode, last, (out or b'').decode('utf-8', 'replace'))
            else:
                AppHelper.callAfter(on_done, p.returncode, last)
    threading.Thread(target=wait, daemon=True).start()


def open_in_terminal(command: str, intro: tuple[str, ...] = ()):
    """Opens a new Terminal window running `command` (a .command file: no Apple Events permission needed).
    Ctrl-C stops the command, not the script, and the window drops into a shell so the output stays."""
    global _TERMINAL_DIR
    if _TERMINAL_DIR is None or not os.path.isdir(_TERMINAL_DIR):
        _TERMINAL_DIR = tempfile.mkdtemp(prefix='codexpool-menubar-')
    fd, path = tempfile.mkstemp(suffix='.command', dir=_TERMINAL_DIR)
    lines = ['#!/bin/zsh -l', 'rm -f -- "$0"', 'clear', 'trap : INT']
    lines += [f'print -r -- {shlex.quote(line)}' for line in intro] + (['print'] if intro else [])
    lines += [command, 'exec /bin/zsh -l']
    with os.fdopen(fd, 'w') as f:
        f.write('\n'.join(lines) + '\n')
    os.chmod(path, 0o700)
    spawn(['/usr/bin/open', '-a', 'Terminal', path])


def cp_command(*args: str) -> str:
    """A command line for a Terminal window: the short `codexpool` wrapper when it is ours, else the full form."""
    argv = [str(CODEXPOOL)] if wrapper_is_ours() else codexpool_argv()
    return ' '.join(shlex.quote(a) for a in (*argv, *args))


def patch_bundle_identity():
    """Give the python process the app's identity (defaults domain, notifications) before AppKit starts."""
    info = NSBundle.mainBundle().infoDictionary()
    try:
        info['CFBundleIdentifier'] = BUNDLE_ID
        info['CFBundleName'] = 'Codex Pool'
        info['LSUIElement'] = '1'
    except (TypeError, AttributeError):
        pass


def activate_app():
    app = NSApplication.sharedApplication()
    if hasattr(app, 'activate'):     # macOS 14+
        app.activate()
    else:
        app.activateIgnoringOtherApps_(True)


def request_rightmost_position():
    """First run only: ask to sit rightmost among third-party items (next to the clock). After a ⌘-drag,
    macOS stores the new position under this same key, so an existing value is left alone."""
    key = f'NSStatusItem Preferred Position {AUTOSAVE_NAME}'
    defaults = NSUserDefaults.standardUserDefaults()
    if defaults.objectForKey_(key) is None:
        defaults.setDouble_forKey_(1.0, key)


class Controller(NSObject):
    """Owns the status item, the popover and the refresh timer."""

    def init(self):
        self = objc.super(Controller, self).init()  # noqa: PLW0642 (the PyObjC init idiom)
        if self is None:
            return None
        self.source = DataSource()
        self.sources = {'codex': self.source}   # + the add-on pool's, from its PoolUI's files
        for pool, ui in POOL_UI.items():
            self.sources[pool] = DataSource(ui.status_file, ui.history_file, pool=pool)
        self.model: Model | None = None      # the pool the popover shows (self.tab)
        self.models: dict = {}               # {'codex': Model, <add-on pool>: Model or None (not installed)}
        self.tab = 'codex'                   # one of POOLS
        self.closed_tab = None               # the tab the popover showed when it last started closing
        self.two = False                     # the item shows both pools
        self.range_key = '24h'
        self.toast: tuple[str, float] | None = None
        self.busy: str | None = None         # an add-on's command runs (its caption): its control is greyed
        self.item = None
        self.popover = None
        self.content = None
        self.bar_signature = None
        self.last_render = 0.0
        self.woke_at = -1e9          # time.monotonic() of the last wake from sleep
        self.first_run_checked = False   # the first-run Setup assistant check has been made
        self.closed_at = -1e9        # time.monotonic() when the popover last started closing
        self.closing_elsewhere = False   # dismiss() is closing it: not a click on the status item
        self.monitors = []           # the NSEvent monitors that close the popover while it shows (watch_outside)
        self.monitor_handlers = ()   # ... and the Python handlers their blocks call
        self.menu_open = False       # a seat menu is up (it tracks events itself until it closes)
        return self

    # -- lifecycle -------------------------------------------------------------------------------------
    def applicationDidFinishLaunching_(self, note):
        request_rightmost_position()
        self.item = NSStatusBar.systemStatusBar().statusItemWithLength_(menubar_length())
        self.item.setAutosaveName_(AUTOSAVE_NAME)
        button = self.item.button()
        button.setImagePosition_(NSImageLeft)
        button.setAlignment_(NSTextAlignmentLeft)
        button.setTarget_(self)
        button.setAction_('togglePopover:')
        self.build_popover()
        self.refresh(force=True)
        timer = NSTimer.timerWithTimeInterval_target_selector_userInfo_repeats_(POLL_EVERY_S, self, 'tick:',
                                                                                None, True)
        timer.setTolerance_(2.0)
        NSRunLoop.currentRunLoop().addTimer_forMode_(timer, NSRunLoopCommonModes)
        NSWorkspace.sharedWorkspace().notificationCenter().addObserver_selector_name_object_(
            self, 'didWake:', NSWorkspaceDidWakeNotification, None)

    @objc.python_method
    def build_popover(self):
        # No NSVisualEffectView: NSPopover draws its own material (Liquid Glass on macOS 26), and the
        # content view is transparent so it shows through.
        self.content = PopoverContent.alloc().initWithFrame_(((0, 0), (WIDTH, 400)))
        self.content.set_handler(self.handle_region)
        vc = NSViewController.alloc().init()
        vc.setView_(self.content)
        self.popover = NSPopover.alloc().init()
        self.popover.setContentViewController_(vc)
        self.popover.setBehavior_(NSPopoverBehaviorTransient)
        self.popover.setAnimates_(True)
        self.popover.setDelegate_(self)

    # -- refresh ---------------------------------------------------------------------------------------
    @objc.python_method
    def current_model(self, pool: str = 'codex') -> Model | None:
        """The pool's model now; None for an add-on's pool when it is not installed."""
        grace = time.monotonic() - self.woke_at < WAKE_GRACE_S
        src = self.sources[pool]
        return src.model(wake_grace=grace) if pool == 'codex' or src.installed else None

    @objc.python_method
    def current_models(self) -> dict:
        return {p: self.current_model(p) for p in POOLS}

    @objc.python_method
    def refresh(self, force: bool = False):
        for src in self.sources.values():
            src.poll(force=force)
        self.apply(self.current_models())

    def tick_(self, timer):
        changed = False
        for src in self.sources.values():
            changed = src.poll() or changed
        models = self.current_models()
        old = self.models or {}
        crossed = any((models[p] is None) != (old.get(p) is None) or
                      (models[p] is not None and models[p].status != old[p].status) for p in POOLS)
        toast_expired = self.toast is not None and time.time() > self.toast[1]
        if toast_expired:
            self.toast = None
        due = self.popover.isShown() and time.time() - self.last_render >= COUNTDOWN_EVERY_S
        if changed or crossed or toast_expired or due:
            self.apply(models)

    def didWake_(self, note):
        """After sleep status.json is old until the guard's next pass: hold off 'not reporting' for a bit."""
        self.woke_at = time.monotonic()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(WAKE_REPOLL_S, self,
                                                                                 'afterWake:', None, False)

    def afterWake_(self, timer):
        self.refresh(force=True)

    @objc.python_method
    def apply(self, models: dict):
        """models: current_models(). Without an add-on's pool (none, or not installed) the item is the Codex meter
        and number as before; with it, one strip with both pools' numbers."""
        if isinstance(models, Model):   # an older caller: the Codex pool alone
            models = {'codex': models}
        self.models = models
        codex = models['codex']
        second = models.get(POOLS[1]) if len(POOLS) > 1 else None
        if second is None:
            self.tab = 'codex'
        self.model = models.get(self.tab) or codex
        self.first_run(codex)
        sig = (menubar_signature(codex), menubar_signature(second) if second else None,
               mark_keys() if second else None)   # a newly installed or updated app redraws the strip's marks
        if sig != self.bar_signature:
            self.bar_signature = sig
            button = self.item.button()
            if second is not None:
                if not self.two:
                    self.two = True
                    self.item.setLength_(strip_length())
                    button.setImagePosition_(NSImageOnly)
                    button.setTitle_('')
                button.setImage_(strip_image(codex, second))
                button.setToolTip_(fresh(strip_tooltip(codex, second)))
                button.setAccessibilityLabel_(fresh(strip_accessibility(codex, second)))
            else:
                if self.two:
                    self.two = False
                    self.item.setLength_(menubar_length())
                    button.setImagePosition_(NSImageLeft)
                    button.setAccessibilityLabel_(None)
                button.setImage_(menubar_image(codex))
                button.setAttributedTitle_(menubar_title(codex))
                button.setToolTip_(fresh(menubar_tooltip(codex)))
        if self.popover.isShown():
            self.render()

    @objc.python_method
    def first_run(self, model: Model):
        """Once, at the first real report: a pool with no Codex seats opens the Setup assistant. Either way the
        decision is remembered in state/setup-shown, so the assistant never opens on its own again."""
        if self.first_run_checked or model.status in ('missing', 'stale', 'down'):
            return   # wait until the guard reports on a running pool
        self.first_run_checked = True
        if SETUP_SHOWN.exists():
            return
        try:
            SETUP_SHOWN.parent.mkdir(parents=True, exist_ok=True)
            SETUP_SHOWN.write_text(utcnow().isoformat() + '\n')
        except OSError:
            return
        if codex_seat_count(self.source.raw) == 0:
            open_settings('setup-welcome')

    @objc.python_method
    def max_height(self) -> float | None:
        window = self.item.button().window()
        screen = (window.screen() if window is not None else None) or NSScreen.mainScreen()
        return screen.visibleFrame().size.height - SCREEN_MARGIN if screen is not None else None

    @objc.python_method
    def render(self, reset_scroll: bool = False):
        toast = self.toast[0] if self.toast and time.time() <= self.toast[1] else None
        tiles = dict(self.models) if len(POOLS) > 1 and self.models.get(POOLS[1]) is not None else None
        model, range_key, busy = self.model, self.range_key, self.busy
        self.content.show(lambda: PopoverLayout(model, range_key, toast, tiles=tiles, busy=busy).build(),
                          self.max_height(), reset_scroll)
        self.popover.setContentSize_((WIDTH, self.content.height()))
        self.last_render = time.time()

    @objc.python_method
    def say(self, text: str):
        self.toast = (text, time.time() + TOAST_S)
        if self.popover.isShown():
            self.render()

    @objc.python_method
    def clicked_pool(self) -> str:
        """Which pool a click on the item means: with both pools, the half under the pointer (the click's x
        against the middle of the gap between the halves); a keyboard or VoiceOver press opens the pool in the
        worse state (down > all out > paid use > reserve > regular)."""
        codex = self.models.get('codex')
        second = self.models.get(POOLS[1]) if len(POOLS) > 1 else None
        if not self.two or codex is None or second is None:
            return 'codex'
        button = self.item.button()
        event = NSApplication.sharedApplication().currentEvent()
        mouse = (NSEventTypeLeftMouseDown, NSEventTypeLeftMouseUp, NSEventTypeRightMouseDown, NSEventTypeRightMouseUp)
        if event is not None and event.type() in mouse and event.window() is not None and \
                event.window() == button.window():
            x = button.convertPoint_fromView_(event.locationInWindow(), None).x
            _, split = strip_layout(codex, second)
            offset = (button.bounds().size.width - strip_length()) / 2   # the image is centred in the button
            return 'codex' if x < offset + split else second.pool
        return max(POOLS, key=lambda p: (pool_severity(self.models[p]), p == 'codex'))

    def togglePopover_(self, sender):
        pool = self.clicked_pool()
        if self.popover.isShown():
            if pool != self.tab:          # the other half: switch, keep it open
                self.show_popover(pool)
            else:
                self.popover.performClose_(sender)
            return
        if time.monotonic() - self.closed_at < REOPEN_GUARD_S and pool == self.closed_tab:
            return   # this is the click that just closed the (transient) popover
        self.show_popover(pool)

    @objc.python_method
    def show_popover(self, pool: str = 'codex'):
        self.refresh()                   # also brings the menu bar item up to date
        if self.models.get(pool) is not None:
            self.tab = pool
            self.model = self.models[pool]
        self.content.set_hover(None)     # no mouseExited arrives when the popover closes
        self.render(reset_scroll=True)
        button = self.item.button()
        self.popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSRectEdgeMinY)
        self.watch_outside()
        activate_app()

    def popoverWillClose_(self, note):
        if not self.closing_elsewhere:   # it may be the transient close on the status item's own mouse-down
            self.closed_at = time.monotonic()
            self.closed_tab = self.tab
        self.unwatch_outside()

    def popoverDidClose_(self, note):
        if not self.popover.isShown():   # unless it was opened again while the close animated
            self.unwatch_outside()

    def applicationDidResignActive_(self, note):
        """Another app came forward (⌘-Tab, a click elsewhere while we were active): close, like a menu."""
        self.dismiss()

    # -- closing on a click anywhere else --------------------------------------------------------------
    # The popover is transient, but the app is an agent that is usually not active, so the popover never
    # hears of clicks in other apps. While it shows: a global monitor closes it on any mouse-down in another
    # app, a local one on Escape, and resigning active closes it too. A global monitor only ever gets events
    # sent to other apps: clicks in the popover and on the status item still go to their views, so the
    # item's click still toggles (REOPEN_GUARD_S) and the popover's own clicks still work.
    @objc.python_method
    def watch_outside(self):
        self.unwatch_outside()
        clicks = NSEventMaskLeftMouseDown | NSEventMaskRightMouseDown | NSEventMaskOtherMouseDown
        self.monitor_handlers = (self.outside_click, self.key_down)
        self.monitors = [m for m in (
            NSEvent.addGlobalMonitorForEventsMatchingMask_handler_(clicks, self.monitor_handlers[0]),
            NSEvent.addLocalMonitorForEventsMatchingMask_handler_(NSEventMaskKeyDown, self.monitor_handlers[1]),
        ) if m is not None]

    @objc.python_method
    def unwatch_outside(self):
        monitors, self.monitors, self.monitor_handlers = self.monitors, [], ()
        for m in monitors:
            NSEvent.removeMonitor_(m)

    @objc.python_method
    def outside_click(self, event):
        """Global monitor: a mouse-down in another app's window, on the desktop, in the menu bar or on another
        menu bar item. A click outside an open seat menu only dismisses the menu."""
        if not self.menu_open and self.popover.isShown() and not self.on_us(event):
            self.dismiss()

    @objc.python_method
    def key_down(self, event):
        """Local monitor (key events sent to us, i.e. while the popover's window is key): Escape closes it. Every
        other key goes on as before (⌘, opens Settings)."""
        mods = event.modifierFlags() & (NSEventModifierFlagCommand | NSEventModifierFlagControl |
                                        NSEventModifierFlagOption | NSEventModifierFlagShift)
        if event.keyCode() == KEY_ESCAPE and not mods and not self.menu_open and self.popover.isShown():
            self.dismiss()
            return None   # handled: no beep
        return event

    @objc.python_method
    def on_us(self, event) -> bool:
        """Whether a click is on the popover's content or the status item. A global monitor never gets those
        (they are sent to us); this only makes sure. Its events have no window: the location is on screen."""
        window, p = event.window(), event.locationInWindow()
        if window is not None:
            p = window.convertPointToScreen_(p)
        button = self.item.button() if self.item is not None else None
        for view in (self.content, button):
            w = view.window() if view is not None else None
            if w is not None:
                rect = w.convertRectToScreen_(view.convertRect_toView_(view.bounds(), None))
                if NSPointInRect(p, rect):
                    return True
        return False

    @objc.python_method
    def dismiss(self):
        """Close for a reason that is not a click on the status item, so the next click on it opens the popover
        at once instead of counting as the click that closed it."""
        if not self.popover.isShown():
            return
        self.closing_elsewhere = True
        try:
            self.popover.performClose_(None)
        finally:
            self.closing_elsewhere = False

    # -- clicks ----------------------------------------------------------------------------------------
    @objc.python_method
    def handle_region(self, key, view, point):
        kind, value = key
        if kind == 'range':
            self.range_key = value
            self.render()
        elif kind == 'pool':
            if value != self.tab and self.models.get(value) is not None:
                self.tab, self.model = value, self.models[value]
                self.content.set_hover(None)
                self.render(reset_scroll=True)
        elif kind == 'seat':
            seat = next((s for s in self.model.seats if (s.name or s.label) == value), None)
            if seat:
                self.menu_open = True   # until the menu closes: popUp… returns only then
                try:
                    self.seat_menu(seat).popUpMenuPositioningItem_atLocation_inView_(None, point, view)
                finally:
                    self.menu_open = False
        elif kind == 'action':
            self.run_action(value)
        elif self.model is not None and self.model.ui is not None:   # the add-on pool's own controls
            self.model.ui.handle_region(kind, value, self)

    @objc.python_method
    def run_action(self, action: str):
        """A footer or banner action, for the pool the popover shows (an add-on pool's tab has its own Status…,
        Pool log, Docs and Add account…, through its command prefix; Doctor covers both)."""
        ui = POOL_UI.get(self.tab) if self.models.get(self.tab) is not None else None
        pre = list(ui.command_prefix) if ui else []
        terminal = {'status': cp_command(*pre, 'status', '--live'), 'doctor': cp_command('doctor'),
                    'log': cp_command(*pre, 'logs', '-f')}
        if ui is None:
            terminal['addseat'] = cp_command('login')
        if action in ('settings', 'addaccount'):
            self.popover.performClose_(None)
            # the pool of the tab you are on (with the add-on's pool installed; else Settings has only the Codex side)
            second = len(POOLS) > 1 and self.models.get(POOLS[1]) is not None
            pool = (ui.id if ui else 'codex') if second else None
            if not open_settings(None if action == 'settings' else 'setup-accounts', pool):
                if action == 'addaccount' and ui is None:   # no Settings window: the Terminal sign-in, as before
                    open_in_terminal(terminal['addseat'])
                else:
                    self.say('Settings aren\u2019t installed; run codexpool install')
        elif action in terminal:
            self.popover.performClose_(None)
            open_in_terminal(terminal[action])
        elif action == 'docs':
            self.popover.performClose_(None)
            docs, url = (ui.docs_path, ui.docs_url) if ui else (DOCS, DOCS_URL)
            spawn(['/usr/bin/open', str(docs) if docs.exists() else url])
        elif action == 'refresh':
            self.say('Refreshing…')
            run_codexpool(['guard'], self.refreshed)
        elif action == 'restart':
            self.say('Restarting the pool…')
            run_codexpool(['restart'], lambda code, err: self.after_action('Pool restarted', 'restart', code, err))
        elif action == 'quit':
            NSApplication.sharedApplication().terminate_(None)

    @objc.python_method
    def refreshed(self, code: int, err: str):
        """A `codexpool guard` pass finished: status.json is as fresh as it gets."""
        if code == 0:
            self.toast = None
        else:
            self.say(f'Refresh failed: {err}' if err else f'Refresh failed (exit {code}); run Doctor')
        self.refresh(force=True)

    @objc.python_method
    def seat_menu(self, seat: Seat) -> NSMenu:
        menu = NSMenu.alloc().initWithTitle_(fresh(seat.label))
        menu.setAutoenablesItems_(False)
        head = menu.addItemWithTitle_action_keyEquivalent_(
            fresh(seat.label + (f'  ·  {seat.email}' if seat.email else '')), None, '')
        head.setEnabled_(False)
        menu.addItem_(NSMenuItem.separatorItem())
        top = max((s.priority for s in self.model.seats if s.priority is not None), default=0)
        priority = '' if seat.priority is None else str(int(seat.priority))

        pool = seat.provider
        ui = POOL_UI.get(pool)

        def add(title, symbol, verb=None, enabled=True, extra=''):
            """An action; with no verb, a line that only says something (disabled, its tooltip the reason)."""
            item = menu.addItemWithTitle_action_keyEquivalent_(title, 'seatAction:' if verb else None, '')
            if verb:
                item.setTarget_(self)
                item.setRepresentedObject_([verb, fresh(seat.name), fresh(seat.label), extra, priority, pool])
            item.setEnabled_(bool(verb) and enabled and bool(seat.name))
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, None)
            if img is not None:
                item.setImage_(img)
            return item

        if seat.resets:   # a banked free reset brings the seat back to full right now
            exp = f', expires {fmt_day(seat.reset_expiry)}' if seat.reset_expiry else ''
            add(f'Use reset now… ({seat.resets} banked{exp})', 'arrow.counterclockwise.circle', 'reset')
        relogin_first = seat.state == 'blocked' or seat.sign_in_soon
        if relogin_first:   # the fix comes first
            add('Re-login…', 'person.badge.key', 'login')
        own = ui.seat_rotation_item(seat) if ui else None   # the add-on's own line in place of Enable/Disable
        if own is not None:
            title, symbol, verb, tip = own
            item = add(title, symbol, verb)
            if tip:
                item.setToolTip_(fresh(tip))
        elif seat.state == 'parked':   # a credits-off park: enable overrides it until the limit resets
            add('Enable (spends credits)…', 'play.circle', 'enable-parked')
        elif seat.state == 'disabled':
            add('Enable', 'play.circle', 'enable')
        else:
            add('Disable', 'pause.circle', 'disable')
        first = seat.priority is not None and seat.priority >= top and \
            sum(1 for s in self.model.seats if s.priority == top) == 1
        # The reserve stays last: moving it first would serve it before the regular seats. With "Soonest reset
        # first" the guard sets the order on every pass (the one after this action included), so it is off then.
        by_reset = self.model.balancing == 'reset'
        item = add('Make first', 'arrow.up.to.line', 'first', enabled=not first and not seat.reserve and not by_reset,
                   extra=f'{int(top) + 10}')
        if by_reset:
            item.setToolTip_(fresh('Soonest reset first sets the order. Change it in Settings \u2192 Balancing.'))
        if not relogin_first:
            add('Re-login…', 'person.badge.key', 'login')
        extras = ui.seat_menu_extra(seat) if ui else []   # e.g. the pool's usage page in the browser
        if extras:
            menu.addItem_(NSMenuItem.separatorItem())
            for title, symbol, verb in extras:
                add(title, symbol, verb)
        return menu

    def seatAction_(self, item):
        verb, name, label, extra, priority, *rest = [str(x) for x in item.representedObject()]
        pool = rest[0] if rest else 'codex'
        ui = POOL_UI.get(pool)
        pre = list(ui.command_prefix) if ui else []
        seat = next((s for s in (self.model.seats if self.model else []) if s.name == name), None)
        if ui is not None and ui.seat_action(verb, seat, self):
            return
        if verb == 'login':
            # --no-open: the sign-in URL is printed, to open in a private window signed in to the right account
            # (the default browser is usually signed in to another one). --priority: a re-login rewrites the
            # seat file, which holds the priority, so pass the current one back.
            self.popover.performClose_(None)
            args = [*pre, 'login', label, '--no-open'] + (['--priority', priority] if priority else [])
            who = seat.email if seat and seat.email else label
            site = f' at {ui.sign_in_site}' if ui else ''
            open_in_terminal(cp_command(*args), intro=(
                f'Re-login {label}: open the sign-in URL below in a private browser window',
                f'and sign in{site} as {who}.'))
            return
        if verb == 'enable-parked' and not self.confirm_spend(seat, label):
            return
        if verb == 'reset':
            if not self.confirm_reset(seat, label):
                return
            self.say(f'Resetting {label}…')
            run_codexpool(['reset', name, '--yes'],
                          lambda code, err: self.after_action(f'{label} is back to full usage', 'reset', code, err))
            return
        args, doing, done = {
            'enable': (['enable', name], f'Enabling {label}…', f'{label} enabled'),
            'enable-parked': (['enable', name], f'Enabling {label}…', f'{label} enabled; it may spend credits'),
            'disable': (['disable', name], f'Disabling {label}…', f'{label} disabled'),
            'first': (['priority', name, extra], f'Moving {label} to the front…', f'{label} is now first'),
        }[verb]
        self.say(doing)
        run_codexpool([*pre, *args], lambda code, err: self.after_action(done, args[0], code, err))

    @objc.python_method
    def confirm_reset(self, seat: Seat | None, label: str) -> bool:
        n = seat.resets if seat else 0
        exp = f' The soonest expires {fmt_day(seat.reset_expiry)}.' if seat and seat.reset_expiry else ''
        alert = NSAlert.alloc().init()
        alert.setMessageText_(fresh(f'Use a reset on {label}?'))
        alert.setInformativeText_(fresh(
            f'{label}\u2019s weekly and 5-hour limits go back to full right away, and the pool can use it again. '
            f'This uses 1 of {n} banked free reset{"s" if n != 1 else ""}.{exp} It never buys a reset.'))
        alert.addButtonWithTitle_('Use Reset')
        alert.addButtonWithTitle_('Cancel')
        self.popover.performClose_(None)
        activate_app()
        return alert.runModal() == NSAlertFirstButtonReturn

    @objc.python_method
    def confirm_spend(self, seat: Seat | None, label: str) -> bool:
        """Enabling a parked seat overrides the credit guard; ask first, since it spends money."""
        until = seat.until if seat else None
        when = f' in {fmt_span((until - utcnow()).total_seconds())}' if until and until > utcnow() else ''
        alert = NSAlert.alloc().init()
        alert.setMessageText_(fresh(f'Enable {label} and spend credits?'))
        ui = POOL_UI.get(seat.provider) if seat is not None else None
        if ui is not None:
            alert.setInformativeText_(fresh(ui.enable_parked_text(label, when)))
        else:
            alert.setInformativeText_(fresh(
                f'The credit guard parked {label} because its plan limit is used up and further requests would '
                f'spend credits. Enabling it overrides the guard until the limit resets{when}.'))
        alert.addButtonWithTitle_('Enable')
        alert.addButtonWithTitle_('Cancel')
        self.popover.performClose_(None)
        activate_app()
        return alert.runModal() == NSAlertFirstButtonReturn

    @objc.python_method
    def after_action(self, done: str, verb: str, code: int, err: str):
        if code == 0:
            self.say(done)
            run_codexpool(['guard'], lambda c, e: self.refresh(force=True))  # show the effect now
        else:
            self.say(f'{verb.capitalize()} failed: {err}' if err else f'codexpool {verb} failed (exit {code})')


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 10. Snapshot mode and main()
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def new_bitmap(w: float, h: float, scale: float = 2.0):
    make = NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_
    rep = make(None, round(w * scale), round(h * scale), 8, 4, True, False, NSDeviceRGBColorSpace, 0, 0)
    rep.setSize_((w, h))
    return rep


class DrawingView(NSView):
    """A flipped view that draws by calling self.fn(); used to render snapshots offscreen."""

    def isFlipped(self):
        return True

    def drawRect_(self, rect):
        self.fn()


def render_offscreen(fn, w: float, h: float, appearance):
    """Draws fn() (flipped coordinates, `appearance`) into a 2x bitmap via NSView caching; no window needed."""
    view = DrawingView.alloc().initWithFrame_(((0, 0), (w, h)))
    view.fn = fn
    view.setAppearance_(appearance)
    rep = new_bitmap(w, h)
    view.cacheDisplayInRect_toBitmapImageRep_(view.bounds(), rep)
    return rep


def write_png(rep, path: str):
    data = rep.representationUsingType_properties_(NSBitmapImageFileTypePNG, {})
    if not data or not data.writeToFile_atomically_(path, True):
        raise SystemExit(f'could not write {path}')


def rgb(r, g, b, a=1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(r, g, b, a)


def snapshot(out: str, appearance_name: str, status_path: Path, history_path: Path | None,
             now: dt.datetime | None, range_key: str, max_height: float | None = None, hover: tuple | None = None,
             pool_status: Path | None = None, pool_history: Path | None = None, pool: str = 'codex',
             marks: str = 'drawn'):
    """pool_status: the add-on pool's status file, which makes that pool installed (both numbers in the item, the
    switcher in the popover; ignored without an add-on); pool: the tab the popover shows; marks: 'drawn' (plain
    shapes, nothing read from /Applications, so the output is reproducible) or 'app' (the logos from the apps on
    this Mac, as live)."""
    global MARKS
    MARKS = marks
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyProhibited)
    dark = appearance_name == 'dark'
    appearance = NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua if dark else NSAppearanceNameAqua)
    source = DataSource(status_path, history_path)
    source.poll(force=True)
    model = codex = source.model(now)
    tiles = None
    if pool_status is not None and len(POOLS) > 1:
        second = POOLS[1]
        csource = DataSource(pool_status, pool_history, pool=second)
        csource.poll(force=True)
        if csource.is_installed(now):
            tiles = {'codex': codex, second: csource.model(now)}
            model = tiles.get(pool, codex)

    # Popover: the real PopoverContent, cached offscreen at 2x, then set on an opaque stand-in for the
    # popover material (vibrancy needs a window).
    content = PopoverContent.alloc().initWithFrame_(((0, 0), (WIDTH, 400)))
    content.setAppearance_(appearance)
    content.show(lambda: PopoverLayout(model, range_key, tiles=tiles).build(), max_height)
    if hover:
        content.set_hover(hover)
        tip = content.lay.tips.get(hover)
        if tip:   # a tooltip can't be drawn offscreen; print it (e.g. --hover tip:headline, the hero's breakdown)
            print(tip)
    height = content.height()
    pixels = new_bitmap(WIDTH, height)
    content.cacheDisplayInRect_toBitmapImageRep_(content.bounds(), pixels)

    margin, radius = 28.0, 14.0
    W, H = WIDTH + 2 * margin, height + 2 * margin
    card = ((margin, margin), (WIDTH, height))

    def compose():
        top, bottom = ((0.16, 0.17, 0.22), (0.08, 0.09, 0.12)) if dark else ((0.80, 0.84, 0.92), (0.70, 0.75, 0.86))
        NSGradient.alloc().initWithStartingColor_endingColor_(rgb(*top), rgb(*bottom)).drawInRect_angle_(
            ((0, 0), (W, H)), 90)
        NSGraphicsContext.saveGraphicsState()
        shadow = NSShadow.alloc().init()
        shadow.setShadowBlurRadius_(24)
        shadow.setShadowOffset_((0, -8))
        shadow.setShadowColor_(rgb(0, 0, 0, 0.45 if dark else 0.22))
        shadow.set()
        fill_rounded(card, radius, rgb(0.165, 0.165, 0.18) if dark else rgb(0.965, 0.965, 0.972))
        NSGraphicsContext.restoreGraphicsState()
        NSGraphicsContext.saveGraphicsState()
        rounded(card, radius).addClip()
        pixels.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            card, ((0, 0), (0, 0)), NSCompositingOperationSourceOver, 1.0, True, None)
        NSGraphicsContext.restoreGraphicsState()
        stroke_rounded(card, radius, rgb(1, 1, 1, 0.12) if dark else rgb(0, 0, 0, 0.10), 1.0)

    write_png(render_offscreen(compose, W, H, appearance), out)

    # Menu bar item at 2x on a menu-bar-like strip: the item's fixed-width box, content left-aligned (both
    # pools: centred), then the clock for scale.
    clock = attributed('Sat 26 Sep  1:07', font(NSFont.menuBarFontOfSize_(0).pointSize()), C.label())
    if tiles:
        image, title = strip_image(tiles['codex'], tiles[POOLS[1]]), None
        item_w = strip_length()
    else:
        image, title = menubar_image(model), menubar_title(model)
        item_w = menubar_length()
    x0, gap = 10.0, 10.0
    bw, bh = x0 + item_w + gap + math.ceil(clock.size().width) + 12, 24.0

    def compose_bar():
        fill_rect(((0, 0), (bw, bh)), rgb(0.13, 0.13, 0.15) if dark else rgb(0.93, 0.93, 0.95))
        if title is None:
            image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                ((x0, (bh - STRIP_H) / 2), (item_w, STRIP_H)), ((0, 0), (0, 0)), NSCompositingOperationSourceOver,
                1.0, True, None)
        else:
            image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
                ((x0, (bh - METER_H) / 2), (METER_W, METER_H)), ((0, 0), (0, 0)), NSCompositingOperationSourceOver,
                1.0, True, None)
            title.drawAtPoint_((x0 + METER_W + 1, (bh - title.size().height) / 2))
        clock.drawAtPoint_((x0 + item_w + gap, (bh - clock.size().height) / 2))

    base = out[:-4] if out.lower().endswith('.png') else out
    write_png(render_offscreen(compose_bar, bw, bh, appearance), f'{base}-menubar.png')
    print(out)
    print(f'{base}-menubar.png')


def run_app(marks: str = 'app'):
    global MARKS
    MARKS = marks
    patch_bundle_identity()
    app = NSApplication.sharedApplication()
    app.setActivationPolicy_(NSApplicationActivationPolicyAccessory)
    controller = Controller.alloc().init()
    app.setDelegate_(controller)
    # An unexpected exception is logged (stderr) and the app keeps running, instead of a modal alert.
    AppHelper.runEventLoop(unexpectedErrorAlert=lambda: True)


def main(argv=None):
    p = argparse.ArgumentParser(prog='codexpool-menubar', description='Codex Pool menu bar app')
    p.add_argument('--snapshot', metavar='OUT.png', help='render the popover and menu bar item to PNGs and exit')
    p.add_argument('--appearance', choices=('light', 'dark'), default='light')
    p.add_argument('--status', type=Path, default=STATUS_FILE, help='status.json to render (snapshot)')
    p.add_argument('--history', type=Path, help='history.jsonl to render (snapshot)')
    p.add_argument('--now', help='pretend the time is this ISO-8601 instant (snapshot)')
    p.add_argument('--range', dest='range_key', choices=tuple(RANGES), default='24h', help='chart range (snapshot)')
    p.add_argument('--max-height', type=float, help='cap the popover height, in pt (snapshot)')
    p.add_argument('--hover', metavar='KIND:VALUE',
                   help='highlight a region, e.g. action:doctor, and print its tooltip, e.g. tip:headline (snapshot)')
    p.add_argument('--pool-status', type=Path, metavar='PATH',
                   help="an add-on pool's status file: that pool is installed (snapshot; default: none)")
    p.add_argument('--pool-history', type=Path, metavar='PATH',
                   help='its history (snapshot; default: the history file named like --pool-status, if any)')
    p.add_argument('--pool', choices=POOLS + pool_aliases(), default='codex',
                   help='the tab the popover shows (snapshot)')
    p.add_argument('--marks', choices=('drawn', 'app'),
                   help="the pools' marks: plain drawn shapes, or the logos from the pools' apps on this Mac "
                        "(default: drawn for a snapshot, app for the live app)")
    args = p.parse_args(argv)
    if not args.snapshot:
        run_app(args.marks or 'app')
        return
    history = args.history
    if history is None:
        sibling = args.status.parent / 'history.jsonl'
        history = sibling if args.status != STATUS_FILE and sibling.exists() else HISTORY_FILE
    now = parse_time(args.now) if args.now else None
    if args.now and now is None:
        p.error(f'--now: not an ISO-8601 time: {args.now}')
    hover = tuple(args.hover.split(':', 1)) if args.hover and ':' in args.hover else None
    pool_history = args.pool_history
    if args.pool_status is not None and pool_history is None:
        sibling = args.pool_status.with_name(args.pool_status.name.replace('status', 'history')
                                             .replace('.json', '.jsonl'))
        pool_history = sibling if sibling != args.pool_status and sibling.exists() else None
    snapshot(args.snapshot, args.appearance, args.status, history, now, args.range_key, args.max_height, hover,
             args.pool_status, pool_history, pool_id(args.pool), args.marks or 'drawn')


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 11. Add-ons: a second pool's PoolUI (addons/<id>/menubar_ext.py; docs/ADDONS.md)
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def pool_aliases() -> tuple:
    """Other names --pool takes for an add-on's pool (the add-on's own id, say)."""
    return tuple(a for ui in POOL_UI.values() for a in getattr(ui, 'aliases', ()))


def pool_id(name: str | None) -> str | None:
    """A pool id, from its id or one of its aliases (None when it is neither)."""
    if name in POOLS:
        return name
    return next((ui.id for ui in POOL_UI.values() if name in getattr(ui, 'aliases', ())), None)


def register_pool_ui(ui):
    """Adds the add-on's pool to POOLS and the tables the drawing reads. One add-on pool at most: the item has two
    halves and the popover two tiles."""
    global POOLS
    if not isinstance(ui.id, str) or ui.id in POOLS or ui.id in POOL_UI:
        raise ValueError(f'pool id {ui.id!r} is taken or not a string')
    if POOL_UI:
        raise ValueError(f'a second add-on pool ({ui.id!r}): the menu bar shows at most two pools')
    POOL_UI[ui.id] = ui
    POOLS += (ui.id,)
    POOL_NAME[ui.id] = ui.title
    POOL_COLORS[ui.id] = tuple(ui.colors)
    if getattr(ui, 'mark', None):
        MARK_APPS[ui.id] = (ui.mark[0], tuple(ui.mark[1]))
    MARK_SCALE[ui.id] = float(getattr(ui, 'mark_scale', 1.0))


def load_pool_extensions(addons_dir: Path = ADDONS_DIR):
    """One PoolUI per addons/<id>/menubar_ext.py, loaded as codexpool_menubar_ext_<id> with this module as the
    argument of its load(). Ordered by id; a broken one is noted on stderr and skipped, and the app runs with the
    Codex pool alone (an add-on's failure never takes the menu bar down)."""
    import importlib.util
    try:
        dirs = sorted(d for d in addons_dir.iterdir() if d.is_dir() and (d / 'menubar_ext.py').is_file())
    except OSError:
        return
    for d in dirs:
        name = f'codexpool_menubar_ext_{d.name}'
        try:
            spec = importlib.util.spec_from_file_location(name, d / 'menubar_ext.py')
            mod = importlib.util.module_from_spec(spec)
            sys.modules[name] = mod
            spec.loader.exec_module(mod)
            register_pool_ui(mod.load(sys.modules[__name__]))
        except Exception as e:  # noqa: BLE001 - an add-on must not take the menu bar down
            sys.modules.pop(name, None)
            print(f'codexpool-menubar: add-on {d.name}: menubar_ext.py failed to load ({e}); running without it',
                  file=sys.stderr)


load_pool_extensions()


if __name__ == '__main__':
    main()
