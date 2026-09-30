#!/usr/bin/env python3
"""Writes the synthetic Claude pool used for the menu bar and Settings screenshots: four made-up accounts, a
claude-status.json the way the guard's Claude pass writes it (status.json's shape, plus per seat `five_hour`,
`scoped[]` and `credits{enabled,used,limit,policy,cap}`), and 24 h of claude-history.jsonl (one sample every 10
minutes).

    python3 docs/images/demo/make_claude_data.py [--now 2026-09-24T16:41:00Z] [--out docs/images/demo]

Accounts: Max A (Max 5x), Max B (Max 5x, credits "last resort" up to a $150 cap, $200 monthly limit), Pro C (Pro)
and Max 20x (Max 20x, the reserve; its credits are on at claude.ai, policy "off"). The Max accounts have a Fable
weekly cap.

Scenarios (claude-status-<name>.json; the ones with a chart also get claude-history-<name>.jsonl):
    regular     Max A serving, Pro C out on its 5-hour window, Max B and the Max 20x reserve ready
    reserve     every regular account is out, Max B waits as the last resort: the Max 20x reserve serves (red)
    lastresort  every plan quota is spent, the reserve's included: Max B serves on credits, $112 of its $150 cap
    down        the regular scenario, but the Claude pool is not running (grey, with a warning)
    direct      the regular scenario with the route set to direct (new Claude Code sessions skip the pool)
    empty       the Claude pool is installed and running with no accounts yet
(No file at all means the Claude pool is not installed.) Nothing here is real: no accounts, no emails, no tokens.
Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from pathlib import Path

UTC = dt.timezone.utc
DEFAULT_NOW = '2026-09-24T16:41:00Z'   # the Codex demo's moment (make_data.py), so both pools read alike
VERSION = '7.3.18-gate-9f8e7d6c5b'
PORT = 8321
CLAUDE_CODE = '2.1.283'

# label, seat file name, plan, weight, priority, reserve, Fable cap
SEATS = (
    ('Max A', 'claude-max-a.json', 'max_5x', 5.0, 300, False, True),
    ('Max B', 'claude-max-b.json', 'max_5x', 5.0, 250, False, True),
    ('Pro C', 'claude-pro-c.json', 'pro', 1.0, 200, False, False),
    ('Max 20x', 'claude-max-20x.json', 'max_20x', 20.0, 100, True, True),
)
OFF = {'enabled': False, 'used': 0.0, 'limit': None, 'policy': 'off', 'cap': None}
LAST_RESORT = {'enabled': True, 'limit': 200.0, 'policy': 'last-resort', 'cap': 150.0}   # Max B
RESERVE_CREDITS = {'enabled': True, 'used': 12.0, 'limit': 500.0, 'policy': 'off', 'cap': None}   # Max 20x

# Weekly usage per account as keyframes (hours relative to now, percent); linear in between, flat outside. Use
# comes in bursts with pauses between, so the popover's bars have a shape.
# `reserve`: spans (from, to] when the reserve served; `credits`: spans when an account served on usage credits.
SCENARIOS = {
    'regular': {
        'week': {
            'Max A': [(-24, 22), (-22.5, 22), (-21.5, 27), (-20.5, 29), (-19, 29), (-17.5, 34), (-16, 35), (-11, 35),
                      (-10, 39), (-9, 43), (-8, 44), (-6, 44), (-5, 48), (-4, 49), (-1.5, 49), (-1, 52), (-0.5, 54),
                      (0, 55)],
            'Max B': [(-24, 26)],
            'Pro C': [(-24, 40), (-21, 40), (-19.5, 46), (-18, 48), (-12, 48), (-11, 55), (-10, 58), (-7.5, 58),
                      (-6.5, 64), (-6, 66), (-3.5, 66), (-2.5, 72), (-2, 74)],
            'Max 20x': [(-24, 20)],
        },
        'reserve': [],
    },
    'reserve': {
        'week': {
            'Max A': [(-24, 70), (-20, 78), (-16, 88), (-13, 96), (-12, 100)],
            'Max B': [(-24, 62), (-12, 62), (-9, 78), (-7, 90), (-5, 100)],
            'Pro C': [(-24, 81), (-14, 90), (-11, 97), (-10.5, 100)],
            'Max 20x': [(-24, 12), (-5, 12), (-3, 19), (-1, 26), (0, 30)],
        },
        'reserve': [(-5, 0.5)],
    },
    'lastresort': {
        'week': {
            'Max A': [(-24, 88), (-20, 100)],
            'Max B': [(-24, 80), (-12, 100)],
            'Pro C': [(-24, 94), (-19, 100)],
            'Max 20x': [(-24, 52), (-12, 52), (-6, 81), (-2.2, 100)],
        },
        'reserve': [(-12, -2.2)],
        'credits': [(-2.2, 0.5)],
    },
}


def at(frames, h):
    if h <= frames[0][0]:
        return frames[0][1]
    for (h0, v0), (h1, v1) in zip(frames, frames[1:]):
        if h0 <= h <= h1:
            return v0 + (v1 - v0) * (h - h0) / (h1 - h0)
    return frames[-1][1]


def iso(t: dt.datetime) -> str:
    return t.astimezone(UTC).isoformat()


def weighted(rows):
    tw = sum(w for w, _ in rows)
    return round(sum(w * u for w, u in rows) / tw, 1) if tw else None


def history(name: str, now: dt.datetime) -> list[dict]:
    sc = SCENARIOS[name]
    rng = random.Random('claude-' + name)
    out = []
    t = now - dt.timedelta(hours=24, seconds=-83)
    while t <= now - dt.timedelta(minutes=2):
        h = (t - now).total_seconds() / 3600
        used = {s[0]: float(round(at(sc['week'][s[0]], h))) for s in SEATS}
        regular = [(s[3], used[s[0]]) for s in SEATS if not s[5]]
        every = [(s[3], used[s[0]]) for s in SEATS]
        out.append({'t': iso(t), 'used': weighted(regular), 'all': weighted(every),
                    'reserve': any(a < h <= b for a, b in sc['reserve']), 'seats': used,
                    'credits': any(a < h <= b for a, b in sc.get('credits', []))})
        t += dt.timedelta(minutes=10, seconds=rng.randint(-20, 20))
    return out


def window(used, reset_at, minutes=None):
    w = {'used': float(used), 'reset_at': iso(reset_at) if reset_at else None}
    if minutes:
        w['window_min'] = minutes
    return w


def seat_row(label, now, state, week, week_reset, five, five_reset, *, fable=None, until=None, detail='',
             credits=None):
    _, name, plan, weight, priority, reserve, has_fable = next(s for s in SEATS if s[0] == label)
    return {
        'label': label, 'name': name, 'provider': 'claude', 'email': '', 'plan': plan, 'priority': priority,
        'state': state, 'detail': detail, 'until': iso(now + until) if until else None,
        'weight': weight, 'reserve': reserve,
        'week': window(week, now + week_reset, 10080),
        'five_hour': window(five, now + five_reset),
        'scoped': [{'name': 'Fable', 'used': float(fable if fable is not None else week),
                    'reset_at': iso(now + week_reset)}] if has_fable else [],
        'credits': {**OFF, 'spending': False, **(credits or {})},
        'week_used': float(week), 'poll_error': None,
    }


def status(name: str, now: dt.datetime, written: dt.datetime, *, running: bool = True, route: str = 'pool') -> dict:
    d = dt.timedelta
    weekly, five_hour = 'weekly limit reached', '5-hour limit reached'
    if name == 'empty':
        rows = []
    elif name == 'reserve':
        rows = [
            seat_row('Max A', now, 'exhausted', 100, d(days=2, hours=6), 100, d(hours=3, minutes=5),
                     until=d(days=2, hours=6), detail=weekly),
            seat_row('Max B', now, 'parked', 100, d(days=3, hours=21), 64, d(hours=2, minutes=30),
                     fable=92, until=d(days=3, hours=21), detail='last resort: waits until every account is out',
                     credits=dict(LAST_RESORT, used=31.0)),
            seat_row('Pro C', now, 'exhausted', 100, d(days=4, hours=11), 100, d(hours=1, minutes=40),
                     until=d(days=4, hours=11), detail=weekly),
            seat_row('Max 20x', now, 'active', 30, d(days=3, hours=9), 41, d(hours=4, minutes=12), fable=22,
                     credits=RESERVE_CREDITS),
        ]
    elif name == 'lastresort':
        rows = [
            seat_row('Max A', now, 'exhausted', 100, d(days=1, hours=18), 100, d(hours=2, minutes=5),
                     until=d(days=1, hours=18), detail=weekly),
            seat_row('Max B', now, 'active', 100, d(days=3, hours=5), 100, d(minutes=50), fable=100,
                     credits=dict(LAST_RESORT, used=112.0, spending=True)),
            seat_row('Pro C', now, 'exhausted', 100, d(days=3, hours=20), 88, d(hours=3, minutes=40),
                     until=d(days=3, hours=20), detail=weekly),
            seat_row('Max 20x', now, 'exhausted', 100, d(days=1, hours=4), 100, d(hours=4),
                     fable=88, until=d(hours=4), detail=five_hour, credits=RESERVE_CREDITS),
        ]
    else:
        rows = [
            seat_row('Max A', now, 'active', 55, d(days=3, hours=4), 62, d(hours=2, minutes=10), fable=36),
            seat_row('Max B', now, 'ready', 26, d(days=5, hours=7), 0, d(hours=5), fable=19,
                     credits=dict(LAST_RESORT, used=31.0)),
            seat_row('Pro C', now, 'cooldown', 74, d(days=5, hours=20), 100, d(hours=1, minutes=12),
                     until=d(hours=1, minutes=12), detail=five_hour),
            seat_row('Max 20x', now, 'ready', 20, d(days=4, hours=9), 0, d(hours=5), fable=14,
                     credits=RESERVE_CREDITS),
        ]
    counted = [r for r in rows if r['state'] != 'disabled']
    serving = next((r for r in rows if r['state'] == 'active'), None)
    on_quota = [r for r in rows if r['state'] in ('active', 'ready') and not r['credits'].get('spending')]
    spender = next((r for r in rows if r['state'] == 'active' and r['credits'].get('spending')), None)
    upcoming = sorted((r['until'], r['label']) for r in rows
                      if r['state'] in ('exhausted', 'parked', 'cooldown') and r['until'])
    every = weighted([(r['weight'], r['week_used']) for r in counted])
    regular = weighted([(r['weight'], r['week_used']) for r in counted if not r['reserve']])
    pool = {
        'running': running, 'installed': True, 'version': VERSION, 'port': PORT,
        'used_pct': every, 'used_pct_all': every, 'used_pct_regular': regular,
        'headline': 'all', 'display': 'left', 'balancing': 'priority', 'route': route,
        'claude_code': CLAUDE_CODE,
        'reserve_in_use': bool(serving and serving['reserve']),
        'regular_available': sum(1 for r in on_quota if not r['reserve']),   # on plan quota, not on credits
        'counted': len(counted), 'seats': len(counted),
        'available': len(on_quota),
        'spending': spender['label'] if spender else None,
    }
    if not running:
        pool['error'] = 'not running'
    return {'generated_at': iso(written), 'pool': pool, 'active': serving['label'] if serving else None,
            'active_name': serving['name'] if serving else None,
            'next_back': {'label': upcoming[0][1], 'at': upcoming[0][0]} if upcoming else None, 'seats': rows}


def main():
    p = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    p.add_argument('--now', default=DEFAULT_NOW)
    p.add_argument('--out', type=Path, default=Path(__file__).resolve().parent)
    a = p.parse_args()
    now = dt.datetime.fromisoformat(a.now.replace('Z', '+00:00'))
    a.out.mkdir(parents=True, exist_ok=True)
    # name, scenario, seconds since the guard wrote it, pool running, route
    for name, scenario, age, running, route in (
            ('regular', 'regular', 12, True, 'pool'), ('reserve', 'reserve', 17, True, 'pool'),
            ('lastresort', 'lastresort', 14, True, 'pool'), ('down', 'regular', 40, False, 'pool'),
            ('direct', 'regular', 12, True, 'direct'), ('empty', 'empty', 12, True, 'pool')):
        st = status(scenario, now - dt.timedelta(seconds=age), now - dt.timedelta(seconds=age), running=running,
                    route=route)
        (a.out / f'claude-status-{name}.json').write_text(json.dumps(st, indent=1) + '\n')
        if name == scenario and scenario in SCENARIOS:
            lines = history(scenario, now)
            (a.out / f'claude-history-{name}.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in lines))
        pool = st['pool']
        print(f'{name}: all accounts {pool["used_pct_all"]}% used, regular {pool["used_pct_regular"]}% used, '
              f'serving {st["active"]}')


if __name__ == '__main__':
    main()
