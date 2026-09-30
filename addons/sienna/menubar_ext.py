"""Sienna's menu bar and Settings extension: the Claude pool as the core apps' second pool.

The core menu bar app (menubar/codexpool_menubar.py) and Settings window (menubar/codexpool_settings.py) keep the
generic two-pool plumbing: a second DataSource, the strip with both numbers, the switcher tiles, the pool switcher
and the `pool` parameter on their model builders. Everything that is Claude by name or by content lives here, behind
one PoolUI object: the status file, the colours and the mark, the words (accounts, Anthropic, claude.ai), the
credits (usage credits and their policy, the last resort, the guard's mismatch warning), the Claude Code route, the
pooled desktop app, the install card and the sign-in flow of the Setup assistant, and the read-only Claude lane's
copy in the Lanes pane.

`load(mb) -> PoolUI` is called by the menu bar module once it is fully defined (PyObjC is up); the Settings module
calls `PoolUI.settings_loaded(st)` after its own definitions. The core reads the members it needs from the object
and never a Claude literal. Tests load this module through mb.POOL_UI['claude'].module.

Everything below `parse_desktop` up to `PoolUI` is pure over the fields (the add-on's tests load it without AppKit).
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path

mb = None    # the menu bar module (load)
st = None    # the Settings module (settings_loaded)

POOL = 'claude'
POOL_DIR = Path.home() / '.codexpool'
# The Claude pool (a second CLIProxyAPI instance for Claude Code). Installed means claude-status.json exists and does
# not say "installed": false; without it the item and the popover are the Codex pool's alone.
STATUS_FILE = POOL_DIR / 'state' / 'claude-status.json'
HISTORY_FILE = POOL_DIR / 'state' / 'claude-history.jsonl'
DOCS = POOL_DIR / 'addons' / 'sienna' / 'docs' / 'SIENNA.md'
DOCS_URL = 'https://github.com/memfactorduke/codex-load-balancer/blob/main/addons/sienna/docs/SIENNA.md'
USAGE_URL = 'https://claude.ai/settings/usage'   # opened in the browser from a seat's menu, never fetched
LAUNCHER = 'claude-pool'      # starts Claude Code through the pool (written by `codexpool claude install`)

SCOPE = {'all': 'all accounts', 'regular': 'regular accounts'}   # the Claude pool calls its seats accounts
SIGN_IN_ENDED = 'Anthropic ended this sign-in'
# Claude plans ("pro", "max_5x", "max_20x", "team", "team_premium", "claude_max", ...); the size is the weight.
PLAN_NAMES = (('enterprise', 'Enterprise'), ('team_premium', 'Team Premium'), ('team', 'Team'),
              ('max', 'Max'), ('pro', 'Pro'), ('free', 'Free'))
CORAL = (0xAA5034, 0xEE8E6F, 0xC15F3C, 0xEE8E6F)   # text light, text dark, fill light, fill dark
MARK = ('com.anthropic.claudefordesktop', ('TrayIconTemplate@3x.png', 'TrayIconTemplate@2x.png', 'TrayIconTemplate.png'))
MARK_SCALE = 1.1   # the spark (thin, even thickened) overhangs the glyph box by 0.55 pt a side (see mb.MARK_SCALE)
ROUTES = ('pool', 'direct')          # claude-status.json pool.route: where new Claude Code sessions go
STALE_AFTER_S = 180


@dataclass
class Scoped:
    """A Claude weekly cap that covers one model family (the Fable weekly limit)."""
    name: str
    used: float | None
    reset_at: dt.datetime | None


@dataclass
class Credits:
    """A Claude account's usage credits (extra usage) and codexpool's policy for them."""
    enabled: bool                # turned on for the account at claude.ai
    used: float | None           # spent this month, in `currency`
    limit: float | None          # the account's monthly limit
    policy: str = 'off'          # off (park at the plan limit) | last-resort (spend only when every account is out)
    cap: float | None = None     # last-resort: codexpool parks it once `used` reaches this
    currency: str = 'USD'
    spending: bool | None = None  # the guard says it is on credits right now (None: an older guard didn't say)
    mismatch: bool = False       # the guard: policy off, yet claude.ai says credits are on (see credits_mismatch)

    @property
    def last_resort(self) -> bool:
        return self.policy == 'last-resort'


def parse_credits(d) -> Credits | None:
    """claude-status.json's credits{enabled, used, limit, policy, cap[, currency, spending, mismatch]}. A file from
    an older guard has no `mismatch`: then there is none to warn about."""
    d = mb.as_dict(d)
    if not d:
        return None
    policy = mb.as_str(d.get('policy')).lower().replace('_', '-')
    return Credits(enabled=d.get('enabled') is True, used=mb.as_num(d.get('used')), limit=mb.as_num(d.get('limit')),
                   policy=policy if policy in ('off', 'last-resort') else 'off', cap=mb.as_num(d.get('cap')),
                   currency=mb.as_str(d.get('currency'), 'USD') or 'USD',
                   spending=d['spending'] is True if isinstance(d.get('spending'), bool) else None,
                   mismatch=d.get('mismatch') is True)


def parse_scoped(d) -> list:
    out = []
    for x in mb.as_list(d.get('scoped')):
        x = mb.as_dict(x)
        name = mb.as_str(x.get('name')) or \
            mb.as_str(mb.as_dict(mb.as_dict(x.get('scope')).get('model')).get('display_name'))
        used = mb.clamp_pct(mb.as_num(x.get('used')))
        if name and used is not None:
            out.append(Scoped(name, used, mb.parse_time(x.get('reset_at'))))
    return out


def credits(seat):
    """The account's Credits (the core seat's `extra`), or None."""
    return getattr(seat, 'extra', None)


# -- the pooled Claude desktop app: claude-status.json's pool.desktop (addons/sienna/docs/DESKTOP.md) ------------------------
#
# The guard's Claude pass writes the block every minute and `codexpool claude desktop …` patches it at once. Two
# things are kept apart, and the control never confuses them: configured_mode is what the app's files say (the
# mode the app opens in next time), running_mode is evidence from the running process's own log (never inferred
# from the files). "On the pool" appears only with running_mode pooled. A file from a guard without the block
# has no Desktop, and the control is not drawn; nor is it when the Claude app is not on this Mac (the backend's
# "Claude app not found" error). Everything below parse_desktop is a pure function over the fields, so the tests
# load it without AppKit.

DESKTOP_MODES = ('pooled', 'claudeai', 'other', 'none', 'unknown')
DESKTOP_RUNNING = ('pooled', '3p', 'other', 'claudeai', 'fallback', 'unknown')
DESKTOP_LABELS = (('Pooled', 'pooled'), ('Claude.ai', 'claudeai'))   # the two segments: (label, value)
DESKTOP_APP_MISSING = 'Claude app not found'                          # the backend's error when the app is not installed


@dataclass
class Desktop:
    configured_mode: str = 'unknown'      # pooled | claudeai | other | none | unknown (the files)
    chooser_disabled: bool = False        # the applied entry hides the Claude.ai sign-in (only with other)
    applied_name: str = ''                # the applied configuration's name ("Pool" is codexpool's)
    running_mode: str | None = None       # pooled | 3p | other | claudeai | fallback | unknown; None: not running
    running_host: str = ''
    app_running: bool = False
    app_started_at: str = ''
    app_bundle_ok: bool = True            # False: a process named Claude runs from elsewhere (running_* unknown)
    restart_required: bool = False        # the running app started before the last change
    owned_drift: bool = False             # the "Pool" entry's owned fields were edited in the app
    current: bool = False                 # the "Pool" entry is what codexpool would write today
    port_ok: bool = False                 # ... and points at the Claude pool's port
    credits_ok: bool | None = None        # every account that can serve has credits off (or a capped last resort)
    credits_problems: list = field(default_factory=list)   # the backend's reasons, one line each
    accepted_credits: list = field(default_factory=list)   # [{label, cap}]: capped last-resort paid use accepted
    txn_pending: bool = False             # a change was interrupted (codexpool claude desktop rollback)
    txn_classes: dict | None = None
    pool_seen_desktop_at: str = ''        # the pool admitted a request from this app process
    app_version: str = ''
    base_url: str = ''
    old_3p_logs: str = ''
    checked_at: str = ''
    last_backup: str = ''
    import_on: bool = False               # the one-time claude.ai history import is unlocked in the pooled app
    errors: list = field(default_factory=list)


@dataclass
class DesktopView:
    """What the control shows: the selected segment (None: neither), whether it can be used, the caption, an
    action (doctor | reopen | setup) and extra lines, each (text, warning)."""
    selected: str | None
    enabled: bool
    caption: str
    action: str | None = None
    lines: list = field(default_factory=list)


@dataclass
class PoolExtra:
    """What claude-status.json's pool block says beyond the core's fields (the core Model's `extra`)."""
    route: str = ''              # pool | direct (pool.route: where new Claude Code sessions go)
    client: str = ''             # the Claude Code version (pool.claude_code)
    desktop: Desktop | None = None   # the pooled desktop app (pool.desktop); None from an older guard


def money(v, currency: str = 'USD') -> str:
    """'$31', '$112.40' (cents only when there are any); other currencies as 'EUR 31' (the core's money(), pure
    here so the tests load the desktop helpers without AppKit)."""
    if v is None:
        return '—'
    text = f'{v:,.0f}' if abs(v - round(v)) < 0.005 else f'{v:,.2f}'
    return f'${text}' if currency.upper() == 'USD' else f'{currency.upper()} {text}'


def parse_desktop(d) -> Desktop | None:
    """pool.desktop as the guard writes it; None without the block (an older guard). Never raises on odd values."""
    if not isinstance(d, dict):
        return None

    def text(key):
        v = d.get(key)
        return v if isinstance(v, str) else ''

    def flag(key, default=False):
        v = d.get(key)
        return v if isinstance(v, bool) else default

    mode = text('configured_mode')
    running = d.get('running_mode')
    if running is not None:
        running = running if isinstance(running, str) and running in DESKTOP_RUNNING else 'unknown'
    credits_ok = d.get('credits_ok')
    owned = d.get('owned') if isinstance(d.get('owned'), dict) else {}
    imp = owned.get('claudeAiImport') if isinstance(owned.get('claudeAiImport'), dict) else {}
    accepted = [{'label': a.get('label'), 'cap': a.get('cap')} for a in (d.get('accepted_credits') or [])
                if isinstance(a, dict) and isinstance(a.get('label'), str)]
    return Desktop(configured_mode=mode if mode in DESKTOP_MODES else 'unknown',
                   chooser_disabled=flag('chooser_disabled'), applied_name=text('applied_name'),
                   running_mode=running, running_host=text('running_host'), app_running=flag('app_running'),
                   app_started_at=text('app_started_at'), app_bundle_ok=flag('app_bundle_ok', True),
                   restart_required=flag('restart_required'), owned_drift=flag('owned_drift'),
                   current=flag('current'), port_ok=flag('port_ok'),
                   credits_ok=credits_ok if isinstance(credits_ok, bool) else None,
                   credits_problems=[p for p in (d.get('credits_problems') or []) if isinstance(p, str)],
                   accepted_credits=accepted, txn_pending=flag('txn_pending'),
                   txn_classes=d.get('txn_classes') if isinstance(d.get('txn_classes'), dict) else None,
                   pool_seen_desktop_at=text('pool_seen_desktop_at'), app_version=text('app_version'),
                   base_url=text('base_url'), old_3p_logs=text('old_3p_logs'), checked_at=text('checked_at'),
                   last_backup=text('last_backup'), import_on=imp.get('enabled') is True,
                   errors=[e for e in (d.get('errors') or []) if isinstance(e, str)])


def desktop_shown(d: Desktop | None) -> bool:
    """The control is drawn only with the block (a guard that writes it) and the Claude app on this Mac."""
    return d is not None and not any(DESKTOP_APP_MISSING in e for e in d.errors)


def desktop_credit_labels(problems: list) -> tuple:
    """(accounts with credits on at claude.ai, accounts without a fresh reading) from the backend's lines."""
    on, stale = [], []
    for p in problems:
        if p.startswith('credits on at claude.ai for '):
            on.append(p[len('credits on at claude.ai for '):].split(':', 1)[0])
        elif p.startswith('no fresh credits reading for '):
            stale.append(p[len('no fresh credits reading for '):].split(':', 1)[0])
    return on, stale


def join_labels(labels: list) -> str:
    return ', '.join(labels[:-1]) + ' and ' + labels[-1] if len(labels) > 1 else ''.join(labels)


def desktop_credit_lines(d: Desktop) -> list:
    """The credit warnings under a pooled desktop, each (text, warning): credits on at claude.ai (the pooled app
    can spend them, and a proxy can't stop every paid request), no fresh reading, and paid use the owner accepted
    with a cap."""
    lines = []
    if d.credits_ok is False:
        on, stale = desktop_credit_labels(d.credits_problems)
        if on:
            lines.append((f'Credits on at claude.ai for {join_labels(on)} · the pooled app can spend them', True))
        elif stale:
            lines.append((f'No fresh credits reading for {join_labels(stale)} · wait for the guard’s next poll',
                          True))
        elif d.credits_problems:
            lines.append(('No account can serve the desktop app', True))
    for a in d.accepted_credits:
        cap = a.get('cap')
        capped = f', up to {money(cap)}' if isinstance(cap, (int, float)) and not isinstance(cap, bool) else ''
        lines.append((f'Paid use accepted on {a["label"]}{capped} · the pooled app can spend credits', False))
    return lines


def desktop_view(d: Desktop, busy: str | None = None) -> DesktopView:
    """The control's state from the block, honest about configured vs running (the table in DESKTOP_3P.md §7.1).
    busy: what the app is doing right now (a command runs): the control is greyed and the caption is that."""
    mode = d.configured_mode
    selected = mode if mode in ('pooled', 'claudeai') else 'claudeai' if mode == 'none' else None
    if busy:
        return DesktopView(selected, False, busy)
    if d.txn_pending:
        return DesktopView(selected, False, 'A change was interrupted', 'doctor')
    if mode == 'unknown':
        return DesktopView(None, False, 'Can’t read the app’s settings', 'doctor')
    if mode == 'none':
        return DesktopView('claudeai', True, 'Not set up', 'setup')
    if mode == 'other':
        if d.chooser_disabled:
            return DesktopView(None, True, 'Another configuration is applied in the app · it hides the Claude.ai '
                                           'sign-in', 'doctor')
        return DesktopView(None, True, 'Another configuration is applied in the app')
    pooled = mode == 'pooled'
    lines, action = [], None
    if pooled:
        lines = desktop_credit_lines(d)
        if d.owned_drift:
            lines.append(('The “Pool” configuration was edited in the app', False))
        elif not d.port_ok:
            lines.append(('The “Pool” configuration points at another port', False))
        elif not d.current:
            lines.append(('The “Pool” configuration is out of date', False))
    where = 'the pool' if pooled else 'Claude.ai'
    rm = d.running_mode
    if not d.app_running:
        caption = 'Configured for the pool · opens on the pool next time' if pooled else 'Configured for Claude.ai'
    elif not d.app_bundle_ok:
        caption, action = 'A process named Claude runs from another path', 'doctor'
    elif d.restart_required:
        caption, action = f'Configured for {where} · reopen Claude to switch', 'reopen'
    elif rm == 'fallback':
        caption, action = 'Claude fell back to standard mode', 'doctor'
    elif pooled:
        if rm == 'pooled':
            caption = 'On the pool · Chat, Cowork and Code · history in Claude-3p' + \
                (' · answering' if d.pool_seen_desktop_at else '')
        elif rm == '3p':
            caption = 'In pooled mode · the pool’s address is confirmed on the first request'
        elif rm == 'other':
            caption, action = 'Running on another address, not the pool', 'doctor'
        else:
            caption = 'Configured for the pool · nothing seen from the app yet'
    elif rm == 'claudeai':
        caption = 'On its own claude.ai account'
    elif rm in ('pooled', '3p', 'other'):
        caption, action = 'Configured for Claude.ai · the running app is still in pooled mode', 'reopen'
    else:
        caption = 'Configured for Claude.ai · nothing seen from the app yet'
    if action is None and pooled and (d.owned_drift or not d.port_ok or not d.current):
        action = 'doctor'
    return DesktopView(selected, True, caption, action, lines)


def desktop_refusal(d: Desktop, target: str, pool_down: bool = False) -> str:
    """Why `codexpool claude desktop <target>` would refuse, in its own words, before Claude is quit for nothing
    ('' when nothing known stands in the way; the command checks again, and its ✗ line shows if it refuses). The
    GUI never passes --reclaim: that is a deliberate terminal choice, and the line names it."""
    if d.txn_pending:
        return ('A change was interrupted before it finished. Run Doctor; codexpool claude desktop rollback '
                'recovers it.')
    if target == 'pooled':
        if d.credits_ok is False and d.credits_problems:
            return '\n'.join(p[:1].upper() + p[1:] for p in d.credits_problems)
        if d.owned_drift:
            return ('The “Pool” configuration was edited in the app. In Terminal, codexpool claude desktop '
                    'pooled --reclaim overwrites the edits after a backup; or leave it as it is.')
        if pool_down:
            return 'The Claude pool is down: the app would open on a pool that can’t answer. Run Doctor.'
    elif target == 'claudeai' and d.chooser_disabled:
        return (f'“{d.applied_name or "The applied configuration"}” hides the Claude.ai sign-in: the app '
                'opens in pooled mode on it whatever the mode file says. Apply another configuration or delete it '
                'in Developer → Configure Third-Party Inference…')
    return ''


def desktop_confirm(target: str, d: Desktop) -> tuple:
    """(title, body, button) of the confirm before a switch or a reopen. Claude quits and opens again, so the body
    says so (or just opens, when it is not running); the switch's body says what moves and what stays."""
    running = d.app_running
    if target == 'pooled':
        opens = 'Claude quits and opens again on the pool.' if running else 'Claude opens on the pool.'
        return ('Switch the desktop app to the pool?',
                f'{opens} Chat, local Cowork and Code then use the pool’s accounts, switching accounts between '
                'requests. Mobile and web sync, cloud Cowork, Remote Control and Claude in Chrome stay with the '
                'Claude.ai mode.' + (' Finish what you are doing in Claude first.' if running else ''), 'Switch')
    if target == 'claudeai':
        opens = 'Claude quits and opens again signed in to its own account.' if running else \
            'Claude opens signed in to its own account.'
        return ('Go back to Claude.ai?',
                f'{opens} Pooled conversations stay in the pool history. Sign-ins the pooled app made (MCP servers, '
                'the import) stay stored there until you use the app’s own “Go back to Claude.ai”. '
                'Switch back any time.', 'Go Back')
    if target == 'import':
        opens = 'Claude quits and opens again' if running else 'Claude opens'
        return ('Import claude.ai history?',
                f'codexpool unlocks the import in the “Pool” configuration, and {opens} on the pool. Then, '
                'in Claude, Settings → Import & export → Import… brings your claude.ai chats and projects over once. '
                'The wizard signs in to claude.ai on its own and stores that sign-in in the pooled profile; the '
                'app’s “Go back to Claude.ai” clears it.', 'Import')
    return ('Reopen Claude now?', 'Claude quits and opens again in the configured mode. Finish what you are doing '
                                  'in Claude first.', 'Reopen')


def desktop_command(target: str) -> list:
    """The command behind a confirmed choice: always --relaunch (codexpool quits and reopens Claude itself) and
    --yes (the confirm was this GUI's); never --reclaim or a credits override."""
    if target == 'reopen':
        return ['claude', 'desktop', 'relaunch', '--yes']
    if target == 'import':
        return ['claude', 'desktop', 'pooled', '--import', '--relaunch', '--yes']
    return ['claude', 'desktop', target, '--relaunch', '--yes']


def desktop_busy_text(target: str) -> str:
    return {'pooled': 'Switching Claude to the pool…', 'claudeai': 'Sending Claude back to Claude.ai…',
            'import': 'Unlocking the import and reopening Claude…'}.get(target, 'Reopening Claude…')


def desktop_closing(out: str, code: int, err: str) -> str:
    """What to say when the command ends: its closing line ('Claude opened on the pool'), else its ✗ line."""
    if code == 0:
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        return (lines[-1] if lines else 'Done').rstrip('.')
    line = (err or f'codexpool claude desktop failed (exit {code})').strip()
    return line[2:] if line.startswith('✗ ') else line


def desktop_tip_suffix(d: Desktop | None) -> str:
    """The menu bar tooltip's word on the desktop app: pooled, or a switch waiting for a reopen."""
    if not desktop_shown(d):
        return ''
    if d.restart_required:
        return ' · desktop switch pending'
    return ' · desktop pooled' if d.configured_mode == 'pooled' else ''


def model_desktop(m) -> Desktop | None:
    x = getattr(m, 'extra', None)
    return x.desktop if isinstance(x, PoolExtra) else None


def model_route(m) -> str:
    x = getattr(m, 'extra', None)
    return x.route if isinstance(x, PoolExtra) else ''


def installed(raw: dict | None, problem: str, age: float | None = None) -> bool:
    """The Claude pool is installed: claude-status.json exists (a half-written or unreadable one counts) and its
    pool.installed is true. A file without the key (an older writer) counts only while it is fresh (age: seconds
    since it was written). `codexpool claude uninstall` leaves "installed": false or removes the file."""
    if raw is None:
        return problem != mb.NO_FILE
    flag = mb.as_dict(raw.get('pool')).get('installed')
    if isinstance(flag, bool):
        return flag
    return age is not None and age <= STALE_AFTER_S


# -- credits copy, shared by the popover and Settings -------------------------------------------------------

MISMATCH_LINE = 'Turn credits off at claude.ai (Settings → Usage)'   # the row's one line; the tip says why


def credits_mismatch(seat) -> bool:
    """The guard's warning (claude-status.json's credits.mismatch): the account's credit policy is Off, yet
    claude.ai's last usage reading says its usage credits are on. Parking is not enough, since a proxy can't stop
    every paid request (fast mode, a Fable turn past its cap: billed before any response), so only turning them off
    at claude.ai closes it. Doctor reports it as an error; the rows show it, a parked account's too. A file from an
    older guard has no flag, and so no warning (an account with credits on and policy off then gets the quieter
    'not used by the pool' line)."""
    c = getattr(seat, 'extra', None)
    return bool(c and c.mismatch)


def mismatch_text(label: str) -> str:
    """The warning in full, where there is room for it: the row's tooltip, and Settings."""
    return (f'Usage credits are on at claude.ai for {label}: turn them off there (Settings → Usage). '
            'codexpool can’t stop every paid request.')


def credits_line(seat, spending: bool | None = None) -> tuple[str, bool] | None:
    """A Claude account's credits line and whether it is spending now, worded alike in the popover and in Settings
    (which passes `spending`): 'Spending credits · $112 of $150 cap'; the last resort 'Credits: last resort · $31 of
    $150 cap' ('Credits: last resort, cap $150' before any spend, '… · off at claude.ai' while claude.ai has its
    credits off, and just 'Credits · …' under a parked line that already says last resort); an account whose
    credits are on at claude.ai but whose policy is off, 'Credits on at claude.ai · not used by the pool', or when
    the guard flags that as a mismatch, the warning 'Turn credits off at claude.ai (Settings → Usage)'."""
    c = credits(seat)
    if c is None:
        return None
    cur = c.currency
    cap = money(c.cap, cur) if c.cap is not None else None
    if seat.spending if spending is None else spending:
        return f'Spending credits · {money(c.used, cur)}' + (f' of {cap} cap' if cap else ''), True
    if credits_mismatch(seat):
        return MISMATCH_LINE, False
    if c.last_resort:
        spent = bool(c.enabled and c.used)
        amount = (f'{money(c.used, cur)} of {cap} cap' if cap else f'{money(c.used, cur)} used') if spent else \
            (f'cap {cap}' if cap else '')
        off = ' · off at claude.ai' if not c.enabled else ''
        if seat.state == 'parked' and 'last resort' in (seat.detail or '').lower():   # said on the line above
            return 'Credits' + (f' · {amount}' if amount else '') + off, False
        return 'Credits: last resort' + ((' · ' if spent else ', ') + amount if amount else '') + off, False
    if c.enabled:
        return 'Credits on at claude.ai · not used by the pool', False
    return None


def credits_tip(seat) -> str:
    c = credits(seat)
    if credits_mismatch(seat):
        return mismatch_text(seat.label)
    if c is None or not (c.enabled or c.last_resort):
        return ''
    if c.last_resort:
        return ('Credits: last resort. Used only after every account’s plan quota is spent, the reserve’s '
                'included; codexpool stops it at the cap.')
    return ('Usage credits are on for this account at claude.ai. Its credit policy is Off: codexpool parks it at its '
            'plan limit instead of spending them.')


LAST_RESORT_ITEM = 'Serves once every other account is out'   # the menu's line in place of Enable; the tip says why


def last_resort_parked(seat) -> bool:
    """A Claude account parked by its last-resort credit policy. It stays parked until every other account's plan
    quota is spent, the reserve's included, and `codexpool claude enable` refuses to override that (it overrides a
    credits-off park only), so the menu and Settings offer no Enable for it and say when it serves instead."""
    c = getattr(seat, 'extra', None)
    return seat.provider == POOL and seat.state == 'parked' and bool(c and c.last_resort)


def last_resort_tip(label: str, reserve: bool = False) -> str:
    """Why there is no Enable, where there is room for it: the menu item's tooltip. The reserve's own line skips
    'the reserve's included'."""
    others = 'every other account’s plan quota is spent' + ('' if reserve else ', the reserve’s included')
    return (f'{label} is the last resort: codexpool brings it back once {others}. Enable can’t override that; '
            'change its credit policy in Settings → Balancing.')


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# The PoolUI: what the core menu bar app reads
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

class PoolUI:
    """The Claude pool for the core apps. Members are read by name (menubar/codexpool_menubar.py, section 1 lists
    them); the Settings members below `settings_loaded` are read by menubar/codexpool_settings.py."""
    id = POOL
    aliases = ('sienna',)          # `--pool sienna` (the add-on's id) means this pool too
    title = 'Claude'               # 'Claude Pool', the switcher tile, the tooltip
    noun = 'account'               # what the pool calls a seat
    account_title = 'Claude'       # 'Add a Claude account…', the Setup switcher's segment
    status_file = STATUS_FILE
    history_file = HISTORY_FILE
    docs_path = DOCS
    docs_url = DOCS_URL
    colors = CORAL
    mark = MARK
    mark_scale = MARK_SCALE
    mark_px = None                 # the template is read at its own pixels
    scope_words = SCOPE
    plan_names = PLAN_NAMES
    sign_in_ended_text = SIGN_IN_ENDED
    sign_in_site = 'claude.ai'
    alarm_word = 'Credits'         # the pill while an account spends credits
    history_alarm_key = 'credits'  # claude-history.jsonl's per-sample flag
    week_key = 'seven_day'         # the weekly window's other name in claude-status.json
    short_key = 'five_hour'        # the 5-hour window
    chart_area = 0.2
    compact_usage = True           # never try ' left' on every figure of a multi-window usage line
    command_prefix = ('claude',)   # `codexpool claude …`
    add_account_title = 'Add a Claude account…'
    switcher_blurb = 'the Claude pool (Claude accounts, for Claude Code)'
    serving_tip = 'New sessions land on this account'
    session_word = 'sessions'
    module = None                  # set by the loader: this module

    # -- parsing ----------------------------------------------------------------------------------------
    @staticmethod
    def installed(raw, problem, age=None) -> bool:
        return installed(raw, problem, age)

    @staticmethod
    def parse_seat(d: dict, seat, now: dt.datetime | None = None):
        """Adds what a Claude account has beyond a Codex seat: the scoped caps, the credits and whether it is
        spending them now (the normalised alarm the core reads)."""
        seat.scoped = parse_scoped(d)
        c = seat.extra = parse_credits(d.get('credits'))
        if c is not None and c.spending is None and now is not None:
            # a file without the guard's spending flag: a last-resort account serving past a used-up window
            c.spending = c.last_resort and c.enabled and seat.at_limit(now)
        seat.spending = bool(c and seat.serving and c.spending)

    @staticmethod
    def parse_pool(pool: dict) -> PoolExtra:
        route = mb.as_str(pool.get('route')).lower()
        return PoolExtra(route=route if route in ROUTES else '',
                         client=mb.as_str(pool.get('claude_code')) or mb.as_str(pool.get('claude_code_version')),
                         desktop=parse_desktop(pool.get('desktop')))

    # -- the mark and the glyph -----------------------------------------------------------------------------
    @staticmethod
    def mark_alpha(rgba: bytes, w: int, h: int, filename: str) -> bytes:
        """The Claude app's own menu bar template: its alpha, thickened by a pixel (the rays are too thin to cover
        a pixel at menu bar size)."""
        return mb.dilate_alpha(rgba[3::4], w, h, mb.spark_dilation(filename))

    @staticmethod
    def draw_glyph(p, cx: float, cy: float, size: float, weight: float):
        """The plain drawn mark: an eight-ray asterisk (four strokes through the centre) on the path p."""
        r = size / 2 - weight / 2 + 0.2
        for i in range(4):
            a = math.radians(i * 45)
            dx, dy = math.cos(a) * r, math.sin(a) * r
            p.moveToPoint_((cx - dx, cy - dy))
            p.lineToPoint_((cx + dx, cy + dy))

    @staticmethod
    def draw_settings_glyph(path, s: float):
        """The Settings switcher's drawn mark (s x s, flipped): an 8-ray asterisk."""
        c = s / 2
        path.setLineWidth_(s * 0.13)
        for k in range(8):
            a = math.radians(k * 45 + 22.5)
            path.moveToPoint_((c + math.cos(a) * s * 0.08, c + math.sin(a) * s * 0.08))
            path.lineToPoint_((c + math.cos(a) * s * 0.45, c + math.sin(a) * s * 0.45))

    # -- words in the item, the tiles and the rows -------------------------------------------------------------
    @staticmethod
    def tip_suffix(m) -> str:
        return desktop_tip_suffix(model_desktop(m))

    @staticmethod
    def spending_line(seat) -> str:
        """The strip tooltip's word on the last resort: 'Max B on credits, $31 of $150'."""
        c = credits(seat)
        return f'{seat.label} on credits, {money(c.used, c.currency)} of {money(c.cap, c.currency)}'

    @staticmethod
    def version_text(m) -> str:
        x = getattr(m, 'extra', None)
        return f'Claude Code {x.client}' if isinstance(x, PoolExtra) and x.client else ''

    @staticmethod
    def seat_right_text(seat, now) -> str | None:
        """The limit that binds is the one whose reset matters: '5h resets in 2h 10m'. None: the core's line."""
        if seat.state in mb.OUT_STATES + ('parked',):
            return None
        label, used, at = mb.seat_windows(seat)[mb.binding_window(seat)]
        if at and at > now and label != 'Week' and (used or 0) > 0:
            return f'{label} resets in {mb.fmt_span((at - now).total_seconds())}'
        return None

    @staticmethod
    def seat_lines(seat, m, stale: bool, has_detail: bool) -> list:
        """The row's extra lines after a blocked/re-login line: why the guard parked the account, then its credits
        (the guard's credits mismatch as a warning line, with the full sentence in the row's tooltip). Each is
        (SF Symbol, its colour, text, text colour)."""
        C = mb.C
        extra = []
        if not has_detail and seat.state == 'parked' and seat.detail:
            extra.append(('pause.circle.fill', C.secondary() if stale else C.orange(), seat.detail, C.secondary()))
        credit = credits_line(seat)
        if credit:
            text, hot = credit
            if credits_mismatch(seat):   # a warning, drawn like 'Re-login soon': orange triangle, calm grey text
                extra.append(('exclamationmark.triangle.fill', C.secondary() if stale else C.orange(), text,
                              C.secondary()))
            else:
                extra.append(('creditcard.fill' if hot else 'creditcard', C.secondary() if stale or not hot else
                              C.orange(), text, C.orange_text() if hot and not stale else C.secondary()))
        return extra

    @staticmethod
    def seat_tip(seat) -> str:
        return credits_tip(seat)

    @staticmethod
    def hero_lines(m) -> list:
        """Under the summary line: what makes a Claude account change, its 5-hour window and a per-model weekly cap
        (Fable). Each (text, font, colour)."""
        lines = []
        serving = m.serving if m.serving_now else None
        if serving is None:
            return lines
        if serving.short and serving.short.used is not None:
            at = serving.short.reset_at
            when = f' · resets in {mb.fmt_span((at - m.now).total_seconds())}' if at and at > m.now else ''
            lines.append((f'5-hour window {mb.fmt_pct(m.shown(serving.short.used))} {m.word} on {serving.label}'
                          f'{when}', mb.font(11), mb.C.secondary()))
        for sc in serving.scoped:
            lines.append((f'{sc.name} weekly {mb.fmt_pct(m.shown(sc.used))} {m.word} on {serving.label}',
                          mb.font(11), mb.C.secondary()))
        return lines

    @staticmethod
    def banner_copy(m):
        """(title, body, (button title, action) or a list of them, or None)."""
        spend = m.spending
        d = model_desktop(m)
        if m.status not in mb.BANNER_STATES and spend is not None:
            c = credits(spend)
            return ('Spending usage credits',
                    f'Every plan quota is spent, the reserve’s included, so {spend.label} is on credits as the '
                    f'last resort: {money(c.used, c.currency)} of its {money(c.cap, c.currency)} cap. codexpool '
                    f'stops it at the cap.', None)
        if m.status == 'down':
            if desktop_shown(d) and d.configured_mode == 'pooled':
                return ('Claude pool is down', 'The desktop app is pooled and can’t answer until the pool is '
                        'back; running Claude Code sessions can’t reach it either.',
                        [('Run Doctor', 'doctor'), ('Back to Claude.ai', ('desktop', 'claudeai'))])
            return ('Claude pool is down', 'Running sessions can’t reach it. New claude-pool sessions start '
                    'direct, on Claude Code’s own login, until it is back.', ('Run Doctor', 'doctor'))
        if m.status == 'stale':
            return ('Claude pool not reporting',
                    'The guard has stopped writing claude-status.json, so these numbers may be out of date.',
                    ('Run Doctor', 'doctor'))
        if m.status == 'empty':
            return ('No accounts in the Claude pool', 'The Claude pool is running but has no accounts yet.',
                    ('Add Account…', 'addaccount'))
        body = {mb.NO_FILE: 'There is no claude-status.json yet. The guard writes one every minute.',
                mb.INCOMPLETE: 'claude-status.json is incomplete.'}.get(m.problem, f'{m.problem}.')
        return 'Claude pool not reporting', body, ('Run Doctor', 'doctor')

    # -- the popover footer: the route and the desktop app --------------------------------------------------
    @staticmethod
    def footer_rows(lay, y: float, row_h: float, f) -> float:
        y = route_row(lay, y, row_h, f)
        if desktop_shown(model_desktop(lay.m)):
            y = desktop_row(lay, y, row_h, f)
        return y

    # -- the account menu and its actions ----------------------------------------------------------------------
    @staticmethod
    def seat_rotation_item(seat):
        """In place of Enable/Disable: (title, symbol, verb or None, tooltip), or None for the core's items."""
        if last_resort_parked(seat):   # `codexpool claude enable` refuses; say when the guard brings it back instead
            return LAST_RESORT_ITEM, 'pause.circle', None, last_resort_tip(seat.label, seat.reserve)
        return None

    @staticmethod
    def seat_menu_extra(seat) -> list:
        """After a separator: (title, symbol, verb). Opens in the browser, for whichever claude.ai account it is
        signed in to."""
        return [('Open claude.ai usage page', 'safari', 'usage')]

    @staticmethod
    def seat_action(verb: str, seat, app) -> bool:
        if verb == 'usage':
            app.popover.performClose_(None)
            mb.spawn(['/usr/bin/open', USAGE_URL])
            return True
        return False

    @staticmethod
    def enable_parked_text(label: str, when: str) -> str:
        return (f'codexpool parked {label} so that it doesn’t spend usage credits. Enabling it overrides its '
                f'credit policy until the limit resets{when}, and it may spend credits.')

    @staticmethod
    def handle_region(kind: str, value, app) -> bool:
        """A click on a region the core doesn't know: the route control and the desktop control."""
        if kind == 'route':
            m = app.model
            if m is not None and value != model_route(m) and confirm_route(app, value):
                app.say(f'Sending new Claude Code sessions {"through the pool" if value == "pool" else "direct"}…')
                mb.run_codexpool(['claude', 'route', value], lambda code, err: app.after_action(
                    'New Claude Code sessions go ' + ('through the pool' if value == 'pool' else 'direct'),
                    'route', code, err))
            return True
        if kind == 'desktop':
            desktop_action(app, value)
            return True
        return False

    # -- Settings ------------------------------------------------------------------------------------------------
    def settings_loaded(self, settings):
        """The Settings module is defined: bind it (its helpers are used below by name)."""
        global st
        st = settings
        st.LANE_STATE.update(LANE_STATE)
        st.ENGINE_STATE.update(ENGINE_STATE)
        st.ENGINE_HINT.update(ENGINE_HINT)

    lane_providers = None          # set below: LANE_PROVIDERS
    lane_provider_ids = ('sienna',)
    balancing_key = 'claude_balancing'
    seats_hint = 'In fill order. Balancing sets the order, the reserve and credits.'
    size_hint = 'Its share of the headline. Pro 1×, Max 5×, Max 20×.'
    welcome_suffix = ' It can pool Claude accounts for Claude Code too.'
    meter_tip_suffix = ' With the Claude pool, it shows one number per pool.'
    install_command = ('claude', 'install')
    installed_note = 'The Claude pool is installed. Add your Claude accounts next.'
    setup_heading = ('Add your Claude accounts', 'Each account joins the Claude pool behind Claude Code. When one '
                                                 'reaches its limit, the next one picks up the same session.')
    same_account_text = 'The same Claude account'
    snapshot_panes = ('setup-install', 'setup-installing', 'balancing-credits', 'balancing-credits-warn',
                      'overview-desktop')   # snapshots of the Claude pool's states (overview-desktop: "What changes" open)

    @staticmethod
    def modes():
        return CLAUDE_MODES

    @staticmethod
    def mode_done(value: str) -> str:
        return ('New sessions now go to the account that resets soonest' if value == 'reset' else
                'New sessions now follow your order')

    @staticmethod
    def order_footer(reset: bool) -> str:
        return ('Re-sorted every minute by weekly reset. Accounts without usage data follow in your order, '
                'which comes back when you switch to “Your order”.' if reset else
                'New sessions go to the first account that has quota left. A session stays on its account for '
                'a day, and moves only when that account reaches a limit.')

    reserve_footer = ('The reserve is used only when every other account is out; the menu bar turns red while it '
                      'serves. Your biggest account, such as a Max 20× plan, makes a good reserve.')

    @staticmethod
    def lane_copy(pid: str) -> dict:
        return ENGINE_COPY

    @staticmethod
    def setup_state(app, keep: list):
        return claude_setup_state(app, keep)

    @staticmethod
    def empty_state(app, keep: list, where: str):
        return claude_empty_state(app, keep, {
            'overview': 'Add your Claude accounts. Each one joins the pool, and Claude Code moves to the next when '
                        'one reaches its limit.',
            'seats': 'Each Claude account you add joins the pool. When one reaches its limit, the next one picks up '
                     'the same session.',
            'balancing': 'Add your Claude accounts first. Balancing decides which account new sessions go to.',
        }[where])

    @staticmethod
    def overview_sections(pane) -> list:
        return overview_sections(pane)

    @staticmethod
    def hero_facts(m) -> list:
        return claude_facts(m)

    @staticmethod
    def hero_extra(m) -> list:
        return claude_lines(m)

    @staticmethod
    def overview_footer(updated: str, weighs: str) -> str:
        return f'{updated}. {weighs} each account by its size: Pro 1×, Max 5×, Max 20×.'

    @staticmethod
    def seat_detail_views(seat, m) -> list:
        return seat_detail_views(seat, m)

    @staticmethod
    def seat_details(pane, seat, m):
        return claude_details(pane, seat, m)

    @staticmethod
    def remove_text(seat) -> str:
        return (f'This deletes the pool’s login {seat.name}. Your Claude account and Claude Code’s own login are not '
                'touched, and you can add the account again later.')

    @staticmethod
    def balancing_sections(pane, m, busy: bool) -> list:
        return [credits_section(pane, m, busy)]

    @staticmethod
    def login_args(name: str, priority) -> tuple:
        """(argv, env) of the sign-in: CODEXPOOL_NO_CLIPBOARD=1, as the Claude pool's contract says (the assistant
        puts the link on the clipboard itself)."""
        args = ['claude', 'login', name, '--no-open'] + (['--priority', str(priority)] if priority is not None else [])
        return args, {'CODEXPOOL_NO_CLIPBOARD': '1'}

    @staticmethod
    def setup_install_card(assistant):
        return install_card(assistant)

    @staticmethod
    def setup_done_rows(assistant) -> list:
        return setup_done_rows(assistant)

    @staticmethod
    def snapshot_pane(pane: str) -> tuple:
        """(the pane or step the snapshot is drawn on, the pool)."""
        return ('balancing' if pane.startswith('balancing') else 'overview' if pane.startswith('overview') else
                'setup-accounts'), POOL

    @staticmethod
    def snapshot_login(pane: str, setup):
        """The Setup assistant's made-up sign-in states, in Claude's words."""
        Login = st.Login
        if pane == 'setup-signin':
            return Login(phase='waiting', label='Max D', url=DEMO_CLAUDE_URL, deadline=1000.0 + 252, copied=1,
                         pool=POOL)
        if pane == 'setup-added':
            return Login(phase='added', label='Max D', seat_file='claude-max-d.json', plan='Max 5×', pool=POOL)
        if pane == 'setup-again':
            return Login(phase='again', label='Pro C', seat_file='claude-pro-c.json', plan='Pro 1×', pool=POOL)
        if pane == 'setup-installing':
            setup.install = st.Install(phase='running', lines=DEMO_INSTALL_LINES)
        return None

    @staticmethod
    def snapshot_prepare(controller, pane: str) -> list:
        """Sheets a snapshot shows on the window (bottom first), after any pane state it needs."""
        if pane == 'overview-desktop':
            controller.panes['overview'].ext['desktop_more'] = True
            return []
        if pane.startswith('balancing-credits'):
            return [demo_credits_sheet(controller, warn=pane == 'balancing-credits-warn')]
        return []


# -- the popover's Claude rows -------------------------------------------------------------------------------

def route_row(lay, y, row_h, f) -> float:
    """Claude tab: where new Claude Code sessions go, as a two-segment control (Pool | Direct). Choosing the
    other one asks first, then runs `codexpool claude route pool|direct`; running sessions stay where they are."""
    PAD, C = mb.PAD, mb.C
    lay.add(mb.draw_symbol, 'arrow.triangle.branch', PAD + 8, y + row_h / 2, 13, C.label(), mb.NSFontWeightRegular)
    lay.text('Claude Code route', PAD + 26, y + (row_h - mb.line_height(f)) / 2, f, C.label())
    lay.segmented(y, row_h, 'route', (('Pool', 'pool'), ('Direct', 'direct')), model_route(lay.m), {
        'pool': 'New Claude Code sessions go through the pool (claude-pool)',
        'direct': 'New Claude Code sessions go direct, on Claude Code’s own login'})
    return y + row_h


def desktop_row(lay, y, row_h, f) -> float:
    """Claude tab: the Claude desktop app, Pooled | Claude.ai (pool.desktop's configured_mode), with a caption
    that tells the truth about the running app (running_mode, from the app's own log: "On the pool" only once
    the app logged the pool's address), an action at its right (Reopen Claude…, Run Doctor, Set Up…) and the
    warnings under it (credits on at claude.ai, an edited "Pool" entry). Choosing the other segment asks
    first, then runs `codexpool claude desktop pooled|claudeai --relaunch --yes`; meanwhile the control is
    greyed and the caption says what is happening."""
    PAD, INNER, WIDTH, SMALL_LH, C = mb.PAD, mb.INNER, mb.WIDTH, mb.SMALL_LH, mb.C
    d = model_desktop(lay.m)
    view = desktop_view(d, lay.busy)
    lay.add(mb.draw_symbol, 'macwindow', PAD + 8, y + row_h / 2, 13, C.label(), mb.NSFontWeightRegular)
    lay.text('Desktop', PAD + 26, y + (row_h - mb.line_height(f)) / 2, f, C.label())
    lay.segmented(y, row_h, 'desktop', DESKTOP_LABELS, view.selected, {
        'pooled': 'Chat, local Cowork and Code in the Claude app use the pool’s accounts, in a separate '
                  'profile (Claude-3p)',
        'claudeai': 'The Claude app on its own claude.ai account, as without codexpool'}, enabled=view.enabled)
    y += row_h
    cf, x, w = mb.font(11), PAD + 26, INNER - 26
    lines = mb.wrap_words(view.caption, cf, w)
    for line in lines:
        lay.text(line, x, y + (SMALL_LH - mb.line_height(cf)) / 2, cf, C.secondary(), width=w)
        y += SMALL_LH
    if view.action:   # link-styled, at the right end of the caption's last line when it fits, else below
        title = {'doctor': 'Run Doctor', 'reopen': 'Reopen Claude…', 'setup': 'Set Up…'}[view.action]
        af = mb.font(11, mb.NSFontWeightMedium)
        aw = mb.text_width(title, af) + 1
        if mb.text_width(lines[-1], cf) + 14 + aw > w:
            y += SMALL_LH
        ay = y - SMALL_LH
        ax = WIDTH - PAD - aw
        lay.region(((ax - 5, ay - 1), (aw + 10, SMALL_LH + 2)), ('desktop', view.action), radius=5, tip={
            'doctor': 'Runs codexpool doctor in Terminal: it says what is wrong and how to fix it',
            'reopen': 'Quits Claude and opens it again in the configured mode (asks first)',
            'setup': 'Sets the Claude app up on the pool and opens it (asks first)'}[view.action])
        lay.text(title, ax, ay + (SMALL_LH - mb.line_height(af)) / 2, af, C.blue_text(), width=aw)
    for text, warn in view.lines:   # a small symbol and text, like a seat row's extra lines
        y += 2
        lay.add(mb.draw_symbol, 'exclamationmark.triangle.fill' if warn else 'exclamationmark.circle',
                x + 6, y + SMALL_LH / 2, 10.5, C.orange() if warn else C.secondary(), mb.NSFontWeightMedium)
        for line in mb.wrap_words(text, cf, w - 17):
            lay.text(line, x + 17, y + (SMALL_LH - mb.line_height(cf)) / 2, cf,
                     C.orange_text() if warn else C.secondary(), width=w - 17)
            y += SMALL_LH
    return y + 6


# -- the popover's actions (the controller is `app`) -------------------------------------------------------------

def alert(title: str, body: str, ok: str, cancel: str = 'Cancel'):
    a = mb.NSAlert.alloc().init()
    a.setMessageText_(mb.fresh(title))
    a.setInformativeText_(mb.fresh(body))
    a.addButtonWithTitle_(mb.fresh(ok))
    a.addButtonWithTitle_(cancel)
    return a


def confirm_route(app, value: str) -> bool:
    """Route: Pool / Direct changes where new Claude Code sessions go; ask first."""
    if value == 'direct':
        a = alert('Send new Claude Code sessions direct?',
                  'New sessions use Claude Code’s own login instead of the Claude pool. Sessions that are '
                  'running stay where they are. You can switch back here at any time.', 'Go Direct')
    else:
        a = alert('Send new Claude Code sessions through the pool?',
                  'New sessions go through the Claude pool again. Sessions that are running stay where they are.',
                  'Use the Pool')
    app.popover.performClose_(None)
    mb.activate_app()
    return a.runModal() == mb.NSAlertFirstButtonReturn


def desktop_action(app, value: str):
    """A click on the control: a segment (pooled | claudeai), Set Up… (the Pooled confirm), Reopen Claude…
    or Run Doctor. A switch asks first, then runs the command with --relaunch --yes; what codexpool would
    refuse (credits on at claude.ai, an interrupted change, an edited "Pool" entry) is said here first, in its
    words, so Claude is not quit for nothing."""
    m = app.model
    d = model_desktop(m) if m is not None and m.pool == POOL else None
    if d is None or app.busy:
        return
    if value == 'doctor':
        app.run_action('doctor')
        return
    view = desktop_view(d)
    target = 'pooled' if value == 'setup' else value
    if target in ('pooled', 'claudeai'):
        if not view.enabled or (value != 'setup' and target == d.configured_mode):
            return
        refusal = desktop_refusal(d, target, pool_down=m.status == 'down')
        if refusal:
            refuse_desktop(app, target, refusal)
            return
    elif target != 'reopen':
        return
    if confirm_desktop(app, target, d):
        desktop_run(app, target)


def desktop_run(app, target: str):
    app.busy = desktop_busy_text(target)
    app.say(app.busy)

    def done(code, err, out):
        app.busy = None
        app.say(desktop_closing(out, code, err))
        if code == 0:
            mb.run_codexpool(['guard'], lambda c, e: app.refresh(force=True))   # the running mode, once logged
        else:
            app.refresh(force=True)   # the command patched claude-status.json before it gave up
    mb.run_codexpool(desktop_command(target), done, want_out=True)


def confirm_desktop(app, target: str, d: Desktop) -> bool:
    title, body, ok = desktop_confirm(target, d)
    a = alert(title, body, ok)
    app.popover.performClose_(None)
    mb.activate_app()
    return a.runModal() == mb.NSAlertFirstButtonReturn


def refuse_desktop(app, target: str, why: str):
    """codexpool would refuse: its reason, and Run Doctor (which says how to fix it) or OK."""
    a = alert('Can’t switch the desktop app to the pool yet' if target == 'pooled' else
              'Can’t send the desktop app back to Claude.ai yet', why, 'Run Doctor', 'OK')
    app.popover.performClose_(None)
    mb.activate_app()
    if a.runModal() == mb.NSAlertFirstButtonReturn:
        mb.open_in_terminal(mb.cp_command('doctor'))


# ══════════════════════════════════════════════════════════════════════════════════════════════════════
# Settings: the Claude side of Overview, Seats and Balancing, the Setup assistant, the lane copy
# ══════════════════════════════════════════════════════════════════════════════════════════════════════

LANE_PROVIDERS = (('sienna', 'Claude', 'engine', None),)   # what `lane providers --json` says, for an older codexpool
PoolUI.lane_providers = LANE_PROVIDERS

CLAUDE_MODES = (   # claude_balancing, in Claude's words
    ('priority', 'Your order', 'Uses the first account until it reaches a limit, then the next. Best for prompt '
                               'caching, which belongs to the account.'),
    ('reset', 'Soonest reset first', 'Uses the account whose weekly quota resets soonest, so none of it goes to '
              'waste. New sessions follow; running sessions stay on their account.'),
)

LANE_STATE = {   # an engine member (sienna: Claude Code, read-only): the engine's state, from `engine_state`
    'engine ok': ('Engine OK', 'green'), 'untested engine': ('Not accepted', 'orange'),
    'no engine': ('No Claude Code', 'red'), 'no launcher': ('No launcher', 'red'), 'pool down': ('Pool down', 'red'),
    'no profile': ('Apply lanes', 'orange'),
}
ENGINE_STATE = {   # `lane providers` detail of the engine provider (before its ': codexpool …' hint) -> plain words
    'engine ok': 'Accepted', 'untested engine': 'Not accepted yet', 'no engine': 'Claude Code isn’t installed',
    'no launcher': 'The Claude pool launcher isn’t installed', 'pool down': 'The Claude pool is down',
    'no profile': 'Apply lanes first',
}
ENGINE_HINT = {   # ... and what to do, for a member row's tooltip
    'untested engine': 'Accept the engine once: Credentials → Accept Engine… (codexpool lane apply --accept-engine).',
    'no engine': 'Install Claude Code first.', 'no launcher': 'Install the Claude pool (codexpool claude install).',
    'pool down': 'The Claude pool must be running for the lane to answer.', 'no profile': 'Apply lanes.',
}
ENGINE_COPY = {   # the Lanes pane's words for the engine provider (menubar/codexpool_settings.py's lane_copy)
    'provider_line': 'Claude Code, read-only, through the Claude pool. ',
    'provider_ready': 'Engine accepted.', 'provider_unready': 'Accept the engine once the lane is saved.',
    'model_line': 'Type a Claude model id the pool serves, e.g. claude-opus-5-5.',
    'credential_sub': 'Save the lane, then Credentials → Accept Engine… runs one read-only probe turn through the '
                      'Claude pool and records the Claude Code version.',
    'no_member_tip': 'Add a Claude member to a lane first; accepting probes that lane’s engine.',
    'row_state': 'read-only, through the Claude pool',
    'accept_title': 'Accept the Claude engine?',
    'accept_body': 'codexpool runs one read-only probe turn through the Claude pool (it spends one request there) '
                   'and records the exact Claude Code version. Do it again after a Claude Code update.',
    'accept_sheet': 'Accepting the Claude engine',
    'accept_sheet_sub': 'Output from codexpool lane apply --accept-engine. It spends one Claude pool request.',
    'accepted': 'Claude engine accepted. Start a new Codex thread to use the lane.',
    'demo_model': ('claude-opus-5-5', 'Opus 5.5'),
}

DEMO_CLAUDE_URL = ('https://claude.ai/oauth/authorize?code=true&client_id=demo&response_type=code'
                   '&redirect_uri=http%3A%2F%2Flocalhost%3A54545%2Fcallback&state=demo')
DEMO_INSTALL_LINES = [   # a made-up `codexpool claude install` in progress
    'codexpool claude install',
    '  CLIProxyAPI 7.3.18: building from source with the gate (profile claude)…',
    '  go build ./cmd/server: ok',
    '  gate self-test: 15 of 15 passed',
    '  config-claude.yaml: written (port 8321)',
    '  auth-claude/: created',
]


def usd(v) -> str:
    """$31, $31.40, $150 (the menu bar app's money())."""
    return mb.money(v) if v is not None else '$—'


def claude_plan_badge(tier: str, weight) -> str:
    """The plan in a `seat …: plan=max_5x` line, as the Seats pane shows it once the guard has seen the account."""
    return mb.plan_badge(tier, weight, PLAN_NAMES)


def scoped_windows(seat) -> list:
    """A Claude account's scoped weekly caps as (name, mb.Window), for seat_meters."""
    return [(x.name, mb.Window(x.used, x.reset_at, None)) for x in (getattr(seat, 'scoped', None) or [])]


def spending_credits(seat, m) -> bool:
    """The account serves past its plan quota on usage credits: the last resort in use."""
    return bool(getattr(seat, 'spending', False) and m.serving_now)


def credits_text(seat, m):
    """(text, urgent) for an account's credits line, or None when there is nothing to say: the popover's wording
    (credits_line), so both windows describe an account alike. The Seats pane's Usage credits row
    (credits_summary) has the month's figures."""
    if credits(seat) is None:
        return None
    return credits_line(seat, spending_credits(seat, m))


def credits_summary(seat, m) -> str:
    """The Seats pane's Usage credits row: the policy, then what claude.ai says; the guard's credits mismatch
    (credits_mismatch) as the warning in full, since this row has room for it."""
    c = credits(seat)
    if c is None:
        return 'No credit data yet.'
    if spending_credits(seat, m):
        return f'Last resort, spending now: {usd(c.used)} of its {usd(c.cap)} cap.'
    if credits_mismatch(seat):
        month = f'{usd(c.used)} of {usd(c.limit)}' if c.limit is not None else usd(c.used)
        return (f'Off, but usage credits are on at claude.ai ({month} this month): turn them off there '
                '(Settings → Usage). codexpool can’t stop every paid request.')
    if c.policy == 'last-resort':
        return f'Last resort, up to {usd(c.cap)}' + (f' · {usd(c.used)} used this month.' if c.enabled else
                                                     ' · credits are off for it at claude.ai.')
    month = f'{usd(c.used)} of {usd(c.limit)}' if c.limit is not None else usd(c.used)
    return 'Off: parked at its plan limit' + (f' · credits on at claude.ai, {month} this month.' if c.enabled else '.')


def rotation_row(seat) -> tuple[str, bool]:
    """A Claude account's In rotation row: its subtitle, and whether the switch is live. An account parked by its
    last-resort policy has no Enable (`codexpool claude enable` refuses it, as the popover's menu says): the switch
    stays off and disabled, and the subtitle says when it serves and where its policy is set."""
    if last_resort_parked(seat):
        return ('Parked as the last resort: serves once every other account is out' +
                ('' if seat.reserve else ', the reserve included') +
                '. To use it sooner, change its credit policy under Usage credits.', False)
    return {'parked': 'Parked by the guard: its plan limit is used up, and more would spend credits.',
            'blocked': f'Needs a new sign-in: {seat.detail or "the login stopped working"}.',
            'disabled': 'Out of rotation. Sessions on it moved to the next account.'}.get(
        seat.state, 'Takes new sessions in its turn in the fill order.'), True


def claude_setup_state(app, keep: list):
    """Overview, Seats and Balancing on the Claude side while the Claude pool isn't installed."""
    return st.empty_state('asterisk', 'coral', 'Add the Claude pool',
                          'A second pool, for Claude Code: add your Claude accounts, and when one reaches its limit '
                          'the next one picks up the same session. It runs next to the Codex pool and changes '
                          'nothing in it or in Claude Code.',
                          [st.button('Set Up the Claude Pool…',
                                     lambda _: app.open_setup('setup-accounts', pool=POOL), keep, primary=True)])


def claude_empty_state(app, keep: list, body: str):
    return st.empty_state('person.crop.circle.badge.plus', 'coral', 'No Claude accounts yet', body,
                          [st.button('Add a Claude Account…', lambda _: app.open_setup('setup-accounts', pool=POOL),
                                     keep, primary=True)])


# -- Overview -------------------------------------------------------------------------------------------------

def claude_facts(m):
    """The Codex columns in Claude's words: sessions and accounts. New sessions skip the pool with the route set
    to direct, and while the pool is down (the claude-pool launcher then starts them on Claude Code's own
    login), so the first column says so instead of naming the account the pool would serve."""
    names = {'New threads': 'New sessions', 'Regular seats': 'Regular accounts'}
    facts = [(names.get(c, c), v.replace(' seats', ' accounts').replace(' seat', ' account'), vc)
             for c, v, vc in st.OverviewPane.facts(m)]
    if model_route(m) == 'direct':
        facts[0] = ('New sessions', 'Direct (own login)', st.NSColor.secondaryLabelColor())
    elif m.status == 'down':
        facts[0] = ('New sessions', 'Direct (pool down)', st.NSColor.secondaryLabelColor())
    return facts


def claude_lines(m) -> list:
    """Under the Claude headline: the last resort spending credits, the serving account's 5-hour window and
    its scoped caps (the windows that move Claude sessions)."""
    out = []
    sv = m.serving if m.serving and m.serving_now and m.reporting else None
    if sv is None:
        return out
    if spending_credits(sv, m):
        c = credits(sv)
        out.append(st.label(f'Last resort: {sv.label} is spending credits, {usd(c.used)} of its {usd(c.cap)} cap',
                            12, st.NSFontWeightMedium, color=mb.C.orange_text(), wrap=st.GROUP_W - 32))
    if sv.short is not None:
        out.append(st.secondary(f'Serving {sv.label} · 5-hour window {st.window_line(m, sv, sv.short, "5h")}', 12))
    for name, win in scoped_windows(sv):
        out.append(st.secondary(f'{name} weekly: {mb.fmt_pct(m.shown(win.used))} {m.word} on {sv.label}', 12))
    return out


def seat_detail_views(seat, m) -> list:
    """Under an account's meters in Overview: why the guard parked it (as the popover says) and its credits (the
    guard's warning as the popover's row: ⚠ and the full tip)."""
    parts = []
    if seat.state == 'parked' and seat.detail:
        parts.append(st.padded(st.hstack([st.symbol_view('pause.circle.fill', 11, mb.C.orange(), box=13),
                                          st.secondary(seat.detail[:1].upper() + seat.detail[1:], 11)], spacing=5),
                               0, 20, 0, 0))
    credit = credits_text(seat, m)
    if credit and credits_mismatch(seat):
        warning = st.hstack([st.symbol_view('exclamationmark.triangle.fill', 11, mb.C.orange(), box=13),
                             st.secondary(credit[0], 11, middle=True)], spacing=5)
        warning.setToolTip_(st.S(mismatch_text(seat.label)))
        parts.append(st.padded(warning, 0, 20, 0, 0))
    elif credit:
        parts.append(st.padded(st.label(credit[0], 11, st.NSFontWeightMedium if credit[1] else st.NSFontWeightRegular,
                                        color=mb.C.orange_text() if credit[1] else st.NSColor.secondaryLabelColor(),
                                        middle=True), 0, 20, 0, 0))
    return parts


def overview_sections(pane) -> list:
    """The Claude side of Overview: the pool's state, the hero, the accounts and the Using it card."""
    store, k, app = pane.store, pane.keep, pane.app
    if not store.installed(POOL):
        return [claude_setup_state(app, k)]
    m = store.model(POOL)
    out = []
    if m.status == 'down':
        out.append(st.empty_state('exclamationmark.triangle.fill', 'orange', 'The Claude pool is down',
                                  'New claude-pool sessions start direct on Claude Code’s own login until it runs '
                                  'again; sessions already on the pool fail.',
                                  [st.button('Check Health', lambda _: app.show_pane('health'), k)]))
    elif m.status in ('stale', 'missing'):
        out.append(st.empty_state('exclamationmark.triangle.fill', 'grey', 'Claude pool not reporting',
                                  'The guard has stopped writing claude-status.json, so these numbers may be out of '
                                  'date.' if m.status == 'stale' else 'There is no Claude pool report yet. The '
                                  'guard writes one every minute.',
                                  [st.button('Check Health', lambda _: app.show_pane('health'), k)]))
    elif m.status == 'empty':
        out.append(PoolUI.empty_state(app, k, 'overview'))
    if m.seats and m.headline is not None:
        out.append(st.section(pane.hero(m, POOL)))
    if m.seats:
        order = 'Soonest reset first' if store.balancing(POOL) == 'reset' else 'Your order'
        out.append(st.section(st.group([pane.seat_row(s, m) for s in m.seats]),
                              'Accounts', footer=pane.footer(m, POOL), header_right=st.secondary(order, 11)))
    out.append(using_card(pane))
    return out


def using_card(pane):
    """How to use the Claude pool: claude-pool, what keeps working and what doesn't, and the route."""
    k, ext = pane.keep, pane.ext
    busy = bool(pane.note and pane.note[0] == 'busy')
    copy = st.button('Copy', lambda _: copy_launcher(pane), k, small=True)
    start = st.form_row('Start Claude Code through the pool',
                        f'Run {LAUNCHER} in Terminal instead of claude. Claude Code keeps its own login, and '
                        'starts direct when the pool isn’t running.', [st.code_chip(LAUNCHER), copy])

    def fact(symbol, color, text):
        return st.hstack([st.symbol_view(symbol, 12, color, st.NSFontWeightMedium, box=16),
                          st.secondary(text, 12, wrap=st.GROUP_W - 2 * st.ROW_X - 30)], spacing=8)
    works = st.vstack([fact('checkmark.circle.fill', mb.C.green(),
                            'Keeps working: claude.ai connectors, artifacts and Claude in Chrome.'),
                       fact('xmark.circle.fill', mb.C.grey(),
                            'Remote Control doesn’t work through a proxy. For it, start a direct session: '
                            'CLAUDEPOOL=off claude.')],
                      spacing=6, full=False, insets=(10, st.ROW_X, 10, st.ROW_X))
    current = store_route(pane.store)
    route = ext.get('want_route') or current
    if ext.get('want_route') == current:
        ext['want_route'] = None
    seg = st.auto(st.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
        ['Pool', 'Direct'], 0, st.target(lambda s: set_route(pane, ROUTES[s.selectedSegment()]), k), 'fire:'))
    seg.setSelectedSegment_(ROUTES.index(route))
    seg.setEnabled_(not busy)
    rows = [start, works,
            st.form_row('Route for new sessions', 'Pool sends them through the pool; Direct uses Claude Code’s own '
                        'login, as without codexpool. Running sessions stay where they are.', seg)]
    m = pane.store.model(POOL)
    if desktop_shown(model_desktop(m)):
        rows += desktop_rows(pane, m, busy)
    note = st.note_row(pane.note_now())
    if note is not None:
        rows.append(note)
    return st.section(st.group(rows), 'Using it')


