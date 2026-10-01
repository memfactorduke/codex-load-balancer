"""Read-only engine lane provider and commands. Runtime names remain unchanged."""
from . import cp, pool, guard

ENGINE_MEMBER_DEFAULTS = {'context_1m': False, 'ultracode': False, 'max_turns': 60, 'turn_timeout': 1200,
                           'pool': 'default'}


ENGINE_ROLE_INSTRUCTIONS = '''\
You are a subagent running on a third-party model reached through codexpool. Do only the assigned task.
This is a read-only Claude Code lane. Use Read, Grep and Glob in the trusted workspace only.
You cannot edit files, run commands, use the network or spawn subagents. Nobody can approve actions mid-turn.
When a change is needed, return the exact diff or command as text for the parent or user to apply.
Finish with a short report: findings, proposed changes as diffs or commands, anything uncertain.
'''


ENGINE_TEST_TASK = ('Read a.txt and README.md with Read and list the workspace with Glob. '
                    'Report the exact CHECK value from README.md and the contents of a.txt. '
                    'Then propose the diff changing alpha to beta as text only. Do not change any file.')


def engine_settings():
    """The v0 inline --settings object. --restricted ignores settings files; the engine must pass this JSON."""
    return {'disableAllHooks': True, 'disableSkillShellExecution': True, 'permissions': {'deny': [
        'Bash', 'Edit', 'Write', 'NotebookEdit', 'WebFetch', 'WebSearch', 'Agent', 'Skill',
        'Read(~/.codexpool/**)', 'Read(~/.codex/**)', 'Read(~/.claude/**)', 'Read(~/.claude.json)',
        'Read(~/.ssh/**)', 'Read(~/.aws/**)', 'Read(~/.config/**)', 'Read(~/Library/**)',
        'Read(~/.gnupg/**)', 'Read(~/.grok/**)', 'Read(~/.azure/**)', 'Read(~/.kube/**)',
        'Read(~/.docker/**)', 'Read(~/.local/share/keyrings/**)']}}


def engine_profile(name):
    if not isinstance(name, str) or not cp.LANE_NAME_OK.fullmatch(name):
        raise cp.LaneError('invalid engine lane name')
    return cp.LANES_DIR / f'{name}-home'


def engine_environment(name, login_path):
    """An allowlist, not an inherited environment. The bearer is a placeholder, never a copied login."""
    profile = engine_profile(name)
    return {'PATH': login_path, 'HOME': str(cp.HOME), 'USER': cp.getpass.getuser(),
            'LANG': cp.os.environ.get('LANG') or 'en_US.UTF-8', 'SHELL': cp.os.environ.get('SHELL') or '/bin/zsh',
            'TMPDIR': str(profile / 'tmp'), 'CLAUDE_CONFIG_DIR': str(profile), 'ANTHROPIC_AUTH_TOKEN': 'codexpool',
            'CLAUDEPOOL': 'required', 'DISABLE_AUTOUPDATER': '1', 'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1',
            'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB': '1'}


def engine_login_path():
    """Capture the login shell's PATH at apply, without forwarding credentials or printing shell output."""
    shell = cp.os.environ.get('SHELL') or '/bin/zsh'
    env = {'HOME': str(cp.HOME), 'USER': cp.getpass.getuser(), 'SHELL': shell,
           'LANG': cp.os.environ.get('LANG') or 'en_US.UTF-8', 'PATH': '/usr/bin:/bin:/usr/sbin:/sbin'}
    try:
        r = cp.subprocess.run([shell, '-lc', 'printf "\\0%s\\0" "$PATH"'], env=env, capture_output=True, timeout=10)
        parts = r.stdout.split(b'\0')
        path = parts[-2].decode() if len(parts) >= 3 else ''
        if r.returncode == 0 and cp.plain_text(path) and all(p.startswith('/') for p in path.split(cp.os.pathsep)):
            return path
    except (OSError, ValueError, cp.subprocess.TimeoutExpired):
        pass
    raise cp.LaneError('cannot capture the login shell PATH for sienna; check SHELL and its login profile')


def engine_directories(plan):
    paths = []
    for lane in plan:
        if any(m['kind'] == 'engine' for m in lane['members']):
            paths += [engine_profile(lane['name']), engine_profile(lane['name']) / 'tmp']
    return paths + ([cp.STATE / 'engine-sessions', cp.STATE / 'engine-turns'] if paths else [])


