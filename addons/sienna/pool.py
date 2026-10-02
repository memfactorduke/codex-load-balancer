"""Claude pool lifecycle, descriptors, commands, status and doctor checks.

Runtime paths and behavior are unchanged by the code extraction.
"""
from . import cp, desktop, desktop_build, gate_build

ADDON_DIR = cp.pathlib.Path(__file__).resolve().parent
CLAUDE_CONFIG_TEMPLATE = ADDON_DIR / 'examples' / 'config-claude.yaml'
CLAUDE_PLIST_TEMPLATE = str(ADDON_DIR / 'launchd' / 'claude.plist.template')  # absolute: render_plist takes it as is

CLAUDE_CURRENT = cp.BIN / 'claude-current'  # the Claude pool's build (its own CLIProxyAPI version, same gate source)

CLAUDE_CONFIG = cp.ROOT / 'config-claude.yaml'  # the Claude pool (subpool claude install), from the add-on's examples/config-claude.yaml

CLAUDE_AUTH = cp.ROOT / 'auth-claude'  # one -claude-login per Claude account (0700)

CLAUDE_WORK = cp.ROOT / 'claude'  # the Claude pool's working directory: its CLIProxyAPI writes logs/ here

CLAUDE_LOGS = CLAUDE_WORK / 'logs'

CLAUDE_MAIN_LOG = CLAUDE_LOGS / 'main.log'  # the Claude pool's own log, apart from the Codex pool's logs/main.log

CLAUDE_SEATS_META = cp.ROOT / 'claude-seats.json'  # per Claude account file: {label, weight, reserve, credits}

CLAUDE_GUARD_FILE = cp.STATE / 'claude-guard.json'  # the guard's Claude pass: park records, plans

CLAUDE_LOCK_FILE = cp.STATE / 'claude-guard.lock'  # the Claude pool's: its own, so one pool's selftest never stops the other's guard

CLAUDE_SELFTEST_JOURNAL = cp.STATE / 'claude-selftest.json'  # subpool claude selftest's journal

CLAUDE_CPA_MIN = (7, 3, 18)  # the first CLIProxyAPI release whose Claude path the Claude pool supports

CPA_VERSION_OK = cp.re.compile(r'^v?(\d+)\.(\d+)\.(\d+)$')


def parse_cpa_version(value):
    """The (major, minor, patch) of a CLIProxyAPI version the Claude pool may run ("7.3.18", "v7.3.20"), or None
    for anything else, a release older than CLAUDE_CPA_MIN included."""
    m = CPA_VERSION_OK.match(value) if isinstance(value, str) else None
    version = tuple(int(g) for g in m.groups()) if m else None
    return version if version and version >= CLAUDE_CPA_MIN else None

CLAUDE_JOB = cp.SETTINGS['claude_label']

CLAUDE_PORT = cp.SETTINGS['claude_port']

PROBE_UA = 'subpool-probe'  # doctor's other-client probe of the Claude pool; excluded the same way

CLAUDE_REFRESH_FAILED_LINE = cp.re.compile(r'credential refresh failed for claude \(([^()]+)\): ')  # the Claude pool's

CLAUDE_SIGN_IN_ENDED_DETAIL = 'Anthropic ended this sign-in'

CLAUDE_SIGN_IN_SOON_DETAIL = 'Anthropic ended this sign-in: sign in again soon'


def claude_pool_instance():
    return cp.PoolInstance('claude', 'Claude pool', CLAUDE_CURRENT, CLAUDE_CONFIG, CLAUDE_PORT, CLAUDE_JOB,
                        CLAUDE_PLIST_TEMPLATE, 'claude', CLAUDE_MAIN_LOG,
                        config_template=CLAUDE_CONFIG_TEMPLATE, config_default_port=8321,
                        probe_cases=claude_probe_cases, rebuild_hint='subpool claude install',
                        next_rebuild='subpool claude install', build_subject='the Claude pool',
                        gate_title='origin gate (claude profile)',
                        gate_fix='the Claude pool needs a build with the claude gate profile and '
                                 'CODEXPOOL_GATE_PROFILE=claude in its plist: subpool claude install')


def claude_seat_pool():
    return cp.SeatPool('claude', 'claude', CLAUDE_PORT, CLAUDE_SEATS_META, CLAUDE_GUARD_FILE, CLAUDE_AUTH,
                    CLAUDE_CONFIG, CLAUDE_CURRENT, CLAUDE_WORK, CLAUDE_MAIN_LOG, 'subpool claude',
                    'claude_balancing', 'account', CLAUDE_LOCK_FILE,
                    title='Claude pool', vendor='Anthropic', noun_title='Claude account',
                    sizes_text='Pro = 1, Max 5× = 5, Max 20× = 20',
                    moves_text='sessions on it move to the next account',
                    reserve_tail='it serves only once every regular account is out.',
                    refresh_failed_line=CLAUDE_REFRESH_FAILED_LINE,
                    sign_in_ended_detail=CLAUDE_SIGN_IN_ENDED_DETAIL, sign_in_soon_detail=CLAUDE_SIGN_IN_SOON_DETAIL,
                    history_file=CLAUDE_HISTORY_FILE, selftest_journal=CLAUDE_SELFTEST_JOURNAL,
                    status_file=CLAUDE_STATUS_FILE, login_flow=claude_login_flow,
                    claims=lambda path, name, plans: claude_file_claims(path, plans.get(name)),
                    claims_context=claude_plans,
                    default_weight=lambda value, claims: float(value) if cp.valid_weight(value) else
                                                       float(claims.get('weight') or 1),
                    enrich_row=claude_enrich_row,
                    new_seat_fields=lambda: {'credits': None, 'added_at': cp.now_utc().isoformat()},
                    on_remove=claude_remove_seat, enable_refusal=claude_enable_refusal,
                    refresh_status=refresh_claude_status_file, seat_state=sienna_guard.claude_seat_state,
                    history_extra=lambda st: {'credits': bool(st['pool'].get('spending'))},
                    refresh_after_meta=True, log_tag='claude ')

CLAUDE_PLAN_WEIGHTS = {'pro': 1, 'max_5x': 5, 'max_20x': 20, 'team': 1.25, 'team_premium': 6.25}  # Pro = 1


def claude_plan(profile):
    """(tier, weight) from Anthropic's /api/oauth/profile answer (pure). The tier is one the Interface contract
    names: pro, max_5x, max_20x, team or team_premium; another organization type passes through without its claude_
    prefix (enterprise, say). The weight is the plan's default session share of the pool (Pro = 1); for an
    unknown plan, use the rate-limit tier's size if given. (None, None) when the answer says nothing of a plan."""
    if not isinstance(profile, dict):
        return None, None
    org = profile.get('organization') if isinstance(profile.get('organization'), dict) else {}
    account = profile.get('account') if isinstance(profile.get('account'), dict) else {}
    kind = str(org.get('organization_type') or '').lower()
    rate = str(org.get('rate_limit_tier') or '').lower()
    size = 20 if '20x' in rate else (5 if '5x' in rate else None)
    if 'team' in kind:
        seat = str(org.get('seat_tier') or '').lower()
        premium = seat == 'team_tier_1' or 'premium' in seat or rate == 'default_claude_max_5x'
        tier = 'team_premium' if premium else 'team'
    elif 'max' in kind or (not kind and account.get('has_claude_max') is True):
        tier = 'max_20x' if size == 20 else 'max_5x'
    elif 'pro' in kind or (not kind and account.get('has_claude_pro') is True):
        tier = 'pro'
    elif kind:
        tier = kind[len('claude_'):] if kind.startswith('claude_') else kind
    else:
        return None, None
    return tier, float(CLAUDE_PLAN_WEIGHTS.get(tier, size or 1))


def claude_plans():
    """{account file: {plan, weight, at}}: the plan each Claude account had at its last profile call (sign-in, then
    the guard's Claude pass), from claude-guard.json. Lenient: a missing or odd file is no plans."""
    guard = cp.read_json(CLAUDE_GUARD_FILE, {})
    plans = guard.get('plans') if isinstance(guard, dict) else None
    return {k: v for k, v in plans.items() if isinstance(v, dict)} if isinstance(plans, dict) else {}


def claude_file_claims(path, plan=None):
    """Non-secret facts from a Claude account file (email, organization or account id, priority), with plan, its
    claude_plans() entry. Tokens never leave here."""
    data = cp.read_json(path, {}) if path else {}
    data = data if isinstance(data, dict) else {}
    plan = plan if isinstance(plan, dict) else {}
    return {'email': data.get('email'), 'plan': plan.get('plan'),
            'weight': plan.get('weight') if cp.valid_weight(plan.get('weight')) else None,
            'account_id': data.get('organization_uuid') or data.get('account_uuid'), 'priority': data.get('priority'),
            'excluded_models': data.get('excluded_models', data.get('excluded-models', [])) if data else None}


def claude_enrich_row(row, claims, record):
    """Keep manual exclusions (including unknown) and per-model quota attribution in seat rows."""
    row['excluded_models'] = claims.get('excluded_models')
    row['model_quotas'] = record.get('model_quotas') or {}


def claude_enable_refusal(pool, seat, guard):
    if guard.get('parked_until') and credit_policy(cp.read_meta(pool), seat['name'])['policy'] == 'last-resort':
        return (f'{seat["label"]} is parked by its last-resort policy; enable cannot override it. '
                'The guard restores it when the other plans are spent and its credit reading permits it. '
                'Change the policy or cap with: subpool claude credits')
    return None


def claude_login_flow(no_open, device):
    return ['-claude-login'] + (['-no-browser'] if no_open else [])

CREDIT_POLICIES = ('off', 'last-resort')


