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
    writes OUT.png (the popover) and OUT-menubar.png (the menu bar item), both at 2x.
    --history defaults to history.jsonl beside --status when that exists, else the live history.
    --max-height caps the popover the way a short screen does (the seat list then scrolls).
    --hover highlights one region, e.g. seat:<seat file name> or action:doctor, and prints its tooltip if it has one
    (tip:headline prints the hero's breakdown).

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
import shlex
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from itertools import pairwise
from pathlib import Path

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
    NSCompositingOperationSourceIn,
    NSCompositingOperationSourceOver,
    NSDeviceRGBColorSpace,
    NSEventModifierFlagCommand,
    NSEventModifierFlagDeviceIndependentFlagsMask,
    NSFont,
    NSFontAttributeName,
    NSFontWeightMedium,
    NSFontWeightRegular,
    NSFontWeightSemibold,
    NSForegroundColorAttributeName,
    NSGradient,
    NSGraphicsContext,
    NSImage,
    NSImageLeft,
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
from Foundation import NSAffineTransform, NSBundle, NSObject, NSRunLoop, NSRunLoopCommonModes, NSTimer, NSUserDefaults
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

BUNDLE_ID = 'com.codexpool.menubar'
AUTOSAVE_NAME = 'CodexPool'

STALE_AFTER_S = 180          # status.json older than this means "pool not reporting"
WAKE_GRACE_S = 120           # ... except this soon after the Mac wakes: the guard needs a pass to catch up
WAKE_REPOLL_S = 15           # re-read the files this long after a wake
POLL_EVERY_S = 10            # how often we stat the status file
COUNTDOWN_EVERY_S = 30       # how often countdown labels refresh while the popover is open
TOAST_S = 8                  # how long an action's feedback replaces the header subtitle
REOPEN_GUARD_S = 0.35        # a click this soon after the popover closed is the click that closed it
HISTORY_TAIL_BYTES = 1 << 20 # read at most the last 1 MiB of history.jsonl
PACE_WINDOW_S = 6 * 3600     # the pace slope looks at the last 6 h of history
PACE_MIN_RISING_S = 30 * 60  # ... and needs at least 30 min of non-dropping samples
MIN_CHART_SAMPLES = 3        # fewer than this in the range shows "Collecting history…"

WIDTH = 340.0                # popover width, pt
PAD = 16.0                   # popover side padding
INNER = WIDTH - 2 * PAD
SCREEN_MARGIN = 40.0         # the popover is at most the screen's visible height minus this; the seats scroll
MIN_LIST_H = 110.0           # ... but always shows at least this much of the seat list

RANGES = {'24h': (24 * 3600, '24h'), '7d': (7 * 24 * 3600, '7d')}

# What the headline covers and how numbers read: pool.headline and pool.display in status.json, which the guard
# copies from settings.json. A file without them (an older guard) gets the defaults, the first of each.
HEADLINES = ('all', 'regular')     # every seat that is not off, reserve included / the regular seats only
DISPLAYS = ('left', 'used')        # count down from 100 % / count up from 0 %
SCOPE = {'all': 'all seats', 'regular': 'regular seats'}

# Seat states written by the guard, and how the popover names them.
SERVING, READY = 'active', 'ready'
OUT_STATES = ('exhausted', 'cooldown')
UNAVAILABLE = OUT_STATES + ('parked', 'disabled', 'blocked')   # rows that cannot serve are dimmed
STATE_PILL = {'active': 'Serving', 'ready': 'Ready', 'exhausted': 'Out', 'cooldown': 'Out',
              'parked': 'Parked', 'blocked': 'Blocked', 'disabled': 'Off'}

# Plan families, matched in order against the plan string ("self_serve_business_prolite" is Business).
PLAN_NAMES = (('enterprise', 'Enterprise'), ('business', 'Business'), ('team', 'Team'), ('edu', 'Edu'),
              ('pro', 'Pro'), ('plus', 'Plus'), ('free', 'Free'))

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


def plan_badge(plan: str, weight) -> str:
    """'team', 1 -> 'Team 1×'; 'self_serve_business_prolite', 5 -> 'Business 5×'; 'pro', 20 -> 'Pro 20×'."""
    p = plan.lower()
    name = next((n for key, n in PLAN_NAMES if key in p), plan.replace('_', ' ').title() if plan else '')
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
    resets: int = 0              # banked free resets (codexpool reset uses one)
    reset_expiry: dt.datetime | None = None   # when the soonest banked reset expires

    @property
    def serving(self) -> bool:
        return self.state == SERVING

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
    serving: Seat | None = None
    seats: list[Seat] = field(default_factory=list)
    regular_ready: int = 0
    regular_total: int = 0
    reserve_seats: list[Seat] = field(default_factory=list)
    next_back: tuple[str, dt.datetime] | None = None
    version: str = ''
    history: list[Sample] = field(default_factory=list)

    @property
    def reporting(self) -> bool:  # the numbers are current
        return self.status not in ('stale', 'missing')

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
    def scope(self) -> str:         # 'all seats' / 'regular seats'
        return SCOPE.get(self.headline_mode, SCOPE['all'])

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


def parse_seat(d) -> Seat | None:
    d = as_dict(d)
    if not d:
        return None
    week = parse_window(d.get('week'))
    week_used = clamp_pct(as_num(d.get('week_used')))
    if week_used is not None and (week is None or week.used is None):
        week = Window(week_used, week.reset_at if week else None, 10080)
    short = parse_window(d.get('short'))
    if short is not None and short.used is None:
        short = None
    weight = as_num(d.get('weight'))
    plan = as_str(d.get('plan')) or as_str(as_dict(d.get('usage')).get('plan'))
    label = as_str(d.get('label')) or as_str(d.get('email')) or as_str(d.get('name')) or 'Seat'
    return Seat(label=label, name=as_str(d.get('name')), email=as_str(d.get('email')),
                plan=plan_badge(plan, weight), state=as_str(d.get('state')).lower() or 'unknown',
                detail=as_str(d.get('detail')).strip(), until=parse_time(d.get('until')),
                priority=as_num(d.get('priority')), weight=weight, reserve=d.get('reserve') is True,
                week=week, short=short, resets=int(as_num(as_dict(d.get('resets')).get('available'), 0) or 0),
                reset_expiry=parse_time(as_dict(d.get('resets')).get('next_expiry')))


def weighted_used(seats: list[Seat]) -> float | None:
    # Weights are relative sizes (Plus = 1, Pro = 20); cap absurd values so the maths stays finite.
    rows = [(min(max(s.weight or 1.0, 0.0), 1e6), s.week.used) for s in seats if s.week and s.week.used is not None]
    total = sum(w for w, _ in rows)
    v = sum(w * u for w, u in rows) / total if total else None
    return v if v is None or math.isfinite(v) else None


def build_model(raw: dict | None, problem: str, history: list[Sample], now: dt.datetime,
                mtime: dt.datetime | None = None, wake_grace: bool = False) -> Model:
    """mtime: when status.json was last written (None in snapshots, whose --now is made up). The age is the
    older of that and generated_at, so a clock step cannot hide a guard that stopped. wake_grace: the Mac
    woke up moments ago, so an old file is not (yet) a guard that stopped."""
    written = (now - mtime).total_seconds() if mtime else None
    if raw is None:
        return Model(now=now, status='missing', problem=problem or NO_FILE, age=written, history=history)

    pool = as_dict(raw.get('pool'))
    generated = parse_time(raw.get('generated_at'))
    ages = [a for a in ((now - generated).total_seconds() if generated else None, written) if a is not None]
    age = max(ages) if ages else None

    seats = [s for s in (parse_seat(x) for x in as_list(raw.get('seats'))) if s]
    seats.sort(key=lambda s: -(s.priority if s.priority is not None else -1e9))  # fill order; stable
    regular = [s for s in seats if not s.reserve]
    reserve = [s for s in seats if s.reserve]

    active_label = as_str(raw.get('active'))
    serving = next((s for s in seats if s.serving), None) or \
        next((s for s in seats if active_label and s.label == active_label and s.available), None)

    mode, display = as_str(pool.get('headline')), as_str(pool.get('display'))
    headline_mode = mode if mode in HEADLINES else HEADLINES[0]
    display = display if display in DISPLAYS else DISPLAYS[0]
    if mode in HEADLINES:   # the guard picked the figure: used_pct is the one `headline` names
        headline = clamp_pct(as_num(pool.get('used_pct')))
        if headline is None:
            headline = clamp_pct(as_num(pool.get(f'used_pct_{mode}')))
    else:                   # an older guard: used_pct is the regular seats, used_pct_all every seat
        headline = clamp_pct(as_num(pool.get('used_pct_all')))
    if headline is None:
        headline = weighted_used([s for s in seats if s.state != 'disabled'] if headline_mode == 'all' else regular)

    nb = as_dict(raw.get('next_back'))
    nb_at = parse_time(nb.get('at'))
    next_back = (as_str(nb.get('label'), '?'), nb_at) if nb_at and nb_at > now else None
    if next_back is None:  # derive it from the seats when the guard did not say
        waiting = [(s.until, s.label) for s in seats if s.until and s.until > now and not s.available]
        if waiting:
            at, label = min(waiting)
            next_back = (label, at)

    regular_ready = sum(1 for s in regular if s.available)
    if not seats:
        regular_ready = int(as_num(pool.get('regular_available'), 0))
    reserve_in_use = pool.get('reserve_in_use') is True or bool(serving and serving.reserve)
    if not reserve_in_use and regular and regular_ready == 0 and any(s.available for s in reserve):
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
                 display=display, serving=serving, seats=seats, regular_ready=regular_ready,
                 regular_total=regular_total, reserve_seats=reserve, next_back=next_back,
                 version=as_str(pool.get('version')), history=history)