def check_engine_directory(path):
    """Refuse symlinks before creating or chmod'ing lane state; never follow one into a user's profile."""
    for part in (path,) + tuple(path.parents):
        if part.is_symlink() or (part.exists() and not part.is_dir()):
            raise cp.LaneError(f'{cp.tilde(part)} is not a plain directory; move it away before lane apply')
        if part == cp.ROOT:
            break


def make_engine_directory(path):
    check_engine_directory(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    cp.os.chmod(path, 0o700)


def engine_acceptance(entry):
    """Only retain complete, recorded expectations. A normal apply never accepts an engine version."""
    versions, expect = entry.get('accepted_versions'), entry.get('init_expect')
    if not isinstance(versions, list) or not isinstance(expect, dict) or not versions:
        return [], {}
    if any(not isinstance(v, str) or not cp.re.fullmatch(r'\d+\.\d+\.\d+', v) for v in versions):
        return [], {}
    for version in versions:
        item = expect.get(version)
        if not isinstance(item, dict) or item.get('permissionMode') != 'default' or item.get('apiKeySource') != 'none' \
                or item.get('mcp_servers') != [] or item.get('plugins') != [] \
                or not isinstance(item.get('tools'), list) or sorted(item['tools'], key=str) != ['Glob', 'Grep', 'Read']:
            return [], {}
    return versions, {v: expect[v] for v in versions}


def engine_launcher_ready():
    if not any(a.id == 'sienna' for a in cp.ADDONS):
        return False
    return cp.script_is_ours(pool.CLAUDE_LAUNCHER, pool.CLAUDE_LAUNCHER_MARK) and cp.os.access(pool.CLAUDE_LAUNCHER, cp.os.X_OK) \
        and cp.read_text(pool.CLAUDE_LAUNCHER) == pool.launcher_text()


def recorded_engines():
    config = cp.read_json(cp.BRIDGE_CONFIG, {})
    engines = config.get('engines') if isinstance(config, dict) else None
    return engines if isinstance(engines, dict) else {}


def engine_state(name=None):
    """Quick, read-only readiness; never run the engine or inspect a login. Unknown versions fail closed."""
    if not engine_launcher_ready():
        return 'no launcher'
    real = pool.real_claude()
    if real is None or not real.is_file() or not cp.os.access(real, cp.os.X_OK):
        return 'no engine'
    engines = recorded_engines()
    entries = [(name, engines.get(name))] if name is not None else list(engines.items())
    version = guard.claude_code_version()
    if not entries:
        return 'untested engine'
    states = []
    for lane, entry in entries:
        if not isinstance(entry, dict) or version not in engine_acceptance(entry)[0]:
            states.append('untested engine')
            continue
        try:
            profile = engine_profile(lane)
            check_engine_directory(profile)
            good = profile.is_dir() and profile.stat().st_mode & 0o777 == 0o700
        except (cp.LaneError, OSError):
            good = False
        if not good:
            states.append('no profile')
            continue
        states.append('engine ok')
    state = 'engine ok' if 'engine ok' in states else states[0]
    if state == 'engine ok' and not cp.port_open(pool.CLAUDE_PORT):
        return 'pool down'
    return state


def engine_state_hint(state):
    return {'no launcher': 'install the current pool launcher (codexpool claude install)',
            'no engine': 'install Claude Code first', 'pool down': 'codexpool claude status ; codexpool doctor',
            'no profile': 'codexpool lane apply',
            'untested engine': 'codexpool lane apply --accept-engine'
            }.get(state, 'codexpool doctor')


def engine_managed_settings(system_dir=None, preferences_dir=None):
    """Names only, never contents: the macOS managed sources still apply under --restricted."""
    system_dir = system_dir or cp.pathlib.Path('/Library/Application Support/ClaudeCode')
    preferences_dir = preferences_dir or cp.pathlib.Path('/Library/Managed Preferences')
    domain = 'com.anthropic.claudecode.plist'
    paths = [system_dir / 'managed-settings.json', system_dir / 'managed-mcp.json', preferences_dir / cp.getpass.getuser() / domain,
             preferences_dir / domain]
    paths += sorted((system_dir / 'managed-settings.d').glob('*.json'))
    return [p for p in paths if p.is_file()]


def doctor_engine(check, lane, prep):
    name = lane['name']
    check(engine_launcher_ready(), f'{name}: pool-required launcher present and current',
          engine_state_hint('no launcher'))
    real = pool.real_claude()
    check(real is not None and real.is_file() and cp.os.access(real, cp.os.X_OK), f'{name}: Claude Code resolves',
          engine_state_hint('no engine'))
    entry = recorded_engines().get(name)
    entry = entry if isinstance(entry, dict) else {}
    version = guard.claude_code_version()
    check(version is not None and version in engine_acceptance(entry)[0],
          f'{name}: engine version {version or "unknown"} ' +
          ('accepted' if version is not None and version in engine_acceptance(entry)[0] else 'untested'),
          engine_state_hint('untested engine'))
    for path in engine_directories([lane]):
        try:
            check_engine_directory(path)
            good = path.is_dir() and path.stat().st_mode & 0o777 == 0o700 and cp.os.access(path, cp.os.W_OK)
        except (cp.LaneError, OSError):
            good = False
        check(good, f'{name}: {cp.tilde(path)} writable, mode 700', 'codexpool lane apply')
    if prep:
        wanted = cp.json.loads(prep['bridge_new'])['engines'][name]
        env = entry.get('env') if isinstance(entry.get('env'), dict) else {}
        check(env.get('PATH') == wanted['env']['PATH'], f'{name}: recorded login PATH matches generated configuration',
              'codexpool lane apply', warn=True)
    code = cp._probe_status(pool.CLAUDE_PORT, {'User-Agent': 'claude-cli/codexpool-launcher', 'X-App': 'cli'})
    check(code == 200, f'{name}: Claude pool answers on 127.0.0.1:{pool.CLAUDE_PORT}', engine_state_hint('pool down'))
    managed = engine_managed_settings()
    check(not managed, f'{name}: managed settings still apply under --restricted: ' + ', '.join(cp.tilde(p) for p in managed)
          if managed else f'{name}: no file-based managed settings found',
          'review managed settings and MCP configuration before enabling the lane')
    cpa = cp.read_json(cp.STATE / 'engine-cpa-check.json', {})
    config = cp.read_json(cp.BRIDGE_CONFIG, {})
    known = config.get('cpa_known_normalisations', [])
    try:
        build = cp.hashlib.sha256(cp.cpa_binary(pool.CLAUDE_CURRENT).read_bytes()).hexdigest()
    except OSError:
        build = None
    good = bool(build and cpa.get('ok') and cpa.get('cpa_sha256') == build and cpa.get('known_normalisations', []) == known
                and cpa.get('claude_version') == version)
    check(good, f'{name}: captured CPA passthrough ' + ('verified for this build and engine' if good else 'not verified or stale'),
          'codexpool doctor --cpa-passthrough /path/to/sanitized-E4-captures')
    if cpa.get('unexpected_paths'):
        check(False, 'pool rewrites Claude requests: ' + ', '.join(cpa['unexpected_paths']),
              'review the differences before accepting any exact path in bridge.json cpa_known_normalisations')
    if known:
        check(False, f'{name}: owner-accepted CPA normalisations: ' + ', '.join(known),
              'review these exact exceptions when refreshing E4 captures', warn=True)


def run_cpa_passthrough(fixtures):
    """Explicit offline compatibility check in a child with fabricated credentials; never the live pool."""
    runner = cp.pathlib.Path(__file__).with_name('cpa_check.py')
    binary = cp.cpa_binary(pool.CLAUDE_CURRENT)
    if not runner.is_file() or not binary.is_file():
        cp.sys.exit('codexpool: CPA capture checker or installed Claude build is missing; update the install first')
    known = cp.read_json(cp.BRIDGE_CONFIG, {}).get('cpa_known_normalisations', [])
    if not isinstance(known, list) or any(not isinstance(p, str) or not p.startswith('$.') for p in known):
        cp.sys.exit('codexpool: cpa_known_normalisations must contain exact JSON paths')
    with cp.tempfile.TemporaryDirectory(prefix='codexpool-cpa-check-') as temp:
        result = cp.pathlib.Path(temp) / 'result.json'
        env = dict(PATH=cp.os.environ.get('PATH', '/usr/bin:/bin'), HOME=temp, TMPDIR=temp,
                   CODEXPOOL_CPA_BINARY=str(binary.resolve()), CODEXPOOL_CPA_FIXTURES=str(cp.pathlib.Path(fixtures).resolve()),
                   CODEXPOOL_CPA_RESULT=str(result), CODEXPOOL_CPA_KNOWN_NORMALISATIONS=cp.json.dumps(known))
        try:
            child = cp.subprocess.run([cp.sys.executable, str(runner), 'Capture'], env=env, cwd=temp,
                                   stdout=cp.subprocess.DEVNULL, stderr=cp.subprocess.DEVNULL, timeout=300)
            report = cp.read_json(result, {})
        except cp.subprocess.TimeoutExpired:
            child, report = None, {}
        report['ok'] = bool(child and child.returncode == 0 and report.get('ok'))
        report['known_normalisations'] = known
        report['checked_at'] = cp.now_utc().isoformat()
        cp.write_json(cp.STATE / 'engine-cpa-check.json', report)


def engine_bridge_module():
    """Load the local implementation, not its server entrypoint or any runtime secret."""
    path = cp.CODE_DIR / 'lanes' / 'bridge.py'
    spec = cp.importlib.util.spec_from_file_location('codexpool_engine_commands', path)
    module = cp.importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.load_extension_module(cp.pathlib.Path(__file__).with_name('bridge_engine.py'))


def accept_lane_engines(prep, dry):
    config = cp.json.loads(prep['bridge_new'] or '{}')
    engines = config.get('engines', {})
    if not engines:
        raise RuntimeError('--accept-engine needs at least one configured engine member')
    bridge = engine_bridge_module()
    for name, engine in engines.items():
        # Refuse missing executables even on a dry run. Never substitute a model or mark a synthetic success.
        try:
            version = bridge.installed_engine_version(engine)
            if not version or not engine_launcher_ready():
                raise RuntimeError('a real installed Claude Code engine and current launcher are required')
        except bridge.BridgeError as error:
            raise RuntimeError(error.message)
        if dry:
            print(f'  would probe {name} ({version}), spending one Claude pool turn; no probe in a dry run')
            continue
        for path in prep.get('provider_prep', {}).get('sienna', {}).get('directories', []):
            make_engine_directory(path)
        route = next(r for r in config['models'].values() if r.get('engine') == name)
        try:
            version, expectation = bridge.accept_engine(engine, route, cp.STATE)
        except bridge.BridgeError as error:
            raise RuntimeError(f'{name}: accept probe refused: {error.message}')
        engine['accepted_versions'] = list(dict.fromkeys(engine.get('accepted_versions', []) + [version]))
        engine.setdefault('init_expect', {})[version] = expectation
        print(f'  {name}: accepted engine {version} after a successful isolated probe')
    if not dry:
        prep['bridge_new'] = cp.json.dumps(config, indent=2) + '\n'


def cmd_claude_lane_resume(args):
    """The temporary spelling of `sienna resume`, until the terminal launcher is renamed."""
    bridge = engine_bridge_module()
    try:
        session = str(bridge.uuid.UUID(args.session))
    except ValueError:
        cp.sys.exit('codexpool: resume needs a session UUID')
    found = [(p, cp.read_json(p, {})) for p in (cp.STATE / 'engine-sessions').glob('*.json')]
    found = [(p, r) for p, r in found if r.get('uuid') == session]
    if len(found) != 1:
        cp.sys.exit('codexpool: no unique lane session matches that UUID')
    path, record = found[0]
    try:
        # Re-read under the lock: a queued Codex turn may have restarted the session.
        with bridge.ThreadLock(path.with_suffix('.lock'), timeout=30) as lock:
            current = cp.read_json(path, {})
            if current.get('uuid') != session:
                cp.sys.exit('codexpool: the lane session changed while waiting for its lock')
            key = str(bridge.uuid.UUID(current['thread_key']))
            if path.stem != key:
                cp.sys.exit('codexpool: invalid lane session record')
            engine = recorded_engines().get(current['member'].split(':', 1)[0])
            if not engine:
                cp.sys.exit('codexpool: the session engine is no longer configured')
            version, env = bridge.engine_preflight(engine)
            bridge.cpa_compatibility(engine, version, cp.STATE, cp.read_json(cp.BRIDGE_CONFIG, {}).get('cpa_known_normalisations', []))
            cwd = bridge.cwd_ok(current['cwd'], home=env['HOME'], profiles=[engine['profile']])
            child = cp.subprocess.Popen([engine['launcher'], '--resume', session], cwd=cwd, env=env, pass_fds=(lock.fd,))
            try:
                code = child.wait()
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=5)
                    except cp.subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
            if code:
                cp.sys.exit(code)
    except bridge.BridgeError as error:
        cp.sys.exit('codexpool: ' + error.message)



