"""Claude CLI account switching delegated to upstream cswap, never a proxy.

Only versioned public JSON is read. Credentials, refresh and switching belong to
cswap. No credential migration from the retired pool is attempted.
"""
import argparse
import copy
import datetime
import json
import math
import os
from pathlib import Path
import shutil
import subprocess

from . import cp

REVISION = '3a4e5c14873eb5b32f182d55c68da98ac8c0db45'
SOURCE = 'https://github.com/realiti4/claude-swap/archive/' + REVISION + '.tar.gz'
STATUS = cp.STATE / 'claude-cli-status.json'
OPTIONS = cp.STATE / 'claude-cli-options.json'
HISTORY = cp.STATE / 'claude-cli-history.jsonl'


def executable():
    return shutil.which('cswap') or (str(Path.home() / '.local/bin/cswap')
           if (Path.home() / '.local/bin/cswap').is_file() else None)


def options():
    try:
        value = json.loads(OPTIONS.read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def reserve_key(row):
    # Account slots can change after removal. Public email identifies the login.
    return str(row.get('email') or '').strip().lower()


def reserves():
    value = options().get('reserves', {})
    return value if isinstance(value, dict) else {}


def call(args, structured=False, ok=(0,), input_text=None):
    binary = executable()
    if not binary:
        raise ValueError('cswap is not installed. Run codexpool claude install when ready.')
    result = subprocess.run([binary] + list(args), input=input_text or '',
                            text=True, capture_output=True, timeout=90)
    # Never echo arbitrary subprocess output: only known public JSON fields pass.
    if result.returncode not in ok:
        raise ValueError('cswap command failed (exit %s). Run cswap %s in Terminal for details.' %
                         (result.returncode, args[0]))
    if not structured:
        return None
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise ValueError('cswap returned invalid JSON; the saved view has not been replaced.')
    if not isinstance(value, dict) or value.get('schemaVersion') != 1 or value.get('error'):
        raise ValueError('Unsupported or unsuccessful cswap JSON response.')
    return value


def percent(value):
    if isinstance(value, bool) or not isinstance(value, (float, int)) or not math.isfinite(value) or value < 0:
        return None
    return min(100.0, max(0.0, float(value)))


def window(value):
    if not isinstance(value, dict):
        return None
    used = percent(value.get('pct'))
    if used is None:
        return None
    return {'used': used, 'reset_at': value.get('resetsAt')}


def project(payload, config=None):
    """Allowlist public account metadata; never persist an arbitrary upstream object."""
    if payload.get('schemaVersion') != 1 or not isinstance(payload.get('accounts'), list):
        raise ValueError('Unsupported cswap account response.')
    accounts, numbers = [], set()
    reserve_accounts = reserves()
    active_number = payload.get('activeAccountNumber')
    for row in payload['accounts']:
        if not isinstance(row, dict):
            raise ValueError('Invalid cswap account row.')
        number = row.get('number')
        if isinstance(number, bool) or not isinstance(number, int) or number < 1 or number in numbers:
            raise ValueError('Invalid or duplicate cswap account number.')
        numbers.add(number)
        active = row.get('active') is True
        if active != (active_number == number):
            raise ValueError('cswap active account fields disagree.')
        usage = row.get('usage') if row.get('usageStatus') == 'ok' else None
        usage = usage if isinstance(usage, dict) else {}
        week, short = window(usage.get('sevenDay')), window(usage.get('fiveHour'))
        exhausted = any(w and w['used'] >= 100 for w in (week, short))
        reserve = reserve_accounts.get(reserve_key(row))
        reserve = reserve if isinstance(reserve, dict) else None
        held = bool(reserve is not None and reserve.get('held') and row.get('disabled'))
        excluded = bool(row.get('disabled') and not held) or bool(reserve and reserve.get('excluded'))
        state = ('disabled' if excluded else 'blocked' if not usage else
                 'exhausted' if exhausted else 'active' if active else 'ready')
        deadlines = []
        for win in (week, short):
            if win and win['used'] >= 100 and isinstance(win.get('reset_at'), str):
                try:
                    deadlines.append(datetime.datetime.fromisoformat(win['reset_at'].replace('Z', '+00:00')))
                except ValueError:
                    pass
        until = max(deadlines).isoformat() if deadlines else None
        subscription = row.get('subscription')
        subscription = subscription if isinstance(subscription, dict) else {}
        from .pool import claude_plan
        plan, weight = claude_plan({'organization': {
            'organization_type': subscription.get('organizationType'),
            'rate_limit_tier': subscription.get('rateLimitTier'),
            'seat_tier': subscription.get('seatTier')}, 'account': {
            'has_claude_max': subscription.get('hasMax'), 'has_claude_pro': subscription.get('hasPro')}})
        if plan == 'max_5x' and '5x' not in str(subscription.get('rateLimitTier') or ''):
            plan, weight = 'max', None  # Max alone cannot distinguish 5× from 20×.
        if plan not in ('pro', 'max_5x', 'max_20x', 'team', 'team_premium'):
            weight = None
        if plan == 'team' and not subscription.get('seatTier') and not subscription.get('rateLimitTier'):
            weight = None
        accounts.append({'until': until, 'name': str(number), 'label': str(row.get('alias') or row.get('email') or 'Account %s' % number),
                         'email': str(row.get('email') or ''), 'provider': 'claude',
                         'state': state, 'priority': 10000 - number, 'plan': plan,
                         'weight': weight, 'capacity_known': weight is not None,
                         'plan_detected_at': subscription.get('fetchedAt'),
                         'reserve': reserve is not None,
                         'reserve_held': held, 'rotation_disabled': bool(row.get('disabled')),
                         'week': week, 'five_hour': short, 'selected': active,
                         'detail': '' if usage else str(row.get('usageStatus') or 'Usage unavailable'),
                         'usage_fetched_at': row.get('usageFetchedAt'),
                         'scoped': [{'name': str(w.get('name', 'Model')), **window(w)}
                                    for w in usage.get('scoped', []) if isinstance(w, dict) and window(w)]})
    if active_number is not None and active_number not in numbers:
        raise ValueError('cswap active account is absent from the account list.')
    selected = next((a for a in accounts if a['selected']), None)
    # The headline is the selected account, not an invented sum of unequal plans.
    used = selected['week']['used'] if selected and selected['week'] else None
    config = config or {}
    return {'generated_at': cp.now_utc().isoformat(),
            'active': selected['label'] if selected else '', 'seats': accounts,
            'pool': {'backend': 'cswap', 'installed': True, 'running': True,
                     'headline': 'all', 'display': 'left', 'used_pct': used,
                     'selected_number': active_number, 'selected_usage_known': used is not None,
                     'auto_switch': options().get('auto_switch') is True,
                     'strategy': config.get('autoswitch.strategy', 'best'),
                     'threshold': config.get('autoswitch.threshold', 90)}}


def refresh():
    payload = call(['list', '--json'], structured=True)
    config = call(['config', 'list', '--json'], structured=True)
    settings = {v['key']: v.get('value') for v in config.get('settings', [])
                if isinstance(v, dict) and v.get('key') in ('autoswitch.strategy', 'autoswitch.threshold')}
    view = project(payload, settings)
    cp.write_json(STATUS, view)
    return view


def guard():
    if not executable() or options().get('enabled') is not True:
        return
    if reserves():
        # Eligibility is our only extension. cswap still chooses and switches.
        sync_reserves(refresh())
    if options().get('auto_switch') is True:
        # Upstream owns its locks, cooldown, hysteresis, choice and credential writes.
        call(['auto', '--once', '--json'], ok=(0, 2, 3))
    refresh()


def sync_reserves(view):
    """Hold reserves until every enabled regular account reaches the threshold.

    Unknown regular usage does not release the reserve. With rotation off, keep
    reserves held; cswap allows disabled accounts to be switched manually.
    """
    config = options()
    records = reserves()
    regular = [a for a in view['seats'] if not a['reserve'] and a['state'] != 'disabled']
    threshold = percent(view.get('pool', {}).get('threshold'))
    threshold = threshold if threshold is not None else 90
    release = config.get('auto_switch') is True and all(
        any(w and w['used'] >= threshold for w in (a['week'], a['five_hour'])) for a in regular)
    changed = False
    for account in view['seats']:
        key = reserve_key(account)
        record = records.get(key)
        if not isinstance(record, dict):
            continue
        hold = not release and not record.get('excluded')
        disabled = hold or bool(record.get('excluded'))
        if hold:
            # Record ownership before disabling so an interrupted pass recovers.
            record['held'] = True
            cp.write_json(OPTIONS, {**config, 'reserves': records})
        if disabled != account.get('rotation_disabled', account['state'] == 'disabled'):
            call(['disable' if disabled else 'enable', account['name']])
            changed = True
        record['held'] = hold
    cp.write_json(OPTIONS, {**config, 'reserves': records})
    return changed


def set_reserve(account, mode):
    view = refresh()
    row = next((a for a in view['seats'] if a['name'] == account), None)
    if row is None or not reserve_key(row):
        raise ValueError('Refresh and choose a saved Claude account with an email address.')
    records, key = reserves(), reserve_key(row)
    if mode == 'on':
        if key not in records:
            records[key] = {'excluded': row['state'] == 'disabled', 'held': False}
    else:
        record = records.get(key, {})
        if record.get('held') and not record.get('excluded'):
            call(['enable', account])
        records.pop(key, None)
    cp.write_json(OPTIONS, {**options(), 'reserves': records})


def guard_descriptor(legacy):
    # Reuse the generic guard-lock contract without registering a Claude proxy.
    descriptor = copy.copy(legacy)
    descriptor.lock = cp.STATE / 'claude-cli.lock'
    return descriptor


def saved():
    try:
        return json.loads(STATUS.read_text())
    except (OSError, ValueError):
        return {'generated_at': cp.now_utc().isoformat(), 'seats': [],
                'pool': {'backend': 'cswap', 'installed': bool(executable()), 'running': False}}


def install(args):
    command = ['uv', 'tool', 'install', '--python', '3.13', 'claude-swap @ ' + SOURCE]
    if args.dry_run:
        print('Install upstream cswap pinned at ' + REVISION)
        print(' '.join(command))
        print('No account import, proxy installation, or automatic switching.')
        return
    from . import pool
    if pool.claude_installed():
        raise ValueError('Retire the old proxy first: codexpool claude retire-proxy (preview), then --yes when ready.')
    if not shutil.which('uv'):
        raise ValueError('Install uv first, then run codexpool claude install again.')
    # An existing cswap is never upgraded or replaced silently.
    if not executable():
        subprocess.run(command, check=True)
    from . import cswap_patch
    cswap_patch.install(executable())
    cp.write_json(OPTIONS, {**options(), 'enabled': True})
    refresh()
    print('Claude CLI connected to cswap. Use plain claude in Terminal.')


def _command(args):
    try:
        action = args.cswap_action
        if action == 'threshold' and (not math.isfinite(args.value) or not 50 <= args.value <= 99.9):
            raise ValueError('Choose a threshold from 50 to 99.9 percent.')
        if action == 'install':
            return install(args)
        if action == 'retire-proxy':
            from . import pool
            return pool.cmd_claude_uninstall(args)
        if action == 'status':
            value = refresh() if args.live else saved()
            print(json.dumps(value, indent=2) if args.json else '\n'.join(
                ('* ' if a.get('selected') else '  ') + a['name'] + '  ' + a['label'] + '  ' + a['state']
                for a in value.get('seats', [])) or 'No cswap accounts. Sign in with claude, then cswap add.')
            return
        if action == 'auto':
            if args.mode == 'on':
                if not executable():
                    raise ValueError('Install cswap before enabling automatic switching.')
                # Do not also run cswap auto or its menu-bar auto-switcher.
            cp.write_json(OPTIONS, {**options(), 'enabled': True, 'auto_switch': args.mode == 'on'})
        elif action == 'strategy':
            call(['config', 'set', 'autoswitch.strategy', args.strategy])
        elif action == 'threshold':
            call(['config', 'set', 'autoswitch.threshold', str(args.value)])
        elif action == 'switch':
            value = call(['switch', args.account, '--json'], structured=True)
            target = value.get('to') or {}
            if str(target.get('number')) != args.account or value.get('reason') not in ('switched', 'already-active'):
                raise ValueError('cswap did not confirm the requested account. Refresh before trying again.')
        elif action == 'remove':
            if not args.yes:
                raise ValueError('Removing a saved account requires --yes.')
            call(['remove', args.account], input_text='y\n')
        elif action == 'detect-plans':
            from . import cswap_patch
            cswap_patch.install(executable())
            refresh()
            print('Automatic Claude plan detection enabled through cswap.')
            return
        elif action == 'reserve':
            set_reserve(args.account, args.mode)
        elif action in ('enable', 'disable'):
            if reserves():
                view = refresh()
                row = next((a for a in view['seats'] if a['name'] == args.account), None)
                records = reserves()
                record = records.get(reserve_key(row or {}))
                if isinstance(record, dict):
                    record.update(excluded=action == 'disable', held=False)
                    cp.write_json(OPTIONS, {**options(), 'reserves': records})
            call([action, args.account])
        elif action == 'add':
            call(['add'])
        elif action == 'label':
            call(['alias', args.account, args.label])
        else:
            raise ValueError('Unsupported Claude CLI action.')
        cp.write_json(OPTIONS, {**options(), 'enabled': True})
        view = refresh()
        if reserves():
            sync_reserves(view)
            refresh()
        print('Claude CLI updated through cswap. Reopen Claude Code to apply a login switch immediately on macOS.')
    except (ValueError, OSError, subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))


