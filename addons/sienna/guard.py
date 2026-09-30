"""Claude usage polling, credit policy and guard pass.

Runtime paths and behavior are unchanged by the code extraction.
"""
from . import cp, desktop
from . import pool as sienna_pool

CLAUDE_USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'  # five_hour, seven_day, limits[], extra_usage

CLAUDE_POLL_SERVING = 3 * 60  # the account new sessions land on (the endpoint throttles; about 180 s is safe)

CLAUDE_POLL_IDLE = 10 * 60  # every other account

CLAUDE_BACKOFF_MAX = 60 * 60  # a 429 from the usage endpoint doubles the wait, up to this

CLAUDE_PLAN_EVERY = 24 * 3600  # the profile call (plan tier, default weight), once a day

CLAUDE_PLAN_RETRY = 3600  # after a profile call that failed

CLAUDE_PASSIVE_RETRY = 6 * 3600  # after Anthropic refused the pool's api-call, try it again this often

CLAUDE_WEEK_MIN = 7 * 24 * 60

CLAUDE_UNKNOWN_RESET = 3600  # a park whose limit names no reset is looked at again after this

CLAUDE_MISCLASSIFIED_PARK = 3600  # an account with credits off billed below its limit: parked this long at first,

CLAUDE_MISCLASSIFIED_MAX = 24 * 3600  # doubling for each alarm within a day of the one before, up to this

CLAUDE_CREDIT_FRESH = 15 * 60  # a credit reading older than this can't hold a last-resort account to its cap

CREDIT_EPSILON = 0.005  # a rise in used credits smaller than this (in the account's currency) is rounding

CLAUDE_SIGNALS = 'anthropic-ratelimit-unified-'  # the passive signals CLIProxyAPI keeps from every Claude answer

CLAUDE_SCOPED_SIGNAL = 'Fable'  # the 7d_oi window's model family, as CLIProxyAPI's own tests name it

CLAUDE_PARK_DETAIL = {
    'credits': 'credits off: plan limit or model exclusion unverified',
    'last-resort': 'last resort: waits until every account is out',
    'no-reading': 'last resort: no current credit reading, so no spending',
    'cap': 'credit cap reached',
    'misclassified': 'billed to credits below its limit',
    'exclusions': 'model exclusions unverified: parked until safe recovery',
}

CLAUDE_TIME_FRACTION = cp.re.compile(r'(\.\d+)(?=[+-]\d\d:?\d\d$|Z$)')


def claude_time(value):
    """A reset time as Anthropic gives it (ISO 8601 with any fraction of a second, or epoch seconds or milliseconds),
    as ISO 8601 in UTC, or None."""
    if value in (None, '') or isinstance(value, bool):
        return None
    n = cp._num(value)
    if n is not None:
        with cp.contextlib.suppress(OverflowError, OSError, ValueError):
            return cp.dt.datetime.fromtimestamp(n / 1000 if n > 1e12 else n, cp.dt.timezone.utc).isoformat()
        return None
    t = cp.parse_time(CLAUDE_TIME_FRACTION.sub('', str(value).strip()))
    return t.astimezone(cp.dt.timezone.utc).isoformat() if t else None


def claude_window(w, minutes=None):
    """{used, reset_at[, window_min]} from one window of the usage endpoint ({utilization: 0-100, resets_at}), or
    None when it gives no figure."""
    if not isinstance(w, dict):
        return None
    used = next((cp._num(w.get(k)) for k in ('utilization', 'percent', 'used_percentage') if cp._num(w.get(k)) is not None),
                None)
    if used is None:
        return None
    out = {'used': round(used, 1), 'reset_at': claude_time(w.get('resets_at') or w.get('reset_at'))}
    if minutes:
        out['window_min'] = minutes
    return out


def claude_credits_from_body(body):
    """{enabled, used, limit, currency} from the usage endpoint's extra_usage (and spend, when it has one), amounts in
    the account's currency: Anthropic gives them in minor units (cents), with their exponent in spend or
    extra_usage.decimal_places (2 when neither says). {} when the answer says nothing of credits."""
    extra = body.get('extra_usage') if isinstance(body.get('extra_usage'), dict) else {}
    spend = body.get('spend') if isinstance(body.get('spend'), dict) else {}
    if not extra and not spend:
        return {}
    used_m = spend.get('used') if isinstance(spend.get('used'), dict) else {}
    limit_m = spend.get('limit') if isinstance(spend.get('limit'), dict) else {}
    places = next((int(x) for x in (cp._num(used_m.get('exponent')), cp._num(limit_m.get('exponent')),
                                    cp._num(extra.get('decimal_places'))) if x is not None and 0 <= x <= 6), 2)

    def money(minor):
        v = cp._num(minor)
        return round(v / 10 ** places, places) if v is not None else None
    used = money(used_m.get('amount_minor')) if cp._num(used_m.get('amount_minor')) is not None \
        else money(extra.get('used_credits'))
    limit = money(limit_m.get('amount_minor')) if cp._num(limit_m.get('amount_minor')) is not None \
        else money(extra.get('monthly_limit'))
    enabled = extra.get('is_enabled') is True if 'is_enabled' in extra else spend.get('enabled') is True
    currency = used_m.get('currency') or limit_m.get('currency') or extra.get('currency') or 'USD'
    return {'enabled': enabled, 'used': used, 'limit': limit, 'currency': str(currency)}


def claude_usage_from_body(body, now=None):
    """A Claude account's usage from Anthropic's /api/oauth/usage answer (pure): five_hour and week
    ({used, reset_at}), scoped (the per-model weekly caps, [{name, used, reset_at, active}]), credits and when."""
    now = now or cp.now_utc()
    limits = [x for x in body.get('limits') or [] if isinstance(x, dict)] if isinstance(body.get('limits'), list) \
        else []

    def of_kind(kind):
        return next((x for x in limits if x.get('kind') == kind), None)
    five = claude_window(body.get('five_hour')) or claude_window(of_kind('session'))
    week = claude_window(body.get('seven_day'), CLAUDE_WEEK_MIN) or claude_window(of_kind('weekly_all'),
                                                                                  CLAUDE_WEEK_MIN)
    scoped, names = [], set()
    for x in limits:
        if x.get('kind') != 'weekly_scoped':
            continue
        scope = x.get('scope') if isinstance(x.get('scope'), dict) else {}
        model = scope.get('model') if isinstance(scope.get('model'), dict) else {}
        w = claude_window(x)
        name = str(model.get('display_name') or model.get('id') or 'model')
        if w and name.lower() not in names:
            names.add(name.lower())
            scoped.append({'name': name, 'used': w['used'], 'reset_at': w['reset_at'],
                           'active': x.get('is_active') is True})
    for key, name in (('seven_day_opus', 'Opus'), ('seven_day_sonnet', 'Sonnet')):  # legacy fields, null lately
        w = claude_window(body.get(key))
        if w and name.lower() not in names:
            names.add(name.lower())
            scoped.append({'name': name, 'used': w['used'], 'reset_at': w['reset_at'], 'active': False})
    return {'five_hour': five, 'week': week, 'scoped': scoped, 'credits': claude_credits_from_body(body),
            'overage': None, 'refused': False, 'at': now.isoformat(), 'source': 'poll'}