class DataSource:
    """Caches status.json and history.jsonl by mtime. poll() is cheap enough to run every few seconds."""

    def __init__(self, status_path: Path = STATUS_FILE, history_path: Path | None = HISTORY_FILE):
        self.status_path = Path(status_path)
        self.history_path = Path(history_path) if history_path else None
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
            self.history = load_history(self.history_path)
            self._history_ns = m
            changed = True
        return changed

    def model(self, now: dt.datetime | None = None, wake_grace: bool = False) -> Model:
        """now: pretend time (snapshots); the file's mtime is only meaningful against the real clock."""
        return build_model(self.raw, self.problem, self.history, now or utcnow(),
                           mtime=self.status_mtime if now is None else None, wake_grace=wake_grace)


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 4. History, pace and the chart series
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def load_history(path: Path | None) -> list[Sample]:
    """history.jsonl lines: {t, used, all, reserve, seats{label: week_used}}. Bad lines are skipped."""
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
                          clamp_pct(as_num(d.get('all')))))
    out.sort(key=lambda s: s.t)
    return out


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
    pts: list[tuple[float, float, bool]]  # (t, value shown, reserve); starts with the last sample before t0, if any
    in_range: int                         # samples inside [t0, t1]
    t0: float                             # the x axis always spans the whole range: now - 24 h (or 7 d) ...
    t1: float                             # ... to now