def credit_policy(meta, name):
    """A Claude account's credit policy from claude-seats.json (pure): {"policy": "off"|"last-resort", "cap": USD or
    None}. off requires credits disabled at Anthropic; a proxy can only observe spending. last-resort
    keeps the account parked until every other plan is spent, then permits credits up to the observed cap."""
    entry = meta.get(name) if isinstance(meta.get(name), dict) else {}
    c = entry.get('credits') if isinstance(entry.get('credits'), dict) else {}
    cap = c.get('cap')
    cap = float(cap) if cp.valid_weight(cap) else None
    return {'policy': 'last-resort', 'cap': cap} if c.get('policy') == 'last-resort' else {'policy': 'off', 'cap': None}


def claude_seat_row(s, meta, guard):
    """A claude-status.json row for an account the guard's Claude pass has not written yet (just signed in): what
    the pool and subpool know, without usage. s: a load_seats row."""
    parked = ((guard.get('seats') or {}).get(s['name']) or {}).get('parked_until')
    return {'name': s['name'], 'label': s['label'], 'provider': 'claude', 'plan': s.get('plan'),
            'weight': s['weight'], 'priority': s['priority'], 'reserve': s['reserve'],
            'state': ('parked' if parked else 'disabled') if s['disabled'] else 'ready',
            'until': parked, 'detail': '', 'five_hour': None, 'week': None, 'scoped': [], 'email': s.get('email'),
            'credits': {'enabled': None, 'used': None, 'limit': None, **credit_policy(meta, s['name']),
                        'currency': 'USD', 'spending': False, 'mismatch': False},
            'week_used': None, 'poll_error': None, 'usage_at': None, 'usage_source': None, 'sign_in_ended': False,
            'order_reason': None}


def refresh_claude_status_file(seats=None):
    """claude-status.json with what a Claude account command just changed (label, size, reserve flag, fill order,
    credit policy, in or out of rotation, added or removed), so the menu bar shows it at once instead of at the
    guard's next Claude pass, which writes the whole file. seats: load_seats of the Claude pool (None: read it now).
    The caller holds the guard lock. Best effort, and only over a file the guard has written: True when it wrote it."""
    st = cp.read_json(CLAUDE_STATUS_FILE, None)
    if not isinstance(st, dict) or not isinstance(st.get('seats'), list):
        return False
    pool = cp.seat_pool('claude')
    if seats is None:
        try:
            seats = cp.load_seats(pool)
        except (cp.PoolDown, cp.ApiError, cp.KeyUnavailable, OSError):
            return False
    meta, guard = cp.read_meta(pool), cp.read_json(pool.guard, {})
    guard = guard if isinstance(guard, dict) else {}
    old = {r.get('name'): r for r in st['seats'] if isinstance(r, dict)}
    rows = []
    for s in (x for x in seats if x['provider'] == 'claude'):
        r = old.get(s['name'])
        if r is None:
            rows.append(claude_seat_row(s, meta, guard))
            continue
        r.update(label=s['label'], weight=s['weight'], priority=s['priority'], reserve=s['reserve'])
        usage = sienna_guard.best_claude_usage(s, guard.get('usage') or {})
        r['credits'] = {**(r.get('credits') if isinstance(r.get('credits'), dict) else {}),
                        **((usage or {}).get('credits') or {}),
                        **credit_policy(meta, s['name'])}
        r['credits']['mismatch'] = r['credits']['policy'] == 'off' and sienna_guard.claude_would_spend(usage)
        parked = ((guard.get('seats') or {}).get(s['name']) or {}).get('parked_until')
        if s['disabled'] and parked:  # parked by the guard (or just now, by a credit policy you changed)
            state, detail, until = sienna_guard.claude_seat_state(s, guard, cp.now_utc())
            r.update(state=state, detail=detail, until=until)
        elif s['disabled']:
            r.update(state='disabled', until=None)
        elif not s['disabled'] and r.get('state') in ('disabled', 'parked'):
            r.update(state='ready', until=None)
        rows.append(r)
    st['seats'] = rows  # the pool's fill order, as load_seats has it
    with cp.contextlib.suppress(OSError):
        cp.write_json(CLAUDE_STATUS_FILE, st)
        return True
    return False


def claude_remove_seat(pool, s):
    """Remove the account's metadata and guard records after CPA deletes its login."""
    cp.update_meta(s['name'], pool, label=None, weight=None, reserve=None, manual_priority=None, credits=None)
    with cp.guard_lock(pool=pool):
        guard = cp.read_guard(pool)
        for key in ('seats', 'plans'):
            if isinstance(guard.get(key), dict):
                guard[key].pop(s['name'], None)
        cp.write_json(pool.guard, guard)
        cp.refresh_status_file(pool)
    print(f'removed {s["name"]}')


def claude_probe_cases(pool):
    browser = {'Origin': cp.PROBE_ORIGIN, 'Sec-Fetch-Site': 'cross-site'}
    return [('Claude Code request', cp._probe_status(pool.port, gate_build.CLAUDE_CODE_HEADERS), 200),
            ('other client', cp._probe_status(pool.port, {'User-Agent': PROBE_UA}), 403),
            ('browser request', cp._probe_status(pool.port, {**gate_build.CLAUDE_CODE_HEADERS, **browser}), 403),
            ('app://- origin', cp._probe_status(pool.port, {**gate_build.CLAUDE_CODE_HEADERS, 'Origin': 'app://-'}), 403)]


def claude_installed():
    """True once subpool claude install has set the Claude pool up: its launchd agent is in place."""
    return (cp.LAUNCH_AGENTS / f'{CLAUDE_JOB}.plist').exists()


def doctor_claude_checks(rep):
    """The Claude pool's own section of subpool doctor: its process, build and gate, apart from the Codex pool's."""
    check = rep.check
    pool = cp.pool_instance('claude')
    rep.section('Claude pool')
    loaded, pid = cp.launchd_loaded(pool.job)
    check(loaded and pid, f'launchd job {pool.job} ' + (f'running (pid {pid})' if pid else 'not running'),
          f'launchctl bootstrap gui/{cp.UID} {cp.LAUNCH_AGENTS / (pool.job + ".plist")}')
    check(cp.port_open(pool.port), f'listening on 127.0.0.1:{pool.port}', 'subpool claude install ; subpool claude logs')
    check(cp.cpa_binary(pool.link).exists(), f'bin/{pool.link.name} → {cp.cpa_version(pool.link) or "(missing)"}',
          'subpool claude install builds it')
    key_error = None
    try:
        cp.mgmt_key()
    except cp.KeyUnavailable as e:
        key_error = e  # the Codex pool's section above already says so
    cp.doctor_build_checks(rep, pool, key_error)
    cfg = pool.config.read_text() if pool.config.exists() else ''
    check('host: "127.0.0.1"' in cfg, 'bound to loopback only', 'subpool claude install')
    check(cp.re.search(r'^api-keys:\s*\[\]', cfg, cp.re.M) is not None, 'client auth open on loopback (api-keys: [])',
          'Claude Code keeps its own login; api-keys must be [] (the claude gate profile protects the pool)')
    for problem in claude_config_problems(cfg):
        check(False, problem, 'subpool claude install rewrites config-claude.yaml from the current template')
    doctor_claude_accounts(rep)
    doctor_claude_launch(rep)
    desktop.doctor(cp, rep)
    doctor_claude_log(rep)


def claude_config_problems(text):
    """Conservative check of our documented YAML form, not a general YAML parser. Never print config values."""
    lines = [line.split(' #', 1)[0].rstrip() for line in text.splitlines()
             if line.strip() and not line.lstrip().startswith('#')]
    problems = []
    cloak = [line for line in lines if line.startswith('disable-claude-cloak-mode:')]
    if cloak != ['disable-claude-cloak-mode: true']:
        problems.append('Claude cloaking is not verified disabled')
    # Exact known-good stanza: custom YAML is flagged for review, never pronounced safe from a substring.
    expected = ['oauth-request-scoped-errors:',
                '  claude:',
                '    - status: 429',
                '      match-regexr:',
                '        - \'(?i)(^\\s*usage credits are required for (fast mode|long context)\\.?\\s*$|"message"\\s*:\\s*"usage credits are required for (fast mode|long context)\\.?")\'',
                '        - \'(?i)(^\\s*fast request rejected\\.?\\s*$|"message"\\s*:\\s*"fast request rejected\\.?")\'',
                '      action: stop',]
    blocks = [lines[i:cp._mapping_end(lines, i)] for i, line in enumerate(lines)
              if line.startswith('oauth-request-scoped-errors:')]
    if blocks != [expected]:
        problems.append('Claude fast/long-context refusal rules differ from the verified template')
    if any(line.startswith('claude-header-defaults:') for line in lines):
        problems.append('Custom Claude header defaults can disagree with the token-count gate detector')
    return problems