def claude_usage_from_signals(quota, scoped_name=None):
    """A Claude account's usage from the anthropic-ratelimit-unified-* headers of its last answer, which CLIProxyAPI
    keeps (quota.signals in /v0/management/auth-files), or None: utilization is a 0-1 fraction there and a reset is
    epoch seconds. These carry no credit amounts; overage is the -overage-status ("allowed": past its limit the
    account is served on credits). scoped_name: what the usage poll calls the scoped weekly cap."""
    sig = {str(k).lower(): v for k, v in ((quota or {}).get('signals') or {}).items()}
    if not any(k.startswith(CLAUDE_SIGNALS) for k in sig):
        return None
    observed = cp.parse_time(quota.get('observed_at'))

    def win(key, minutes=None, length=None):
        frac = cp._num(sig.get(f'{CLAUDE_SIGNALS}{key}-utilization'))
        rejected = str(sig.get(f'{CLAUDE_SIGNALS}{key}-status') or '').lower() == 'rejected'
        if frac is None and not rejected:
            return None
        used = round(frac * 100, 1) if frac is not None else 100.0
        reset = claude_time(sig.get(f'{CLAUDE_SIGNALS}{key}-reset'))
        if reset is None and observed and length:  # no reset given: it can't be in force longer than its window
            reset = (observed + cp.dt.timedelta(minutes=length)).isoformat()
        out = {'used': max(used, 100.0) if rejected else used, 'reset_at': reset}
        if minutes:
            out['window_min'] = minutes
        return out
    oi = win('7d_oi', length=CLAUDE_WEEK_MIN)
    return {'five_hour': win('5h', length=5 * 60), 'week': win('7d', CLAUDE_WEEK_MIN, CLAUDE_WEEK_MIN),
            'scoped': [{'name': scoped_name or CLAUDE_SCOPED_SIGNAL, 'used': oi['used'], 'reset_at': oi['reset_at'],
                        'active': False}] if oi else [],
            'credits': {}, 'overage': str(sig.get(f'{CLAUDE_SIGNALS}overage-status') or '').lower() or None,
            'overage_in_use': str(sig.get(f'{CLAUDE_SIGNALS}overage-in-use') or '').strip().lower() == 'true',
            'refused': str(sig.get(f'{CLAUDE_SIGNALS}status') or '').lower() == 'rejected',
            'at': observed.isoformat() if observed else None, 'source': 'live'}


def best_claude_usage(seat, cache, now=None):
    """A Claude account's usage, field by field from the newest source that has it: the live signals CLIProxyAPI
    keeps from its last answer and the guard's last usage poll (cache: claude-guard.json usage). A window past its
    reset no longer counts; credit amounts come from the poll only. None when neither says anything."""
    now = now or cp.now_utc()
    polled = cache.get(seat['name']) if isinstance(cache.get(seat['name']), dict) else {}
    scoped_names = [x.get('name') for x in polled.get('scoped') or [] if isinstance(x, dict)]
    live = claude_usage_from_signals(seat.get('quota') or {}, scoped_names[0] if len(scoped_names) == 1 else None)
    sources = [u for u in (live, polled if polled.get('at') else None) if u]
    if not sources:
        return None
    sources.sort(key=lambda u: cp.parse_time(u.get('at')) or cp.dt.datetime(2000, 1, 1, tzinfo=cp.dt.timezone.utc),
                 reverse=True)
    merged = {'at': sources[0].get('at'), 'source': sources[0].get('source')}
    for key in ('five_hour', 'week'):
        merged[key] = next((u[key] for u in sources if cp._open(u.get(key), now)), None)
    merged['scoped'] = next(([x for x in u['scoped'] if cp._open(x, now)] for u in sources if u.get('scoped')), [])
    merged['credits'] = dict(polled.get('credits') or {}) if isinstance(polled.get('credits'), dict) else {}
    merged['overage'] = (live or {}).get('overage')
    merged['overage_in_use'] = (live or {}).get('overage_in_use', False)
    merged['overage_at'] = (live or {}).get('at')
    merged['refused'] = bool(sources[0].get('refused'))
    merged['polled_at'] = polled.get('at')
    return merged


def claude_limits_hit(usage, now=None, scoped=True):
    """[(what, reset time or None)] for the limits of a Claude account's usage that are used up (100% or more, still
    in force): '5-hour', 'weekly' and, with scoped, each model's weekly cap by name (pure)."""
    now = now or cp.now_utc()
    if not usage:
        return []
    hit = [(what, cp.parse_time(w.get('reset_at'))) for what, w in (('5-hour', usage.get('five_hour')),
                                                                 ('weekly', usage.get('week')))
           if cp._open(w, now) and (cp._num(w.get('used')) or 0) >= 100]
    if scoped:
        hit += [(str(x.get('name') or 'model'), cp.parse_time(x.get('reset_at'))) for x in usage.get('scoped') or []
                if isinstance(x, dict) and cp._open(x, now) and (cp._num(x.get('used')) or 0) >= 100]
    return hit


def claude_limit_until(hit, now=None):
    """When the used-up limits all reset (the latest reset among them), ISO, or None when none names one."""
    now = now or cp.now_utc()
    resets = [t for _, t in hit if t and t > now]
    return max(resets).isoformat() if resets else None


def claude_limit_detail(hit):
    """'weekly limit reached', '5-hour limit reached' or 'Fable weekly limit reached' for the binding limit."""
    names = [what for what, _ in hit]
    for what in ('weekly', '5-hour'):
        if what in names:
            return f'{what} limit reached'
    return f'{names[0]} weekly limit reached' if names else ''


def claude_would_spend(usage):
    """True when a request past the plan limit would be paid with usage credits: they are on for the account at
    Anthropic (extra_usage.is_enabled), or its last answer said overage is allowed. A newer credits-off poll
    supersedes retained live headers; equal timestamps keep the overage warning."""
    if not usage:
        return False
    read = cp.parse_time(usage.get('polled_at'))
    overage = cp.parse_time(usage.get('overage_at'))
    if (usage.get('credits') or {}).get('enabled') is False and read and (overage is None or overage < read):
        return False
    return (usage.get('credits') or {}).get('enabled') is True or usage.get('overage_in_use') is True or \
        usage.get('overage') in ('allowed', 'allowed_warning')


def claude_seat_state(seat, guard, now, pool_only=False):
    """(state, detail, until) for one Claude account, the way seat_state has it for a Codex seat, in the Claude
    pool's words: parked says why the guard parked it (credits off, last resort, cap, misclassified), and an ended
    sign-in is Anthropic's."""
    g = (guard.get('seats') or {}).get(seat['name']) or {}
    if seat['disabled'] and g.get('parked_until'):
        detail = CLAUDE_PARK_DETAIL.get(g.get('parked_reason'), 'parked by the guard')
        return 'parked', detail, g['parked_until']
    state = cp._pool_state(seat, g, now)
    if pool_only or not g.get('sign_in_ended') or state[0] in ('parked', 'disabled'):
        return state
    return ('ready', sienna_pool.CLAUDE_SIGN_IN_SOON_DETAIL, None) if state[0] == 'ready' else \
        ('blocked', sienna_pool.CLAUDE_SIGN_IN_ENDED_DETAIL, None)


def claude_can_serve(row, now=None):
    """Whether a Claude status row can take new sessions on its plan quota: ready (or in a cooldown shorter than a
    blip) and not at its 5-hour or weekly limit, where it could only serve on usage credits."""
    now = now or cp.now_utc()
    if row.get('state') == 'cooldown':
        u = cp.parse_time(row.get('until'))
        if u is None or (u - now).total_seconds() > cp.BLIP_SECONDS:
            return False
    elif row.get('state') not in ('active', 'ready'):
        return False
    return not claude_limits_hit(row.get('usage'), now, scoped=False)


