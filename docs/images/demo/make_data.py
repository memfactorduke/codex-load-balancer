#!/usr/bin/env python3
"""Writes the synthetic pool used for the README screenshots: five made-up seats, a status.json the way
`subpool guard` writes it, and 24 h of history.jsonl (one sample every 10 minutes).

    python3 docs/images/demo/make_data.py [--now 2026-09-24T16:41:00Z] [--out docs/images/demo]

Scenarios (files written to --out), all with the default settings ("headline": "all", "display": "left"):
    regular   Work B serving, Work A out with a banked free reset, the Pro 20x reserve idle (green)
    reserve   every regular seat is out or parked, so the Pro 20x reserve is serving (red)
    down      the regular scenario, but the guard stopped writing status.json 26 minutes ago (grey)
    used      the regular scenario with "display": "used" (it shares history-regular.jsonl)
    reset     the regular scenario with "balancing": "reset": the guard put Team first, whose weekly quota resets
              soonest, so new threads go there (it shares history-regular.jsonl)

Nothing here is real: no accounts, no emails, no tokens. Stdlib only.
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import random
from pathlib import Path

UTC = dt.timezone.utc
DEFAULT_NOW = '2026-09-24T16:41:00Z'   # Thursday 09:41 on the US west coast
VERSION = '7.3.18-gate-1a2b3c4d5e'
PORT = 8319

# label, seat file name, plan, weight, priority, reserve
SEATS = (
    ('Work A', 'codex-work-a.json', 'self_serve_business_prolite', 5.0, 300, False),
    ('Work B', 'codex-work-b.json', 'self_serve_business_prolite', 5.0, 250, False),
    ('Team', 'codex-team.json', 'team', 1.0, 200, False),
    ('Personal', 'codex-personal.json', 'plus', 1.0, 100, False),
    ('Pro 20x', 'codex-pro.json', 'pro', 20.0, 10, True),
)

# Weekly usage per seat as keyframes (hours relative to now, percent); linear in between, flat outside.
# A drop between two close keyframes is a weekly reset. `reserve`: spans (from, to] when the reserve served.
SCENARIOS = {
    'regular': {
        'week': {
            'Work A': [(-24, 41), (-22.5, 50), (-21.2, 57), (-20.2, 58), (-18, 76), (-16, 91), (-14.9, 100)],
            'Work B': [(-24, 88), (-14.9, 88), (-13.5, 93), (-12, 97), (-10.22, 97), (-10.2, 0), (-9.2, 0),
                       (-8.2, 14), (-7.2, 26), (-2.4, 26), (-1.6, 31), (-0.8, 38), (0, 44)],
            'Team': [(-24, 76)],
            'Personal': [(-24, 9)],
            'Pro 20x': [(-24, 34)],
        },
        'reserve': [],
    },
    'reserve': {
        'week': {
            'Work A': [(-24, 58), (-22, 66), (-20.5, 74), (-19.5, 76), (-17.2, 96), (-17.0, 100)],
            'Work B': [(-24, 100)],
            'Team': [(-24, 30), (-17.0, 30), (-16, 41), (-15, 52), (-14, 60), (-9, 60), (-8.2, 64), (-7.5, 66),
                     (-3.7, 66), (-2.5, 68), (-1.5, 70), (-0.8, 72)],
            'Personal': [(-24, 90), (-14, 90), (-13.2, 96), (-12.5, 100)],
            'Pro 20x': [(-24, 38), (-12.5, 38), (-9, 44), (-0.8, 44), (0, 47)],
        },
        'reserve': [(-12.5, -9.0), (-0.8, 0.5)],
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
    rng = random.Random(name)
    out = []
    t = now - dt.timedelta(hours=24, seconds=-97)
    while t <= now - dt.timedelta(minutes=2):
        h = (t - now).total_seconds() / 3600
        used = {label: float(round(at(sc['week'][label], h))) for label, *_ in SEATS}
        regular = [(w, used[label]) for label, _, _, w, _, res in SEATS if not res]
        every = [(w, used[label]) for label, _, _, w, _, _ in SEATS]
        out.append({'t': iso(t), 'used': weighted(regular), 'all': weighted(every),
                    'reserve': any(a < h <= b for a, b in sc['reserve']), 'seats': used})
        t += dt.timedelta(minutes=10, seconds=rng.randint(-20, 20))
    return out


def window(used, minutes, reset_at):
    return {'used': float(used), 'window_min': minutes, 'reset_at': iso(reset_at) if reset_at else None}


def seat_row(label, now, state, week, week_reset, *, short=None, short_reset=None, until=None, detail='',
             resets=0, reset_days=None):
    _, name, plan, weight, priority, reserve = next(s for s in SEATS if s[0] == label)
    d = dt.timedelta
    wk = window(week, 10080, now + week_reset)
    sh = window(short, 300, now + short_reset) if short is not None else None
    usage = {'primary': sh or wk, 'secondary': wk if sh else None,
             'credits': {'has_credits': state == 'parked', 'unlimited': False},
             'plan': plan, 'refused': state in ('exhausted', 'parked'), 'at': iso(now - d(minutes=4)),
             'source': 'poll', 'allowed': state not in ('exhausted', 'parked')}
    return {
        'label': label, 'name': name, 'provider': 'codex', 'email': '', 'plan': plan, 'priority': priority,
        'state': state, 'detail': detail, 'until': iso(now + until) if until else None, 'usage': usage,
        'weight': weight, 'reserve': reserve, 'week': wk, 'short': sh, 'week_used': float(week),
        'poll_error': None,
        'resets': {'available': resets,
                   'next_expiry': iso(now + d(days=reset_days)) if resets and reset_days else None,
                   'title': ('Full reset (Weekly + 5 hr)' if sh else 'Full reset') if resets else None,
                   'polled_at': iso(now - d(minutes=4))},
    }


# "balancing": "reset": the fill order the guard computes for the regular scenario (soonest weekly reset first;
# Work A is out, so its place doesn't matter), with the reason status.json gives for each seat's place
RESET_ORDER = (('Team', 1000, 'resets in 1d 18h'), ('Personal', 990, 'resets in 5d 2h'),
               ('Work B', 980, 'resets in 6d 13h'), ('Work A', 970, 'out until its weekly reset'),
               ('Pro 20x', 10, 'reserve: used last'))


def status(name: str, now: dt.datetime, written: dt.datetime, headline: str = 'all', display: str = 'left',
           balancing: str | None = None) -> dict:
    d = dt.timedelta
    out = 'usage limit reached'
    if name == 'reserve':
        rows = [
            seat_row('Work A', now, 'exhausted', 100, d(days=3, hours=4), until=d(days=3, hours=4), detail=out),
            seat_row('Work B', now, 'exhausted', 100, d(hours=19, minutes=25), until=d(hours=19, minutes=25),
                     detail=out, resets=1, reset_days=27),
            seat_row('Team', now, 'exhausted', 72, d(days=5, hours=6), short=100, short_reset=d(hours=1, minutes=12),
                     until=d(hours=1, minutes=12), detail=out),
            seat_row('Personal', now, 'parked', 100, d(days=4, hours=2), until=d(days=4, hours=2),
                     detail='credit guard: would spend credits'),
            seat_row('Pro 20x', now, 'active', 47, d(days=3, hours=9), resets=1, reset_days=18),
        ]
    else:
        rows = [
            seat_row('Work A', now, 'exhausted', 100, d(days=2, hours=7, minutes=12),
                     until=d(days=2, hours=7, minutes=12), detail=out, resets=1, reset_days=26),
            seat_row('Work B', now, 'active', 44, d(days=6, hours=13, minutes=48)),
            seat_row('Team', now, 'ready', 76, d(days=1, hours=18), short=0, short_reset=d(hours=5)),
            seat_row('Personal', now, 'ready', 9, d(days=5, hours=2)),
            seat_row('Pro 20x', now, 'ready', 34, d(days=3, hours=9), resets=1, reset_days=18),
        ]
    if balancing == 'reset':   # Team is first now, so new threads go there; Work B keeps its running threads
        for label, priority, reason in RESET_ORDER:
            row = next(r for r in rows if r['label'] == label)
            row['priority'], row['order_reason'] = priority, reason
            row['state'] = {'Team': 'active', 'Work B': 'ready'}.get(label, row['state'])
        rows.sort(key=lambda r: -r['priority'])
    counted = [r for r in rows if r['state'] != 'disabled']
    serving = next((r for r in rows if r['state'] == 'active'), None)
    upcoming = sorted((r['until'], r['label']) for r in rows if r['state'] in ('exhausted', 'parked', 'cooldown'))
    every = weighted([(r['weight'], r['week_used']) for r in counted])
    regular = weighted([(r['weight'], r['week_used']) for r in counted if not r['reserve']])
    pool = {
        'running': True, 'version': VERSION, 'port': PORT,
        'used_pct': every if headline == 'all' else regular, 'used_pct_all': every, 'used_pct_regular': regular,
        'headline': headline, 'display': display, **({'balancing': balancing} if balancing else {}),
        'reserve_in_use': bool(serving and serving['reserve']),
        'regular_available': sum(1 for r in rows if r['state'] in ('active', 'ready') and not r['reserve']),
        'left_weight': round(sum(r['weight'] * (100 - r['week_used']) / 100 for r in counted
                                 if r['state'] in ('active', 'ready')), 2),
        'counted': len(counted), 'seats': len(counted),
        'available': sum(1 for r in rows if r['state'] in ('active', 'ready')),
        'resets_available': sum(r['resets']['available'] for r in rows),
    }
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
    for name, scenario, age, display in (('regular', 'regular', 12, 'left'), ('reserve', 'reserve', 21, 'left'),
                                         ('down', 'regular', 26 * 60, 'left'), ('used', 'regular', 12, 'used'),
                                         ('reset', 'regular', 12, 'left')):
        st = status(scenario, now - dt.timedelta(seconds=age), now - dt.timedelta(seconds=age), display=display,
                    balancing='reset' if name == 'reset' else None)
        (a.out / f'status-{name}.json').write_text(json.dumps(st, indent=1) + '\n')
        if name == scenario:
            lines = history(scenario, now)
            (a.out / f'history-{name}.jsonl').write_text(''.join(json.dumps(x) + '\n' for x in lines))
        pool = st['pool']
        print(f'{name}: all seats {pool["used_pct_all"]}% used, regular seats {pool["used_pct_regular"]}% used, '
              f'shown as {pool["display"]}, serving {st["active"]}')


if __name__ == '__main__':
    main()