def store_route(store) -> str:
    """claude-status.json pool.route: where new Claude Code sessions go (pool | direct)."""
    v = model_route(store.model(POOL))
    return v if v in ROUTES else ROUTES[0]


def desktop_rows(pane, m, busy: bool) -> list:
    """Using it → Desktop: the popover's control with room for the whole story. Pooled | Claude.ai is what the
    app's files say (configured_mode); the caption tells the truth about the running app (running_mode, from
    its own log). Then the warnings (credits on at claude.ai, an edited "Pool" entry), the action the state
    asks for (Reopen Claude…, Check Health, Set Up…), "What changes" and the one-time claude.ai history
    import, off until asked for."""
    k, d = pane.keep, model_desktop(m)
    view = desktop_view(d, pane.ext.get('desktop_busy'))
    seg = st.auto(st.NSSegmentedControl.segmentedControlWithLabels_trackingMode_target_action_(
        [t for t, _ in DESKTOP_LABELS], 0,
        st.target(lambda s: set_desktop(pane, DESKTOP_LABELS[s.selectedSegment()][1]), k), 'fire:'))
    seg.setSelectedSegment_(next((i for i, (_, v) in enumerate(DESKTOP_LABELS) if v == view.selected), -1))
    seg.setEnabled_(view.enabled and not busy)
    seg.setToolTip_(st.S('Pooled: Chat, local Cowork and Code in the Claude app use the pool’s accounts, in a '
                         'separate profile (Claude-3p). Claude.ai: the app on its own account, as without '
                         'codexpool.'))
    act = None
    if view.action == 'reopen':
        act = st.button('Reopen Claude…', lambda _: desktop_go(pane, 'reopen'), k, enabled=not busy)
    elif view.action == 'doctor':
        act = st.button('Check Health', lambda _: pane.app.show_pane('health'), k)
    elif view.action == 'setup':
        act = st.button('Set Up…', lambda _: desktop_go(pane, 'pooled'), k, enabled=not busy)
    rows = [st.form_row('Desktop', view.caption, [act, seg])]
    for text, warn in view.lines:
        rows.append(st.fact_row(text, warn))
    rows.append(desktop_changes(pane))
    if d.configured_mode == 'pooled':
        if d.import_on:
            rows.append(st.form_row('Import claude.ai history', 'Unlocked. In Claude: Settings → Import & export → '
                                    'Import…. It copies your claude.ai chats and projects once; the wizard stores '
                                    'its own sign-in in the pooled profile.', st.secondary('On', 12)))
        else:
            rows.append(st.form_row('Import claude.ai history', 'Bring your claude.ai chats and projects over once. '
                                    'Off until you turn it on.',
                                    st.button('Import claude.ai history…', lambda _: desktop_go(pane, 'import'), k,
                                              enabled=view.enabled and not busy)))
    return rows