def doctor_claude_accounts(rep):
    """The Claude accounts as the guard's Claude pass last saw them (claude-status.json): usage polling, each
    account's state and its credit policy."""
    check = rep.check
    rep.section('Claude accounts')
    st, age = claude_status_file()
    if st is None:
        check(False, f'{cp.tilde(CLAUDE_STATUS_FILE)} not written yet', 'the guard writes it within a minute; if it '
              'stays missing: subpool logs --guard', warn=True)
        seats = claude_status_now()['seats']
    else:
        check(age is not None and age < 300, f'usage polling: the guard last wrote the Claude accounts '
              f'{cp.when(cp.parse_time(st.get("generated_at"))) if age is not None else "at an unknown time"}',
              'subpool logs --guard')
        if isinstance(st.get('pool'), dict) and st['pool'].get('usage_polling') == 'passive':
            check(False, 'usage polling is passive: Anthropic refused the pool\'s usage call, so usage is as of each '
                  'account\'s last served request and credit amounts are unknown (last-resort accounts spend nothing)',
                  'the guard tries the call again every few hours; see subpool logs --guard', warn=True)
        seats = [r for r in st.get('seats') or [] if isinstance(r, dict)]
    check(len(seats) >= 1, f'{len(seats)} Claude account(s) in the pool', 'subpool claude login "<Label>" --priority <n>')
    for r in seats:
        label = str(r.get('label') or r.get('name'))
        state = r.get('state') or 'unknown'
        text = f'{label}: {state}' + (f' ({r["detail"]})' if r.get('detail') else '') + \
            (f', back {cp.when(r["until"])}' if r.get('until') else '')
        q = cp.shlex.quote(label)
        check(state not in ('blocked', 'unknown'), text,
              f'subpool claude login {q}' + (f' --priority {r["priority"]}' if r.get('priority') is not None else ''))
        cred = r.get('credits') if isinstance(r.get('credits'), dict) else None
        if not cred:
            continue
        policy, cap, used = cred.get('policy'), cred.get('cap'), cred.get('used')
        if policy == 'last-resort':
            capped = isinstance(cap, (int, float)) and cap > 0
            check(capped, f'{label}: credits as the last resort, cap ${cap or 0:g}' if capped else
                  f'{label}: credits as the last resort without a cap',
                  f'subpool claude credits {q} last-resort --cap <USD>')
            if capped:
                check(True, f'{label}: set a matching ${cap:g} member spend limit at claude.ai (Settings → Usage)')
            if capped and isinstance(used, (int, float)) and used >= cap:
                check(False, f'{label} has spent ${used:g} of its ${cap:g} cap', 'the guard keeps it parked until '
                      'the monthly credits reset; raise the cap with subpool claude credits', warn=True)
        elif cred.get('mismatch') or cred.get('enabled') is True:
            check(False, f'Usage credits are on at claude.ai for {label}: turn them off there (Settings → Usage). '
                  "subpool can't stop every paid request.", 'turn usage credits off at claude.ai (Settings → Usage)')
    for name, g in (cp.read_json(CLAUDE_GUARD_FILE, {}).get('seats') or {}).items():
        if g.get('exclusion_error'):
            check(False, f'{name}: {g["exclusion_error"]}', 'subpool logs --guard', warn=True)
    reserves = [str(r.get('label')) for r in seats if r.get('reserve')]
    if len(reserves) > 1:
        check(False, f'{len(reserves)} reserve accounts: {", ".join(reserves)}', 'one reserve is the usual setup: '
              'subpool claude reserve <account> --off', warn=True)


def doctor_claude_launch(rep):
    """How Claude Code gets to the pool: the launcher, the shim, the route, and no ANTHROPIC_* in launchd."""
    check = rep.check
    rep.section('Claude Code')
    ours = cp.script_is_ours(CLAUDE_LAUNCHER, CLAUDE_LAUNCHER_MARK)
    if ours:
        check(CLAUDE_LAUNCHER.read_text() == launcher_text(), f'{cp.tilde(CLAUDE_LAUNCHER)} present'
              + ('' if CLAUDE_LAUNCHER.read_text() == launcher_text() else ' but out of date (another port or home)'),
              'subpool claude install')
    else:
        check(False, f'{cp.tilde(CLAUDE_LAUNCHER)} ' + ('is not subpool\'s' if CLAUDE_LAUNCHER.exists() else 'missing'),
              'subpool claude install' if not CLAUDE_LAUNCHER.exists() else
              f'remove {cp.tilde(CLAUDE_LAUNCHER)}, then subpool claude install', warn=CLAUDE_LAUNCHER.exists())
    real = real_claude()
    check(real is not None, f'Claude Code: {cp.tilde(real) if real else "not found in ~/.local/bin or on PATH"}',
          'install Claude Code (the launcher starts ~/.local/bin/claude, else the first claude on PATH)', warn=True)
    check(True, 'Background Claude Code sessions run direct: start them with CLAUDEPOOL=off')
    route = claude_route()
    check(True, f'route: {route} (new sessions ' + ('go through the pool while it answers)' if route == 'pool' else
                                                     'start direct; subpool claude route pool)'))
    if cp.script_is_ours(CLAUDE_SHIM, CLAUDE_SHIM_MARK):
        on_path = str(SHIMS) in cp.os.environ.get('PATH', '').split(cp.os.pathsep)
        check(CLAUDE_SHIM.read_text() == shim_text(), f'shim {cp.tilde(CLAUDE_SHIM)}' +
              ('' if on_path else ' (not on this shell\'s PATH)'), 'subpool claude shim install')
    found = [name for name in ANTHROPIC_LAUNCHD_VARS if cp.launchd_getenv(name) is not None]
    check(not found, 'no ANTHROPIC_* variable in launchd\'s environment' if not found else
          f'{", ".join(found)} set in launchd\'s environment (every app started from the Dock gets it)',
          'launchctl unsetenv ' + ' '.join(found) + '; subpool sets ANTHROPIC_BASE_URL only for the process '
          'claude-pool starts', warn=True)


def doctor_claude_log(rep):
    """The Claude pool's own log, last 24 h: thinking-signature rejections (PLAN §3) and gate rejections."""
    check = rep.check
    rep.section('Recent Claude pool errors (last 24h of logs)')
    counts = {}
    cutoff = (cp.dt.datetime.now() - cp.dt.timedelta(hours=24)).strftime('%Y-%m-%d %H:%M')
    for log in sorted(CLAUDE_LOGS.glob('main*.log')):
        with cp.contextlib.suppress(OSError):
            if cp.dt.datetime.fromtimestamp(log.stat().st_mtime) < cp.dt.datetime.now() - cp.dt.timedelta(hours=24):
                continue
            for line in log.read_text(errors='replace').splitlines():
                m = cp.LOG_LINE.match(line)
                if not m or m.group(1) < cutoff:
                    continue
                src, msg = m.group(2), m.group(3)
                if src in ('codexpool_gate.go', 'codexpool_gate_sienna.go'):
                    if 'rejected unrecognised token-count client' in msg:
                        counts['token-count'] = counts.get('token-count', 0) + 1
                        continue
                    # doctor's own probes: the browser one (PROBE_ORIGIN), the other client (PROBE_UA) and app://-,
                    # the Codex app's origin (the Codex app has a pool of its own; only doctor sends it here)
                    if not any(p in msg for p in (cp.PROBE_ORIGIN, f'"{PROBE_UA}"', 'origin="app://-"')):
                        counts['gate'] = counts.get('gate', 0) + 1
                    continue
                if src == 'gin_logger.go':
                    continue  # access-log lines echo request paths, which any client controls
                if any(p in msg for p in CLAUDE_SIGNATURE_PATTERNS):
                    counts['signature'] = counts.get('signature', 0) + 1
    n = counts.get('signature', 0)
    check(not n, 'no thinking-signature rejections' if not n else
          f'{n} thinking-signature rejection(s) (Invalid `signature` in `thinking` block / bound to a different '
          'conversation)', 'an account switch broke a thinking chain; Claude Code retries without the earlier '
          'thinking. Check the pair with subpool claude selftest A B; upgrade CLIProxyAPI if it keeps happening',
          warn=True)
    if counts.get('gate'):
        check(False, f'{counts["gate"]} requests from other clients or browsers blocked by the gate',
              f'something local tried to use the Claude pool; see: grep "codexpool gate" {cp.tilde(CLAUDE_MAIN_LOG)}',
              warn=True)
    if counts.get('token-count'):
        check(False, 'Claude Code token-count client is not recognised by this pool',
              'Claude Code X.Y may be newer than this pool knows; update subpool or run direct. '
              'Background token counting is also refused until CPA supports its native identity', warn=True)


def claude_cpa_version():
    """The CLIProxyAPI version the Claude pool is to run: claude_cpa from settings.json, else the newest release,
    which must be CLAUDE_CPA_MIN or later."""
    if cp.SETTINGS['claude_cpa']:
        return cp.SETTINGS['claude_cpa'].lstrip('v')
    latest = cp._github_json('releases/latest')['tag_name'].lstrip('v')
    if parse_cpa_version(latest) is None:
        raise RuntimeError(f'the newest CLIProxyAPI release, {latest}, is older than the Claude pool supports '
                           f'({".".join(map(str, CLAUDE_CPA_MIN))}); set claude_cpa in {cp.tilde(cp.SETTINGS_FILE)}')
    return latest

CLAUDE_STATUS_FILE = cp.STATE / 'claude-status.json'  # written by the guard's Claude pass (Interface contract)

CLAUDE_HISTORY_FILE = cp.STATE / 'claude-history.jsonl'

CLAUDE_ROUTE_FILE = cp.STATE / 'claude-route'  # "pool" or "direct": where new Claude Code sessions go

CLAUDE_ROUTES = ('pool', 'direct')

CLAUDE_LAUNCHER = cp.HOME / '.local' / 'bin' / 'claude-pool'

SHIMS = cp.ROOT / 'shims'

CLAUDE_SHIM = SHIMS / 'claude'

CLAUDE_REAL = cp.HOME / '.local' / 'bin' / 'claude'  # where Claude Code's installer puts it; else the first on PATH

LAUNCHER_MARK = '# subpool claude launcher'  # in both scripts; each skips any "claude" that carries it

CLAUDE_LAUNCHER_MARK = f'{LAUNCHER_MARK}, written by `subpool claude install`'

CLAUDE_SHIM_MARK = f'{LAUNCHER_MARK} (the claude shim), written by `subpool claude shim install`'

CLAUDE_PROBE_MS = 300  # how long a launch waits for the Claude pool before it starts Claude Code direct