def chart_data(m: Model, range_key: str) -> ChartData:
    """The headline's history as the hero shows it: the same seats, as used or as left (falling, then jumping up
    at a weekly reset)."""
    span_s = RANGES.get(range_key, RANGES['24h'])[0]
    t1 = m.now.timestamp()
    t0 = t1 - span_s
    rows = [(s.t, m.shown(v), s.reserve) for s in m.history
            if (v := sample_value(s, m.headline_mode)) is not None and s.t <= t1 + 300]
    inside = [r for r in rows if r[0] >= t0]
    before = [r for r in rows if r[0] < t0][-1:]   # lets the line enter from the left edge
    pts = before + inside
    return ChartData(pts, len(inside), t0, max([t1] + [p[0] for p in pts]))


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# 5. Theme: semantic colours and fonts
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

def srgb(hex_rgb: int, alpha: float = 1.0):
    return NSColor.colorWithSRGBRed_green_blue_alpha_(((hex_rgb >> 16) & 255) / 255.0,
                                                      ((hex_rgb >> 8) & 255) / 255.0, (hex_rgb & 255) / 255.0,
                                                      alpha)


_DYNAMIC: dict = {}


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
    """The headline number's colour: green while a regular seat serves, red on the reserve, else grey."""
    return {'regular': C.green_text, 'reserve': C.red_text}.get(m.status, C.secondary)()


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
    number above them, else by threshold (grey when nothing serves). Seat bars keep their threshold colours."""
    return C.red() if m.status == 'reserve' else bar_fill(m.headline, m.degraded)


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


def draw_sparkline(rect, pts, t0, t1, grey: bool):
    """The headline (as used or as left) on a fixed 0-100 % scale across [t0, t1]: a 1.5 pt line over a soft
    gradient, green while a regular seat served and red while the reserve did (all grey when nothing is serving
    now). A dashed stub carries the last value on to 'now' (t1); a dot marks the latest sample."""
    (x, y), (w, h) = rect
    top, bottom = y + 2.5, y + h - 0.5
    span = max(1.0, t1 - t0)

    def px(t):
        return x + (t - t0) / span * w

    def py(v):
        return bottom - (v / 100.0) * (bottom - top)

    def colour(reserve):
        return C.grey() if grey else (C.red() if reserve else C.green())

    fill_rect(((x, y + h - 0.5), (w, 0.5)), C.separator())               # 0 %
    guide = NSBezierPath.bezierPath()                                     # 50 %
    guide.moveToPoint_((x, py(50)))
    guide.lineToPoint_((x + w, py(50)))
    guide.setLineWidth_(0.5)
    guide.setLineDash_count_phase_([2.0, 3.0], 2, 0)
    C.separator().setStroke()
    guide.stroke()

    line = NSBezierPath.bezierPath()
    for i, (t, v, _) in enumerate(pts):
        (line.moveToPoint_ if i == 0 else line.lineToPoint_)((px(t), py(v)))
    line.setLineWidth_(1.5)
    line.setLineJoinStyle_(NSLineJoinStyleRound)
    line.setLineCapStyle_(NSLineCapStyleRound)
    area = line.copy()
    area.lineToPoint_((px(pts[-1][0]), bottom))
    area.lineToPoint_((px(pts[0][0]), bottom))
    area.closePath()

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
        c = colour(reserve)
        NSGraphicsContext.saveGraphicsState()
        NSBezierPath.clipRect_(((lo, y - 2), (hi - lo, h + 3)))
        NSGradient.alloc().initWithStartingColor_endingColor_(
            c.colorWithAlphaComponent_(0.28), c.colorWithAlphaComponent_(0.0)).drawInBezierPath_angle_(area, 90)
        c.setStroke()
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
    """The meter, or a warning glyph in its place when nothing is serving. Drawn lazily, so its colours
    follow the menu bar's own light/dark appearance (NSImage re-draws per appearance)."""
    degraded = m.degraded

    def handler(rect):
        if degraded:
            draw_warning_glyph(0, 0)
        else:
            draw_meter(0, 0, m)
        return True
    img = NSImage.imageWithSize_flipped_drawingHandler_((METER_W, METER_H), True, handler)
    img.setTemplate_(False)
    return img