def claude_rows(seats, guard, now=None, meta=None, pool_only=False):
    """claude-status.json's seat rows (the Interface contract's shape), in the pool's fill order, before any is
    marked active. seats: the Claude pool's load_seats rows; guard: claude-guard.json; meta: claude-seats.json."""
    now = now or cp.now_utc()
    meta = cp.read_meta(cp.seat_pool('claude')) if meta is None else meta
    cache = guard.get('usage') or {}
    passive = guard.get('passive') if isinstance(guard.get('passive'), dict) else None
    rows = []
    for s in seats:
        if s['provider'] != 'claude':
            continue
        state, detail, until = claude_seat_state(s, guard, now, pool_only=pool_only)
        usage = best_claude_usage(s, cache, now)
        hit = claude_limits_hit(usage, now)
        plan_hit = claude_limits_hit(usage, now, scoped=False)
        spends = claude_would_spend(usage)
        if state == 'ready' and plan_hit and (not spends or (usage or {}).get('refused')):
            # its own usage says it is out; the pool learns on its next request (a 429, then a cooldown)
            state, detail, until = 'exhausted', claude_limit_detail(plan_hit), claude_limit_until(plan_hit, now)
        elif state == 'ready' and hit and not detail:
            detail = claude_limit_detail(hit)  # a model's weekly cap: the account still serves other models
        u = usage or {}
        week = u.get('week')
        week_used = cp._num((week or {}).get('used'))
        if week_used is None and state in ('exhausted', 'blocked'):
            week_used = 100.0
        c = dict(u.get('credits') or {})
        policy = sienna_pool.credit_policy(meta, s['name'])
        polled = cache.get(s['name']) if isinstance(cache.get(s['name']), dict) else {}
        error = polled.get('poll_error')
        if passive and u.get('source') != 'poll':
            error = (f'usage as of last served ({cp.when(u["at"])})' if u.get('at') else 'usage as of last served') + \
                ': Anthropic refused the pool\'s usage call'
        rows.append({
            'label': s['label'], 'name': s['name'], 'provider': 'claude', 'email': s.get('email'),
            'plan': s.get('plan'), 'priority': s['priority'], 'state': state, 'detail': detail, 'until': until,
            'weight': s.get('weight', 1.0), 'reserve': s.get('reserve', False),
            'week': week, 'five_hour': u.get('five_hour'), 'scoped': list(u.get('scoped') or []),
            'credits': {'enabled': c.get('enabled') if 'enabled' in c else None, 'used': c.get('used'),
                        'limit': c.get('limit'), **policy, 'currency': c.get('currency') or 'USD',
                        'spending': False, 'mismatch': policy['policy'] == 'off' and spends},
            'week_used': min(week_used, 100.0) if week_used is not None else None,
            'poll_error': error, 'usage_at': u.get('at'), 'usage_source': u.get('source'),
            'sign_in_ended': bool(((guard.get('seats') or {}).get(s['name']) or {}).get('sign_in_ended')),
            'order_reason': None, 'usage': usage,
        })
        r = rows[-1]
        r['credits']['spending'] = bool(state == 'ready' and spends and
                                        (plan_hit or u.get('overage_in_use')) and not u.get('refused'))
    return rows


def claude_status_down(reason, running):
    """claude-status.json while the Claude pool can't be read: its pool block, no accounts."""
    return {'generated_at': cp.now_utc().isoformat(),
            'pool': {'running': running, 'installed': True, 'version': cp.cpa_version(sienna_pool.CLAUDE_CURRENT),
                     'port': sienna_pool.CLAUDE_PORT, 'error': reason, 'headline': cp.SETTINGS['headline'],
                     'display': cp.SETTINGS['display'], 'balancing': cp.SETTINGS['claude_balancing'],
                     'route': sienna_pool.claude_route(), 'claude_code': claude_code_version(),
                     'desktop': desktop.desktop_status(live_version='')},
            'active': None, 'active_name': None, 'next_back': None, 'seats': []}


def build_claude_status(seats, guard, meta=None, pool_only=False, live_version=''):
    """claude-status.json: status.json's shape for the Claude pool (Interface contract), each account with its
    five_hour, week, scoped caps and credits {enabled, used, limit, policy, cap, currency, spending, mismatch}.
    live_version comes from the Claude auth-files response; missing evidence never causes a network probe."""
    now = cp.now_utc()
    rows = claude_rows(seats, guard, now, meta, pool_only=pool_only)
    if cp.SETTINGS['claude_balancing'] == 'reset':
        for r in rows:
            r['order_reason'] = cp.order_reason(dict(r, short=r.get('five_hour')), now)
    active_row = next((r for r in rows if r['state'] == 'ready'), None)
    if active_row:
        active_row['state'] = 'active'
    upcoming = sorted((cp.parse_time(r['until']), r['label']) for r in rows
                      if r['state'] in ('exhausted', 'parked', 'cooldown') and cp.parse_time(r['until']))
    passive = guard.get('passive') if isinstance(guard.get('passive'), dict) else None
    pool = {'running': True, 'installed': True, 'version': cp.cpa_version(sienna_pool.CLAUDE_CURRENT), 'port': sienna_pool.CLAUDE_PORT,
            'error': None}
    pool['desktop'] = desktop.desktop_status(seats, guard, meta if meta is not None else cp.read_meta(cp.seat_pool('claude')),
                                     live_version=live_version or '')
    counted = cp.headline_rows(rows)
    cp.set_headline(pool, rows)
    spender = next((r for r in rows if r['state'] == 'active' and r['credits'].get('spending')), None)
    on_quota = [r for r in rows if r['state'] in ('active', 'ready') and not r['credits'].get('spending')]
    pool.update(balancing=cp.SETTINGS['claude_balancing'], route=sienna_pool.claude_route(), claude_code=claude_code_version(),
                reserve_in_use=bool(active_row and active_row['reserve']),
                regular_available=len([r for r in on_quota if not r['reserve']]),  # on plan quota, not on credits
                left_weight=round(sum(r['weight'] * (100 - r['week_used']) / 100 for r in counted
                                      if r['state'] in ('active', 'ready')), 2),
                counted=len(counted), seats=len([r for r in rows if r['state'] != 'disabled']),
                available=len(on_quota),
                spending=spender['label'] if spender else None,
                usage_polling='passive' if passive else 'active')
    for r in rows:
        r.pop('usage', None)
    return {
        'generated_at': now.isoformat(),
        'pool': pool,
        'active': active_row['label'] if active_row else None,
        'active_name': active_row['name'] if active_row else None,
        'next_back': {'label': upcoming[0][1], 'at': upcoming[0][0].isoformat()} if upcoming else None,
        'seats': rows,
    }


def claude_code_version():
    """The installed Claude Code's version, from where its installer's link ~/.local/bin/claude points
    (…/versions/2.1.283): no process is started and no file of it is read. None when it can't be told."""
    with cp.contextlib.suppress(OSError, RuntimeError, ValueError):
        if sienna_pool.CLAUDE_REAL.is_symlink():
            m = cp.re.search(r'(?<![\d.])(\d+\.\d+\.\d+)(?![\d.])', cp.os.path.realpath(sienna_pool.CLAUDE_REAL))
            if m:
                return m.group(1)
    return None


def claude_api_call_refused(code, data):
    """Whether an api-call answer is Anthropic refusing the pool's own connection (PLAN §4.5: CLIProxyAPI's api-call
    uses Go's stock TLS, which Anthropic's edge may fingerprint): a 403 that is a challenge page, not a JSON error."""
    raw = data.get('_raw') if isinstance(data, dict) and set(data) == {'_raw'} else None
    text = (raw if raw is not None else cp.json.dumps(data) if not isinstance(data, str) else data).lower()
    return code == 403 and (raw is not None or any(w in text for w in ('cloudflare', 'challenge', 'just a moment')))