CLAUDE_POOL_ENV = (('ENABLE_TOOL_SEARCH', 'true'), ('CLAUDE_CODE_PROMPT_CACHE_TTL', '1h'),
                   ('ANTHROPIC_DEFAULT_HAIKU_MODEL', 'claude-sonnet-5'))

ANTHROPIC_LAUNCHD_VARS = ('ANTHROPIC_BASE_URL', 'ANTHROPIC_AUTH_TOKEN', 'ANTHROPIC_API_KEY', 'ANTHROPIC_MODEL',
                          'ANTHROPIC_CUSTOM_HEADERS', 'ANTHROPIC_DEFAULT_HAIKU_MODEL')

CLAUDE_SIGNATURE_PATTERNS = ('Invalid `signature` in `thinking` block', 'bound to a different conversation')

CLAUDE_PATH_LINE = 'export PATH="$HOME/.subpool/shims:$PATH"'


def claude_pool_url():
    return f'http://127.0.0.1:{CLAUDE_PORT}'


def claude_route():
    """Where new Claude Code sessions go: "pool" (the default) or "direct", from state/claude-route."""
    try:
        value = CLAUDE_ROUTE_FILE.read_text().strip()
    except OSError:
        return 'pool'
    return value if value in CLAUDE_ROUTES else 'pool'


def real_claude():
    """Claude Code, as the launcher finds it: ~/.local/bin/claude, else the first claude on PATH, never a script that
    carries LAUNCHER_MARK (the launcher, the shim or a copy) and never one in the shims directory. None: not found."""
    if CLAUDE_REAL.exists() and not cp.script_is_ours(CLAUDE_REAL, LAUNCHER_MARK):
        return CLAUDE_REAL
    for d in cp.os.environ.get('PATH', '').split(cp.os.pathsep):
        c = cp.pathlib.Path(d.rstrip('/') or '/') / 'claude'
        if d and d.startswith('/') and cp.pathlib.Path(d.rstrip('/') or '/') != SHIMS and c.is_file() \
                and cp.os.access(c, cp.os.X_OK) and not cp.script_is_ours(c, LAUNCHER_MARK):
            return c
    return None


def launcher_text(mark=CLAUDE_LAUNCHER_MARK, name='claude-pool'):
    """The claude-pool launcher (or, with the shim's mark and name, the claude shim). POSIX sh, one exec:
    CLAUDEPOOL=off or route "direct" start Claude Code as it is; otherwise a 300 ms probe of the Claude pool decides,
    and a pool that does not answer means a direct start with one warning line on stderr. CLAUDEPOOL=required
    ignores the route file and exits 75 if the probe fails, without starting Claude Code. The real claude is
    ~/.local/bin/claude, else the first claude on PATH, never a script that carries LAUNCHER_MARK (this one, the shim,
    or a copy) and never one in the shims directory."""
    q = cp.shlex.quote
    url = claude_pool_url()
    env = ''.join(f'  : "${{{k}:={v}}}"; export {k}\n' for k, v in CLAUDE_POOL_ENV)
    return f'''#!/bin/sh
{mark}
# Starts Claude Code through the subpool Claude pool: ANTHROPIC_BASE_URL={url} for this process only,
# with Claude Code's own claude.ai login. CLAUDEPOOL=off {name} starts it direct. Rewritten by subpool; edits are lost.
pool_url={q(url)}
route_file={q(str(CLAUDE_ROUTE_FILE))}
shims={q(str(SHIMS))}
first={q(str(CLAUDE_REAL))}
mark={q(LAUNCHER_MARK)}

find_claude() {{
  set -f; old_ifs=$IFS; IFS=:
  set -- "${{first%/claude}}" $PATH
  IFS=$old_ifs; set +f
  for d in "$@"; do
    case $d in /*) ;; *) continue ;; esac
    d=${{d%/}}
    [ "$d" = "$shims" ] && continue
    c=$d/claude
    [ -f "$c" ] && [ -x "$c" ] || continue
    head -c 200 "$c" 2>/dev/null | grep -qF "$mark" && continue
    printf '%s\\n' "$c"
    return 0
  done
  return 1
}}

pool_answers() {{
  code=$(curl -s -o /dev/null -w '%{{http_code}}' -m {CLAUDE_PROBE_MS / 1000:g} --noproxy '*' \\
    -H 'User-Agent: claude-cli/subpool-launcher' -H 'X-App: cli' "$pool_url/healthz" 2>/dev/null)
  case $code in
    200) return 0 ;;
    [234][0-9][0-9]) [ "${{CLAUDEPOOL:-}}" != required ] && return 0 ;;
  esac
  return 1
}}

real=$(find_claude) || {{
  echo "{name}: Claude Code (claude) is not in ~/.local/bin or on PATH; install it first" >&2
  exit 127
}}
route=pool
[ "${{CLAUDEPOOL:-}}" != required ] && [ -r "$route_file" ] && read -r route < "$route_file"
why=
if [ "${{CLAUDEPOOL:-}}" = off ]; then
  :
elif [ "$route" = direct ] && [ "${{CLAUDEPOOL:-}}" != required ]; then
  why='the route is direct (subpool claude route pool sends new sessions through the pool)'
elif pool_answers; then
  export ANTHROPIC_BASE_URL="$pool_url"
{env}  exec "$real" "$@"
else
  why="the Claude pool does not answer on ${{pool_url#http://}} (subpool claude status)"
fi
if [ "${{CLAUDEPOOL:-}}" = required ]; then
  echo "{name}: $why; pool required, Claude Code was not started" >&2
  exit 75
fi
[ -n "$why" ] && echo "{name}: $why; starting Claude Code direct, on its own account" >&2
[ "${{ANTHROPIC_BASE_URL:-}}" = "$pool_url" ] && unset ANTHROPIC_BASE_URL
exec "$real" "$@"
'''


def shim_text():
    return launcher_text(CLAUDE_SHIM_MARK, 'claude')


def write_claude_route(value):
    cp.STATE.mkdir(parents=True, exist_ok=True)
    fd, tmp = cp.tempfile.mkstemp(dir=cp.STATE, prefix='.claude-route.', suffix='.tmp')
    with cp.os.fdopen(fd, 'w') as f:
        f.write(value + '\n')
    cp.os.chmod(tmp, 0o644)
    cp.os.replace(tmp, CLAUDE_ROUTE_FILE)


def patch_claude_status(**pool_fields):
    """Show a change in claude-status.json's pool block at once, before the guard's next pass rewrites the file
    (it reads the same sources, so both agree). No file, nothing to patch."""
    with cp.guard_lock(pool=claude_seat_pool()):
        st = cp.read_json(CLAUDE_STATUS_FILE, None)
        if not isinstance(st, dict):
            return
        pool = st.get('pool') if isinstance(st.get('pool'), dict) else {}
        pool.update(pool_fields)
        st['pool'] = pool
        cp.write_json(CLAUDE_STATUS_FILE, st)


def claude_record(**fields):
    """Merge fields into install.json's "claude" record (what subpool claude install set up); None removes one."""
    rec = cp.read_json(cp.INSTALL_FILE, {})
    rec = rec if isinstance(rec, dict) else {}
    claude = rec.get('claude') if isinstance(rec.get('claude'), dict) else {}
    for k, v in fields.items():
        if v is None:
            claude.pop(k, None)
        else:
            claude[k] = v
    cp.record_install(claude=claude)


def claude_build_wanted():
    """(version, build dir name) the Claude pool should run: claude_cpa, else the version it runs now (install
    never moves it to a newer release by itself), else the newest release; built with today's gate source. The
    version is None when it can only come from GitHub and GitHub does not answer."""
    have = cp.cpa_version(CLAUDE_CURRENT)
    m = cp.re.match(r'v?(\d+\.\d+\.\d+)-gate-', have or '')
    if cp.SETTINGS['claude_cpa']:
        version = cp.SETTINGS['claude_cpa'].lstrip('v')
    elif m and cp.cpa_binary(CLAUDE_CURRENT).exists():
        version = m.group(1)
    else:
        try:
            version = claude_cpa_version()
        except (cp.urllib.error.URLError, OSError, ValueError, KeyError):
            return None, None
    return version, f'{version}-gate-{cp.gate_id(profile="claude")}'  # as cpa_version() reads a build link

class ClaudeSteps(cp.Steps):
    TOTAL = 6


def claude_install_build(st):
    """Step 1: the Claude pool's own CLIProxyAPI build (bin/claude-current). True when the link moved."""
    st.step('CLIProxyAPI for the Claude pool, with the origin gate (claude profile)')
    pool = cp.pool_instance('claude')
    try:
        src = cp.GATE_SOURCE.read_text()
    except OSError:
        src = ''
    if 'CODEXPOOL_GATE_PROFILE' not in src:  # builds come from ~/.subpool/build, which subpool install copies
        msg = f'{cp.tilde(cp.GATE_SOURCE)} ' + ('is missing' if not src else 'has no claude gate profile (it predates the '
                                                                       'Claude pool)')
        fix = 'update subpool first: the installer, or subpool install from a 1.3.0 or later checkout'
        if not st.dry:
            raise RuntimeError(f'{msg}; {fix}')
        st.fail(msg, fix)
        return False
    have = cp.cpa_version(CLAUDE_CURRENT)
    version, want = claude_build_wanted()
    if version is None:
        if not st.dry:
            raise RuntimeError('cannot ask GitHub for the newest CLIProxyAPI release; set claude_cpa in '
                               f'{cp.tilde(cp.SETTINGS_FILE)} or retry when online')
        version, want = 'latest', None
    if (want and have == want and cp.cpa_binary(CLAUDE_CURRENT).exists() and
            desktop_build.desktop_wire_record_path('v' + have).exists()):
        st.ok(f'bin/claude-current → {have} (claude_cpa sets another version)')
        return False
    why = '' if not have else f' (it runs {have}; the gate source or claude_cpa changed)'
    st.change(f'build (or reuse) CLIProxyAPI v{version} with build/codexpool_gate.go (gate {cp.gate_id(profile="claude")}), run the gate '
              f'self-test for both profiles, run the desktop wire test, and point bin/claude-current at it{why}; bin/current is left alone',
              cp.build_for, pool, version)
    return True