def menubar_title(m: Model):
    """' 53%' (left, or used) in green/red; the last known % in grey when degraded; '—' only without a usable
    status file."""
    color = C.secondary() if m.degraded else headline_text(m)
    return attributed(' ' + fmt_pct(m.shown(m.headline)), menubar_font(), color)


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
            None if serving is None else round(serving), m.serving.label if m.serving else '')


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
ROW_PAD, ROW_GAP = 4.0, 2.0      # seat rows: inner top/bottom padding, space between rows
SMALL_LH = 13.0                  # seat rows set their 11 pt lines on a tight 13 pt line


def fmt_day(t: dt.datetime | None) -> str:
    return t.astimezone().strftime('%b %-d') if t else ''


def seat_right_text(seat: Seat, now: dt.datetime) -> str:
    if seat.state in OUT_STATES + ('parked',):
        back = seat.until or (seat.week.reset_at if seat.week else None)
        return f'Back in {fmt_span((back - now).total_seconds())}' if back and back > now else 'Back soon'
    reset = seat.week.reset_at if seat.week else None
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
        size = f'{min(max(s.weight or 1.0, 0.0), 1e6):g}×'   # the weight, as weighted_used() counts it
        parts = [s.label, f'{size} reserve' if s.reserve else size, figure] + serving
        when = seat_right_text(s, m.now)   # 'Back in 2d 7h' for out and parked seats, else 'Resets in 6d 13h'
        if when:
            parts.append(when[0].lower() + when[1:])
        lines.append(' · '.join(parts))
    lines.append(f'Weighted by size: {fmt_pct(m.shown(m.headline))} {m.word} · {m.scope}')
    return '\n'.join(lines)