def desktop_changes(pane):
    """A disclosure row: what the pooled app keeps and what it loses."""
    open_ = pane.ext.get('desktop_more', False)
    head = st.hstack([st.symbol_view('chevron.down' if open_ else 'chevron.right', 10,
                                     st.NSColor.secondaryLabelColor(), st.NSFontWeightSemibold, box=14),
                      st.label('What changes when the app is pooled', 13)], spacing=6,
                     insets=(9, st.ROW_X, 9, st.ROW_X), min_h=36)

    def toggle():
        pane.ext['desktop_more'] = not open_
        pane.app.rebuild(pane)
    row = st.clickable(head, toggle, 'What changes when the app is pooled')
    if not open_:
        return row
    facts = st.vstack([
        st.fact_row('Kept: Chat, local Cowork, Code, projects, artifacts, scheduled tasks and memory, on this Mac.',
                    symbol='checkmark.circle.fill', color=mb.C.green()),
        st.fact_row('Not while pooled: mobile and web sync, cloud Cowork, Remote Control, Claude in Chrome, voice, '
                    'Design, Security and Tag, and exact token counts.', symbol='xmark.circle.fill',
                    color=mb.C.grey()),
        st.fact_row('Code transcripts are shared with Claude Code in the terminal (~/.claude/projects).',
                    symbol='info.circle', color=mb.C.grey()),
        st.fact_row('Your claude.ai chats come over once, when you ask: Import claude.ai history…, then in the '
                    'pooled app Settings → Import & export → Import….', symbol='info.circle', color=mb.C.grey()),
    ], spacing=0, insets=(0, 0, 4, 0))
    return st.vstack([row, facts], spacing=0)