def claude_poll_usage(guard, seats, now, serving, save):
    """Step 1 of the Claude pass: poll each account's usage through the Claude pool's api-call ($TOKEN$), the
    serving account every CLAUDE_POLL_SERVING, the others every CLAUDE_POLL_IDLE, backing off on a 429. When
    Anthropic refuses the api-call itself, the pass falls back to the passive signals for CLAUDE_PASSIVE_RETRY."""
    passive = guard.get('passive') if isinstance(guard.get('passive'), dict) else None
    if passive and (cp.parse_time(passive.get('retry_at')) or now) > now:
        return
    order = sorted(seats, key=lambda s: guard['usage'].get(s['name'], {}).get('timed_out', False))
    for s in order:
        g = guard['seats'].get(s['name'], {})
        if s['disabled'] and not g.get('parked_until'):
            continue  # off by you: no traffic, nothing to watch
        cached = guard['usage'].get(s['name'], {})
        every = CLAUDE_POLL_SERVING if s['name'] == serving else CLAUDE_POLL_IDLE
        last = cp.parse_time(cached.get('polled_at'))
        if last and (now - last).total_seconds() < max(every, cached.get('backoff') or 0):
            continue
        try:
            code, data = sienna_pool.claude_seat_call(s, CLAUDE_USAGE_URL)
        except cp.PoolTimeout:
            guard['usage'][s['name']] = {**cached, 'polled_at': now.isoformat(), 'timed_out': True,
                                         'poll_error': 'usage poll timed out'}
            break
        except cp.ApiError as e:
            guard['usage'][s['name']] = {**cached, 'polled_at': now.isoformat(), 'poll_error': cp.short(str(e), 160)}
            continue
        except (cp.PoolDown, cp.KeyUnavailable):
            break
        if code == 200 and isinstance(data, dict) and '_raw' not in data:
            try:
                usage = claude_usage_from_body(data, now)
            except (AttributeError, TypeError, ValueError) as e:
                guard['usage'][s['name']] = {**cached, 'polled_at': now.isoformat(),
                                             'poll_error': f'unexpected usage shape: {cp.short(str(e), 100)}'}
                continue
            guard['usage'][s['name']] = {**usage, 'polled_at': now.isoformat()}
            if guard.pop('passive', None):
                cp.log_line('guard: claude: Anthropic answers the pool\'s usage call again; active polling is back')
            continue
        if code == 429:
            wait = min(max(every, cached.get('backoff') or 0) * 2, CLAUDE_BACKOFF_MAX)
            guard['usage'][s['name']] = {**cached, 'polled_at': now.isoformat(), 'backoff': wait,
                                         'poll_error': f'usage endpoint busy (429); next try in {cp.span_text(wait)}'}
            continue
        if claude_api_call_refused(code, data):
            why = f'HTTP {code} from api.anthropic.com to the pool\'s own usage call'
            if not passive:
                cp.log_line(f'guard: claude: {why}; usage now comes from each account\'s last served request')
                cp.notify('Claude usage polling is off', 'Anthropic refused the pool\'s usage call. Usage now shows '
                       'as of each account\'s last served request. Run: codexpool doctor')
            guard['passive'] = {'since': (passive or {}).get('since') or now.isoformat(), 'why': why,
                                'retry_at': (now + cp.dt.timedelta(seconds=CLAUDE_PASSIVE_RETRY)).isoformat()}
            break
        guard['usage'][s['name']] = {**cached, 'polled_at': now.isoformat(),
                                     'poll_error': f'HTTP {code}: {cp.short(cp.json.dumps(data), 120)}'}
    save()


def claude_poll_plans(guard, seats, now, save, meta):
    """Step 0 of the Claude pass: each account's plan tier from Anthropic's profile call, once a day (sooner after
    one that failed), into claude-guard.json plans; the seat rows take the plan and, unless you gave a size, its
    weight."""
    plans = guard.setdefault('plans', {})
    for s in seats:
        p = plans.get(s['name']) if isinstance(plans.get(s['name']), dict) else {}
        at, tried = cp.parse_time(p.get('at')), cp.parse_time(p.get('tried'))
        fresh = at and (now - at).total_seconds() < CLAUDE_PLAN_EVERY
        if fresh or (tried and (now - tried).total_seconds() < CLAUDE_PLAN_RETRY):
            continue
        try:
            tier, weight, problem = sienna_pool.claude_profile_plan(s, reraise=(cp.PoolTimeout,))
        except cp.PoolTimeout as e:  # Anthropic isn't answering the pool: the next account's call would wait as long
            plans[s['name']] = {**p, 'tried': now.isoformat(timespec='seconds'), 'error': cp.short(str(e), 120)}
            save()
            break  # one timeout per pass at most; the others are asked on the next passes
        if tier:
            plans[s['name']] = {'plan': tier, 'weight': weight, 'at': now.isoformat(timespec='seconds')}
        else:
            plans[s['name']] = {**p, 'tried': now.isoformat(timespec='seconds'), 'error': problem}
        save()
    claude_apply_plans(guard, seats, meta)


def claude_apply_plans(guard, seats, meta):
    """Give load_seats rows the plan claude-guard.json has for each account and, unless you gave a size, its
    weight."""
    plans = guard.get('plans') if isinstance(guard.get('plans'), dict) else {}
    for s in seats:
        p = plans.get(s['name']) if isinstance(plans.get(s['name']), dict) else {}
        if p.get('plan'):
            s['plan'] = p['plan']
            entry = meta.get(s['name']) if isinstance(meta.get(s['name']), dict) else {}
            if not cp.valid_weight(entry.get('weight')) and cp.valid_weight(p.get('weight')):
                s['weight'] = float(p['weight'])


def claude_unpark(guard, seats, now, save, pool):
    """Step 2: put back the accounts whose park has run out (their limit reset), forget park records that never took
    effect or that you undid (codexpool claude enable), and let overrides expire. An account whose park ran out while
    another limit is still used up (or whose estimated reset came early) and would spend credits stays parked, until
    that limit's reset; step 3 then applies its credit policy to it with the reading step 1 just took."""
    by_name = {s['name']: s for s in seats}
    for name, g in guard['seats'].items():
        if name not in by_name:
            continue
        until = cp.parse_time(g.get('parked_until'))
        if until and not by_name[name]['disabled']:
            for k in ('parked_until', 'parked_reason', 'parked_at', 'parked_not_before'):
                g.pop(k, None)
            save()
        elif until and now >= until:
            if sienna_pool.credit_policy(cp.read_meta(pool), name)['policy'] == 'last-resort':
                if g.get('parked_reason') == 'misclassified':
                    g['parked_reason'] = 'last-resort'
                    save()
                continue  # never briefly enable before rechecking other plans and the cap
            if g.get('parked_reason') in ('credits', 'last-resort', 'no-reading', 'cap', 'exclusions'):
                continue  # the credit pass restores it only when exclusions can be checked safely
            usage = best_claude_usage(by_name[name], guard['usage'], now)
            hit = claude_limits_hit(usage, now, scoped=False) if claude_would_spend(usage) else []
            if hit:
                g['parked_until'] = claude_limit_until(hit, now) or \
                    (now + cp.dt.timedelta(seconds=CLAUDE_UNKNOWN_RESET)).isoformat()
                if g.get('parked_reason') == 'misclassified':
                    g['parked_reason'] = 'credits'  # at a limit now: step 3 gives it its policy's reason
                save()
                cp.log_line(f'guard: claude: {by_name[name]["label"]} stays parked ({claude_limit_detail(hit)}) until '
                         f'{g["parked_until"]}')
                continue
            try:
                cp.set_disabled(name, False, pool)
            except (cp.ApiError, cp.PoolDown, cp.KeyUnavailable):
                continue  # keep the record; retry next pass
            for k in ('parked_until', 'parked_reason', 'parked_at', 'parked_not_before'):
                g.pop(k, None)
            polled = cp.parse_time((guard['usage'].get(name) or {}).get('polled_at'))
            if not polled or polled < until:
                guard['usage'].pop(name, None)  # a reading from before the reset: poll it afresh next pass
            by_name[name]['disabled'] = False
            save()
            cp.notify('Claude account back in the pool', f'{by_name[name]["label"]} has reset.')
        override = cp.parse_time(g.get('override_until'))
        if override and now >= override:
            g.pop('override_until', None)
            save()