def claude_config_current():
    """True when the port and required safety settings match the current template."""
    try:
        text = CLAUDE_CONFIG.read_text()
        m = cp.re.search(r'(?m)^port:\s*(\d+)', text)
    except OSError:
        return False
    return bool(m) and int(m.group(1)) == CLAUDE_PORT and not claude_config_problems(text)


def claude_write_config():
    cp.write_private(CLAUDE_CONFIG, cp.render_config(cp.mgmt_key(), cp.pool_instance('claude')))


def claude_make_dirs():
    CLAUDE_AUTH.mkdir(mode=0o700, parents=True, exist_ok=True)
    cp.os.chmod(CLAUDE_AUTH, 0o700)  # the Claude accounts' logins: only you
    CLAUDE_LOGS.mkdir(parents=True, exist_ok=True)
    cp.STATE.mkdir(parents=True, exist_ok=True)


def claude_install_files(st):
    """Step 2: config-claude.yaml, auth-claude/ (0700), the working and log directory, the route. True when the
    config changed (the running pool must restart to read it)."""
    st.step('config-claude.yaml, auth-claude/ and the route')
    changed = False
    if claude_config_current():
        st.ok(f'{cp.tilde(CLAUDE_CONFIG)} (port {CLAUDE_PORT}, the Codex pool\'s management key)')
    else:
        what = 'rewrite' if CLAUDE_CONFIG.exists() else 'write'
        st.change(f'{what} {cp.tilde(CLAUDE_CONFIG)} (mode 600) from the add-on template examples/config-claude.yaml: loopback, port '
                  f'{CLAUDE_PORT}, api-keys: [], fill-first, session affinity 24h, auth-dir {cp.tilde(CLAUDE_AUTH)}, the '
                  f'management key the Codex pool uses ({cp.KEYCHAIN_SERVICE})', claude_write_config)
        changed = True
    private = CLAUDE_AUTH.is_dir() and cp.os.stat(CLAUDE_AUTH).st_mode & 0o777 == 0o700
    if private and CLAUDE_LOGS.is_dir():
        st.ok(f'{cp.tilde(CLAUDE_AUTH)} (mode 700) and {cp.tilde(CLAUDE_LOGS)}')
    else:
        st.change(f'create {cp.tilde(CLAUDE_AUTH)} (mode 700, one sign-in per Claude account) and {cp.tilde(CLAUDE_LOGS)} '
                  '(the Claude pool\'s own log)', claude_make_dirs)
    if CLAUDE_ROUTE_FILE.exists():
        st.ok(f'route: {claude_route()} ({cp.tilde(CLAUDE_ROUTE_FILE)})')
    else:
        st.change(f'write {cp.tilde(CLAUDE_ROUTE_FILE)}: pool (subpool claude route direct sends new sessions direct)',
                  write_claude_route, 'pool')
    return changed


def claude_install_agent(st, restart):
    """Step 3: the launchd agent, (re)started when its plist, build or config changed."""
    st.step(f'launchd agent {CLAUDE_JOB} (port {CLAUDE_PORT})')
    plist = cp.LAUNCH_AGENTS / f'{CLAUDE_JOB}.plist'
    text = cp.render_plist(CLAUDE_JOB, CLAUDE_PLIST_TEMPLATE)
    current = plist.read_text() if plist.exists() else None
    loaded, pid = cp.launchd_loaded(CLAUDE_JOB)
    if current == text and loaded:
        if restart:
            st.change(f'{CLAUDE_JOB} loaded; restart it (its build or config changed)', cp.kick_agent, CLAUDE_JOB)
        else:
            st.ok(f'{CLAUDE_JOB} loaded' + (f' (pid {pid})' if pid else ''))
    else:
        what = (f'write {cp.tilde(plist)} from the add-on template launchd/claude.plist.template' if current is None else
                f'update {cp.tilde(plist)} from the add-on template launchd/claude.plist.template' if current != text else
                f'{cp.tilde(plist)} is current')
        st.change(f'{what} (CODEXPOOL_GATE_PROFILE=claude, working directory {cp.tilde(CLAUDE_WORK)}); load it '
                  f'(launchctl bootstrap gui/{cp.UID})', cp.load_agent, CLAUDE_JOB, plist, text,
                  current is not None and current != text)
    want = cp.re.sub(r'^v?(.*)-gate-', r'\1+gate.', cp.cpa_version(CLAUDE_CURRENT) or '') or None  # as X-CPA-VERSION says
    if not st.dry and not cp.wait_port_healthy(CLAUDE_PORT, 30, want):
        raise RuntimeError(f'the Claude pool did not answer on 127.0.0.1:{CLAUDE_PORT} within 30 s; see: '
                           'subpool claude logs')


def claude_install_gate(st):
    """Step 4: the live gate must enforce the claude profile, or the pool is stopped again."""
    st.step('Gate probe on the Claude pool')
    if st.dry:
        st.change(f'probe 127.0.0.1:{CLAUDE_PORT}: a Claude Code request must get 200; another client, a browser '
                  'request and the app://- origin 403 (a failed probe stops the Claude pool again)')
        return
    cases = cp.gate_probe_cases(cp.pool_instance('claude'))
    text = ', '.join(f'{what} {got}' for what, got, _ in cases)
    if all(got == want for _, got, want in cases):
        st.ok(f'origin gate (claude profile): {text}')
        return
    cp.unload_agent(CLAUDE_JOB)  # stopped, and its plist removed: launchd must not start it again at the next login
    raise RuntimeError(f'the Claude pool\'s gate does not keep other clients out ({text}), so it was stopped and its '
                       f'launchd agent removed. Its build must come from this build/codexpool_gate.go: rm '
                       f'{cp.tilde(CLAUDE_CURRENT)} and re-run subpool claude install')


def claude_install_launcher(st):
    """Step 5: ~/.local/bin/claude-pool."""
    st.step('The claude-pool launcher')
    text = launcher_text()
    how = f'starts Claude Code with ANTHROPIC_BASE_URL={claude_pool_url()} after a {CLAUDE_PROBE_MS} ms probe'
    if not (CLAUDE_LAUNCHER.exists() or CLAUDE_LAUNCHER.is_symlink()):
        st.change(f'write {cp.tilde(CLAUDE_LAUNCHER)} ({how})', cp.write_script, CLAUDE_LAUNCHER, text)
    elif not cp.script_is_ours(CLAUDE_LAUNCHER, CLAUDE_LAUNCHER_MARK):
        st.warn(f'{cp.tilde(CLAUDE_LAUNCHER)} exists and is not subpool\'s; left alone',
                f'remove it and re-run subpool claude install')
        return
    elif CLAUDE_LAUNCHER.read_text() == text:
        st.ok(f'{cp.tilde(CLAUDE_LAUNCHER)}')
    else:
        st.change(f'update {cp.tilde(CLAUDE_LAUNCHER)} ({how})', cp.write_script, CLAUDE_LAUNCHER, text)
    if cp.script_is_ours(CLAUDE_SHIM, CLAUDE_SHIM_MARK) and CLAUDE_SHIM.read_text() != shim_text():
        st.change(f'update {cp.tilde(CLAUDE_SHIM)} (the same launcher, as claude)', cp.write_script, CLAUDE_SHIM, shim_text())
    if str(CLAUDE_LAUNCHER.parent) not in cp.os.environ.get('PATH', '').split(cp.os.pathsep):
        st.warn(f'{cp.tilde(CLAUDE_LAUNCHER.parent)} is not on your PATH',
                'echo \'export PATH="$HOME/.local/bin:$PATH"\' >> ~/.zshrc, then open a new terminal')


def claude_install_record(st):
    """Step 6: install.json's "claude" record, and claude-status.json's pool.installed."""
    st.step('Record the install')
    what = (f'record in {cp.tilde(cp.INSTALL_FILE)}: the agent {CLAUDE_JOB}, port {CLAUDE_PORT}, the build, the launcher'
            + (' and the shim' if cp.script_is_ours(CLAUDE_SHIM, CLAUDE_SHIM_MARK) else ''))

    def record():
        claude_record(agent=CLAUDE_JOB, port=CLAUDE_PORT, build=cp.cpa_version(CLAUDE_CURRENT),
                      launcher=str(CLAUDE_LAUNCHER) if cp.script_is_ours(CLAUDE_LAUNCHER, CLAUDE_LAUNCHER_MARK) else None,
                      installed_at=cp.now_utc().isoformat(timespec='seconds'), uninstalled_at=None)
        patch_claude_status(installed=True, route=claude_route())
    st.change(what, record)