def set_desktop(pane, value: str):
    """A segment clicked: the other mode asks first; the current one clicked again just redraws."""
    d = model_desktop(pane.store.model(POOL))
    if d is None or value == d.configured_mode:
        pane.app.rebuild(pane)
        return
    desktop_go(pane, value)


def desktop_go(pane, target: str):
    def feedback(kind, text):
        if kind is None:   # cancelled or refused: the control shows the files' state again
            pane.app.rebuild(pane)
            return
        pane.ext['desktop_busy'] = text if kind == 'busy' else None
        pane.say(kind, text)
    desktop_switch(pane.app, target, feedback)


def copy_launcher(pane):
    pane.say('ok', f'Copied {LAUNCHER}' if st.copy_text(LAUNCHER) else 'Couldn’t copy the command')


def set_route(pane, value: str):
    ext = pane.ext
    if value == (ext.get('want_route') or store_route(pane.store)):
        pane.app.rebuild(pane)   # the current choice clicked again
        return
    ext['want_route'] = value

    def forget(_r=None):
        if ext.get('want_route') is not None:
            ext['want_route'] = None
            pane.app.rebuild(pane)
    done = ('New Claude Code sessions go through the pool' if value == 'pool' else
            'New Claude Code sessions go direct')
    pane.run(['claude', 'route', value], 'Saving…', done, then=lambda r: None if r.ok else forget(), settled=forget)