def claude_credit_allowed(row, rows, now):
    """Whether current observations permit last-resort credits (not a pre-request enforcement mechanism)."""
    policy = row.get('credits') or {}
    usage = row.get('usage') or {}
    used, cap = cp._num(policy.get('used')), cp._num(policy.get('cap'))
    read = cp.parse_time(usage.get('polled_at'))
    return policy.get('policy') == 'last-resort' and used is not None and cap is not None and used < cap and \
        read is not None and 0 <= (now - read).total_seconds() <= CLAUDE_CREDIT_FRESH and \
        all(claude_limits_hit(r.get('usage'), now, scoped=False) for r in rows if r['name'] != row['name'])


def claude_model_exclusions(row, rows, now):
    """Only known model families: a scoped cap must not bench unrelated plan capacity."""
    if claude_credit_allowed(row, rows, now):
        return []
    families = {'fable', 'opus', 'sonnet'}
    hit = claude_limits_hit(row.get('usage'), now)
    wanted = {name.lower() for name, _ in hit if name.lower() in families}
    mismatch = (row.get('credits') or {}).get('policy', 'off') == 'off' and claude_would_spend(row.get('usage'))
    if mismatch and row.get('plan') not in ('max_5x', 'max_20x', 'team_premium'):
        wanted.add('fable')  # unknown/Enterprise plans do not prove included access
    usage = row.get('usage') or {}
    if mismatch:
        wanted.update(str(w.get('name') or '').lower() for w in usage.get('scoped') or []
                      if isinstance(w, dict) and cp._open(w, now) and (cp._num(w.get('used')) or 0) >= 95
                      and str(w.get('name') or '').lower() in families)
    return sorted('claude-' + family + '*' for family in wanted)


def claude_sync_exclusions(seat, wanted, guard, save, pool):
    """Preserve manual exclusions and journal our additions before PATCH. Verify routing, not just persistence.
    The existing non-secret claims reader supplies metadata; never download credentials through management."""
    g = guard['seats'].setdefault(seat['name'], {})
    previous_error = g.get('exclusion_error')
    owned = set(g.get('excluded_models_owned') or [])
    if not wanted and not owned:
        g.pop('exclusion_error', None)
        g.pop('exclusion_pending', None)
        g.pop('exclusion_park_notified', None)
        return
    current = seat.get('excluded_models')
    if not isinstance(current, list) or any(not isinstance(x, str) for x in current):
        g['exclusion_error'] = 'Cannot preserve existing model exclusions: metadata unavailable'
        if g['exclusion_error'] != previous_error:
            cp.log_line(f'guard: claude: {seat["label"]}: model exclusion not verified')
        save()
        return
    current = {x.strip().lower() for x in current if x.strip()}
    manual = current - owned
    desired = manual | set(wanted)
    new_owned = set(wanted) - manual
    # Keep old ownership until PATCH succeeds, so interrupted clears can be retried.
    g['excluded_models_owned'] = sorted(owned | new_owned)
    save()
    try:
        if current != desired:
            cp.api('PATCH', '/v0/management/auth-files/fields',
                {'name': seat['name'], 'excluded_models': sorted(desired)}, port=pool.port)
            seat['excluded_models'] = sorted(desired)
        g['excluded_models_owned'] = sorted(new_owned)
        if seat['disabled']:
            # CPA unregisters disabled accounts. Read back saved metadata, never require models.
            saved = next((s.get('excluded_models') for s in cp.load_seats(pool) if s['name'] == seat['name']), None)
            if not isinstance(saved, list) or not set(wanted).issubset({str(x).lower() for x in saved}):
                raise ValueError('Model exclusions are not verified in the auth file')
            g.pop('exclusion_pending', None)
            g.pop('exclusion_error', None)
            save()
            return
        path = '/v0/management/auth-files/models?' + cp.urllib.parse.urlencode({'name': seat['name']})
        result = cp.api('GET', path, port=pool.port)
        models = result.get('models') if isinstance(result, dict) else None
        if not isinstance(models, list) or not models:
            raise ValueError('No registered models to verify')
        ids = [str(m.get('id', '')).lower() for m in models if isinstance(m, dict)]
        if any(mid.startswith(pattern[:-1]) for mid in ids for pattern in wanted):
            raise ValueError('Model exclusions are pending CPA registration')
        g.pop('exclusion_error', None)
        g.pop('exclusion_pending', None)
        g.pop('exclusion_park_notified', None)
    except (cp.ApiError, cp.PoolDown, cp.KeyUnavailable, ValueError) as e:
        g['exclusion_error'] = cp.short(str(e), 160)
        if g['exclusion_error'] != previous_error:
            cp.log_line(f'guard: claude: {seat["label"]}: model exclusion not verified')
    save()


def claude_credits_disabled(usage, now):
    """A recent successful usage reading says Anthropic itself refuses billable requests."""
    usage = usage or {}
    read = cp.parse_time(usage.get('polled_at'))
    overage = cp.parse_time(usage.get('overage_at')) if usage.get('overage_in_use') or \
        usage.get('overage') in ('allowed', 'allowed_warning') else None
    return (usage.get('credits') or {}).get('enabled') is False and read is not None and \
        0 <= (now - read).total_seconds() <= CLAUDE_CREDIT_FRESH and (overage is None or overage < read)


def claude_alarm_until(g, now):
    """Repeated overage observations and credit increases share the same capped backoff."""
    last = cp.parse_time(g.get('alarm_at'))
    n = int(g.get('alarms') or 0) + 1 if last and 0 <= (now - last).total_seconds() < CLAUDE_MISCLASSIFIED_MAX else 1
    g.update(alarms=n, alarm_at=now.isoformat())
    until = (now + cp.dt.timedelta(seconds=min(CLAUDE_MISCLASSIFIED_PARK * 2 ** min(n - 1, 10),
                                           CLAUDE_MISCLASSIFIED_MAX))).isoformat()
    g['parked_not_before'] = until  # a later limit reading must not shorten an alarm's backoff
    return until


def claude_park_recoverable(g, usage, now):
    """Missing live headers are not a reset: require expiry or a successful poll newer than the park.
    Alarm/exclusion backoffs always run to their deadline, even when disabled-seat metadata verifies."""
    if (cp.parse_time(g.get('parked_not_before')) or now) > now:
        return False
    until = cp.parse_time(g.get('parked_until'))
    if until and now >= until:
        return True
    read = cp.parse_time((usage or {}).get('polled_at'))  # successful reading, not the last attempted poll
    parked = cp.parse_time(g.get('parked_at'))
    return bool(read and parked and parked < read <= now and
                (now - read).total_seconds() <= CLAUDE_CREDIT_FRESH)


def claude_credit_override(g, row, now):
    """Only policy off can be temporarily overridden by an explicit enable."""
    return (row.get('credits') or {}).get('policy') == 'off' and \
        (cp.parse_time(g.get('override_until')) or now) > now