def cmd_claude_install(args):
    """Set the Claude pool up, or repair it; every step checks first and changes only what is missing or wrong."""
    missing = cp.missing_install()
    if missing:
        cp.sys.exit(f'subpool claude install: subpool itself is not installed yet ({", ".join(missing)} missing). '
                 'Run subpool install first; the Claude pool uses its toolchain, key and guard.')
    st = ClaudeSteps(args.dry_run)
    print(f'subpool claude install{" --dry-run: nothing is changed; each step says what it would do" if st.dry else ""}'
          f'\n  home {cp.tilde(cp.ROOT)} · port {CLAUDE_PORT} · launchd {CLAUDE_JOB} · logins {cp.tilde(CLAUDE_AUTH)}')
    try:
        moved = claude_install_build(st)
        changed = claude_install_files(st)
        claude_install_agent(st, moved or changed)
        claude_install_gate(st)
        claude_install_launcher(st)
        claude_install_record(st)
    except (RuntimeError, cp.KeyUnavailable) as e:
        cp.sys.exit(f'\nclaude install stopped: {e}\nFix that and re-run subpool claude install; every step is safe to '
                 'repeat.')
    if st.dry:
        print('\nDry run: nothing was changed.'
              + (f' The {len(st.problems)} problem(s) marked ✗ would stop a real install.' if st.problems else ''))
        cp.sys.exit(1 if st.problems else 0)
    seats = sorted(CLAUDE_AUTH.glob('*.json'))
    if seats:
        print(f'\nUp to date: {len(seats)} Claude account(s) in the pool. subpool claude status shows them.')
        return
    print('\nInstalled. Claude Code keeps its own login; nothing changes until you start it through the pool.\n'
          '  1. Add each Claude account (browser sign-in):  subpool claude login "<Label>" --priority <n>\n'
          '     For an account your browser is not signed in to, add --no-open and open the link in a private window.\n'
          f'  2. Start Claude Code through the pool:  {CLAUDE_LAUNCHER.name}   (CLAUDEPOOL=off {CLAUDE_LAUNCHER.name} '
          'or plain claude: direct)\n'
          '  3. Optional: subpool claude reserve "<Label>", subpool claude credits "<Label>" last-resort --cap 200,\n'
          '     subpool claude shim install (makes plain claude go through the pool too).\n'
          'subpool claude uninstall --yes undoes this and keeps the account logins.')


def claude_uninstall_plan():
    """[(text, fn)] of what taking the Claude pool out does; the account logins, config and builds stay."""
    plan = []
    if desktop.desktop_recorded():
        def desktop_before_uninstall():
            with desktop.desktop_backend() as backend:
                backend.uninstall()
                for event in backend.events:
                    print(event)
        plan.append(('clean subpool desktop setup before stopping the pool (best effort)', desktop_before_uninstall))
    plist = cp.LAUNCH_AGENTS / f'{CLAUDE_JOB}.plist'
    if plist.exists() or cp.launchd_loaded(CLAUDE_JOB)[0]:
        plan.append((f'stop the launchd agent {CLAUDE_JOB} and delete {cp.tilde(plist)}', lambda: cp.unload_agent(CLAUDE_JOB)))
    for path, mark in ((CLAUDE_LAUNCHER, CLAUDE_LAUNCHER_MARK), (CLAUDE_SHIM, CLAUDE_SHIM_MARK)):
        if cp.script_is_ours(path, mark):
            plan.append((f'remove {cp.tilde(path)}', path.unlink))
    if CLAUDE_ROUTE_FILE.exists():
        plan.append((f'remove {cp.tilde(CLAUDE_ROUTE_FILE)}', CLAUDE_ROUTE_FILE.unlink))

    def record():
        claude_record(launcher=None, shim=None, uninstalled_at=cp.now_utc().isoformat(timespec='seconds'))
        patch_claude_status(installed=False)
    plan.append((f'mark the Claude pool uninstalled ({cp.tilde(cp.INSTALL_FILE)}, {cp.tilde(CLAUDE_STATUS_FILE)})', record))
    return plan


def claude_keep_note():
    return (f'keep {cp.tilde(CLAUDE_AUTH)} (the Claude account logins), {cp.tilde(CLAUDE_CONFIG)}, the builds and logs '
            '(subpool claude install brings it back)')


def cmd_claude_uninstall(args):
    plan = claude_uninstall_plan()
    if len(plan) == 1 and not claude_installed():  # only the record: nothing of the Claude pool's is in place
        print('The Claude pool is not installed; nothing to do.')
        return
    shim_line = cp.script_is_ours(CLAUDE_SHIM, CLAUDE_SHIM_MARK)
    if not args.yes:
        steps = [text for text, _ in plan] + [claude_keep_note()]
        print('subpool claude uninstall would:\n' + '\n'.join(f'  {i + 1}. {p}' for i, p in enumerate(steps))
              + '\nClaude Code itself is not touched: running pooled sessions stop answering (start them again with '
                'claude --resume).\nRe-run with --yes.')
        return
    for text, fn in plan:
        try:
            fn()
            print(f'  ✓ {text}')
        except FileNotFoundError:
            print(f'  ✓ {text}')
        except OSError as e:
            print(f'  ✗ {text} ({e.strerror or e})')
    print(f'  ✓ {claude_keep_note()}')
    print('\nThe Claude pool is uninstalled. Claude Code keeps its own login and starts direct.'
          + (f'\nThe shim is gone: remove the line {CLAUDE_PATH_LINE} from your shell profile when convenient.'
             if shim_line else ''))


def cmd_claude_route(args):
    """Where new Claude Code sessions go. Running sessions stay where they started."""
    if args.route is None:
        print(claude_route())
        return
    write_claude_route(args.route)
    patch_claude_status(route=args.route)
    if args.route == 'pool':
        print('route: pool. New Claude Code sessions started with claude-pool (or the shim) go through the Claude '
              'pool while it answers; running sessions stay where they are.')
    else:
        print('route: direct. New Claude Code sessions start direct, on Claude Code\'s own account; running sessions '
              'stay where they are. subpool claude route pool turns it back.')


def cmd_claude_shim(args):
    if args.action == 'install':
        if CLAUDE_SHIM.exists() and not cp.script_is_ours(CLAUDE_SHIM, CLAUDE_SHIM_MARK):
            cp.sys.exit(f'subpool claude shim: {cp.tilde(CLAUDE_SHIM)} exists and is not subpool\'s; left alone')
        cp.write_script(CLAUDE_SHIM, shim_text())
        claude_record(shim=str(CLAUDE_SHIM))
        on_path = str(SHIMS) in cp.os.environ.get('PATH', '').split(cp.os.pathsep)
        print(f'wrote {cp.tilde(CLAUDE_SHIM)}: plain claude then goes through the Claude pool (route pool and the pool '
              'answering), else starts direct with a one-line warning. CLAUDEPOOL=off claude always starts direct.')
        if on_path:
            print(f'{cp.tilde(SHIMS)} is already on your PATH.')
        else:
            print(f'Add this line to ~/.zprofile (subpool never edits shell profiles), then open a new terminal:\n'
                  f'  {CLAUDE_PATH_LINE}')
        return
    if not (CLAUDE_SHIM.exists() or CLAUDE_SHIM.is_symlink()):
        print(f'no shim at {cp.tilde(CLAUDE_SHIM)}')
    elif not cp.script_is_ours(CLAUDE_SHIM, CLAUDE_SHIM_MARK):
        cp.sys.exit(f'subpool claude shim: {cp.tilde(CLAUDE_SHIM)} is not subpool\'s; left alone')
    else:
        CLAUDE_SHIM.unlink()
        print(f'removed {cp.tilde(CLAUDE_SHIM)}: plain claude starts direct again.')
    claude_record(shim=None)
    print(f'Remove the line {CLAUDE_PATH_LINE} from your shell profile if you added it.')


def claude_status_file():
    """claude-status.json as the guard wrote it, and its age in seconds (None: no file)."""
    st = cp.read_json(CLAUDE_STATUS_FILE, None)
    if not isinstance(st, dict):
        return None, None
    at = cp.parse_time(st.get('generated_at'))
    return st, (cp.now_utc() - at).total_seconds() if at else None


def claude_status_now():
    """A live look at the Claude pool, in claude-status.json's full shape: each account as the pool has it now (its
    management API), with the plans, usage and credit readings the guard's Claude pass last took (claude-guard.json)
    and your labels, sizes, reserve flags and credit policies (claude-seats.json). Nothing is polled or written."""
    pool = cp.pool_instance('claude')
    running = cp.port_open(pool.port)
    out = {'generated_at': cp.now_utc().isoformat(timespec='seconds'), 'live': True,
           'pool': {'installed': claude_installed(), 'running': running, 'route': claude_route(), 'port': pool.port,
                    'version': None, 'error': None},
           'seats': []}
    if not running:
        return out
    claude = cp.seat_pool('claude')
    try:
        listing, headers = cp.api('GET', '/v0/management/auth-files', timeout=10, want_headers=True, port=pool.port)
        seats = [s for s in cp.load_seats(claude, listing=listing) if s['provider'] == 'claude']
    except (cp.PoolDown, cp.ApiError, cp.KeyUnavailable) as e:
        out['pool']['error'] = cp.short(str(e), 200)
        return out
    guard = cp.read_json(claude.guard, {})
    guard = guard if isinstance(guard, dict) else {}
    meta = cp.read_meta(claude)
    sienna_guard.claude_apply_plans(guard, seats, meta)
    live_version = {k.lower(): v for k, v in headers.items()}.get('x-cpa-version') or ''
    st = sienna_guard.build_claude_status(seats, guard, meta, live_version=live_version)
    st['live'] = True
    st['pool'].update(installed=out['pool']['installed'], port=pool.port,
                      version={k.lower(): v for k, v in headers.items()}.get('x-cpa-version') or st['pool']['version'])
    return st


def claude_status_view(live=False):
    """(status dict, where it came from): the guard's claude-status.json while fresh, else a live look."""
    st, age = claude_status_file()
    if live or st is None or age is None or age > 180:
        return claude_status_now(), 'live'
    pool = st.get('pool') if isinstance(st.get('pool'), dict) else {}
    pool.setdefault('route', claude_route())
    st['pool'] = pool
    return st, f'guard, {int(age)} s ago'


def pct_or_dash(win):
    used = (win or {}).get('used') if isinstance(win, dict) else None
    return f'{used:g}%' if isinstance(used, (int, float)) else '—'


