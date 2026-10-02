"""Claude cross-account conversation selftest.

Runtime paths and behavior are unchanged by the code extraction.
"""
from . import cp
from . import pool as sienna_pool

CLAUDE_SELFTEST_N = 269  # the answer to turn 1's puzzle: n mod 7 = 3, n mod 11 = 5, n mod 13 = 9

CLAUDE_SELFTEST_PROMPTS = (
    'Without using any tool, think it through carefully, then find the smallest positive integer n with n mod 7 = 3, '
    'n mod 11 = 5 and n mod 13 = 9. Reply with N=<n> and one sentence on how you found it.',
    '/compact',
    'Without using any tool: add 9 to the N you found earlier in this conversation. Reply only with ANSWER=<number>.')


def claude_selftest_env():
    """The environment for the selftest's claude -p turns: yours, with what the claude-pool launcher adds
    (ANTHROPIC_BASE_URL for these processes only, and CLAUDE_POOL_ENV where you have not set it). Nothing global."""
    env = dict(cp.os.environ, ANTHROPIC_BASE_URL=sienna_pool.claude_pool_url())
    env.pop('CLAUDEPOOL', None)
    for k, v in sienna_pool.CLAUDE_POOL_ENV:
        if not env.get(k):  # the launcher's := also defaults an empty value
            env[k] = v
    return env


def claude_turn_facts(stdout):
    """What one `claude -p --output-format stream-json --verbose` turn said (pure): {session_id, answer, errors,
    thinking}. thinking counts the thinking blocks in its assistant messages; errors has the result's error text."""
    out = {'session_id': None, 'answer': '', 'errors': [], 'thinking': 0}
    result = None
    for line in (stdout or '').splitlines():
        try:
            ev = cp.json.loads(line)
        except ValueError:
            continue
        if not isinstance(ev, dict):
            continue
        out['session_id'] = ev.get('session_id') or out['session_id']
        if ev.get('type') == 'assistant':
            content = (ev.get('message') or {}).get('content') if isinstance(ev.get('message'), dict) else None
            out['thinking'] += sum(1 for c in content or [] if isinstance(c, dict)
                                   and c.get('type') in ('thinking', 'redacted_thinking'))
        elif ev.get('type') == 'result':
            result = ev
    if result is None:
        out['errors'].append('no result from claude -p')
    else:
        out['answer'] = str(result.get('result') or '')
        if result.get('is_error') or (result.get('subtype') not in (None, 'success')):
            out['errors'].append(cp.short(out['answer'] or str(result.get('subtype')), 200))
    return out


def _run_claude(claude, args, env, cwd, timeout=600):
    """One claude -p turn for the selftest; its facts (claude_turn_facts), with a failed exit counted as an error."""
    cmd = [str(claude), '-p', '--output-format', 'stream-json', '--verbose'] + args
    try:
        r = cp.subprocess.run(cmd, cwd=cwd, env=env, capture_output=True, text=True, timeout=timeout,
                           stdin=cp.subprocess.DEVNULL)
    except (OSError, cp.subprocess.SubprocessError) as e:
        return {'session_id': None, 'answer': '', 'errors': [cp.short(str(e), 200)], 'thinking': 0}
    facts = claude_turn_facts(r.stdout)
    if r.returncode != 0 and not facts['errors']:
        facts['errors'].append(f'claude exited {r.returncode}: {cp.short(r.stderr, 160)}')
    return facts


def claude_selftest_verdict(turns, bad_log, compact):
    """(verdict, why) for a Claude selftest (pure). turns: [(title, facts)] in order, the last one on account B;
    bad_log: signature lines from the Claude pool's log. PASS: every turn worked, no signature error, the first turn
    thought and the turn on B used its answer. INCONCLUSIVE: all worked but no thinking was seen."""
    want = len(turns) == (3 if compact else 2)
    if not want or any(f['errors'] for _, f in turns) or bad_log:
        return 'FAIL', 'see above'
    m = cp.re.search(r'ANSWER\s*=\s*(-?\d+)', turns[-1][1]['answer'])
    n = cp.re.search(r'N\s*=\s*(-?\d+)', turns[0][1]['answer'])
    expected = int(n.group(1)) + 9 if n else CLAUDE_SELFTEST_N + 9
    if not m or int(m.group(1)) != expected:
        return 'FAIL', f'the turn on the second account did not carry the first answer (wanted ANSWER={expected})'
    if not turns[0][1]['thinking']:
        return 'INCONCLUSIVE', 'the first turn showed no thinking blocks, so no signature was carried across'
    return 'PASS', 'thinking' + (' and a /compact' if compact else '') + ' survived the account move'