class PopoverLayout:
    """Builds the popover top to bottom. Each section method takes y and returns the y below it."""

    def __init__(self, m: Model, range_key: str = '24h', toast: str | None = None):
        self.m = m
        self.range_key = range_key if range_key in RANGES else '24h'
        self.toast = toast
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
        y = self.header(11.0)
        banner = m.status in BANNER_STATES
        hero = bool(m.seats) and m.headline is not None
        if banner or hero:
            y = self.rule(y)
            if banner:
                y = self.banner(y) + (16 if hero else 0)
            if hero:
                y = self.hero(y)
        if m.status not in ('missing', 'empty') or self.samples >= MIN_CHART_SAMPLES:
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
        m = self.m
        if m.status == 'regular':
            return 'Regular', C.green_text(), C.soft(C.green(), 0.16)
        if m.status == 'reserve':
            return 'Reserve', C.red_text(), C.soft(C.red(), 0.16)
        word = {'allout': 'All out', 'down': 'Down', 'stale': 'Stale', 'empty': 'No seats'}.get(m.status, 'No data')
        return word, C.secondary(), C.wash(0.08)

    def header(self, y: float) -> float:
        tf, sf = font(15, NSFontWeightSemibold), font(11)
        self.text('Codex Pool', PAD, y, tf, C.label())
        text, fg, bg = self.header_pill()
        pw = pill_width(text)
        self.add(draw_pill, text, WIDTH - PAD - pw, y + (line_height(tf) - PILL_H) / 2, fg, bg)
        y += line_height(tf) + 1
        self.text(self.subtitle(), PAD, y, sf, C.secondary(), width=INNER - pw - 8)
        return y + line_height(sf)

    # -- 2. problem banner (down / not reporting / no seats) -----------------------------------------
    def banner_copy(self):
        """(title, body, (button title, action))."""
        m = self.m
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
        down = self.m.status == 'down'
        title, body, (button, action) = self.banner_copy()
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
        bw = button_width(button)
        self.region(((x, by), (bw, BUTTON_H)), ('action', action), radius=BUTTON_H / 2,
                    tint=C.soft(C.orange(), 0.16) if down else C.wash(0.08))
        bfont = button_font()
        self.text(button, x, by + (BUTTON_H - line_height(bfont)) / 2, bfont,
                  C.orange_text() if down else C.label(), width=bw, align='center')
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
        parts = [f'{m.regular_ready} of {m.regular_total} regular seats ready'] if m.regular_total else []
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
            lines.append((' · '.join(parts), font(12), C.label()))
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
        chart_h, af = 44.0, font(10)
        if data.in_range < MIN_CHART_SAMPLES:   # an empty chart of the same size, so nothing jumps later
            self.add(fill_rect, ((PAD, y + chart_h - 0.5), (INNER, 0.5)), C.separator())
            f = font(11)
            self.text('Collecting history…', PAD, y + (chart_h - line_height(f)) / 2, f, C.secondary(),
                      width=INNER, align='center')
        else:
            self.add(draw_sparkline, ((PAD, y), (INNER, chart_h)), data.pts, data.t0, data.t1, not m.serving_now)
        y += chart_h + 5
        self.text(f'{RANGES[self.range_key][1]} ago', PAD, y, af, C.secondary())
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
        self.section_title('Seats', y, line_height(hf))
        self.text('Priority order', PAD, y + hf.ascender() - nf.ascender(), nf, C.secondary(), width=INNER,
                  align='right')
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
            fg, bg = (C.red_text(), C.soft(C.red())) if seat.reserve else (C.green_text(), C.soft(C.green()))
        elif seat.state == 'parked':
            fg, bg = C.orange_text(), C.soft(C.orange())
        else:
            fg, bg = C.red_text(), C.soft(C.red())
        w = pill_width(text)
        self.add(draw_pill, text, WIDTH - PAD - w, mid - PILL_H / 2, fg, bg)
        return w

    def seat_row(self, seat: Seat, y: float) -> float:
        """Line 1: name, plan (and reserve) in small text, state at the right. Then the weekly bar (Team seats
        add a thin 5-hour bar under it), then '49% used' / 'Resets in 6d 12h'. Blocked seats get a detail line.
        Returns the y below the row."""
        m = self.m
        stale = not m.reporting
        dim = stale or seat.unavailable
        name_f, small_f = font(13, NSFontWeightSemibold), font(11)
        lh, sh = line_height(name_f), SMALL_LH
        bars = [(seat.week, 5.0)] + ([(seat.short, 3.0)] if seat.short else [])
        bars_h = sum(h for _, h in bars) + 3.0 * (len(bars) - 1)
        blocked = seat.state == 'blocked'
        detail = (seat.detail or 'Needs attention') if blocked else ''
        row_h = ROW_PAD + lh + 4 + bars_h + 4 + sh + (sh + 1 if detail else 0) + ROW_PAD
        key = ('seat', seat.name or seat.label)
        reset_tip = ''
        if seat.resets:
            exp = f', soonest expires {fmt_day(seat.reset_expiry)}' if seat.reset_expiry else ''
            reset_tip = f'{seat.resets} banked reset{"s" if seat.resets != 1 else ""}{exp}. Click to use one.'
        self.region(((PAD - 8, y), (INNER + 16, row_h)), key, radius=9,
                    tint=C.row_tint() if seat.serving and m.serving_now else None,
                    tip='  '.join(t for t in (detail, reset_tip) if t) or None)

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

        # bars
        by = top + lh + 4
        for win, h in bars:
            used = win.used if win else None
            self.add(draw_bar, PAD, by, INNER, h, m.shown(used), bar_fill(used, dim))
            by += h + 3
        y3 = top + lh + 4 + bars_h + 4

        # line 3
        lw = self.usage_text(seat, PAD, y3, small_f, dim, stale)
        if blocked:
            f = font(11, NSFontWeightMedium)
            self.text('Re-login needed', PAD + lw + 12, y3, f, C.secondary() if stale else C.red_text(),
                      width=INNER - lw - 12, align='right')
            dy = y3 + sh + 1
            self.add(draw_symbol, 'exclamationmark.circle.fill', PAD + 5, dy + sh / 2, 10,
                     C.secondary() if stale else C.red(), NSFontWeightRegular, (10, 10))
            self.text(detail, PAD + 14, dy, small_f, C.secondary(), width=INNER - 14, truncate='middle')
        else:
            right = seat_right_text(seat, m.now)
            if seat.resets and seat.unavailable and not stale and seat.state != 'disabled':
                right = f'{right} · reset available' if right else 'Reset available'
            self.text(right, PAD + lw + 12, y3, small_f, C.secondary(), width=INNER - lw - 12, align='right')
        return y + row_h

    def usage_text(self, seat: Seat, x: float, y: float, f, dim: bool, stale: bool) -> float:
        """'49% used' / '51% left', or for Team seats 'Week 64% · 5h 100%' / 'Week 36% left · 5h 0% left' with the
        window that binds in the label colour. Returns the width drawn."""
        m = self.m
        week = seat.week.used if seat.week else None
        if not seat.short:
            text = f'{fmt_pct(m.shown(week))} {m.word}'
            self.text(text, x, y, f, C.secondary() if dim else C.label())
            return text_width(text, f)
        short = seat.short.used
        binding = 'short' if (short or 0) > (week or 0) else 'week'
        tail = ' left' if m.left else ''
        x0 = x
        for i, (which, text, pct) in enumerate((('week', f'Week {fmt_pct(m.shown(week))}{tail}', week),
                                                 ('short', f'5h {fmt_pct(m.shown(short))}{tail}', short))):
            if i:
                self.text(' · ', x, y, f, C.secondary())
                x += text_width(' · ', f)
            strong = which == binding and not stale and (not dim or (pct or 0) >= 99.5)
            self.text(text, x, y, f, C.label() if strong else C.secondary())
            x += text_width(text, f)
        return x - x0

    # -- 6. footer -----------------------------------------------------------------------------------
    def footer(self, y: float) -> float:
        f = font(13)
        row_h = 22.0
        for symbol, title, action in FOOTER_ROWS:
            y = self.footer_row(y, row_h, symbol, title, action, f)
        y = self.rule(y, 5, 5)
        for symbol, title, action, hint in APP_ROWS:
            y = self.footer_row(y, row_h, symbol, title, action, f, hint)
        y = self.footer_row(y, row_h, None, 'Quit', 'quit', f)
        version = self.m.version.split('-gate')[0].split('+gate')[0]
        if version:
            vf = font(10.5)
            self.text(f'CLIProxyAPI {version}', PAD, y - row_h + (row_h - line_height(vf)) / 2, vf, C.tertiary(),
                      width=INNER - 2, align='right')
        return y

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
        self.set_hover(key if key and key[0] != 'range' else None)

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