def print_claude_status(st, source):
    pool = st.get('pool') or {}
    if not pool.get('running', True):
        print(f'Claude pool is NOT running on :{CLAUDE_PORT}. Try: subpool claude install ; subpool claude logs')
        return
    if pool.get('error'):
        print(f'Claude pool is running but subpool cannot read it: {pool["error"]}\nTry: subpool doctor')
        return
    seats = st.get('seats') or []
    spends = [r for r in seats if isinstance(r.get('credits'), dict) and r['credits'].get('spending')]
    ready = sum(1 for r in seats if r.get('state') in ('active', 'ready') and r not in spends)  # on plan quota
    serving = next((r.get('label') + (' (on usage credits)' if r in spends else '') for r in seats
                    if r.get('state') == 'active'), None)
    print(f'Claude pool  {cp.total_size_text(seats)}{ready} of {len(seats)} accounts available  ·  '
          f'route: {pool.get("route") or claude_route()}  ·  '
          f'new sessions → {serving or "NONE" if seats else "no accounts yet"}  ·  ({source})')
    if not seats:
        print('Add a Claude account: subpool claude login "<Label>" --priority <n>')
        return
    print()
    print(f'  {"account":<22} {"plan":<12} {"prio":>4}  {"state":<10} {"5h":>5} {"week":>5}  {"credits":<22} note')
    for r in seats:
        cred = r.get('credits') if isinstance(r.get('credits'), dict) else {}
        if cred.get('policy') == 'last-resort':
            credits = f'last resort, cap ${cred.get("cap") or 0:g}'
        elif cred:
            credits = 'off'
        else:
            credits = ''
        if cred.get('enabled') and isinstance(cred.get('used'), (int, float)):
            credits += f' (${cred["used"]:g} used)'
        size = f'{r["weight"]:g}×' if isinstance(r.get('weight'), (int, float)) else ''
        plan = ' '.join(x for x in (str(r.get('plan') or ''), size) if x)
        note = r.get('detail') or ''
        if r.get('until'):
            note = f'{note}; back {cp.when(r["until"])}' if note else f'back {cp.when(r["until"])}'
        prio = r.get('priority') if r.get('priority') is not None else ''
        print(f'{cp.STATE_ICON.get(r.get("state"), "?")} {str(r.get("label") or r.get("name")):<22} {plan:<12} '
              f'{prio:>4}  {str(r.get("state")):<10} {pct_or_dash(r.get("five_hour")):>5} '
              f'{pct_or_dash(r.get("week")):>5}  {credits:<22} {note}')


def cmd_claude_status(args):
    if not claude_installed():
        if args.json:
            print(cp.json.dumps({'pool': {'installed': False, 'route': claude_route()}, 'seats': []}, indent=2))
            return
        cp.sys.exit('subpool claude status: the Claude pool is not installed. subpool claude install sets it up.')
    st, source = claude_status_view(args.live)
    if args.json:
        print(cp.json.dumps(st, indent=2))
    else:
        print_claude_status(st, source)

CLAUDE_PROFILE_URL = 'https://api.anthropic.com/api/oauth/profile'  # the account's plan (organization, rate tier)

CLAUDE_OAUTH_HEADERS = {'Authorization': 'Bearer $TOKEN$', 'Accept': 'application/json',
                        'anthropic-beta': 'oauth-2025-04-20', 'User-Agent': 'claude-code/2.1.283'}


def claude_seat_call(seat, url, timeout=30):
    """GET an api.anthropic.com OAuth endpoint as one Claude account, through the Claude pool's management api-call
    (the pool puts that account's token where $TOKEN$ is; subpool never sees it). Returns (HTTP status, parsed
    body); raises PoolDown, PoolTimeout, ApiError or KeyUnavailable when the pool itself does not answer."""
    header = dict(CLAUDE_OAUTH_HEADERS)
    version = sienna_guard.claude_code_version()
    if version:  # the endpoint throttles other clients hard: say which Claude Code this Mac has
        header['User-Agent'] = f'claude-code/{version}'
    req = {'auth_index': seat['auth_index'], 'method': 'GET', 'url': url, 'header': header}
    try:
        r = cp.api('POST', '/v0/management/api-call', req, timeout=timeout + 5, port=CLAUDE_PORT)
    except cp.ApiError as e:
        if e.code == 502:  # the pool's own reply: Anthropic did not answer in time
            raise cp.PoolTimeout(f'api.anthropic.com did not answer: {cp.short(str(e.body), 80)}') from None
        raise
    code = r.get('status_code') or r.get('statusCode')
    raw = r.get('body')
    try:
        data = cp.json.loads(raw) if isinstance(raw, str) and raw else (raw or {})
    except ValueError:
        data = {'_raw': str(raw)[:200]}
    return code, data


def claude_profile_plan(seat, reraise=()):
    """(tier, weight, problem) for a Claude account from Anthropic's profile call: problem is None, or why there is
    no tier (the pool's api-call failed, or Anthropic said no). reraise: exceptions to let through instead."""
    try:
        code, data = claude_seat_call(seat, CLAUDE_PROFILE_URL)
    except reraise:
        raise
    except (cp.PoolDown, cp.PoolTimeout, cp.ApiError, cp.KeyUnavailable) as e:
        return None, None, cp.short(str(e), 120)
    if code != 200:
        return None, None, f'HTTP {code}: {cp.short(cp.json.dumps(data), 100)}'
    tier, weight = claude_plan(data)
    return tier, weight, None if tier else 'the answer names no plan'


def record_claude_plan(name, tier, weight):
    """Keep an account's plan in claude-guard.json (plans), where load_seats and the guard's Claude pass read it."""
    pool = cp.seat_pool('claude')
    with cp.guard_lock(pool=pool):
        guard = cp.read_guard(pool)
        plans = guard.get('plans') if isinstance(guard.get('plans'), dict) else {}
        plans[name] = {'plan': tier, 'weight': weight, 'at': cp.now_utc().isoformat(timespec='seconds')}
        guard['plans'] = plans
        cp.write_json(pool.guard, guard)


def cmd_claude_login(args):
    """Add a Claude account to the Claude pool (or sign one in again): CLIProxyAPI's own -claude-login with the
    Claude pool's build and config, a new OAuth login of the pool's own. Claude Code's login is never used."""
    pool = cp.seat_pool('claude')
    if not claude_installed() or not cp.cpa_binary(pool.link).exists():
        cp.sys.exit('subpool claude login: the Claude pool is not installed. subpool claude install sets it up.')
    problem = cp.label_problem(args.label)
    if problem:
        cp.sys.exit(f'subpool claude login: {problem}')
    if args.no_open:
        print('Getting a claude.ai sign-in link. Open it in a browser signed in to the Claude account to add (a '
              'private window or its own browser profile), sign in and approve.\n')
    else:
        print('Opening the claude.ai sign-in in your browser. Sign in with the Claude account to add and approve.\n'
              '(If the browser does not open, use the URL printed below.)\n')
    print('This is a login of the pool\'s own: Claude Code keeps its own login as it is.\n')
    CLAUDE_LOGS.mkdir(parents=True, exist_ok=True)
    before = {p.name for p in pool.auth.glob('claude-*.json')}
    mark = cp.log_mark(pool.log)
    saved, _ = cp.login_seat(no_open=args.no_open, on_url=cp.link_copier(args.no_copy), pool=pool)
    if not saved:
        errors = cp.sign_in_errors(mark, pool.log)
        cp.sys.exit('\nlogin did not complete; nothing changed' + (f' (the pool said: {errors[-1]})' if errors else ''))
    new = saved.name not in before
    # Re-login keeps the name and added_at: credits polls must follow the latest account addition,
    # not a credential refresh. Removing and adding the account again goes through fresh_seat_meta.
    label = args.label if new else (cp.read_meta(pool).get(saved.name) or {}).get('label')
    if new:
        cp.fresh_seat_meta(saved.name, args.label, pool)
    priority = args.priority
    if priority is not None:
        loaded = cp.set_priority_when_loaded(saved.name, priority, label, pool) is not None
    elif new:  # after the accounts already in the pool and before the reserve
        priority = cp.set_priority_when_loaded(saved.name, cp.new_seat_place(saved.name, provider='claude'), label, pool)
        loaded = priority is not None
    else:
        loaded = True
    seat = None
    if loaded:
        with cp.contextlib.suppress(cp.PoolDown, cp.ApiError, cp.KeyUnavailable):
            seat = next((s for s in cp.load_seats(pool) if s['name'] == saved.name), None)
    tier, weight, why = claude_profile_plan(seat) if seat else (None, None, 'the pool has not loaded it yet')
    if tier:
        record_claude_plan(saved.name, tier, weight)
    c = claude_file_claims(saved)
    print(f'\nseat {saved.name}: {c["email"]} plan={tier or "unknown"} account={c["account_id"]}'
          + (f' label={label}' if label else '') + (f' priority={priority}' if priority is not None else ''))
    if not tier:
        print(f'Its plan is not known yet ({why}); the guard asks again, and subpool claude weight sets its size.')
    if not new:
        shown = label or cp.default_label(c['email'], tier)
        if args.label and args.label.lower() != (label or '').lower():
            print(f'That was {shown} once more (the same Claude account): its login is refreshed, nothing was added '
                  'and it keeps its name. For another account, open the link in a browser signed in to it.')
        else:
            print(f'{shown} was in the pool already: its login is refreshed.')
    with cp.guard_lock(pool=pool):
        cp.refresh_status_file(pool)