def cmd_claude_selftest(args):
    """Move one throwaway Claude Code conversation from account A to account B through the Claude pool and check it
    survives (PLAN §3): a turn with thinking on A, an optional /compact, then A out of rotation and the next turn on
    B. Spends a few requests on both accounts; runs only when you say so."""
    pool = cp.seat_pool('claude')
    if not sienna_pool.claude_installed():
        cp.sys.exit('subpool claude selftest: the Claude pool is not installed. subpool claude install sets it up.')
    if not cp.port_open(pool.port):
        cp.sys.exit(f'subpool claude selftest: the Claude pool does not answer on :{pool.port} (subpool claude status)')
    claude = sienna_pool.real_claude()
    if claude is None:
        cp.sys.exit('subpool claude selftest: Claude Code (claude) is not in ~/.local/bin or on PATH')
    seats = [s for s in cp.load_seats(pool) if s['provider'] == 'claude']
    a, b = cp.match_seat(seats, args.from_seat), cp.match_seat(seats, args.to_seat)
    if a['name'] == b['name']:
        cp.sys.exit('pick two different accounts')
    for s in (a, b):
        if s['disabled'] or s['unavailable']:
            cp.sys.exit(f'{s["label"]} is {"disabled" if s["disabled"] else "unavailable"}; pick accounts that are ready')
    print(f'selftest: a throwaway claude -p conversation, first on {a["label"]}, then on {b["label"]}'
          + (' with a /compact before the move' if args.compact else '') + '. The other accounts are out of rotation '
          'for a few minutes, and it spends a few requests from both accounts.')
    if not args.yes:
        if input('Run it now? [y/N] ').strip().lower() not in ('y', 'yes'):
            cp.sys.exit('cancelled')
    journal = cp.selftest_journal(pool)
    turns = []
    with cp.guard_lock(pool=pool):
        cp.recover_selftest({}, pool)
        seats = [s for s in cp.load_seats(pool) if s['provider'] == 'claude']
        movable = [s for s in seats if not s['disabled']]
        toggled = {}
        log_start = pool.log.stat().st_size if pool.log.exists() else 0
        work = cp.tempfile.mkdtemp(prefix='subpool-claude-selftest-')
        env = claude_selftest_env()
        extra = ['--model', args.model] if args.model else []

        def only(seat):
            for s in movable:
                if s['name'] not in toggled:
                    toggled[s['name']] = s['disabled']
                    cp.write_json(journal, {'pid': cp.os.getpid(), 'toggled': toggled})
                cp.set_disabled(s['name'], s['name'] != seat['name'], pool)

        for sig in (cp.signal.SIGHUP, cp.signal.SIGTERM):
            cp.signal.signal(sig, cp._raise_exit)
        try:
            only(a)
            t1 = _run_claude(claude, extra + [CLAUDE_SELFTEST_PROMPTS[0]], env, work)
            turns.append((f'turn 1 on {a["label"]}', t1))
            if t1['errors'] or not t1['session_id']:
                raise RuntimeError('turn 1 failed')
            if args.compact:
                t2 = _run_claude(claude, extra + ['--resume', t1['session_id'], CLAUDE_SELFTEST_PROMPTS[1]], env, work)
                turns.append((f'turn 2 on {a["label"]} (/compact)', t2))
                if t2['errors']:
                    raise RuntimeError('turn 2 failed')
            only(b)
            t3 = _run_claude(claude, extra + ['--resume', t1['session_id'], CLAUDE_SELFTEST_PROMPTS[2]], env, work)
            turns.append((f'turn {3 if args.compact else 2} on {b["label"]} (the other account)', t3))
        except RuntimeError:
            pass
        finally:
            for sig in (cp.signal.SIGHUP, cp.signal.SIGTERM, cp.signal.SIGINT):
                cp.signal.signal(sig, cp.signal.SIG_IGN)
            restored = True
            for name, was in toggled.items():
                try:
                    cp.set_disabled(name, was, pool)
                except (cp.ApiError, cp.PoolDown):
                    restored = False
            if restored:
                with cp.contextlib.suppress(OSError):
                    journal.unlink()
            else:
                print('WARNING: could not restore every account; the guard will retry from the journal.')
            cp.shutil.rmtree(work, ignore_errors=True)
    log_tail = ''
    if pool.log.exists():
        with pool.log.open('rb') as f:
            f.seek(log_start)
            log_tail = f.read().decode(errors='replace')
    bad = [ln for ln in log_tail.splitlines() if any(p in ln for p in sienna_pool.CLAUDE_SIGNATURE_PATTERNS)]
    verdict, why = claude_selftest_verdict(turns, bad, args.compact)
    for title, t in turns:
        print(f'  [{"ok " if not t["errors"] else "FAIL"}] {title}: {t["answer"][-80:]!r}')
        for e in t['errors']:
            print(f'         {e}')
    for line in bad[:5]:
        print(f'  pool log: {line[:200]}')
    print(f'  thinking blocks in turn 1: {turns[0][1]["thinking"] if turns else 0}')
    print(f'RESULT: {verdict}: {why}')
    record = cp.read_json(cp.STATE / 'claude-selftests.json', [])
    record = record if isinstance(record, list) else []
    record.append({'at': cp.now_utc().isoformat(), 'from': a['label'], 'to': b['label'], 'compact': args.compact,
                   'model': args.model, 'result': verdict, 'thinking': turns[0][1]['thinking'] if turns else 0})
    cp.write_json(cp.STATE / 'claude-selftests.json', record)
    cp.sys.exit({'PASS': 0, 'FAIL': 1, 'INCONCLUSIVE': 2}[verdict])