def claude_overage_family(seat, observed):
    """Attribute an overage only when CPA has a matching model observation; a scoped cap alone is not attribution."""
    families = set()
    for model, quota in (seat.get('model_quotas') or {}).items():
        usage = claude_usage_from_signals(quota)
        if not usage or not usage.get('overage_in_use') or cp.parse_time(usage.get('at')) != observed:
            continue
        match = cp.re.match(r'^claude-(fable|opus|sonnet)(?:-|$|\[)', model.lower())
        if not match:
            return None
        families.add(match.group(1))
    return next(iter(families)) if len(families) == 1 else None


def claude_credit_pass(guard, seats, now, save, pool, meta, finalize=True):
    """Step 3: each account's credit policy, and the misclassification alarm (PLAN §4.4). The park record is always
    written before the account is disabled, and an account you disabled yourself is never enabled.
    - off: shared limits park the seat; scoped limits exclude only that model family.
    - last-resort (with a cap): always parked while any other account (the reserve included) has plan quota; once
      every other account is out, it is enabled to spend credits until they reach the cap (then parked) or another
      account can serve again (then parked again). Without a credit reading it never spends.
    - alarm: a new overage observation or credits rising below plan limits can be fast mode or other billable
      traffic. Park outside policy; observations cannot prevent the first billable request."""
    rows = claude_rows(seats, guard, now, meta)
    by_row = {r['name']: r for r in rows}
    seen = guard.setdefault('credits_seen', {})
    for name in list(seen):
        if name not in by_row:
            del seen[name]
    spending_was = guard.get('spending')
    for s in seats:
        r = by_row[s['name']]
        wanted = claude_model_exclusions(r, rows, now)
        claude_sync_exclusions(s, wanted, guard, save, pool)
        g = guard['seats'].setdefault(s['name'], {})
        g.pop('exclusion_recovery_pending', None)
        alarm_at = cp.parse_time(g.get('alarm_at'))
        if s['disabled'] and g.get('parked_until') and (g.get('parked_reason') == 'misclassified' or
                (g.get('parked_reason') in ('credits', 'exclusions') and alarm_at is not None and
                 alarm_at == cp.parse_time(g.get('parked_at')))):
            g.setdefault('parked_not_before', g['parked_until'])  # preserve pre-upgrade alarm parks too
        if finalize and s['disabled'] and g.get('parked_reason') in ('credits', 'exclusions', 'last-resort', 'cap', 'no-reading') and \
                r['credits']['policy'] == 'off' and not claude_limits_hit(r.get('usage'), now, scoped=False) and \
                (g.get('parked_reason') not in ('credits', 'exclusions') or
                 claude_park_recoverable(g, r.get('usage'), now)) and \
                (claude_credits_disabled(r.get('usage'), now) or not g.get('exclusion_error')):
            try:
                cp.set_disabled(s['name'], False, pool)
            except (cp.ApiError, cp.PoolDown, cp.KeyUnavailable):
                continue
            expired = (cp.parse_time(g.get('parked_until')) or now) <= now
            for key in ('parked_until', 'parked_reason', 'parked_at', 'parked_not_before'):
                g.pop(key, None)
            if expired:
                cp.notify('Claude account back in the pool', f'{s["label"]} has reset.')
            s['disabled'], r['state'] = False, 'ready'
            g.pop('exclusion_recovery_pending', None)
            save()
            claude_sync_exclusions(s, wanted, guard, save, pool)

    def park(s, until, reason, title, body):
        g = guard['seats'].setdefault(s['name'], {})
        until = max(cp.parse_time(until), cp.parse_time(g.get('parked_not_before')) or now).isoformat()
        g.update(parked_until=until, parked_reason=reason, parked_at=now.isoformat())
        save()  # the record first, so a crash cannot orphan a disabled account
        try:
            cp.set_disabled(s['name'], True, pool)
        except (cp.ApiError, cp.PoolDown, cp.KeyUnavailable) as e:
            cp.log_line(f'guard: claude: could not park {s["label"]}: {cp.short(str(e), 120)}')
            return False  # step 2 drops the record next pass if the account is still enabled; this step retries
        s['disabled'] = True
        by_row[s['name']]['state'] = 'parked'
        cp.log_line(f'guard: claude: parked {s["label"]} ({reason}) until {until}')
        if title:
            cp.notify(title, body)
        return True

    def ours(s):  # disabled by the guard (a park record), not by you
        return s['disabled'] and bool((guard['seats'].get(s['name']) or {}).get('parked_until'))

    for s in seats:
        g, r = guard['seats'][s['name']], by_row[s['name']]
        wanted = claude_model_exclusions(r, rows, now)
        if finalize and wanted and g.get('exclusion_error'):
            claude_sync_exclusions(s, wanted, guard, save, pool)  # one bounded registration retry this pass
        if finalize and wanted and g.get('exclusion_error') and not s['disabled'] and \
                not claude_credit_override(g, r, now) and \
                not claude_credits_disabled(r.get('usage'), now) and claude_would_spend(r.get('usage')):
            until = claude_alarm_until(g, now)  # count the failed exclusion even at a shared plan limit
            hit = claude_limits_hit(r.get('usage'), now, scoped=False)
            if hit:
                until = claude_limit_until(hit, now) or \
                    (now + cp.dt.timedelta(seconds=CLAUDE_UNKNOWN_RESET)).isoformat()
            if park(s, until, 'credits',
                    None if g.get('exclusion_park_notified') else 'Claude model exclusion pending',
                    f'{s["label"]}: required model exclusions are not verified; '
                    'parked to limit further spending. Turn credits off at claude.ai (Settings → Usage).'):
                g['exclusion_park_notified'] = True
                save()

    for s in seats:
        g, r = guard['seats'][s['name']], by_row[s['name']]
        u = r.get('usage') or {}
        observed = cp.parse_time(u.get('overage_at'))
        seen_at = cp.parse_time(g.get('overage_seen_at'))
        if not u.get('overage_in_use') or not observed or not 0 <= (now - observed).total_seconds() <= \
                CLAUDE_CREDIT_FRESH or (seen_at and observed <= seen_at):
            continue
        # Shared plan limits are parked by the policy step below, until their reset, not an alarm backoff.
        if not s['disabled'] and not claude_credit_override(g, r, now) and \
                spending_was != s['name'] and not claude_credit_allowed(r, rows, now) and \
                not claude_limits_hit(u, now, scoped=False):
            # A known scoped limit is handled by model exclusions; an unattributed billable request cannot be.
            family = claude_overage_family(s, observed)
            excluded = family and 'claude-' + family + '*' in claude_model_exclusions(r, rows, now)
            if excluded and g.get('exclusion_error') and not finalize:
                continue  # give registration the rest of this guard pass, then check again
            if not excluded or g.get('exclusion_error'):
                until = claude_alarm_until(g, now)
                if not park(s, until, 'misclassified', 'Claude account billed to credits below its limit',
                            f'{s["label"]}: Anthropic reported usage credits in use outside its policy; parked. '
                            'Turn credits off at claude.ai to prevent spending before the guard can observe it.'):
                    continue
        g['overage_seen_at'] = observed.isoformat()
        save()

    # credits spent below the plan limit, on each new credit reading. Fast mode (/fast) bills usage credits even
    # with plan quota left, and so would Anthropic billing the pool's requests as third-party: the two look the same
    # from here. Last-resort still needs all shared plan quota spent; credits off parks the seat.
    for s in seats:
        g = guard['seats'].setdefault(s['name'], {})
        r = by_row[s['name']]
        c = (guard['usage'].get(s['name']) or {}).get('credits') or {}
        used, polled = cp._num(c.get('used')), (guard['usage'].get(s['name']) or {}).get('at')
        if used is None or not polled:
            continue
        below = not claude_limits_hit(r['usage'], now)
        prev = seen.get(s['name']) if isinstance(seen.get(s['name']), dict) else None
        if prev and prev.get('at') == polled:
            continue  # no new reading since the last pass
        watched = not s['disabled'] and not claude_credit_override(g, r, now)
        rose = bool(prev) and watched and bool(prev.get('below')) and below and \
            used > (cp._num(prev.get('used')) or 0) + CREDIT_EPSILON
        allowed = claude_credit_allowed(r, rows, now)
        seen[s['name']] = {'used': used, 'below': below, 'at': polled, 'rising': rose, 'allowed': allowed}
        if not rose:
            continue
        cur, q, policy = c.get('currency') or 'USD', cp.shlex.quote(s['label']), sienna_pool.credit_policy(meta, s['name'])
        spent = f'{money_text(prev["used"], cur)} → {money_text(used, cur)}'
        if policy['policy'] == 'last-resort':
            cap = policy['cap']
            if not allowed and (cap is None or used < cap):
                if prev.get('allowed') or spending_was == s['name']:
                    continue  # permission ended since the previous reading; re-park normally below
                until = claude_alarm_until(g, now)
                park(s, until, 'misclassified', 'Claude credit policy violation',
                     f'{s["label"]} spent credits while plan quota remains; parked. Fast mode always bills credits.')
                continue
            if cap is not None and used >= cap:
                until = (now + cp.dt.timedelta(seconds=CLAUDE_UNKNOWN_RESET)).isoformat()
                park(s, until, 'cap', 'Claude credit cap reached',
                     f'{s["label"]} has spent {money_text(used, cur)} of its {money_text(cap, cur)} cap below its plan '
                     f'limit (fast mode bills credits); parked until {cp.when(until)}. Raise it: codexpool claude '
                     f'credits {q} last-resort --cap <USD>')
            elif not prev.get('rising'):
                cp.log_line(f'guard: claude: {s["label"]} spends usage credits below its plan limit ({spent})')
                cp.notify('Claude account spending usage credits',
                       f'{s["label"]} spent usage credits below its plan limit ({spent}). Fast mode (/fast) bills '
                       'credits like this; if you are not using it, check the account at claude.ai.'
                       + (f' codexpool stops it at its {money_text(cap, cur)} cap.' if cap is not None else ''))
            continue
        until = claude_alarm_until(g, now)
        park(s, until, 'misclassified', 'Claude account billed to credits below its limit',
             f'{s["label"]} spent usage credits below its plan limit ({spent}), and its credits are off in codexpool; '
             f'parked until {cp.when(until)}. Fast mode (/fast) bills credits like this; if you are not using it, check '
             f'the account at claude.ai. Back now: codexpool claude enable {q}')
    save()

    # plan limits and each account's policy
    others_serve = {s['name']: any(claude_can_serve(r, now) for n, r in by_row.items() if n != s['name'])
                    for s in seats}
    spender = None
    for s in seats:  # in the fill order: the first last-resort account that may spend is the one that does
        g = guard['seats'].setdefault(s['name'], {})
        r = by_row[s['name']]
        policy = sienna_pool.credit_policy(meta, s['name'])
        usage = r['usage']
        hit = claude_limits_hit(usage, now, scoped=False)
        if (s['disabled'] and not ours(s)) or claude_credit_override(g, r, now) or \
                (g.get('parked_reason') == 'misclassified' and not hit):
            continue  # yours to change (disabled, or enabled over the guard), or waiting out the alarm
        if policy['policy'] != 'last-resort' and (not hit or not claude_would_spend(usage)):
            continue  # on its plan quota, or out without credits (the pool refuses it; nothing is spent)
        until = claude_limit_until(hit, now) or (now + cp.dt.timedelta(seconds=CLAUDE_UNKNOWN_RESET)).isoformat()
        until = max(cp.parse_time(until), cp.parse_time(g.get('parked_not_before')) or now).isoformat()
        q = cp.shlex.quote(s['label'])
        if policy['policy'] != 'last-resort':
            if not s['disabled']:
                park(s, until, 'credits', 'Claude account parked', f'{s["label"]} is at its plan limit '
                     f'({claude_limit_detail(hit)}) and would spend usage credits. Back {cp.when(until)}. '
                     f'Override: codexpool claude enable {q}')
            elif g.get('parked_reason') != 'credits' or g.get('parked_until') != until:
                # A fresh poll can reveal a shared limit after the early overage pass already parked it.
                g.update(parked_reason='credits', parked_until=until)
                save()
            continue
        c = (usage or {}).get('credits') or {}
        used, cap, cur = cp._num(c.get('used')), policy['cap'], c.get('currency') or 'USD'
        read = cp.parse_time((usage or {}).get('polled_at'))
        fresh = read is not None and 0 <= (now - read).total_seconds() <= CLAUDE_CREDIT_FRESH
        was_spending = spending_was == s['name'] and not s['disabled']
        if used is not None and cap is not None and used >= cap:
            reason, title, body = 'cap', 'Claude credit cap reached', (
                f'{s["label"]} has spent {money_text(used, cur)} of its {money_text(cap, cur)} cap; parked until '
                f'{cp.when(until)}. Raise it: codexpool claude credits {q} last-resort --cap <USD>')
        elif used is None or cap is None or not fresh:
            reason, title, body = 'no-reading', 'Claude account parked', (
                f'{s["label"]}: without a current credit reading codexpool cannot hold it to '
                f'its cap, so it spends nothing. Back {cp.when(until)}.')
        elif not claude_credit_allowed(r, rows, now) or others_serve[s['name']] or spender is not None:
            reason, title, body = 'last-resort', 'Claude account parked', (
                f'{s["label"]} waits as the very last resort, until '
                f'every other account is out. Back {cp.when(until)}.')
            if was_spending:
                title, body = 'Claude account parked again', (
                    f'Another account can serve again, so {s["label"]} stops spending usage credits '
                    f'({money_text(used, cur)} of its {money_text(cap, cur)} cap spent).')
        else:
            spender = s['name']
            if ours(s):
                try:
                    cp.set_disabled(s['name'], False, pool)
                except (cp.ApiError, cp.PoolDown, cp.KeyUnavailable) as e:
                    cp.log_line(f'guard: claude: could not enable {s["label"]} as the last resort: {cp.short(str(e), 120)}')
                    continue
                for k in ('parked_until', 'parked_reason', 'parked_at', 'parked_not_before'):
                    g.pop(k, None)
                s['disabled'] = False
                r['state'] = 'ready'
                save()
            if spending_was != s['name']:
                spending = claude_would_spend(usage) and (claude_limits_hit(usage, now) or
                                                         (usage or {}).get('overage_in_use'))
                cp.log_line(f'guard: claude: {s["label"]} serves as the last resort')
                cp.notify('Claude is spending usage credits' if spending else 'Claude is serving as the last resort',
                       f'Every other account is out, so {s["label"]} serves as the last resort '
                       f'({money_text(used, cur)} of its {money_text(cap, cur)} '
                       'cap). codexpool parks it at the cap or as soon as another account can serve.')
            continue
        if s['disabled']:  # parked already: keep the record's reason up to date, quietly
            if g.get('parked_reason') != reason or (cp.parse_time(g.get('parked_until')) or now) <= now or \
                    (hit and g.get('parked_until') != until):
                g.update(parked_reason=reason, parked_until=until)
                save()
            continue
        park(s, until, reason, title, body)
    if spender:
        guard['spending'] = spender
    else:
        guard.pop('spending', None)
    save()