def desktop_switch(app, target: str, feedback):
    """Pooled | Claude.ai, Reopen Claude… and Import claude.ai history…: what codexpool would refuse is said
    first, in its words, with Check Health (so Claude is not quit for nothing); then the confirm; then
    `codexpool claude desktop … --relaunch --yes` in the background. feedback(kind, text) shows the progress
    where it was asked for, then the command's closing line ('Claude opened on the pool') or its ✗ line;
    feedback(None, None) means nothing happened (cancelled, refused)."""
    m = app.store.model(POOL)
    d = model_desktop(m)
    if not desktop_shown(d):
        return
    refusal = desktop_refusal(d, 'pooled' if target == 'import' else target, pool_down=m.status == 'down')
    if refusal:
        app.ask('Can’t send the desktop app back to Claude.ai yet' if target == 'claudeai' else
                'Can’t switch the desktop app to the pool yet', refusal, 'Check Health',
                lambda: (feedback(None, None), app.show_pane('health')), cancelled=lambda: feedback(None, None))
        return
    title, body, ok = desktop_confirm(target, d)

    def go():
        feedback('busy', desktop_busy_text(target))

        def done(r):
            feedback('ok' if r.ok else 'error', desktop_closing(r.out, r.code, r.message() if not r.ok else ''))
            if r.ok:
                app.after_change()   # the running mode, once the app has logged it
            else:
                app.store.poll(force=True)   # the command patched claude-status.json before it gave up
        app.run(desktop_command(target), done)
    app.ask(title, body, ok, go, cancelled=lambda: feedback(None, None))