def open_settings(pane: str | None = None) -> bool:
    """Starts the Settings window (codexpool_settings.py) on `pane`, without blocking. It runs with this app's own
    interpreter, which has PyObjC (codexpool's own Python may not), given explicitly as for every action: never
    through PATH. A Settings window that is already open comes forward and shows the pane (it is single instance).
    Its output goes to logs/settings.log, as with `codexpool gui`, so a traceback is never lost."""
    if not SETTINGS_SCRIPT.exists():
        return False
    argv = [sys.executable, str(SETTINGS_SCRIPT)] + (['--pane', pane] if pane else [])
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


def run_codexpool(args: list[str], on_done=None):
    """Runs `codexpool <args>` without blocking. on_done(returncode, last stderr line) runs on the main thread
    when it exits. codexpool never prints tokens; its errors go to stderr."""
    try:
        p = subprocess.Popen([*codexpool_argv(), *args], cwd=str(POOL_DIR), stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, start_new_session=True)
    except OSError as e:
        if on_done:
            on_done(127, e.strerror or str(e))
        return

    def wait():
        _, err = p.communicate()
        lines = [ln.strip() for ln in err.decode('utf-8', 'replace').splitlines() if ln.strip()]
        if on_done:
            AppHelper.callAfter(on_done, p.returncode, lines[-1] if lines else '')
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
        self.model: Model | None = None
        self.range_key = '24h'
        self.toast: tuple[str, float] | None = None
        self.item = None
        self.popover = None
        self.content = None
        self.bar_signature = None
        self.last_render = 0.0
        self.woke_at = -1e9          # time.monotonic() of the last wake from sleep
        self.first_run_checked = False   # the first-run Setup assistant check has been made
        self.closed_at = -1e9        # time.monotonic() when the popover last started closing
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
    def current_model(self) -> Model:
        return self.source.model(wake_grace=time.monotonic() - self.woke_at < WAKE_GRACE_S)

    @objc.python_method
    def refresh(self, force: bool = False):
        self.source.poll(force=force)
        self.apply(self.current_model())

    def tick_(self, timer):
        changed = self.source.poll()
        model = self.current_model()
        crossed = self.model is None or model.status != self.model.status  # e.g. went stale without a write
        toast_expired = self.toast is not None and time.time() > self.toast[1]
        if toast_expired:
            self.toast = None
        due = self.popover.isShown() and time.time() - self.last_render >= COUNTDOWN_EVERY_S
        if changed or crossed or toast_expired or due:
            self.apply(model)

    def didWake_(self, note):
        """After sleep status.json is old until the guard's next pass: hold off 'not reporting' for a bit."""
        self.woke_at = time.monotonic()
        NSTimer.scheduledTimerWithTimeInterval_target_selector_userInfo_repeats_(WAKE_REPOLL_S, self,
                                                                                 'afterWake:', None, False)

    def afterWake_(self, timer):
        self.refresh(force=True)

    @objc.python_method
    def apply(self, model: Model):
        self.model = model
        self.first_run(model)
        sig = menubar_signature(model)
        if sig != self.bar_signature:
            self.bar_signature = sig
            button = self.item.button()
            button.setImage_(menubar_image(model))
            button.setAttributedTitle_(menubar_title(model))
            button.setToolTip_(fresh(menubar_tooltip(model)))
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
        model, range_key = self.model, self.range_key
        self.content.show(lambda: PopoverLayout(model, range_key, toast).build(), self.max_height(), reset_scroll)
        self.popover.setContentSize_((WIDTH, self.content.height()))
        self.last_render = time.time()

    @objc.python_method
    def say(self, text: str):
        self.toast = (text, time.time() + TOAST_S)
        if self.popover.isShown():
            self.render()

    def togglePopover_(self, sender):
        if self.popover.isShown():
            self.popover.performClose_(sender)
            return
        if time.monotonic() - self.closed_at < REOPEN_GUARD_S:
            return   # this is the click that just closed the (transient) popover
        self.refresh()                   # also brings the menu bar item up to date
        self.content.set_hover(None)     # no mouseExited arrives when the popover closes
        self.render(reset_scroll=True)
        button = self.item.button()
        self.popover.showRelativeToRect_ofView_preferredEdge_(button.bounds(), button, NSRectEdgeMinY)
        activate_app()

    def popoverWillClose_(self, note):
        self.closed_at = time.monotonic()

    # -- clicks ----------------------------------------------------------------------------------------
    @objc.python_method
    def handle_region(self, key, view, point):
        kind, value = key
        if kind == 'range':
            self.range_key = value
            self.render()
        elif kind == 'seat':
            seat = next((s for s in self.model.seats if (s.name or s.label) == value), None)
            if seat:
                self.seat_menu(seat).popUpMenuPositioningItem_atLocation_inView_(None, point, view)
        elif kind == 'action':
            self.run_action(value)

    @objc.python_method
    def run_action(self, action: str):
        terminal = {'status': cp_command('status', '--live'), 'doctor': cp_command('doctor'),
                    'log': cp_command('logs', '-f'), 'addseat': cp_command('login')}
        if action in ('settings', 'addaccount'):
            self.popover.performClose_(None)
            if not open_settings(None if action == 'settings' else 'setup-accounts'):
                if action == 'addaccount':   # no Settings window installed: the Terminal sign-in, as before
                    open_in_terminal(terminal['addseat'])
                else:
                    self.say('Settings aren\u2019t installed; run codexpool install')
        elif action in terminal:
            self.popover.performClose_(None)
            open_in_terminal(terminal[action])
        elif action == 'docs':
            self.popover.performClose_(None)
            spawn(['/usr/bin/open', str(DOCS) if DOCS.exists() else DOCS_URL])
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

        def add(title, symbol, verb, enabled=True, extra=''):
            item = menu.addItemWithTitle_action_keyEquivalent_(title, 'seatAction:', '')
            item.setTarget_(self)
            item.setEnabled_(enabled and bool(seat.name))
            item.setRepresentedObject_([verb, fresh(seat.name), fresh(seat.label), extra, priority])
            img = NSImage.imageWithSystemSymbolName_accessibilityDescription_(symbol, None)
            if img is not None:
                item.setImage_(img)

        if seat.resets:   # a banked free reset brings the seat back to full right now
            exp = f', expires {fmt_day(seat.reset_expiry)}' if seat.reset_expiry else ''
            add(f'Use reset now… ({seat.resets} banked{exp})', 'arrow.counterclockwise.circle', 'reset')
        if seat.state == 'blocked':   # the fix comes first
            add('Re-login…', 'person.badge.key', 'login')
        if seat.state == 'parked':
            add('Enable (spends credits)…', 'play.circle', 'enable-parked')
        elif seat.state == 'disabled':
            add('Enable', 'play.circle', 'enable')
        else:
            add('Disable', 'pause.circle', 'disable')
        first = seat.priority is not None and seat.priority >= top and \
            sum(1 for s in self.model.seats if s.priority == top) == 1
        # The reserve stays last: moving it first would serve it before the regular seats.
        add('Make first', 'arrow.up.to.line', 'first', enabled=not first and not seat.reserve,
            extra=f'{int(top) + 10}')
        if seat.state != 'blocked':
            add('Re-login…', 'person.badge.key', 'login')
        return menu

    def seatAction_(self, item):
        verb, name, label, extra, priority = [str(x) for x in item.representedObject()]
        seat = next((s for s in (self.model.seats if self.model else []) if s.name == name), None)
        if verb == 'login':
            # --no-open: the sign-in URL is printed, to open in a private window signed in to the right account
            # (the default browser is usually signed in to another one). --priority: a re-login rewrites the
            # seat file, which holds the priority, so pass the current one back.
            self.popover.performClose_(None)
            args = ['login', label, '--no-open'] + (['--priority', priority] if priority else [])
            who = seat.email if seat and seat.email else label
            open_in_terminal(cp_command(*args), intro=(
                f'Re-login {label}: open the sign-in URL below in a private browser window',
                f'and sign in as {who}.'))
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
        run_codexpool(args, lambda code, err: self.after_action(done, args[0], code, err))

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
             now: dt.datetime | None, range_key: str, max_height: float | None = None, hover: tuple | None = None):
    NSApplication.sharedApplication().setActivationPolicy_(NSApplicationActivationPolicyProhibited)
    dark = appearance_name == 'dark'
    appearance = NSAppearance.appearanceNamed_(NSAppearanceNameDarkAqua if dark else NSAppearanceNameAqua)
    source = DataSource(status_path, history_path)
    source.poll(force=True)
    model = source.model(now)

    # Popover: the real PopoverContent, cached offscreen at 2x, then set on an opaque stand-in for the
    # popover material (vibrancy needs a window).
    content = PopoverContent.alloc().initWithFrame_(((0, 0), (WIDTH, 400)))
    content.setAppearance_(appearance)
    content.show(lambda: PopoverLayout(model, range_key).build(), max_height)
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

    # Menu bar item at 2x on a menu-bar-like strip: the item's fixed-width box, content left-aligned,
    # then the clock for scale.
    image, title = menubar_image(model), menubar_title(model)
    item_w = menubar_length()
    clock = attributed('Sat 26 Sep  1:07', font(NSFont.menuBarFontOfSize_(0).pointSize()), C.label())
    x0, gap = 10.0, 10.0
    bw, bh = x0 + item_w + gap + math.ceil(clock.size().width) + 12, 24.0

    def compose_bar():
        fill_rect(((0, 0), (bw, bh)), rgb(0.13, 0.13, 0.15) if dark else rgb(0.93, 0.93, 0.95))
        image.drawInRect_fromRect_operation_fraction_respectFlipped_hints_(
            ((x0, (bh - METER_H) / 2), (METER_W, METER_H)), ((0, 0), (0, 0)), NSCompositingOperationSourceOver,
            1.0, True, None)
        title.drawAtPoint_((x0 + METER_W + 1, (bh - title.size().height) / 2))
        clock.drawAtPoint_((x0 + item_w + gap, (bh - clock.size().height) / 2))

    base = out[:-4] if out.lower().endswith('.png') else out
    write_png(render_offscreen(compose_bar, bw, bh, appearance), f'{base}-menubar.png')
    print(out)
    print(f'{base}-menubar.png')


def run_app():
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
    args = p.parse_args(argv)
    if not args.snapshot:
        run_app()
        return
    history = args.history
    if history is None:
        sibling = args.status.parent / 'history.jsonl'
        history = sibling if args.status != STATUS_FILE and sibling.exists() else HISTORY_FILE
    now = parse_time(args.now) if args.now else None
    if args.now and now is None:
        p.error(f'--now: not an ISO-8601 time: {args.now}')
    hover = tuple(args.hover.split(':', 1)) if args.hover and ':' in args.hover else None
    snapshot(args.snapshot, args.appearance, args.status, history, now, args.range_key, args.max_height, hover)


if __name__ == '__main__':
    main()