def claude_apply_credit_policy(pool):
    """Step 3 of the Claude pass right away, on the usage the guard last read: after you change an account's credit
    policy, so an account that may no longer spend is parked now, not a minute later. The caller holds the guard
    lock. Quietly does nothing when the pool can't be read (the next guard pass applies it)."""
    guard = cp.read_guard(pool)
    guard.setdefault('seats', {})
    guard.setdefault('usage', {})
    try:
        seats = [s for s in cp.load_seats(pool) if s['provider'] == 'claude']
    except (cp.PoolDown, cp.ApiError, cp.KeyUnavailable):
        return
    meta = cp.read_meta(pool)
    claude_apply_plans(guard, seats, meta)
    claude_credit_pass(guard, seats, cp.now_utc(), lambda: cp.write_json(pool.guard, guard), pool, meta)


def money_text(amount, currency='USD'):
    """'$12.40' (US dollars), '12.40 EUR' otherwise."""
    v = cp._num(amount) or 0.0
    return f'${v:,.2f}' if (currency or 'USD').upper() == 'USD' else f'{v:,.2f} {currency}'


def claude_balance_rows(seats, guard):
    """The Claude status rows reset balancing orders (balance_pass rows_of): its week and, as short, five_hour."""
    return [dict(r, short=r.get('five_hour')) for r in build_claude_status(seats, guard)['seats']]