# -- Seats ------------------------------------------------------------------------------------------------------

def claude_details(pane, seat, m):
    """A Claude account's settings: the Codex seat's, in `codexpool claude …` commands, with its credit policy
    (set in Balancing) in place of banked resets, which Claude doesn't have."""
    k = pane.keep
    busy = bool(pane.note and pane.note[0] == 'busy')
    name = seat.name or seat.label
    field_ = st.text_field(seat.label, 190)

    def rename(sender):
        new = str(sender.stringValue()).strip()
        if new.startswith('-'):
            sender.setStringValue_(st.S(seat.label))
            pane.say('error', 'A name can’t start with a dash.')
        elif new and new != seat.label:
            pane.run(['claude', 'label', name, new], f'Renaming {seat.label}…', f'{seat.label} is now {new}')
    field_.setTarget_(st.target(rename, k))
    field_.setAction_('fire:')

    size = st.auto(st.NSPopUpButton.alloc().initWithFrame_pullsDown_(((0, 0), (90, 26)), False))
    current = seat.weight or 1.0
    values = sorted(set(st.SIZES) | {current})
    size.addItemsWithTitles_([st.S(f'{v:g}×') for v in values])
    size.selectItemAtIndex_(values.index(current))

    def resize(sender):
        v = values[sender.indexOfSelectedItem()]
        if v != current:
            pane.run(['claude', 'weight', name, f'{v:g}'], f'Resizing {seat.label}…',
                     f'{seat.label} now counts {v:g}×')
    size.setTarget_(st.target(resize, k))
    size.setAction_('fire:')

    rotation = st.auto(st.NSSwitch.alloc().init())
    rotation.setControlSize_(st.NSControlSizeSmall)
    rotation.setState_(0 if seat.state in ('disabled', 'parked') else 1)

    def toggle_rotation(sender):
        if sender.state() == 1:
            if seat.state == 'parked':
                sender.setState_(0)
                pane.app.ask(f'Enable {seat.label} and spend credits?',
                             f'The guard parked {seat.label} because its plan limit is used up and more requests '
                             'would spend usage credits. Enabling it overrides the guard until the limit resets.',
                             'Enable', lambda: pane.run(['claude', 'enable', name], f'Enabling {seat.label}…',
                                                        f'{seat.label} enabled; it may spend credits'))
            else:
                pane.run(['claude', 'enable', name], f'Enabling {seat.label}…', f'{seat.label} is back in rotation')
        else:
            pane.run(['claude', 'disable', name], f'Disabling {seat.label}…', f'{seat.label} is out of rotation')
    rotation.setTarget_(st.target(toggle_rotation, k))
    rotation.setAction_('fire:')
    state_sub, live = rotation_row(seat)   # a last-resort park: the switch is off and stays so
    for c in (field_, size):
        c.setEnabled_(not busy)
    rotation.setEnabled_(not busy and live)

    rows = [
        st.form_row('Name', f'Pool login {seat.name}' if seat.name else None, field_),
        st.form_row('Size', 'Its share of the headline. Pro 1×, Max 5×, Max 20×.', size),
        pane.order_row(seat, m),
        st.form_row('In rotation', state_sub, rotation),
        pane.balancing_link('Usage credits', credits_summary(seat, m)),
        st.form_row('Claude sign-in', 'Anthropic ended this sign-in. Sign in again to keep the account serving.'
                    if seat.sign_in_soon else 'Sign in again if the account is blocked or you changed its password.',
                    st.button('Sign In Again…', lambda _: pane.app.open_setup(
                        'setup-accounts', label=seat.label, pool=POOL,
                        priority=None if seat.priority is None else int(seat.priority)), k, enabled=not busy)),
        st.form_row('Remove from pool', 'Deletes the pool’s login for it. Your Claude account and Claude Code’s own '
                    'login are not touched.',
                    st.button('Remove…', lambda _: pane.confirm_remove(seat), k, enabled=not busy)),
    ]
    note = st.note_row(pane.note_now())
    if note is not None:
        rows.append(note)
    return st.group(rows)