def command(args):
    from . import pool
    with cp.guard_lock(wait=True, pool=guard_descriptor(pool.claude_seat_pool())) as acquired:
        if not acquired:
            raise SystemExit('Claude CLI is busy. Try again after the current operation finishes.')
        return _command(args)


def read_live():
    from . import pool
    with cp.guard_lock(wait=True, pool=guard_descriptor(pool.claude_seat_pool())) as acquired:
        if not acquired:
            raise ValueError('Claude CLI is busy.')
        return refresh()


def add_parser(sub):
    root = sub.add_parser('sienna', aliases=['claude'], help='Claude CLI account switcher, powered by cswap',
                         description='Switch Claude Code CLI accounts using upstream cswap. No Claude desktop, proxy or Codex lane.')
    children = root.add_subparsers(dest='cswap_action', required=True)
    for name in ('install', 'status', 'add', 'switch', 'enable', 'disable', 'label', 'detect-plans', 'reserve', 'auto', 'strategy', 'threshold', 'retire-proxy', 'remove'):
        item = children.add_parser(name)
        item.set_defaults(fn=command)
        if name in ('retire-proxy', 'remove'):
            item.add_argument('--yes', action='store_true')
        if name == 'install':
            item.add_argument('--dry-run', action='store_true')
        if name == 'status':
            item.add_argument('--json', action='store_true')
            item.add_argument('--live', action='store_true')
        if name in ('switch', 'enable', 'disable', 'label', 'reserve', 'remove'):
            item.add_argument('account', type=account_number)
        if name == 'reserve':
            item.add_argument('mode', choices=('on', 'off'), nargs='?', default='on')
        if name == 'label':
            item.add_argument('label')
        if name == 'auto':
            item.add_argument('mode', choices=('on', 'off'))
        if name == 'strategy':
            item.add_argument('strategy', choices=('best', 'consume-first'))
        if name == 'threshold':
            item.add_argument('value', type=float, choices=None)


def account_number(value):
    if not value.isdigit() or int(value) < 1:
        raise argparse.ArgumentTypeError('Use the numeric cswap account slot.')
    return str(int(value))