def cmd_claude_credits(args):
    """Set a Claude account's credit policy in claude-seats.json; the guard's Claude pass enforces it."""
    pool = cp.seat_pool('claude')
    if args.policy == 'off' and args.cap is not None:
        cp.sys.exit('subpool claude credits: --cap goes with last-resort (off spends no credits at all)')
    if args.cap is not None and not cp.valid_weight(args.cap):
        cp.sys.exit(f'subpool claude credits: the cap is a positive number of US dollars, not {args.cap:g}')
    with cp.guard_lock(pool=pool):
        s = cp.match_seat([x for x in cp.load_seats(pool) if x['provider'] == 'claude'], args.seat)
        was = credit_policy(cp.read_meta(pool), s['name'])
        cap = args.cap if args.cap is not None else was['cap']
        if args.policy == 'last-resort' and cap is None:
            cp.sys.exit(f'subpool claude credits: last-resort needs a cap, the most {s["label"]} may spend: '
                     f'subpool claude credits {cp.shlex.quote(s["label"])} last-resort --cap <USD>')
        cp.update_meta(s['name'], pool, credits=None if args.policy == 'off' else {'policy': 'last-resort',
                                                                                 'cap': float(cap)})
        guard = cp.read_guard(pool)
        g = (guard.get('seats') or {}).get(s['name']) or {}
        if g.pop('override_until', None):  # a new policy ends `subpool claude enable`'s override of the old one
            cp.write_json(pool.guard, guard)
            print(f'{s["label"]}: the credit guard\'s override (subpool claude enable) ends; the new policy applies.')
        sienna_guard.claude_apply_credit_policy(pool)
        cp.refresh_status_file(pool)
    if args.policy == 'off':
        print(f'{s["label"]}: policy off. Turn usage credits off at claude.ai (Settings → Usage). '
              "Only Anthropic's switch prevents every paid request; the guard observes spending afterwards.")
    else:
        print(f'{s["label"]}: credits as the very last resort, up to ${float(cap):g} this month. It spends them only '
              'once every other account, the reserve included, is out; the guard parks it again at the cap or as '
              'soon as another account can serve. Usage credits must also be on for it at claude.ai.')


def cmd_claude_logs(args):
    cp.os.execvp('tail', ['tail', '-n', str(args.lines)] + (['-F'] if args.follow else []) + [str(CLAUDE_MAIN_LOG)])


def cmd_claude(args):
    args.claude_fn(args)


def add_claude_parser(sub):
    """One sienna command family; claude remains an argparse alias for every existing invocation."""
    s = sub.add_parser('sienna', aliases=['claude'], help='the Claude pool: Claude Code through several Claude accounts (see addons/sienna/docs/SIENNA.md)',
                       description='The Claude pool: a second CLIProxyAPI instance (its own build, port, config, '
                                   'logins and launchd agent) that Claude Code reaches with ANTHROPIC_BASE_URL only, '
                                   'keeping its own claude.ai login. subpool never sets that variable globally: '
                                   'the claude-pool launcher sets it for the one process it starts.')
    s.set_defaults(fn=cmd_claude)
    claude = s.add_subparsers(dest='claude_cmd', required=True,
                              metavar='{install,uninstall,status,login,enable,disable,label,weight,priority,reserve,'
                                      'remove,order,credits,route,shim,selftest,logs,resume,lane-resume,accept-engine,cpa-check,desktop}')
    desktop.add_parser(claude, desktop.cmd_desktop)
    from . import lane_engine
    lane_engine.add_parser(claude)
    x = claude.add_parser('install', help='set up or repair the Claude pool: its build, config, auth-claude/, launchd '
                                          'agent, gate probe and the claude-pool launcher')
    x.add_argument('--dry-run', action='store_true', help='print every step without changing anything')
    x.set_defaults(claude_fn=cmd_claude_install)
    x = claude.add_parser('uninstall', help='stop the Claude pool and remove its agent, launcher and shim (keeps '
                                            'auth-claude/, the config and builds)')
    x.add_argument('--yes', action='store_true', help='do it (without --yes it only prints the plan)')
    x.set_defaults(claude_fn=cmd_claude_uninstall)
    x = claude.add_parser('status', help='the Claude accounts and where new sessions go')
    x.add_argument('--json', action='store_true', help='print claude-status.json (what the menu bar reads) as JSON')
    x.add_argument('--live', action='store_true', help='ask the Claude pool now instead of the guard\'s file')
    x.set_defaults(claude_fn=cmd_claude_status)
    add_claude_seat_parsers(claude)
    x = claude.add_parser('route', help='where new Claude Code sessions go: pool or direct (no argument: print it)')
    x.add_argument('route', nargs='?', choices=CLAUDE_ROUTES)
    x.set_defaults(claude_fn=cmd_claude_route)
    x = claude.add_parser('shim', help='an optional ~/.subpool/shims/claude, so plain claude goes through the pool '
                                       '(prints the PATH line to add; never edits shell profiles)')
    x.add_argument('action', choices=('install', 'remove'))
    x.set_defaults(claude_fn=cmd_claude_shim)
    x = claude.add_parser('selftest', help='move a throwaway claude -p conversation between two accounts and check '
                                           'its thinking survives (spends a few requests; asks first)')
    x.add_argument('from_seat', metavar='A', help='the account the conversation starts on')
    x.add_argument('to_seat', metavar='B', help='the account it moves to')
    x.add_argument('--compact', action='store_true', help='also run /compact on A before the move')
    x.add_argument('--model', help='the model for the test turns (default: Claude Code\'s own)')
    x.add_argument('--yes', action='store_true', help='skip the confirmation prompt')
    x.set_defaults(claude_fn=sienna_selftest.cmd_claude_selftest)
    x = claude.add_parser('logs', help='tail the Claude pool log')
    x.add_argument('-f', '--follow', action='store_true')
    x.add_argument('-n', '--lines', type=int, default=60)
    x.set_defaults(claude_fn=cmd_claude_logs)
    return claude


def add_claude_seat_parsers(claude):
    """subpool claude login|enable|disable|label|weight|priority|reserve|remove|order|credits: the Claude pool's
    accounts, with the same arguments as the Codex seat commands and the same code (pool "claude")."""
    x = claude.add_parser('login', help='add a Claude account to the pool, or sign one in again (a login of the '
                                        'pool\'s own; Claude Code keeps its login)')
    x.add_argument('label', metavar='LABEL', help='the name for the account')
    x.add_argument('--no-open', action='store_true',
                   help='print the sign-in link instead of opening it (open it where you are signed in to that '
                        'Claude account)')
    x.add_argument('--no-copy', action='store_true',
                   help='leave the clipboard alone (by default a printed sign-in link is also copied to it; '
                        'CODEXPOOL_NO_CLIPBOARD=1 does the same)')
    x.add_argument('--priority', type=int,
                   help='higher is used first (default for a new account: after the accounts already in the pool, '
                        'before the reserve)')
    x.set_defaults(claude_fn=cmd_claude_login)
    for name, fn, hlp in (('enable', cp.cmd_enable, 'put an account back in rotation (overrides the credit guard)'),
                          ('disable', cp.cmd_disable, 'take an account out of rotation')):
        x = claude.add_parser(name, help=hlp)
        x.add_argument('seat', metavar='SEAT')
        x.set_defaults(claude_fn=fn, pool='claude')
    x = claude.add_parser('label', help='rename an account')
    x.add_argument('seat', metavar='SEAT')
    x.add_argument('label', metavar='LABEL')
    x.set_defaults(claude_fn=cp.cmd_label, pool='claude')
    x = claude.add_parser('weight', help="set an account's share of the pool total (Pro = 1, Max 5× = 5, Max 20× = 20)")
    x.add_argument('seat', metavar='SEAT')
    x.add_argument('weight', type=float)
    x.set_defaults(claude_fn=cp.cmd_weight, pool='claude')
    x = claude.add_parser('priority', help='change fill order (higher first)')
    x.add_argument('seat', metavar='SEAT')
    x.add_argument('priority', type=int)
    x.set_defaults(claude_fn=cp.cmd_priority, pool='claude')
    x = claude.add_parser('reserve', help='mark an account as reserve: it fills last, red when serving')
    x.add_argument('seat', metavar='SEAT')
    x.add_argument('--off', action='store_true', help='make it a regular account again (it moves before any reserve)')
    x.set_defaults(claude_fn=cp.cmd_reserve, pool='claude')
    x = claude.add_parser('remove', help='delete an account\'s login file from the pool (the login is not revoked)')
    x.add_argument('seat', metavar='SEAT')
    x.add_argument('--yes', action='store_true')
    x.set_defaults(claude_fn=cp.cmd_remove, pool='claude')
    x = claude.add_parser('order', help='set the fill order in one go: subpool claude order SEAT [SEAT ...]',
                          description='Set the fill order: the accounts named come first, in that order, then the '
                                      'other regular accounts in the order they had, then the reserve accounts '
                                      '(always last, in their own order). The order is kept as yours in '
                                      'claude-seats.json, which "claude_balancing": "reset" leaves for later.')
    x.add_argument('seats', nargs='+', metavar='SEAT', help='an account label (or part of one), first used first')
    x.set_defaults(claude_fn=cp.cmd_order, pool='claude')
    x = claude.add_parser('credits', help='whether an account may spend usage credits: off (the default) or '
                                          'last-resort with a cap in USD',
                          description='off: turn usage credits off at claude.ai; the guard cannot prevent every paid '
                                      'request. last-resort: it may spend them once every other account, the reserve '
                                      'included, is out, until observed spending reaches --cap USD this month. Set a matching '
                                      'member spend limit at claude.ai too.')
    x.add_argument('seat', metavar='SEAT')
    x.add_argument('policy', choices=CREDIT_POLICIES)
    x.add_argument('--cap', type=float, metavar='USD',
                   help='for last-resort: the most it may spend, in US dollars (default: the cap it has already)')
    x.set_defaults(claude_fn=cmd_claude_credits)