# -- Balancing: usage credits ---------------------------------------------------------------------------------

def credits_section(pane, m, busy: bool):
    """Per account: Off, or Last resort up to a cap. A popup whose items are the choices: Off runs at once;
    Last resort… and Change Cap… open a sheet that asks for the cap first."""
    k = pane.keep
    want_credits = pane.ext.setdefault('want_credits', {})   # account key -> (policy, cap) chosen here, not shown yet
    rows = []
    for seat in sorted(m.seats, key=lambda s: s.reserve):
        key = st.seat_key(seat)
        c = credits(seat)
        want = want_credits.get(key)
        if want is not None and c is not None and (c.policy, c.cap if c.policy == 'last-resort' else None) == want:
            want_credits.pop(key)
            want = None
        policy, cap = want if want is not None else ((c.policy, c.cap) if c else ('off', None))
        left = [st.dot(st.seat_dot_color(seat, m), ring=seat.state == 'disabled' and m.reporting),
                st.label(seat.label, 13, st.NSFontWeightMedium)]
        if seat.plan:
            left.append(st.plan_pill(seat.plan))
        if spending_credits(seat, m):
            left.append(st.pill('Spending credits', mb.C.red_text, lambda: mb.C.soft(mb.C.red())))
        if c is None:
            sub = 'No credit data yet'
        elif c.enabled:
            sub = (f'Credits on at claude.ai · {usd(c.used)} of {usd(c.limit)} this month' if c.limit is not None
                   else f'Credits on at claude.ai · {usd(c.used)} this month')
        else:
            sub = 'Credits off at claude.ai'
        text = st.vstack([st.hstack(left, spacing=8, cluster=True), st.padded(st.secondary(sub, 11), 0, 18, 0, 0)],
                         spacing=3, full=False)
        titles = ['Off'] + ([f'Last resort, up to {usd(cap)}', 'Change Cap…'] if policy == 'last-resort'
                            else ['Last resort…'])
        menu = st.popup(titles, 1 if policy == 'last-resort' else 0,
                        lambda sender, s=seat, p=policy: pick_credits(pane, s, p, sender.indexOfSelectedItem()), k,
                        width=190)
        menu.setEnabled_(not busy and c is not None)
        rows.append(st.hstack([text], [menu], spacing=8, insets=(8, st.ROW_X, 8, st.ROW_X), min_h=48))
    rows.append(pane.note_for('credits'))
    return st.section(st.group(rows), 'Usage credits',
                      footer='Last resort: used only after every account’s plan quota is spent, the reserve’s '
                             'included; codexpool stops it at the cap. Off: an account at its plan limit is parked '
                             'until it resets, so it never spends credits.')