def engine_test(codex, role, want_model, compaction, seed):
    """One test: a throwaway Codex thread spawns the role once. (ok, one-line reason, seconds)."""
    work = cp.pathlib.Path(cp.tempfile.mkdtemp(prefix='codexpool-lanetest-', dir=cp.pathlib.Path.cwd()))
    try:
        bridge = engine_bridge_module()
        try:
            bridge.cwd_ok(str(work), home=cp.HOME)
        except bridge.BridgeError:
            return (False, 'run the engine test from inside a Codex-trusted git repository', 0.0)
        markers = None
        if compaction:
            rng, markers = (cp.random.Random(seed), [])
            for i in range(1, 9):
                lines = ['%04d ' % n + 'lorem ipsum dolor sit amet consectetur adipiscing elit sed do' for n in range(450)]
                lines[225] = f'MARKER-{i}-{rng.randrange(10 ** 6):06d}'
                markers.append(lines[225])
                (work / f'big{i}.txt').write_text('\n'.join(lines) + '\n')
        else:
            (work / 'a.txt').write_text('alpha\n')
        check = cp.secrets.token_hex(12)
        (work / 'README.md').write_text('CHECK: ' + check + '\n')
        before = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
        task = 'Read big1.txt through big8.txt with Read, one at a time. After each, state its MARKER line. Finish by listing all 8 markers in order. Do not change any file.' if compaction else ENGINE_TEST_TASK
        argv = [codex, 'exec', '--json', '--skip-git-repo-check', '-s', 'workspace-write', '-C', str(work), '-c', 'notify=[]'] + (['-c', 'model_auto_compact_token_limit=70000'] if compaction else []) + [cp.lane_test_prompt(role, task)]
        start = cp.time.time()
        try:
            r = cp._run_codex(argv, timeout=1500 if compaction else 600)
        except cp.subprocess.TimeoutExpired:
            return (False, 'codex exec timed out', cp.time.time() - start)
        seconds = cp.time.time() - start

        if r['code'] != 0:
            return (False, f"codex exec exit {r['code']}: {cp.short(' '.join(r['errors']), 100)}", seconds)
        after = {p.name: p.read_bytes() for p in work.iterdir() if p.is_file()}
        if after != before or any((p.is_dir() for p in work.iterdir())):
            return (False, 'the read-only engine test changed the workspace', seconds)
        answer = r.get('answer', '')
        expected = markers if compaction else [check, 'alpha', 'beta']
        if any((value not in answer for value in expected)):
            return (False, 'the engine answer omitted file contents or the proposed diff', seconds)
        if markers and [answer.find(value) for value in markers] != sorted((answer.find(value) for value in markers)):
            return (False, 'the engine answer reordered the markers', seconds)
        child = cp._child_rollout(r['thread_id'], start - 1) if r['thread_id'] else None
        if not child:
            return (False, 'no subagent rollout found (did the spawn fail?)', seconds)
        facts = cp._child_facts(child)
        if facts['models'] != {want_model}:
            return (False, f"the subagent ran on {', '.join(sorted(facts['models'])) or 'no model'}, not {want_model}", seconds)
        if facts['parse_failures']:
            return (False, f"{facts['parse_failures']} tool call(s) failed to parse (run codexpool lane apply)", seconds)
        if compaction and (not facts['compactions']):
            return (False, 'the subagent never compacted', seconds)
        records = [cp.read_json(p, {}) for p in (cp.STATE / 'engine-sessions').glob('*.json')]
        records = [r for r in records if r.get('cwd') == str(work)]
        engines = recorded_engines()
        if not any((any(cp.pathlib.Path(engines.get(r.get('member', '').split(':')[0], {}).get('profile', work)).rglob(r.get('uuid', 'missing') + '.jsonl')) for r in records)):
            return (False, 'no isolated lane transcript found', seconds)
        return (True, f"compacted {facts['compactions']}x, all 8 markers kept" if compaction else 'read-only answer and lane transcript verified', seconds)
    finally:
        cp.shutil.rmtree(work, ignore_errors=True)