def claude_guard_pass():
    """The guard's pass over the Claude pool (after the Codex pass, only while the Claude pool is installed): plan
    tiers, usage, parks that ran out, credit policies, healing, balancing, notifications, then claude-status.json and
    claude-history.jsonl. Its state is claude-guard.json; the Codex pass's files are never touched."""
    pool = cp.seat_pool('claude')
    guard = cp.read_guard(pool)
    guard.setdefault('seats', {})
    guard.setdefault('usage', {})

    def save():
        cp.write_json(pool.guard, guard)

    headers = {}  # live evidence from this pass only, never a persisted or cross-pool version
    tokens = cp.seat_tokens(pool)  # before any evidence below, so a sign-in during this pass never counts as ended
    try:
        seats = [s for s in cp.load_seats(pool, response_headers=headers) if s['provider'] == 'claude']
    except cp.PoolDown as e:
        guard['down_count'] = guard.get('down_count', 0) + 1
        if guard['down_count'] == cp.DOWN_ALERT_AFTER:
            cp.notify('Claude pool is not responding', 'launchd keeps restarting it. Run: codexpool doctor')
        save()
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, claude_status_down(str(e), False))
        return
    except (cp.ApiError, cp.KeyUnavailable) as e:
        msg = cp.short(str(e), 160)
        if not guard.get('mgmt_error_notified'):
            cp.notify('codexpool is locked out of the Claude pool', f'{msg[:90]}. Its credit guard is off. '
                   'Run: codexpool doctor')
            guard['mgmt_error_notified'] = True
        save()
        cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, claude_status_down(
            msg, cp.port_open(sienna_pool.CLAUDE_PORT) if isinstance(e, cp.KeyUnavailable) else True))
        return
    if guard.get('down_count', 0) >= cp.DOWN_ALERT_AFTER or guard.get('mgmt_error_notified'):
        cp.notify('Claude pool is back', 'codexpool can see the Claude pool again.')
    guard['down_count'] = 0
    guard.pop('mgmt_error_notified', None)
    cp.recover_selftest(guard, pool)

    now = cp.now_utc()
    by_name = {s['name']: s for s in seats}
    for key in ('seats', 'usage', 'plans'):
        if isinstance(guard.get(key), dict):
            for name in list(guard[key]):
                if name not in by_name:
                    del guard[key][name]
    meta = cp.read_meta(pool)

    # Act on CPA's response observations before any slower profile/usage calls. No extra usage poll required.
    claude_apply_plans(guard, seats, meta)
    claude_credit_pass(guard, seats, now, save, pool, meta, finalize=False)
    claude_poll_plans(guard, seats, now, save, meta)  # 0. plan tiers (daily)
    claude_poll_usage(guard, seats, now, guard.get('announced'), save)  # 1. usage
    claude_unpark(guard, seats, now, save, pool)  # 2. parks that ran out
    claude_credit_pass(guard, seats, now, save, pool, meta)  # 3. credit policies and the alarm
    cp.heal_pass(guard, seats, now, tokens, save, pool)  # 4. healing, sign-ins Anthropic ended
    # 4b. balancing ("claude_balancing"), and a fill order you changed yourself since the last pass
    reordered = cp.balance_pass(guard, seats, now, pool, claude_balance_rows)
    reordered = bool(guard.pop('you_reordered', None)) or reordered

    # 5. tell the user when the pool moves on, runs dry or reaches the reserve (the pool's own view of the accounts,
    #    as for Codex); the parks, credits and alarms of step 3 have told their own news
    with cp.contextlib.suppress(cp.PoolDown, cp.ApiError, cp.KeyUnavailable):
        seats = [s for s in cp.load_seats(pool, response_headers=headers) if s['provider'] == 'claude']
        claude_apply_plans(guard, seats, meta)
    live_version = {k.lower(): v for k, v in headers.items()}.get('x-cpa-version') or ''
    st = build_claude_status(seats, guard, meta, live_version=live_version)
    marked = any(g.get('sign_in_ended') for g in guard['seats'].values())
    served = build_claude_status(seats, guard, meta, pool_only=True, live_version=live_version) if marked else st
    t = cp.now_utc()

    def holds_slot(r):
        if guard['seats'].get(r['name'], {}).get('heal_failures'):
            return False  # on heal probation: it flips between ready and blocked; don't announce it
        if r['state'] in ('active', 'ready'):
            return True
        u = cp.parse_time(r['until'])
        return r['state'] == 'cooldown' and u is not None and (u - t).total_seconds() <= cp.BLIP_SECONDS

    rows = served['seats']
    cur_row = next((r for r in rows if holds_slot(r)), None)
    probation_ready = any(r['state'] in ('active', 'ready') and guard['seats'].get(r['name'], {}).get('heal_failures')
                          for r in rows)
    prev_name, cur_name = guard.get('announced'), cur_row['name'] if cur_row else None
    labels = {r['name']: r for r in st['seats']}
    rebalanced = reordered and any(r['name'] == prev_name and holds_slot(r) for r in rows)
    if prev_name and cur_name and prev_name != cur_name and not rebalanced:
        old = labels.get(prev_name)
        why = (f'{old["label"]}: {old["detail"] or old["state"]}' + (f', back {cp.when(old["until"])}' if old['until'] else '')
               if old else 'previous account removed')
        cp.notify(f'Claude now on {cur_row["label"]}', why)
    if prev_name and not cur_name and rows and not probation_ready:
        nb = served.get('next_back')
        cp.notify('All Claude accounts are out', f'Next back: {nb["label"]} at {cp.when(nb["at"])}' if nb else
               'No reset time known.')
    if cur_name and not prev_name and guard.get('announced_known'):
        cp.notify('Claude pool has capacity again', f'Now on {cur_row["label"]}.')
    if cur_row and cur_row.get('reserve') and prev_name and not labels.get(prev_name, {}).get('reserve'):
        cp.notify('Claude is now on the reserve account', f'{cur_row["label"]} is serving: the regular accounts are '
               'spent.')
    guard['announced'] = cur_name if cur_name or not probation_ready else prev_name
    guard['announced_known'] = bool(rows)
    guard['last_pass'] = now.isoformat()
    save()
    cp.write_json(sienna_pool.CLAUDE_STATUS_FILE, st)
    cp.record_history(st, guard, now, pool)