def pick_credits(pane, seat, policy: str, index: int):
    if index == 0:
        if policy != 'off':
            set_credits(pane, seat, 'off', None)
        return
    if policy == 'last-resort' and index == 1:
        return   # the current choice
    pane.app.rebuild(pane)   # the popup goes back to what is set until the sheet saves
    pane.app.present_sheet(CreditsSheet(pane.app, seat, credits(seat), lambda s, cap: credits_saved(pane, s, cap)),
                           pane=None)


def set_credits(pane, seat, policy: str, cap: float | None):
    key = st.seat_key(seat)
    pane.where = 'credits'
    want_credits = pane.ext.setdefault('want_credits', {})
    want_credits[key] = (policy, cap)

    def forget(_r=None):
        if want_credits.pop(key, None) is not None:
            pane.app.rebuild(pane)
    pane.run(['claude', 'credits', key, 'off'], f'Updating {seat.label}…',
             f'{seat.label} never spends credits now', then=lambda r: None if r.ok else forget(), settled=forget)


def credits_saved(pane, seat, cap: float):
    """The Credits sheet saved a last-resort cap."""
    pane.where = 'credits'
    want_credits = pane.ext.setdefault('want_credits', {})
    want_credits[st.seat_key(seat)] = ('last-resort', cap)
    pane.say('ok', f'{seat.label} may spend up to {usd(cap)} as the last resort')

    def forget():
        if want_credits.pop(st.seat_key(seat), None) is not None:
            pane.app.rebuild(pane)
    pane.app.after_change(forget)


def parse_usd(text: str) -> float | None:
    """'$200', '200', '1,500.50' -> a positive amount; None for anything else."""
    t = text.strip().replace('$', '').replace(',', '').strip()
    try:
        v = float(t)
    except ValueError:
        return None
    return v if math.isfinite(v) and 0 < v <= 100000 else None


class CreditsSheet:
    """Last resort… and Change Cap…: asks for the cap, then runs `codexpool claude credits SEAT last-resort --cap N`.
    Spending money is never one click: the popup opens this sheet, and Allow is its only way in. (Built on the
    Settings module's Sheet once that is loaded: see `sheet_class`.)"""

    def __new__(cls, app, seat, credits_, done=None):
        return sheet_class()(app, seat, credits_, done)


_SHEET = None


def sheet_class():
    """The CreditsSheet as a subclass of the Settings module's Sheet, made once st is known."""
    global _SHEET
    if _SHEET is not None:
        return _SHEET

    class _CreditsSheet(st.Sheet):
        width = 480.0

        def __init__(self, app, seat, credits_, done=None):
            super().__init__(app)
            self.seat, self.credits, self.done = seat, credits_, done
            c = credits_
            start = c.cap if c and c.policy == 'last-resort' and c.cap else (c.limit if c and c.limit else None)
            self.text = f'{start:g}' if start else ''
            self.change = bool(c and c.policy == 'last-resort')
            self.problem = ''
            self.job = None
            self.field = None

        def heading(self):
            if self.change:
                return f'Change the cap for {self.seat.label}', None
            return f'Let {self.seat.label} spend credits as the last resort?', None

        def harvest(self):
            if self.field is not None:
                self.text = str(self.field.stringValue())

        def build(self):
            W, c = self.inner, self.credits
            ROW_X = st.ROW_X
            field_ = st.text_field(self.text, 90, 'USD', right=True)
            field_.cell().setSendsActionOnEndEditing_(False)   # Return saves; leaving the field doesn't
            field_.setTarget_(st.target(lambda _s: self.save(), self.keep))
            field_.setAction_('fire:')
            field_.setEnabled_(self.job is None)
            self.field = field_
            so_far = f' It has used {usd(c.used)} this month.' if c and c.enabled and c.used is not None else ''
            if self.seat.reserve:   # the reserve itself: its own quota is the one spent last
                when = f'{self.seat.label} (the reserve) spends credits only after every account’s plan quota is ' \
                    'spent, its own included.'
            else:
                when = f'{self.seat.label} spends credits only after every account’s plan quota is spent, the ' \
                    'reserve’s included.'
            rows = [st.padded(st.secondary(f'{when} Then it spends them until another account comes back or it '
                                           'reaches the cap, where codexpool stops it.', 12, wrap=W - 2 * ROW_X),
                              11, ROW_X, 11, ROW_X),
                    st.form_row('Cap this month', f'Credits it may spend in a month, in US dollars.{so_far}',
                                [st.label('$', 13, color=st.NSColor.secondaryLabelColor()), field_], width=W)]
            if c is not None and c.limit is not None:
                rows.append(st.padded(st.secondary(f'claude.ai’s own monthly limit for it, {usd(c.limit)}, applies '
                                                   'too.', 11, wrap=W - 2 * ROW_X), 9, ROW_X, 9, ROW_X))
            if c is not None and not c.enabled:
                rows.append(st.note_row(('error', 'Usage credits are off for this account at claude.ai, so it '
                                                  'can’t spend any until you turn them on there (Settings › Usage).'),
                                        W))
            if self.problem:
                rows.append(st.note_row(('error', self.problem), W))
            return [st.group(rows, W)]

        def buttons(self):
            k = self.keep
            busy = self.job is not None
            left = [st.hstack([st.spinner(), st.secondary('Saving…', 11)], spacing=6, cluster=True)] if busy else []
            return left, [st.cancel_button(lambda _: self.close(), k, enabled=not busy),
                          st.button('Save Cap' if self.change else 'Allow Credits', lambda _: self.save(), k,
                                    primary=True, enabled=not busy)]

        def save(self):
            if self.job is not None:
                return
            self.harvest()
            cap = parse_usd(self.text)
            if cap is None:
                self.problem = 'Enter the cap in dollars, such as 50.'
                self.render()
                return
            key = st.seat_key(self.seat)

            def done(r):
                self.job = None
                if r.ok:
                    self.close()
                    if self.done:
                        self.done(self.seat, cap)
                else:
                    self.problem = r.message()
                    self.render()
            self.problem = ''
            self.job = st.Job(['claude', 'credits', key, 'last-resort', '--cap', f'{cap:g}'], done)
            self.render()

    _SHEET = _CreditsSheet
    _CreditsSheet.__name__ = 'CreditsSheet'
    return _SHEET


def demo_credits_sheet(controller, warn: bool = False):
    """balancing-credits: Last resort… on the first account with credits on at claude.ai that isn't a last resort
    yet; -warn: on one whose credits are off there (the sheet warns that it can't spend any)."""
    m = controller.store.model(POOL)
    seat = next((s for s in m.seats if credits(s) and credits(s).policy == 'off' and credits(s).enabled != warn),
                m.seats[0] if m.seats else None)
    if seat is None:
        raise SystemExit('balancing-credits needs --pool-status with accounts')
    sheet = CreditsSheet(controller, seat, credits(seat))
    sheet.text = '50'
    return sheet


# -- the Setup assistant: installing the Claude pool, the Done step ---------------------------------------------

def install_card(assistant):
    ins, k, W = assistant.install, assistant.keep, st.SETUP_BODY_W
    ROW_X = st.ROW_X
    assistant.install_text = None
    if ins.phase == 'running':
        head = st.hstack([st.spinner(), st.label('Installing the Claude pool…', 13, st.NSFontWeightSemibold)],
                         [st.button('Stop', lambda _: assistant.stop_install(), k)], spacing=8,
                         insets=(12, ROW_X, 4, ROW_X), min_h=40)
        rows = [head, st.padded(st.secondary('Building its own CLIProxyAPI can take a few minutes.', 12), 0,
                                ROW_X + 24, 4, ROW_X),
                st.padded(assistant.install_output(W - 2 * ROW_X), 4, ROW_X, 12, ROW_X)]
        return st.section(st.group(rows, W, rules=False), 'Install the Claude pool', width=W)
    icon = st.canvas(lambda w, h: st.draw_icon_square(0, 0, 36, 'asterisk', 'coral'), 36, 36)
    again = ins.phase == 'failed'
    go = st.button('Try Again' if again else 'Install the Claude Pool', lambda _: assistant.start_install(), k,
                   primary=True)
    text = st.vstack([st.label('A second pool, for Claude Code', 13, st.NSFontWeightSemibold),
                      st.secondary('It runs next to the Codex pool, on its own port, with its own accounts. Claude '
                                   'Code and the Codex pool stay as they are: you start Claude Code through it with '
                                   f'{LAUNCHER}.', 12, wrap=W - 2 * ROW_X - 50)], spacing=3, full=False)
    top = st.hstack([icon, text], spacing=12, insets=(14, ROW_X, 10, ROW_X))
    top.setAlignment_(3)   # top: the icon sits by the title
    rows = [top, st.hstack([st.secondary('Runs codexpool claude install.', 11)], [go], spacing=10,
                           insets=(4, ROW_X + 48, 12, ROW_X), min_h=40)]
    if again:
        rows.append(st.note_row(('error', ins.message), W))
        if ins.lines:
            rows.append(st.padded(assistant.install_output(W - 2 * ROW_X, 110), 0, ROW_X, 12, ROW_X))
    return st.section(st.group(rows, W, rules=False), 'Install the Claude pool', width=W,
                      footer='Then add your Claude accounts here, one sign-in each.')


def setup_done_rows(assistant) -> list:
    """The Done step's rows for the Claude pool: the launcher, and the desktop app once the credits check passes."""
    store, k, W = assistant.app.store, assistant.keep, st.SETUP_BODY_W
    if not store.installed(POOL):
        return []
    rows = [st.form_row('Claude Code', f'Start it through the Claude pool with {LAUNCHER} in Terminal. Its own '
                        'login stays as it is.', st.code_chip(LAUNCHER), width=W)]
    desktop = desktop_offer(store.model(POOL))
    if desktop is not None:
        text, offer = desktop
        act = st.button('Set Up Pooled Desktop…',
                        lambda _: desktop_switch(assistant.app, 'pooled', assistant.desktop_feedback), k) if offer \
            else ()
        rows.append(st.form_row('Claude desktop app', text, act, width=W))
    return rows


def desktop_offer(m):
    """The Done step's optional line on the desktop app: (text, offer it) or None. Offered when the Claude pool
    has an account and the credits check passes; when it fails, the line is the reason instead; nothing once the
    app is pooled, or without the app or the block."""
    d = model_desktop(m)
    if not desktop_shown(d) or not m.seats or d.txn_pending:
        return None
    pooled = d.configured_mode == 'pooled'
    if d.credits_ok is False:
        on, stale = desktop_credit_labels(d.credits_problems)
        if on and pooled:
            return (f'Credits are on at claude.ai for {join_labels(on)}, and the pooled desktop app can spend '
                    'them: turn them off there (Settings → Usage).', False)
        if on:
            return (f'Turn credits off at claude.ai for {join_labels(on)} first (Settings → Usage); then the '
                    'desktop app can run on the pool too.', False)
        if stale and not pooled:
            return (f'Wait for a fresh credits reading for {join_labels(stale)}; then the desktop app can run on '
                    'the pool too.', False)
        return None
    if d.credits_ok is True and not pooled:
        return ('Also run the Claude desktop app on the pool? Chat, local Cowork and Code then use the pool’s '
                'accounts.', True)
    return None


def load(menubar):
    """The core menu bar module hands itself over once it is defined; returns the PoolUI."""
    global mb
    mb = menubar
    ui = PoolUI()
    ui.module = sys.modules[__name__]
    return ui