class Provider:
    kind, title, needs = 'engine', 'Claude', 'engine'
    member_keys = tuple(ENGINE_MEMBER_DEFAULTS)
    lane_keys = ('max_turns', 'turn_timeout')

    def member_id(self, model):
        return model[len('claude-'):] if model.startswith('claude-') else model

    def cpa_headers(self, member):
        return {'x-codex-turn-metadata': '$x-codex-turn-metadata',
                'x-codex-parent-thread-id': '$x-codex-parent-thread-id'}

    def validate_member(self, member, spec, at):
        fields, errors = {}, []
        for k, default in ENGINE_MEMBER_DEFAULTS.items():
            value = member.get(k, spec.get(k, default) if k in self.lane_keys else default)
            if k in ('context_1m', 'ultracode') and not isinstance(value, bool):
                errors.append(f'{at}: "{k}" must be true or false')
            elif k == 'ultracode' and value:
                errors.append(f'{at}: "ultracode" is reserved; v0 is read-only and requires false')
            elif k in self.lane_keys and not cp.positive_int(value):
                errors.append(f'{at}: "{k}" must be a positive whole number' + (' of seconds' if k == 'turn_timeout' else ''))
            elif k == 'pool' and value != 'default':
                errors.append(f'{at}: unknown pool {cp.json.dumps(value)}; only "default" is available (pool selection is reserved)')
            fields[k] = value
        fields['context'] = member.get('context') or (1000000 if fields['context_1m'] is True else 200000)
        return fields, errors

    def validate_lane(self, spec, members, at):
        return [f'{at}: "{k}" must be a positive whole number and needs a sienna member' for k in self.lane_keys
                if k in spec and (not cp.positive_int(spec[k]) or not any(m['provider'] == 'sienna' for m in members))]

    def member_display(self, member):
        return f'Claude: {member["name"]}'

    def lane_display(self, lane):
        return 'Claude'

    def role_instructions(self, lane):
        return ENGINE_ROLE_INSTRUCTIONS

    def agents_sentence(self, lane):
        return (' Read-only: answers, plans and reviews with Read, Grep and Glob. It cannot edit, run commands '
                'or use the network; ask for proposed diffs or commands as text for you to apply.')

    def bridge_config(self, plan, previous, prep):
        login_path = prep.get('sienna', {}).get('login_path') if isinstance(prep, dict) else prep
        engines, models = {}, {}
        old_engines = previous.get('engines') if isinstance(previous.get('engines'), dict) else {}
        for lane in plan:
            for m in lane['members']:
                if m['provider'] != 'sienna':
                    continue
                name = lane['name']
                engine = {'launcher': str(pool.CLAUDE_LAUNCHER),
                          'launcher_sha256': cp.hashlib.sha256(pool.launcher_text().encode()).hexdigest(),
                          'profile': str(engine_profile(name)),
                          'env': engine_environment(name, login_path if login_path is not None else cp.os.environ.get('PATH', '')),
                          'settings': engine_settings(),
                          'pool': {'port': pool.CLAUDE_PORT, 'route_file': str(pool.CLAUDE_ROUTE_FILE)}}
                old = old_engines.get(name)
                accepted, expect = [], {}
                if isinstance(old, dict) and all(old.get(k) == v for k, v in engine.items()):
                    accepted, expect = engine_acceptance(old)
                engines[name] = dict(engine, accepted_versions=accepted, init_expect=expect)
                models[m['bridge_model']] = dict(extension='sienna', engine=name, upstream_model=m['model'],
                                                 effort=lane['effort'], **{k: m[k] for k in ENGINE_MEMBER_DEFAULTS})
        top = {'engines': engines}
        if 'cpa_known_normalisations' in previous:
            known = previous['cpa_known_normalisations']
            if not isinstance(known, list) or any(not isinstance(p, str) or not p.startswith('$.') for p in known):
                raise cp.LaneError('bridge.json cpa_known_normalisations must be a list of exact JSON paths')
            top['cpa_known_normalisations'] = known
        return top, models

    def prepare(self, plan, capture_path):
        directories = engine_directories(plan)
        for path in directories:
            check_engine_directory(path)
        login_path = engine_login_path() if directories and capture_path else None
        if directories and not capture_path:
            paths = {e.get('env', {}).get('PATH') for e in recorded_engines().values() if isinstance(e, dict)}
            login_path = next(iter(paths)) if len(paths) == 1 and None not in paths else ''
        return {'sienna': {'directories': directories, 'login_path': login_path}}

    def apply(self, st, prep, dry):
        for path in prep.get('provider_prep', {}).get('sienna', {}).get('directories', []):
            if path.is_dir() and path.stat().st_mode & 0o777 == 0o700:
                st.ok(f'{cp.tilde(path)} (mode 700)')
            else:
                st.change(f'create or chmod 700 {cp.tilde(path)} (isolated lane state)', make_engine_directory, path)

    def state(self, lane, member, health):
        state = engine_state(lane['name'])
        if state != 'engine ok':
            return '✕', state, engine_state_hint(state)
        if health is None:
            return '✕', 'bridge down', ''
        if f'lane-{lane["name"]}-{member["id"]}' not in health:
            return '✕', 'not in bridge', ''
        return '●', 'engine ok', ''

    def ready(self):
        state = engine_state()
        ready = state == 'engine ok'
        return ready, state if ready else state + ': ' + engine_state_hint(state)

    def models(self, base_url, key_name):
        raise cp.LaneError('sienna uses the installed Claude Code model ids; enter a model id and display name '
                           '(no provider key or HTTP model catalog)')

    def doctor(self, check, lane, prep):
        doctor_engine(check, lane, prep)

    def test(self, *args):
        return engine_test(*args)

    def add_apply_parser(self, parser):
        class Accept(cp.argparse.Action):
            def __call__(self, parser, namespace, values, option_string=None):
                namespace.accept_engine = True
                namespace.before_apply = accept_lane_engines
        parser.add_argument('--accept-engine', action=Accept, nargs=0, default=False,
                            help='probe and accept the installed engine (one Claude pool turn; never probes on --dry-run)')


def cmd_accept_engine(args):
    cp.lane_apply(cp.load_lanes(), args.dry_run, before_apply=accept_lane_engines)


def cmd_cpa_check(args):
    run_cpa_passthrough(args.fixtures)
    if not cp.read_json(cp.STATE / 'engine-cpa-check.json', {}).get('ok'):
        cp.sys.exit('codexpool: CPA compatibility check failed; see codexpool doctor')


def add_parser(sub):
    x = sub.add_parser('resume', aliases=['lane-resume'], help='resume an isolated lane session under its thread lock')
    x.add_argument('session', metavar='UUID')
    x.set_defaults(claude_fn=cmd_claude_lane_resume)
    x = sub.add_parser('accept-engine', help='probe and accept the configured engine lanes')
    x.add_argument('--dry-run', action='store_true')
    x.set_defaults(claude_fn=cmd_accept_engine)
    x = sub.add_parser('cpa-check', help='check sanitized captures against a separate CPA process')
    x.add_argument('fixtures', metavar='FIXTURES')
    x.set_defaults(claude_fn=cmd_cpa_check)


def add_doctor_parser(parser):
    parser.add_argument('--cpa-passthrough', metavar='FIXTURES', help='check sanitized E4 captures in a separate CPA process')


def before_doctor(args):
    if getattr(args, 'cpa_passthrough', None):
        run_cpa_passthrough(args.cpa_passthrough)


def install_bridge_config(config):
    """Add dispatch metadata to legacy routes without touching accepted launch/env/build identity."""
    changed = False
    for route in config.get('models', {}).values():
        if 'engine' in route and 'extension' not in route:
            route['extension'] = 'sienna'
            changed = True
    if any(r.get('extension') == 'sienna' for r in config.get('models', {}).values()):
        path = str(cp.ROOT / 'addons/sienna/bridge_engine.py')
        if config.get('extensions', {}).get('sienna') != path:
            config.setdefault('extensions', {})['sienna'] = path
            changed = True
    return changed
