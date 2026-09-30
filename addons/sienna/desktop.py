"""Pooled desktop file backend. Standard library, Python 3.9.

Only SafeFiles accesses the configuration store. No normal-profile lookups, token
stores, or app automation occur on the status/guard path. Runtime dependencies are
injected as the CLI module; tests use its existing fake HOME.
"""
import contextlib
import ctypes
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
import pathlib
import plistlib
import pwd
import re
import secrets
import stat
import subprocess
import sys
import time
import uuid
from . import cp, pool, guard as sienna_guard
from .desktop_build import desktop_cpa_compatibility

DESKTOP_TEST_HOME = None  # explicit test injection; never an environment bypass
DESKTOP_AS = cp.HOME / 'Library/Application Support'
DESKTOP_P1 = DESKTOP_AS / 'Claude'  # lexical boundary only; never stat/open/resolve this path
DESKTOP_P3 = DESKTOP_AS / 'Claude-3p'
DESKTOP_LOGS_3P = cp.HOME / 'Library/Logs/Claude-3p'
DESKTOP_LOGS_1P = cp.HOME / 'Library/Logs/Claude'

OWNED = frozenset('inferenceProvider inferenceGatewayBaseUrl inferenceGatewayAuthScheme '
                  'inferenceGatewayApiKey inferenceCredentialKind modelDiscoveryEnabled '
                  'inferenceModels deploymentDisplayName'.split())
OPTIONS = frozenset('defaultModelEffort toolSearchEnabled claudeAiImport'.split())
REFUSED = {
    'disableDeploymentModeChooser': 'hides the Claude.ai sign-in and overrides deploymentMode',
    'inferenceCustomHeaders': 'would change the request identity the pool checks',
    'egressProxyUrl': 'would route the loopback gateway through a proxy',
    'egressProxyPacUrl': 'would route the loopback gateway through a proxy',
    'inferenceGatewayManagedClaudeCode': 'changes how the engine is managed behind the gateway',
}
for _suffix in ('', 'Args', 'SilentRefreshEnabled', 'TimeoutSec', 'TtlSec', 'Windows'):
    REFUSED['inferenceCredentialHelper' + _suffix] = 'a credential helper beside a static key'
ALIASES = dict(zip(
    'inferenceGatewayHeaders isDxtEnabled isDxtDirectoryEnabled isDxtSignatureRequired '
    'trustBootstrapLocalExec enduserAttribution'.split(),
    'inferenceCustomHeaders isDesktopExtensionEnabled isDesktopExtensionDirectoryEnabled '
    'isDesktopExtensionSignatureRequired trustBootstrapDelivery endUserAttribution'.split()))
CANONICAL = {k.lower(): k for k in OWNED | OPTIONS | set(REFUSED) | set(ALIASES.values())}
ID = re.compile(r'^[a-f0-9-]{36}$')
VERIFIED = {'app': '2.9939.2', 'engine': '2.1.281', 'cpa': '7.3.18-gate-b3efb6adf8'}
DEFAULT_MODELS = [('claude-opus-5-5', 'Opus 5.5', 'opus'), ('claude-fable-5-1', 'Fable 5.1', 'fable'),
                  ('claude-sonnet-5', 'Sonnet 5', 'sonnet'), ('claude-opus-5', 'Opus 5', 'opus'),
                  ('claude-opus-4-8', 'Opus 4.8', 'opus')]
DEFAULT_OPTIONS = {'models': None, 'effort': 'high', 'one_m': False, 'import': False, 'tool_search': True}


class DesktopError(RuntimeError):
    pass


def encoded(obj):
    return (json.dumps(obj, sort_keys=True, indent=2) + '\n').encode()


def same_value(left, right):
    # JSON booleans and numbers are distinct even though Python's True == 1.
    return encoded(left) == encoded(right)


def digest(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


def value_hash(obj, key):
    if key not in obj:
        return 'absent'
    return hashlib.sha256(json.dumps(obj[key], sort_keys=True, separators=(',', ':')).encode()).hexdigest()[:12]


def resolved(obj):
    """Canonical spellings take precedence over legacy spellings, including odd case."""
    result = {CANONICAL.get(k.lower(), k): v for k, v in obj.items()
              if k.lower() not in {a.lower() for a in ALIASES}}
    for old, new in ALIASES.items():
        for key in obj:
            if key.lower() == old.lower():
                result.setdefault(new, obj[key])
    return result


def object_json(data, label):
    try:
        obj = json.loads(data) if data is not None else {}
        if not isinstance(obj, dict):
            raise ValueError()
        return obj
    except (ValueError, UnicodeError):
        raise DesktopError(label + ': not JSON; not guessing') from None


def version_tuple(value):
    return tuple(int(n) for n in re.findall(r'\d+', value or '')[:3])


class SafeFiles:
    """Pin every directory from HOME downward; all file operations are fd-relative.

    Check links BEFORE open (including hard links), and fstat again before reading.
    A directory rename cannot redirect an already opened parent. Never resolve P1:
    rejecting symlinks in the full ancestor chain proves containment without even a
    realpath/lstat syscall naming the normal profile.
    """
    def __init__(self, cp):
        self.cp = cp
        self.home = cp.HOME
        self.p3 = DESKTOP_P3
        self.state = cp.STATE
        self.fds = {}

    def close(self):
        for fd in self.fds.values():
            os.close(fd)
        self.fds.clear()

    def allowed(self, path):
        path = pathlib.Path(path)
        if '..' in path.parts or not path.is_absolute():
            raise DesktopError('unexpected path')
        roots = (self.p3, self.state, DESKTOP_LOGS_3P, pool.CLAUDE_LOGS)
        if not any(path == root or root in path.parents for root in roots):
            raise DesktopError('path is outside the desktop stores')
        return path

    def check_stat(self, st, path, directory=False):
        if st.st_uid != os.getuid():
            raise DesktopError(str(path) + ': not yours')
        if directory:
            if st.st_mode & 0o022:
                raise DesktopError(str(path) + ': directory is group/other writable')
            if not stat.S_ISDIR(st.st_mode):
                raise DesktopError(str(path) + ': is a symlink or not a directory')
        elif not stat.S_ISREG(st.st_mode):
            raise DesktopError('unexpected file type at ' + str(path))
        elif st.st_nlink != 1:
            raise DesktopError(str(path) + ' has ' + str(st.st_nlink) +
                               ' links; codexpool will not read a file shared with another location')

    def directory(self, path, create=False):
        path = pathlib.Path(path)
        if path in self.fds:
            self.check_stat(os.fstat(self.fds[path]), path, True)
            return self.fds[path]
        if path == self.cp.ROOT and path.is_symlink():
            # Only the install root may redirect; pin its verified target.
            target = path.resolve(strict=True)
            st = os.lstat(target)
            self.check_stat(st, target, True)
            fd = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        elif path == self.home:
            st = os.lstat(path)
            self.check_stat(st, path, True)
            fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        else:
            if self.home not in path.parents:
                raise DesktopError('directory is outside HOME')
            parent = self.directory(path.parent, create)
            if parent is None:
                return None
            try:
                st = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                if not create:
                    return None
                if not (path == self.p3 or self.p3 in path.parents or path == self.state or self.state in path.parents):
                    raise DesktopError('required parent directory is missing: ' + str(path))
                os.mkdir(path.name, 0o700, dir_fd=parent)
                os.fsync(parent)
                st = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
            self.check_stat(st, path, True)
            fd = os.open(path.name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        opened = os.fstat(fd)
        try:
            self.check_stat(opened, path, True)
            if (opened.st_dev, opened.st_ino) != (st.st_dev, st.st_ino):
                raise DesktopError('directory changed while opening ' + str(path))
        except BaseException:
            os.close(fd)
            raise
        self.fds[path] = fd
        return fd

    def info(self, path):
        path = self.allowed(path)
        fd = self.directory(path.parent)
        if fd is None:
            return None
        try:
            st = os.stat(path.name, dir_fd=fd, follow_symlinks=False)
        except FileNotFoundError:
            return None
        self.check_stat(st, path)
        return st

    def read(self, path, tail=None, head=None):
        path = self.allowed(path)
        before = self.info(path)
        if before is None:
            return None
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=self.directory(path.parent))
        with os.fdopen(fd, 'rb') as f:
            st = os.fstat(f.fileno())
            self.check_stat(st, path)
            if (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino):
                raise DesktopError('file changed while opening ' + str(path))
            if tail is not None:
                f.seek(max(0, st.st_size - tail))
            return f.read() if head is None else f.read(head)

    def lines(self, path, offset=0, progress=None):
        path = self.allowed(path)
        before = self.info(path)
        if before is None:
            return
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=self.directory(path.parent))
        with os.fdopen(fd, 'rb') as source:
            st = os.fstat(source.fileno())
            self.check_stat(st, path)
            if (st.st_dev, st.st_ino) != (before.st_dev, before.st_ino):
                raise DesktopError('log changed while opening')
            source.seek(offset if offset <= st.st_size else 0)
            for line in source:
                if not line.endswith(b'\n'):
                    source.seek(-len(line), os.SEEK_CUR)
                    yield line.decode(errors='replace')
                    break
                yield line.decode(errors='replace')
            if progress is not None:
                progress.update(offset=source.tell(), device=st.st_dev, inode=st.st_ino)

    def obj(self, path):
        return object_json(self.read(path), pathlib.Path(path).name)

    def write(self, path, data):
        path = self.allowed(path)
        self.info(path)
        parent = self.directory(path.parent, data is not None)
        if parent is None:
            return
        if data is None:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(path.name, dir_fd=parent)
            os.fsync(parent)
            return
        temp = '.' + path.name + '.' + secrets.token_hex(8)
        fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_WRONLY, 0o600, dir_fd=parent)
        try:
            with os.fdopen(fd, 'wb') as f:
                self.check_stat(os.fstat(f.fileno()), path)
                f.write(data)
                f.flush()
                os.fsync(f.fileno())
            self.info(path)  # refuse a link/type swap before replacement too
            os.replace(temp, path.name, src_dir_fd=parent, dst_dir_fd=parent)
            os.fsync(parent)
        finally:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(temp, dir_fd=parent)
        if digest(self.read(path)) != digest(data):
            raise DesktopError('write verification failed: ' + path.name)

    def names(self, path):
        fd = self.directory(path)
        return os.listdir(fd) if fd is not None else []

    def rmdir_empty(self, path):
        path = self.allowed(path)
        if self.directory(path) is None or self.names(path):
            return
        os.rmdir(path.name, dir_fd=self.directory(path.parent))
        os.fsync(self.directory(path.parent))
        os.close(self.fds.pop(path))

    @contextlib.contextmanager
    def lock(self, name='claude-guard.lock'):
        path = self.state / name  # same lock as the guard's Claude pass
        self.info(path)
        parent = self.directory(self.state, True)
        fd = os.open(path.name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
        try:
            self.check_stat(os.fstat(fd), path)
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


class Desktop:
    def __init__(self, cp):
        self.cp = cp
        self.fs = SafeFiles(cp)
        self.library = DESKTOP_P3 / 'configLibrary'
        self.mode_path = DESKTOP_P3 / 'claude_desktop_config.json'
        self.meta_path = self.library / '_meta.json'
        self.state_path = cp.STATE / 'desktop.json'
        self.journal = cp.STATE / 'desktop-txn.json'
        self.url = 'http://127.0.0.1:' + str(pool.CLAUDE_PORT)
        self.events = []
        self.closing = []
        self.steps = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.fs.close()

    def note(self, text):
        self.events.append(text)

    def step(self, title):
        if self.steps is not None:
            self.steps.step(title)

    def entry_path(self, entry_id):
        if not isinstance(entry_id, str) or not ID.fullmatch(entry_id):
            raise DesktopError('invalid configuration id; not guessing')
        return self.library / (entry_id + '.json')

    def ground(self, external=True):
        if external:
            if DESKTOP_TEST_HOME != self.cp.HOME and str(self.cp.HOME) != pwd.getpwuid(os.getuid()).pw_dir:
                raise DesktopError("HOME differs from the account's home directory; not guessing which profile it uses")
            if self.cp.launchd_getenv('CLAUDE_USER_DATA_DIR'):
                raise DesktopError('the app\'s profile directory is redirected by CLAUDE_USER_DATA_DIR; not guessing where 3p data lives')
        # No realpath(P1): full ancestor checks reject both symlink and renamed-parent attacks.
        for directory in (DESKTOP_AS, DESKTOP_P3, self.library, self.cp.ROOT, self.cp.STATE):
            self.fs.directory(directory)
        if self.cp.STATE != self.cp.ROOT / 'state' or DESKTOP_P3 != DESKTOP_AS / 'Claude-3p':
            raise DesktopError('desktop roots are not in their expected locations')
        for path in (self.meta_path, self.mode_path, self.state_path, self.journal, pool.CLAUDE_STATUS_FILE):
            self.fs.info(path)
        if external:
            managed = [pathlib.Path('/Library/Managed Preferences/com.anthropic.claudefordesktop.plist'),
                       self.cp.HOME / 'Library/Managed Preferences/com.anthropic.claudefordesktop.plist',
                       pathlib.Path('/Library/Managed Preferences') / pwd.getpwuid(os.getuid()).pw_name /
                       'com.anthropic.claudefordesktop.plist']
            if any(os.path.lexists(p) for p in managed):
                raise DesktopError('a managed profile is present: the app ignores local configuration')
        meta = self.fs.obj(self.meta_path)
        if 'hybridPointer' in meta:
            raise DesktopError('a managed hybridPointer is present: the app ignores local configuration')
        if meta:
            if (not isinstance(meta.get('entries'), list) or not isinstance(meta.get('appliedId'), str) or
                (meta['appliedId'] and not ID.fullmatch(meta['appliedId'])) or
                any(not isinstance(e, dict) or not isinstance(e.get('id'), str) or not ID.fullmatch(e['id']) or
                    not isinstance(e.get('name'), str) for e in meta['entries']) or
                len({e['id'] for e in meta['entries']}) != len(meta['entries'])):
                raise DesktopError('_meta.json: not JSON configuration metadata; not guessing')
        elif self.fs.read(self.meta_path) is not None:
            raise DesktopError('_meta.json: not JSON configuration metadata; not guessing')
        self.fs.obj(self.mode_path)
        return meta or {'appliedId': '', 'entries': []}

    def app(self, discover=True):
        candidates = [self.cp.HOME / 'Applications/Claude.app']
        if DESKTOP_TEST_HOME != self.cp.HOME:
            candidates.insert(0, pathlib.Path('/Applications/Claude.app'))
        try:
            if not discover:
                raise FileNotFoundError()
            r = subprocess.run(['mdfind', 'kMDItemCFBundleIdentifier == "com.anthropic.claudefordesktop"'],
                               capture_output=True, text=True, timeout=5)
            for line in r.stdout.splitlines():
                p = pathlib.Path(line)
                if pathlib.Path('/Applications') in p.parents or self.cp.HOME / 'Applications' in p.parents:
                    candidates.append(p)
        except (OSError, subprocess.SubprocessError):
            pass
        candidates = sorted({p for p in candidates if p.is_dir()})
        if len(candidates) > 1:
            try:
                r = subprocess.run(['osascript', '-e',
                    'POSIX path of (path to application id "com.anthropic.claudefordesktop")'],
                    capture_output=True, text=True, check=True, timeout=5)
                selected = pathlib.Path(r.stdout.strip())
                if selected.is_dir():
                    candidates = [selected]
            except (OSError, subprocess.SubprocessError):
                # Discovery unavailable: prefer the last validated bundle, then the
                # standard install. Multiple copies alone are not a doctor failure.
                saved = self.fs.obj(self.state_path).get('bundle_path')
                candidates.sort(key=lambda p: (str(p) != saved, p != pathlib.Path('/Applications/Claude.app'), str(p)))
        if not candidates:
            raise DesktopError('Claude app not found: install it from claude.ai/download')
        bundle = candidates[0]
        try:
            with open(bundle / 'Contents/Info.plist', 'rb') as f:
                info = plistlib.load(f)
            if info.get('CFBundleIdentifier') != 'com.anthropic.claudefordesktop':
                raise ValueError()
            ver = info['CFBundleShortVersionString']
            if not version_tuple(ver):
                raise ValueError()
        except (OSError, ValueError, KeyError):
            raise DesktopError('Claude app version could not be read') from None
        return {'bundle_path': str(bundle), 'app_version': ver}

    def process_started(self, pid, fallback):
        """macOS proc_bsdinfo supplies the microseconds ps lstart omits."""
        class BSDInfo(ctypes.Structure):
            _fields_ = ([(name, ctypes.c_uint32) for name in
                         ('flags', 'status', 'xstatus', 'pid', 'ppid', 'uid', 'gid', 'ruid', 'rgid', 'svuid', 'svgid', 'rfu')]
                        + [('comm', ctypes.c_char * 16), ('name', ctypes.c_char * 32)]
                        + [(name, ctypes.c_uint32) for name in ('nfiles', 'pgid', 'pjobc', 'tdev', 'tpgid', 'nice')]
                        + [('seconds', ctypes.c_uint64), ('microseconds', ctypes.c_uint64)])
        try:
            lib = ctypes.CDLL('/usr/lib/libproc.dylib')
            lib.proc_pidinfo.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int]
            lib.proc_pidinfo.restype = ctypes.c_int
            value = BSDInfo()
            size = lib.proc_pidinfo(int(pid), 3, 0, ctypes.byref(value), ctypes.sizeof(value))
            if size == ctypes.sizeof(value) and value.pid == int(pid) and value.seconds:
                return dt.datetime.fromtimestamp(value.seconds + value.microseconds / 1000000, dt.timezone.utc), 0.000001
        except (OSError, AttributeError):
            pass
        return fallback, 1.0

    def process(self, bundle):
        result = {'app_running': False, 'app_started_at': None, 'app_bundle_ok': True}
        try:
            r = subprocess.run(['pgrep', '-x', 'Claude'], capture_output=True, text=True, timeout=5)
            for pid in r.stdout.split():
                if not pid.isdigit():
                    continue
                row = subprocess.run(['ps', '-p', pid, '-o', 'lstart=', '-o', 'comm='],
                                     capture_output=True, text=True, timeout=5).stdout.strip()
                match = re.match(r'(.{24})\s+(.+)$', row)
                result['app_running'] = True
                if not match or match[2] != str(pathlib.Path(bundle or '/') / 'Contents/MacOS/Claude'):
                    result['app_bundle_ok'] = False
                    continue
                started, resolution = self.process_started(pid, dt.datetime.strptime(match[1], '%a %b %d %H:%M:%S %Y').astimezone())
                result.update(app_started_at=started.isoformat(), app_start_resolution=resolution)
        except (OSError, ValueError, subprocess.SubprocessError):
            result.update(app_running=True, app_bundle_ok=False)
        return result

    def render(self, options):
        models = options.get('models')
        rows = DEFAULT_MODELS if models is None else [(m, m, next((t for t in ('opus', 'sonnet', 'fable') if t in m), None)) for m in models]
        if not rows or len(rows) > 200 or any(not re.fullmatch(r'claude-[a-z0-9][a-z0-9.-]*', row[0]) for row in rows):
            raise DesktopError('models must be 1–200 full Claude model ids')
        if options.get('effort') not in (None, 'low', 'medium', 'high'):
            raise DesktopError('defaultModelEffort must be low, medium or high')
        value = {'inferenceProvider': 'gateway', 'inferenceGatewayBaseUrl': self.url,
                 'inferenceGatewayAuthScheme': 'bearer', 'inferenceGatewayApiKey': 'codexpool',
                 'inferenceCredentialKind': 'static', 'modelDiscoveryEnabled': False,
                 'deploymentDisplayName': 'Pool', 'inferenceModels': []}
        for i, (name, label, tier) in enumerate(rows):
            model = {'name': name, 'labelOverride': label, 'maxEffort': 'high'}
            if tier:
                model['anthropicFamilyTier'] = tier
            if i == 0:
                model['isFamilyDefault'] = True
            if options.get('one_m'):
                model['supports1m'] = True
            value['inferenceModels'].append(model)
        if options.get('effort') is not None:
            value['defaultModelEffort'] = options['effort']
        if options.get('tool_search'):
            value['toolSearchEnabled'] = True
        if options.get('import'):
            value['claudeAiImport'] = {'enabled': True}
        return value

    def merge(self, entry, state, args, creating=False):
        entry = resolved(entry)
        for key in sorted(set(entry) & set(REFUSED)):
            raise DesktopError('"Pool" carries ' + key + ': ' + REFUSED[key] +
                               '. Remove it in Developer → Configure Third-Party Inference…')
        old = state.get('owned') or {}
        drift = [k for k in OWNED if value_hash(entry, k) != value_hash(old, k)] if not creating else []
        if drift and not getattr(args, 'reclaim', False):
            diff = '; '.join(k + ': ' + value_hash(old, k) + ' → ' + value_hash(entry, k) for k in sorted(drift))
            raise DesktopError('"Pool" was edited in the app (' + diff + '): codexpool sienna desktop pooled --reclaim overwrites them, or leave it')
        options = dict(DEFAULT_OPTIONS, **(state.get('options') or {}))
        for key, option in [('defaultModelEffort', 'effort'), ('toolSearchEnabled', 'tool_search'), ('claudeAiImport', 'import')]:
            if not creating and value_hash(entry, key) != value_hash(old, key):
                val = entry.get(key)
                if key == 'claudeAiImport':
                    if val is not None and (not isinstance(val, dict) or set(val) != {'enabled'} or not isinstance(val['enabled'], bool)):
                        raise DesktopError('claudeAiImport has an unsupported value; edit it in the app')
                    val = bool(val and val['enabled'])
                elif key == 'toolSearchEnabled':
                    if val is not None and not isinstance(val, bool):
                        raise DesktopError('toolSearchEnabled must be a boolean')
                    val = bool(val)
                options[option] = val
                self.note('! adopted from the app: ' + key)
        for arg, option in [('models', 'models'), ('effort', 'effort'), ('one_m', 'one_m'),
                            ('import_history', 'import'), ('tool_search', 'tool_search')]:
            val = getattr(args, arg, None)
            if val is not None:
                options[option] = val.split(',') if arg == 'models' and isinstance(val, str) else val
                self.note('+ ' + option + ' (from the flag)')
        owned = self.render(options)
        merged = {k: v for k, v in entry.items() if k not in OWNED | OPTIONS}
        merged.update(owned)
        self.note('✓ user keys kept: ' + ', '.join(sorted(set(merged) - OWNED - OPTIONS)))
        for k in sorted(set(entry) | set(merged)):
            if value_hash(entry, k) != value_hash(merged, k):
                self.note('+ ' + k + ': ' + value_hash(entry, k) + ' → ' + value_hash(merged, k))
        return merged, owned, options

    def credits(self, seats, guard, meta, now=None):
        cp = self.cp
        now = now or cp.now_utc()
        changed = None
        unknown_added = False
        for seat in seats:
            if seat.get('provider') != 'claude':
                continue
            added = cp.parse_time((meta.get(seat['name']) or {}).get('added_at'))
            if added is None:
                try:
                    born = os.stat(seat['path'], follow_symlinks=False).st_birthtime
                    added = dt.datetime.fromtimestamp(born, dt.timezone.utc)
                except (OSError, KeyError, TypeError, AttributeError):
                    unknown_added = True
            if added is not None:
                changed = max(changed, added) if changed else added
        rows, paid, problems, polls = [], [], [], []
        for seat in seats:
            if seat.get('provider') != 'claude':
                continue
            status, _, _ = sienna_guard.claude_seat_state(seat, guard, now)
            if status in ('disabled', 'blocked'):
                continue
            label = seat['label']
            usage = sienna_guard.best_claude_usage(seat, guard.get('usage') or {}, now) or {}
            read = cp.parse_time(usage.get('polled_at'))
            enabled = (usage.get('credits') or {}).get('enabled')
            policy = pool.credit_policy(meta, seat['name'])
            fresh = (not unknown_added and read is not None and changed is not None and read >= changed and
                     0 <= (now - read).total_seconds() <= sienna_guard.CLAUDE_CREDIT_FRESH and isinstance(enabled, bool))
            cap = policy.get('cap')
            capped = policy['policy'] == 'last-resort' and isinstance(cap, (int, float)) and not isinstance(cap, bool) and math.isfinite(cap) and cap > 0
            if not fresh or (enabled is False and not sienna_guard.claude_credits_disabled(usage, now)):
                problem = 'no fresh credits reading for ' + label + ': wait for the guard\'s next poll (codexpool sienna status)'
            elif enabled is True and not capped:
                problem = 'credits on at claude.ai for ' + label + ': turn them off there (Settings → Usage). codexpool can\'t stop every paid request.'
            else:
                problem = None
            rows.append(label)
            if problem:
                problems.append(problem)
            else:
                polls.append(read)
                if enabled:
                    paid.append({'label': label, 'cap': cap})
        if not rows:
            problems.append('no account can serve')
        return {'ok': not problems, 'problems': problems, 'paid': paid,
                'check': {'at': now.isoformat(), 'accounts_changed_at': changed.isoformat() if changed else None,
                          'oldest_polled_at': min(polls).isoformat() if polls else None, 'labels': rows}}

    def live_credits(self):
        pool = self.cp.seat_pool('claude')
        return self.credits(self.cp.load_seats(pool), self.cp.read_guard(pool), self.cp.read_meta(pool))

    def pool_ok(self):
        return pool.claude_installed() and all(actual == expected for _, actual, expected in
                                                self.cp.gate_probe_cases(self.cp.pool_instance('claude')))

    def load(self):
        meta = self.ground(external=False)
        state = self.fs.obj(self.state_path)
        mode = self.fs.obj(self.mode_path)
        entry_id = state.get('entry_id')
        # Legacy removals retained ownership outside desktop.json. Inspect only
        # those generated records, never any file backup or credential store.
        if not state:
            for name in sorted(self.fs.names(self.cp.STATE), reverse=True):
                if re.fullmatch(r'desktop.json.removed-[a-f0-9-]{36}', name):
                    archived = self.fs.obj(self.cp.STATE / name)
                    candidate = archived.get('entry_id')
                    if candidate and self.fs.info(self.entry_path(candidate)) is not None:
                        state, entry_id = dict(archived, removed=True), candidate
                        break
        state = self.clean_state(state)
        entry = self.fs.obj(self.entry_path(entry_id)) if entry_id else {}
        return meta, mode, state, entry

    @staticmethod
    def clean_state(state):
        # Retire old ownership claims; none of these can affect another provider.
        cleaned = {k: v for k, v in state.items() if k not in
                   ('original_applied_id', 'original_mode', 'policy_inherited', 'created_mode_file', 'undo')}
        if isinstance(cleaned.get('created_entry'), dict):
            cleaned['created_entry'] = {k: v for k, v in cleaned['created_entry'].items() if k in OWNED | OPTIONS}
        return cleaned

    def revision(self, path):
        info = self.fs.info(path)
        return (info.st_dev, info.st_ino, info.st_mtime_ns, digest(self.fs.read(path))) if info else None

    def other_configuration(self, meta, entry_id):
        return (any(e['id'] != entry_id for e in meta['entries']) or
                bool(meta.get('appliedId') and meta['appliedId'] != entry_id) or
                any(name.endswith('.json') and name != '_meta.json' and name != str(entry_id) + '.json'
                    for name in self.fs.names(self.library)))

    def manual_alternative(self):
        return ('another third-party configuration exists; codexpool leaves the desktop to you. '
                "Use Claude's Developer → Configure Third-Party Inference: gateway " + self.url +
                ', placeholder key codexpool, model discovery off; list models: ' +
                ', '.join(row[0] for row in DEFAULT_MODELS) + '.')

    def require_only_pool(self):
        meta, _, state, _ = self.load()
        # An interrupted first creation has not published desktop.json yet.
        entry_id = state.get('entry_id') or self.fs.obj(self.journal).get('entry_id')
        if self.other_configuration(meta, entry_id):
            raise DesktopError(self.manual_alternative())

    def key_value(self, obj, key, entry_id):
        if key == '@membership':
            return {'present': True, 'value': any(e.get('id') == entry_id for e in obj.get('entries', []))}
        return {'present': key in obj, **({'value': obj[key]} if key in obj else {})}

    def key_path(self, role, entry_id):
        return {'mode': self.mode_path, 'meta': self.meta_path,
                'entry': self.entry_path(entry_id) if entry_id else None}[role]

    def validate_journal(self, journal):
        if journal.get('version') != 2:
            return False
        if not ID.fullmatch(str(journal.get('id', ''))) or journal.get('command') not in ('pooled', 'claudeai', 'remove'):
            raise DesktopError('invalid transaction journal; not guessing')
        if not isinstance(journal.get('steps'), list) or not isinstance(journal.get('started'), int):
            raise DesktopError('invalid key undo journal')
        for item in journal['steps']:
            role, key = item.get('role'), item.get('key')
            allowed = {'mode': {'deploymentMode'}, 'meta': {'appliedId', '@membership'},
                       'entry': OWNED | OPTIONS}
            if role not in allowed or key not in allowed[role]:
                raise DesktopError('journal names a key codexpool does not manage')
            if self.key_path(role, journal.get('entry_id')) is None:
                raise DesktopError('missing entry id in journal')
            for side in ('before', 'after'):
                value = item.get(side)
                if not isinstance(value, dict) or not isinstance(value.get('present'), bool):
                    raise DesktopError('invalid key undo record')
        return True

    def classify(self, journal):
        if not self.validate_journal(journal):
            return {'legacy': 'ignored'}
        groups = {}
        for item in journal['steps']:
            groups.setdefault((item['role'], item['key']), []).append(item)
        classes = {}
        for (role, key), items in groups.items():
            current = self.key_value(self.fs.obj(self.key_path(role, journal['entry_id'])), key, journal['entry_id'])
            kind = ('written' if same_value(current, items[-1]['after']) else 'untouched' if same_value(current, items[0]['before'])
                    else 'written' if any(same_value(current, i['after']) for i in items) else 'foreign')
            old = classes.get(role)
            classes[role] = 'foreign' if 'foreign' in (old, kind) else kind if old in (None, kind) else 'partial'
        return classes

    def checkpoint(self, name):
        """Fault-injection seam after durable boundaries."""

    def patch_status(self, patch):
        status = self.fs.obj(pool.CLAUDE_STATUS_FILE)
        status.setdefault('pool', {})['desktop'] = patch
        self.fs.write(pool.CLAUDE_STATUS_FILE, encoded(status))

    def publish(self, journal):
        state = self.clean_state(journal['state_after']) if journal['state_after'] is not None else None
        self.fs.write(self.state_path, encoded(state) if state is not None else None)
        self.checkpoint('published-state')
        patch = self.status(probe=False)
        patch.update(journal.get('status_patch') or {})
        patch.update(txn_pending=False, txn_classes=None)
        self.patch_status(patch)
        self.checkpoint('published-status')
        journal['phase'] = 'committed'
        self.fs.write(self.journal, encoded(journal))
        self.checkpoint('committed')
        self.fs.write(self.journal, None)

    def apply_keys(self, items, entry_id, undo=False, retain_entry=False):
        """CAS each changed key against a fresh object; never replay a file snapshot."""
        if not items:
            return
        path = self.key_path(items[0]['role'], entry_id)
        obj = self.fs.obj(path)
        changed = False
        for item in items:
            key = item['key']
            expected, target = (item['after'], item['before']) if undo else (item['before'], item['after'])
            current = self.key_value(obj, key, entry_id)
            if same_value(current, target):
                continue
            if not same_value(current, expected):
                self.note('! kept changed key: ' + item['role'] + '.' + key)
                if not undo:
                    raise DesktopError('configuration changed during planning: ' + item['role'] + '.' + key)
                continue
            if key == '@membership':
                entries = obj.get('entries', [])
                if target['value']:
                    entries.append({'id': entry_id, 'name': 'Pool'})
                else:
                    entries = [e for e in entries if e.get('id') != entry_id]
                obj['entries'] = entries
            elif target['present']:
                obj[key] = target['value']
            else:
                obj.pop(key, None)
            changed = True
        if not changed:
            return
        # Do not recreate absent, untouched files. Remove only empty containers
        # created by this transaction (or explicitly emptied by remove).
        if items[0]['role'] == 'meta' and obj.get('entries') and 'appliedId' not in obj:
            obj['appliedId'] = ''
        empty = not obj or items[0]['role'] == 'meta' and obj in ({'entries': []}, {'entries': [], 'appliedId': ''})
        delete = empty and not retain_entry and (undo and not items[0]['file_existed'] or not undo and items[0].get('delete_empty'))
        self.fs.write(path, None if delete else encoded(obj))

    def prepare(self, command, writes, state_after, entry_id, remove_dir=False):
        steps, paths = [], set()
        for role, path, data in writes:
            if path in paths:
                raise DesktopError('a transaction cannot write a profile file twice')
            paths.add(path)
            before, after = self.fs.obj(path), object_json(data, role)
            keys = {'mode': {'deploymentMode'}, 'meta': {'appliedId', '@membership'},
                    'entry': OWNED | OPTIONS}[role]
            # Secrets in an edited owned field must never enter an undo log.
            if role == 'entry' and before.get('inferenceGatewayApiKey') not in (None, 'codexpool') and before != after:
                raise DesktopError('remove the edited inferenceGatewayApiKey in the app before reclaiming; no credential is backed up')
            if role == 'entry' and before.get('inferenceGatewayBaseUrl') != after.get('inferenceGatewayBaseUrl'):
                url = self.cp.urllib.parse.urlsplit(str(before.get('inferenceGatewayBaseUrl') or ''))
                if url.username or url.password or url.query or url.fragment or url.path not in ('', '/'):
                    raise DesktopError('remove credentials/path/query from the edited gateway URL in the app before reclaiming')
            batch = []
            for key in sorted(keys):
                old, new = self.key_value(before, key, entry_id), self.key_value(after, key, entry_id)
                if not same_value(old, new):
                    batch.append(dict(role=role, key=key, before=old, after=new,
                                      file_existed=self.fs.info(path) is not None, delete_empty=data is None))
            steps.append(batch)
        flat = [item for batch in steps for item in batch]
        recorded = self.fs.obj(self.state_path)
        previous = self.clean_state(recorded) or None
        if recorded == (previous or {}) and not flat and (state_after is None or all((previous or {}).get(k) == state_after.get(k)
                           for k in ('owned', 'options', 'removed'))):
            return None
        txn = str(uuid.uuid4())
        if state_after is not None:
            state_after = dict(state_after, txn=txn, last_backup=None)
            state_after.pop('undo', None)
            if flat:
                state_after['written_at'] = self.cp.now_utc().isoformat()
        return dict(version=2, id=txn, command=command, entry_id=entry_id, steps=flat, batches=steps,
                    started=-1, state_before=previous, state_after=state_after, phase='prepared',
                    remove_dir=remove_dir, entry_after_hash=digest(next((data for role, _, data in writes if role == 'entry'),
                    self.fs.read(self.entry_path(entry_id)) if entry_id else None)), status_patch={'credits_ok': True, 'credits_problems': [],
                    'accepted_credits': (state_after or {}).get('accepted_credits') or []} if command == 'pooled' else {})

    def transaction(self, command, writes, state_after, entry_id, remove_dir=False, prepared=None):
        journal = prepared or self.prepare(command, writes, state_after, entry_id, remove_dir)
        if journal is None:
            self.note('✓ nothing to change')
            return False
        self.fs.write(self.journal, encoded(journal))
        self.checkpoint('prepared')
        index = -1
        for batch in journal['batches']:
            if not batch:
                continue
            index += len(batch)
            journal['started'] = index
            self.fs.write(self.journal, encoded(journal))
            self.apply_keys(batch, entry_id)
            self.checkpoint(batch[0]['role'])
        if remove_dir:
            self.fs.rmdir_empty(self.library)
        self.publish(journal)
        return True

    def recover(self, explicit=False, check_only=False, force_undo=False):
        if self.fs.info(self.journal) is None:
            return
        journal = self.fs.obj(self.journal)
        if not self.validate_journal(journal):
            # Old journals depend on file backups which may contain credentials.
            # Never open those backups, replay their contents, or trust their paths.
            if not check_only:
                self.fs.write(self.journal, None)
            self.note('! ignored legacy file-backup journal; configuration left unchanged; legacy backup folders were not read')
            return
        if check_only:
            raise DesktopError('an interrupted desktop change needs recovery; quit Claude, then run codexpool claude desktop rollback')
        classes = self.classify(journal)
        groups = {}
        for item in journal['steps']:
            groups[(item['role'], item['key'])] = item
        all_after = all(same_value(self.key_value(self.fs.obj(self.key_path(role, journal['entry_id'])), key, journal['entry_id']), item['after'])
                        for (role, key), item in groups.items())
        if all_after and not force_undo:
            self.publish(journal)
            self.note('✓ finished the interrupted change')
            return
        if 'foreign' in classes.values() and not explicit:
            raise DesktopError('owned keys changed after an interruption: codexpool claude desktop rollback')
        # Group CAS undo by file. Leaving 3p writes explicit 1p FIRST; entering
        # 3p writes mode LAST. An absent old mode is unsafe with a retained entry.
        attempted = journal['steps'][:journal.get('started', -1) + 1]
        entry_id = journal['entry_id']
        path = self.entry_path(entry_id) if entry_id else None
        entry = self.fs.obj(path) if path else {}
        meta = self.fs.obj(self.meta_path)
        renamed = any(e['id'] == entry_id and e['name'] != 'Pool' for e in meta.get('entries', []))
        retain = renamed or bool(path and self.fs.info(path) and
                                 digest(encoded(entry)) != journal.get('entry_after_hash'))
        batches = {}
        for item in reversed(attempted):
            item = dict(item)
            role, key = item['role'], item['key']
            if role == 'meta' and key == '@membership' and retain and not item['before'].get('value'):
                continue
            if role == 'meta' and key == 'appliedId' and item['before'].get('value') not in ('', entry_id, None):
                item['before'] = {'present': True, 'value': ''}
            if role == 'mode':
                if item['before'].get('value') != '3p':
                    item['before'] = {'present': True, 'value': '1p'}
                elif meta.get('appliedId') not in ('', entry_id, None):
                    self.note('! kept safe mode: applied configuration changed')
                    continue
            # Old journals could write mode twice; the oldest before wins.
            batches.setdefault(role, {})[key] = item
        if 'mode' not in batches and meta.get('appliedId') == entry_id and not journal.get('state_before'):
            batches['mode'] = {'deploymentMode': dict(role='mode', key='deploymentMode', file_existed=True,
                before={'present': True, 'value': '1p'},
                after=self.key_value(self.fs.obj(self.mode_path), 'deploymentMode', entry_id))}
        to_3p = any(i['before'].get('value') == '3p' for i in batches.get('mode', {}).values())
        order = ('entry', 'meta', 'mode') if to_3p else ('mode', 'meta', 'entry')
        for role in order:
            self.apply_keys(list(batches.get(role, {}).values()), entry_id, undo=True,
                            retain_entry=retain and role == 'entry')
        restored = self.clean_state(journal.get('state_before') or {}) or None
        # Keep the UUID when user keys survive an interrupted first creation.
        path = self.entry_path(journal['entry_id']) if journal.get('entry_id') else None
        if restored is None and path and self.fs.info(path) is not None:
            restored = dict(self.clean_state(journal.get('state_after') or {}), removed=True,
                            owned={k: v for k, v in (journal.get('state_after') or {}).get('owned', {}).items()
                                   if k in self.fs.obj(path)})
        self.fs.write(self.state_path, encoded(restored) if restored is not None else None)
        self.fs.write(self.journal, None)
        self.patch_status(self.status(probe=False))
        self.note('✓ rolled back changed keys; later user edits kept')

    def plan(self, command, args, app, credits=None):
        meta, mode, state, entry = self.load()
        entry_id = state.get('entry_id')
        listed = {e['id']: e for e in meta['entries']}
        writes = []
        result = dict(state)
        remove_dir = False
        if command == 'pooled':
            self.require_only_pool()
            creating = not entry_id or self.fs.info(self.entry_path(entry_id)) is None
            if creating:
                entry_id = entry_id or str(uuid.uuid4())
                entry = {}
            merged, owned, options = self.merge(entry, state, args, creating)
            if not state or state.get('removed'):
                result.update(created_dir=self.fs.directory(self.library) is None,
                              created_meta=self.fs.read(self.meta_path) is None)
            writes.append(('entry', self.entry_path(entry_id), encoded(merged)))
            if entry_id not in listed:
                meta['entries'].append({'id': entry_id, 'name': 'Pool'})
            meta['appliedId'] = entry_id
            writes.append(('meta', self.meta_path, encoded(meta)))
            writes.append(('mode', self.mode_path, encoded(dict(mode, deploymentMode='3p'))))
            result.update(entry_id=entry_id, owned=owned, options=options, removed=False,
                          created_entry={k: v for k, v in merged.items() if k in OWNED | OPTIONS}
                          if creating else state.get('created_entry'),
                          base_url=self.url, bundle_path=app['bundle_path'],
                          verified_with=VERIFIED, seen={'app': app['app_version'], 'engine': self.engine_version(),
                                                      'cpa': self.cp.cpa_version(pool.CLAUDE_CURRENT)},
                          credits_check=(credits or {}).get('check'), accepted_credits=(credits or {}).get('paid') or None)
            self.closing.append('Set up. Open Claude and it runs on the pool (the sidebar footer says "Pool"). '
                      'Back: codexpool sienna desktop claudeai. The app\'s own "Go back to Claude.ai" also signs the pooled app out.')
            if options['import'] and getattr(args, 'import_history', None):
                self.note('Import enabled: the wizard stores its own sign-in in the pooled profile.')
            self.closing.append('The first pooled launch downloads its own engine and sandbox image; this can take a few minutes.')
        elif command in ('claudeai', 'remove'):
            if command == 'claudeai':
                self.require_only_pool()
            if not state:
                self.note('✓ no codexpool desktop ownership record; no files changed')
                return [], None, None, False
            # A later manual provider selection belongs to the user. Never apply it,
            # read its settings, or change the mode underneath it.
            if meta.get('appliedId') in ('', entry_id):
                writes.append(('mode', self.mode_path, encoded(dict(mode, deploymentMode='1p'))))
            if meta.get('appliedId') == entry_id:
                meta['appliedId'] = ''
            delete = (command == 'remove' and bool(state.get('created_entry')) and
                      same_value(entry, state['created_entry']) and
                      listed.get(entry_id, {}).get('name', 'Pool') == 'Pool')
            if delete:
                meta['entries'] = [e for e in meta['entries'] if e['id'] != entry_id]
            elif command == 'remove' and entry:
                self.note('! edited or renamed Pool entry kept for reuse, including its permissions')
            remove_meta = not meta['entries'] and not meta.get('appliedId') and state.get('created_meta')
            if self.fs.info(self.meta_path) is not None:
                writes.append(('meta', self.meta_path, None if remove_meta else encoded(meta)))
            if delete:
                writes.append(('entry', self.entry_path(entry_id), None))
            remaining = set(self.fs.names(self.library)) - ({'_meta.json'} if remove_meta else set())
            if delete:
                remaining.discard(entry_id + '.json')
            remove_dir = bool(command == 'remove' and state.get('created_dir') and not remaining)
            result.update(removed=command == 'remove', last_backup=None)
            if meta.get('appliedId'):
                self.closing.append("Removed codexpool setup; the user's later configuration choice is unchanged.")
            else:
                self.closing.append('Done. The app opens on Claude.ai; saved configurations and history are kept.')
        return writes, result, entry_id, remove_dir

    def uninstall(self):
        """No app discovery/automation. Best effort; unsafe files stay for the owner."""
        try:
            with self.fs.lock():
                self.ground(external=False)
                # Do not roll forward a pending activation during uninstall.
                self.recover(explicit=True, force_undo=True)
                writes, state, entry_id, remove_dir = self.plan('remove', None, {})
                self.transaction('remove', writes, state, entry_id, remove_dir)
                self.note('Desktop cleanup complete. Reopen Claude yourself to use Claude.ai; '
                          'any edited Pool entry, other configurations and history were kept.')
        except (DesktopError, OSError, ValueError, KeyError, TypeError) as error:
            self.note('! Desktop cleanup incomplete; uninstall continues. Claude-3p/configLibrary, '
                      'Claude-3p/claude_desktop_config.json and state/desktop*.json may still need cleanup. Use Developer → Configure Third-Party Inference '
                      'to unapply Pool and choose Claude.ai before reopening. ' +
                      (str(error) if isinstance(error, DesktopError) else 'Could not validate or write desktop files.'))

    def engine_version(self):
        versions = [name for name in self.fs.names(DESKTOP_P3 / 'claude-code') if re.fullmatch(r'\d+\.\d+\.\d+', name)]
        return max(versions, key=version_tuple) if versions else None

    def running_evidence(self, status, state, mode, meta):
        cp = self.cp
        started = cp.parse_time(status['app_started_at'])
        changed = cp.parse_time(state.get('written_at'))
        # ps lstart has one-second resolution. Do not pretend a subsecond write
        # can be ordered against it; only a strictly later second proves a restart.
        status['restart_required'] = bool(status['app_running'] and started and changed and
                                          (started < changed if status.get('app_start_resolution') == 0.000001 else
                                           int(started.timestamp()) < int(changed.timestamp())))
        if not status['app_running']:
            status['running_mode'] = None
            return
        if not started or not status['app_bundle_ok']:
            return
        evidence = self.app_log_evidence(started)
        active, fallback = evidence.get('active'), evidence.get('fallback')
        status['running_host'] = evidence.get('host')
        if fallback:
            status['config_error'] = 'the app logged [custom-3p] configError (values redacted)'
        if fallback:
            status['running_mode'] = 'fallback'
        elif active:
            status['running_mode'] = ('other' if evidence.get('provider_other') or evidence.get('host_other') else
                                      'pooled' if evidence.get('pool_host') else '3p')
        elif not status['restart_required'] and status['configured_mode'] == 'claudeai':
            try:
                # The sole normal-mode log access: metadata only, never open its contents.
                st = os.stat(DESKTOP_LOGS_1P / 'main.log', follow_symlinks=False)
                if stat.S_ISREG(st.st_mode) and st.st_mtime >= started.timestamp():
                    status['running_mode'] = 'claudeai'
            except FileNotFoundError:
                pass
    def app_log_evidence(self, started):
        # Separate diagnostics lock: status also runs inside the guard lock. Cache
        # offsets and classified evidence only, never raw log lines or credentials.
        cache_path = self.cp.STATE / 'desktop-log-cache.json'
        with self.fs.lock('desktop-log.lock'):
            cache = self.fs.obj(cache_path)
            previous = encoded(cache)
            if cache.get('started') != started.isoformat() or cache.get('base_url') != self.url:
                cache = {'started': started.isoformat(), 'base_url': self.url, 'files': {}, 'evidence': {}}
            evidence = cache['evidence']
            files = {}
            paths = []
            for name in self.fs.names(DESKTOP_LOGS_3P):
                if name.startswith('main') and (name.endswith('.log') or name.startswith('main.log.')):
                    path = DESKTOP_LOGS_3P / name
                    st = self.fs.info(path)
                    if st:
                        paths.append((st.st_mtime_ns, path, st))
            for _, path, st in sorted(paths):
                ident = str(st.st_dev) + ':' + str(st.st_ino)
                cursor = cache['files'].get(ident, {})
                offset = cursor.get('offset', 0) if st.st_size >= cursor.get('offset', 0) else 0
                prefix_size = cursor.get('prefix_size', min(st.st_size, 256))
                prefix = digest(self.fs.read(path, head=prefix_size))
                if cursor.get('prefix') not in (None, prefix):
                    offset = 0
                # An atomic replacement or rotation has a different inode. A
                # truncated same-inode log starts at zero; retained evidence survives.
                progress = {}
                if offset == st.st_size and cursor.get('mtime_ns') == st.st_mtime_ns:
                    files[ident] = cursor
                    continue
                for line in self.fs.lines(path, offset, progress):
                    try:
                        at = dt.datetime.strptime(line[:19], '%Y-%m-%d %H:%M:%S').astimezone()
                    except ValueError:
                        continue
                    if int(at.timestamp()) < int(started.timestamp()):
                        continue
                    if '[custom-3p] configError:' in line:
                        evidence['fallback'] = True
                    if '[custom-3p] 3P mode active' in line:
                        evidence['active'] = True
                        match = re.search(r"provider: ['\"]([^'\"]+)", line)
                        evidence['provider_other'] = bool(match and match[1] != 'gateway')
                    match = re.search(r'\[custom-3p\] inference apiHost=(\S+)', line)
                    if match:
                        parsed = self.cp.urllib.parse.urlsplit(match[1])
                        evidence.update(pool_host=match[1] == self.url, host_other=match[1] != self.url,
                                        host=parsed.scheme + '://' + (parsed.hostname or '') +
                                        (':' + str(parsed.port) if parsed.port else ''))
                prefix_size = min(st.st_size, 256)
                files[ident] = dict(progress, mtime_ns=st.st_mtime_ns, prefix_size=prefix_size,
                                    prefix=digest(self.fs.read(path, head=prefix_size)))
            cache['files'] = files
            if encoded(cache) != previous:
                self.fs.write(cache_path, encoded(cache))
            return evidence

    def pool_started(self):
        loaded, pid = self.cp.launchd_loaded(pool.CLAUDE_JOB)
        if not loaded or not pid:
            return None
        started, _ = self.process_started(pid, None)
        return started

    def gate_evidence(self, status):
        raw = self.fs.read(pool.CLAUDE_MAIN_LOG, tail=2 * 1024 * 1024) or b''
        cutoff = self.cp.now_utc() - dt.timedelta(hours=24)
        started = self.pool_started()
        status['pool_started_at'] = started.isoformat() if started else None
        for line in raw.decode(errors='replace').splitlines():
            match = re.search(r'\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d', line)
            if not match:
                continue
            try:
                at = dt.datetime.fromisoformat(match[0]).astimezone()
            except ValueError:
                continue
            if ('codexpool gate: first request from client' in line and 'claude-desktop-3p' in line and
                    started and int(at.timestamp()) >= int(started.timestamp())):
                status['pool_seen_desktop_at'] = at.isoformat()
            if at < cutoff:
                continue
            if 'rejected unrecognised token-count client' in line and 'claude-desktop-3p' in line:
                status['token_count_rejections_24h'] += 1
            if 'rejected client' in line:
                if 'claude-desktop-3p' in line:
                    status['engine_identity_rejections_24h'] += 1
                elif 'Electron' in line:
                    status['electron_rejections_24h'] += 1

    def status(self, seats=None, guard=None, meta_seats=None, probe=True, live_version=None):
        s = dict(configured_mode='unknown', chooser_disabled=False, running_mode='unknown', running_host=None,
                 app_running=False, app_started_at=None, app_start_resolution=1.0, app_bundle_ok=True, restart_required=False,
                 ours=False, current=False, owned_drift=False, app_version=None, applied_name=None, base_url=self.url,
                 port_ok=False, credits_ok=None, credits_problems=[], accepted_credits=[], txn_pending=False,
                 txn_classes=None, pool_seen_desktop_at=None, pool_started_at=None, legacy_backups=[], old_3p_logs=None, checked_at=self.cp.now_utc().isoformat(),
                 owned=None, last_backup=None, errors=[], conflicts=[], placeholder_ok=True, models_ok=True,
                 mode_explicit=True, verified_with=dict(VERIFIED), seen={}, policy_inherited=None,
                 wire_fixtures_synthesised=True, token_count_rejections_24h=0, engine_identity_rejections_24h=0,
                 electron_rejections_24h=0)
        s.update(desktop_cpa_compatibility(live_version=live_version))
        try:
            s['legacy_backups'] = sorted(n for n in self.fs.names(self.cp.STATE) if n.startswith('desktop-backup-'))
            self.ground(external=probe)
            s['txn_pending'] = self.fs.read(self.journal) is not None
            if s['txn_pending']:
                s['txn_classes'] = self.classify(self.fs.obj(self.journal))
            meta, mode, state, entry = self.load()
            applied_id = meta.get('appliedId')
            other = self.other_configuration(meta, state.get('entry_id'))
            applied = entry if applied_id and applied_id == state.get('entry_id') else {}
            if other:
                s['errors'].append(self.manual_alternative())
            s['chooser_disabled'] = resolved(applied).get('disableDeploymentModeChooser') is True
            if other:
                configured = 'other'
            elif mode.get('deploymentMode') == '1p':
                configured = 'claudeai'
            elif not applied_id or not applied:
                configured = 'none'
            elif s['chooser_disabled']:
                configured = 'other'
            elif mode.get('deploymentMode') == '1p':
                configured = 'claudeai'
            elif mode.get('deploymentMode') == '3p' and applied_id == state.get('entry_id'):
                configured = 'pooled'
            else:
                configured = 'other'
            s['configured_mode'] = configured
            s['applied_name'] = next((e['name'] for e in meta['entries'] if e['id'] == applied_id), None)
            s['mode_explicit'] = not applied_id or mode.get('deploymentMode') in ('1p', '3p')
            normalized = resolved(entry)
            old = state.get('owned') or {}
            s['owned_drift'] = bool(entry and any(value_hash(normalized, k) != value_hash(old, k) for k in OWNED))
            s['ours'] = bool(entry and old and not s['owned_drift'])
            s['port_ok'] = normalized.get('inferenceGatewayBaseUrl') == self.url
            rendering = self.render(dict(DEFAULT_OPTIONS, **(state.get('options') or {})))
            s['owned'] = rendering  # never echo the disk entry or a tampered snapshot's credentials
            s['current'] = s['ours'] and all(value_hash(normalized, k) == value_hash(rendering, k) for k in OWNED | OPTIONS)
            s['conflicts'] = sorted(set(normalized) & set(REFUSED))
            s['placeholder_ok'] = not entry or normalized.get('inferenceGatewayApiKey') == 'codexpool'
            models = normalized.get('inferenceModels')
            s['models_ok'] = not entry or (normalized.get('modelDiscoveryEnabled') is False and isinstance(models, list) and bool(models) and
                all(isinstance(m, dict) and re.fullmatch(r'claude-[a-z0-9][a-z0-9.-]*', str(m.get('name', ''))) and m.get('maxEffort') == 'high' for m in models))
            s['last_backup'] = None  # compatibility field; file backups are no longer made
            app = {'bundle_path': state.get('bundle_path'), 'app_version': (state.get('seen') or {}).get('app')}
            try:
                app = self.app(discover=probe)
            except DesktopError as e:
                s['errors'].append(str(e))
            s.update(app)
            s['seen'] = {'app': app.get('app_version'), 'engine': self.engine_version(), 'cpa': self.cp.cpa_version(pool.CLAUDE_CURRENT)}
            s.update(self.process(app.get('bundle_path')))
            self.running_evidence(s, state, mode, meta)
            self.gate_evidence(s)
            if self.fs.directory(DESKTOP_P3) is None:
                times = [self.fs.info(DESKTOP_LOGS_3P / n).st_mtime for n in self.fs.names(DESKTOP_LOGS_3P)
                         if n.endswith('.log') and self.fs.info(DESKTOP_LOGS_3P / n)]
                if times:
                    s['old_3p_logs'] = dt.datetime.fromtimestamp(max(times)).strftime('%Y-%m-%d')
            if seats is not None:
                credit = self.credits(seats, guard or {}, meta_seats or {})
                s.update(credits_ok=credit['ok'], credits_problems=credit['problems'], accepted_credits=credit['paid'])
        except (DesktopError, OSError, ValueError, KeyError, TypeError) as e:
            s['errors'].append(str(e) if isinstance(e, DesktopError) else 'desktop files could not be validated')
            s['configured_mode'] = 'unknown'
            # Even an invalid journal must remain visible as pending.
            with contextlib.suppress(OSError, DesktopError):
                s['txn_pending'] = self.fs.info(self.journal) is not None
        return s

    def confirm_relaunch(self, args):
        if getattr(args, 'yes', False):
            return
        if not os.isatty(0) or input('Reopen Claude now? Finish current tasks first. [y/N] ').strip().lower() not in ('y', 'yes'):
            raise DesktopError('relaunch requires confirmation; confirm in the GUI or pass --yes')

    def quit_app(self, bundle):
        subprocess.run(['osascript', '-e', 'tell application id "com.anthropic.claudefordesktop" to quit'],
                       capture_output=True, text=True, check=True, timeout=20)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            r = subprocess.run(['osascript', '-e', 'application id "com.anthropic.claudefordesktop" is running'],
                               capture_output=True, text=True, timeout=5)
            if r.stdout.strip() == 'false' and not self.process(bundle)['app_running']:
                return
            time.sleep(0.25)
        raise DesktopError('Claude did not quit; finish what it is doing and try again')

    def open_app(self, bundle):
        subprocess.run(['open', bundle], check=True, timeout=10)
        end = time.monotonic() + 20
        while time.monotonic() < end:
            p = self.process(bundle)
            if p['app_running'] and p['app_bundle_ok'] and p['app_started_at']:
                break
            time.sleep(0.25)
        else:
            raise DesktopError('the app did not start; open it yourself and check codexpool sienna desktop status')
        end = time.monotonic() + 60
        while True:
            s = self.status(probe=False)
            with self.fs.lock():
                self.patch_status(s)
            if s['running_mode'] in ('pooled', 'other', 'claudeai', 'fallback') or time.monotonic() >= end:
                break
            time.sleep(1)
        self.note({'pooled': 'Claude opened on the pool.', 'claudeai': 'Claude opened on Claude.ai.',
                   '3p': 'Claude opened in 3p mode; the pool address is confirmed on the first request.',
                   'other': 'Claude opened on another configuration (3p), not on Claude.ai.',
                   'fallback': 'Claude fell back to standard mode; run codexpool doctor.'}.get(s['running_mode'],
                  'Claude opened; could not confirm the mode yet (codexpool sienna desktop status).'))

    def command(self, args):
        command = args.desktop_cmd
        if command == 'status':
            s = self.status()
            try:
                c = self.live_credits()
                s.update(credits_ok=c['ok'], credits_problems=c['problems'], accepted_credits=c['paid'])
            except (self.cp.PoolDown, self.cp.ApiError, self.cp.KeyUnavailable, OSError):
                pass
            print(json.dumps(s, indent=2) if getattr(args, 'json', False) else
                  'Configured: ' + s['configured_mode'] + '; running: ' + str(s['running_mode']) +
                  ('; reopen Claude to switch' if s['restart_required'] else '') +
                  '\n' + s['cpa_compatibility_reason'] +
                  '\n' + '\n'.join(s['errors'] + s['credits_problems']))
            return
        if command == 'pooled':
            compatibility = desktop_cpa_compatibility()
            if not compatibility['cpa_compatible']:
                raise DesktopError(compatibility['cpa_compatibility_reason'])
            self.steps = self.cp.Steps(getattr(args, 'dry_run', False))
            self.step('Check the ground')
        self.ground()
        if command in ('pooled', 'claudeai'):
            self.require_only_pool()
        if command == 'reveal':
            self.note('Key-level undo only; legacy backups are not read or copied.')
            if self.fs.directory(self.library) is not None:
                subprocess.run(['open', str(self.library)], check=True)
            return
        app = self.app()
        if version_tuple(app['app_version']) < version_tuple(VERIFIED['app']):
            raise DesktopError('Claude is older than the version the file layout was verified with')
        if version_tuple(app['app_version']) > version_tuple(VERIFIED['app']):
            self.note('! not verified with this app version; doctor watches the app log')
        dry = getattr(args, 'dry_run', False) or command == 'remove' and not getattr(args, 'yes', False)
        relaunch = getattr(args, 'relaunch', False) or command == 'relaunch'
        process = self.process(app['bundle_path'])
        if not process['app_bundle_ok']:
            raise DesktopError('a process named Claude runs from another path, not the validated app')
        if process['app_running'] and not relaunch and not dry:
            raise DesktopError('Claude is running: quit it first, or pass --relaunch')
        if dry:
            self.step('Credits')
            if self.fs.read(self.journal) is not None:
                self.note('[dry-run] pending transaction: ' + json.dumps(self.classify(self.fs.obj(self.journal))))
                return
            credit = self.live_credits() if command == 'pooled' else None
            if credit and not credit['ok']:
                for problem in credit['problems']:
                    self.note('✗ ' + problem)
            if command == 'pooled' and not self.pool_ok():
                self.note('✗ the app would open on a pool that cannot answer: codexpool sienna install')
            self.step('Quit Claude (only after confirmation; dry run does not quit)')
            self.step('Render the Pool entry')
            writes, state, _, _ = self.plan(command, args, app, credit)
            self.step('Journal changed keys (plan only)')
            self.step('Write and set the mode (plan only)')
            self.step('Publish, commit, open (plan only)')
            for role, path, data in writes:
                self.note('[dry-run] ' + role + ': ' + str(digest(self.fs.read(path))) + ' → ' + str(digest(data)))
            if state and command == 'pooled':
                self.note('codexpool would write: ' + json.dumps(state['owned'], sort_keys=True))
            return
        if relaunch:
            self.confirm_relaunch(args)
        # Validate all refusals before quitting. Pending recovery while an app is
        # running refuses here instead of closing the app and then failing.
        with self.fs.lock():
            self.recover(explicit=command == 'rollback', check_only=process['app_running'])
            credit = self.live_credits() if command == 'pooled' else None
            if credit and not credit['ok']:
                raise DesktopError('\n'.join(credit['problems']))
            if command == 'pooled' and not self.pool_ok():
                raise DesktopError('the app would open on a pool that cannot answer: codexpool sienna install')
            writes, state, entry_id, remove_dir = self.plan(command, args, app, credit)
            prepared = self.prepare(command, writes, state, entry_id, remove_dir) if command not in ('rollback', 'relaunch') else None
            planned_state = self.revision(self.state_path)
            planned_journal = self.revision(self.journal)
        quit_attempted = False
        committed = False
        quit_completed = False
        open_attempted = False
        transaction_started = False
        try:
            if relaunch and process['app_running']:
                quit_attempted = True
                self.quit_app(app['bundle_path'])
                quit_completed = True
            if command not in ('rollback', 'relaunch'):
                with self.fs.lock():
                    if self.process(app['bundle_path'])['app_running']:
                        raise DesktopError('Claude is running: quit it first')
                    if self.revision(self.state_path) != planned_state or self.revision(self.journal) != planned_journal:
                        raise DesktopError('desktop.json or the journal changed since planning; retry the command')
                    if command in ('pooled', 'claudeai'):
                        self.require_only_pool()
                    # Repeat CAS preflight after quit: a preference write during quit
                    # is preserved; a change to one of our keys refuses and reopens.
                    if prepared:
                        transaction_started = True
                        self.transaction(command, writes, state, entry_id, remove_dir, prepared=prepared)
                        committed = True
                    else:
                        self.note('✓ nothing to change')
                    self.events.extend(self.closing)
            if relaunch:
                open_attempted = True
                self.open_app(app['bundle_path'])
        except Exception as error:
            if quit_attempted and (quit_completed or not self.process(app['bundle_path'])['app_running']):
                try:
                    # A readiness failure can occur after launch. Stop that process
                    # before restoring keys, without blocking the credit guard.
                    if open_attempted and self.process(app['bundle_path'])['app_running']:
                        self.quit_app(app['bundle_path'])
                    with self.fs.lock():
                        current_journal = self.fs.obj(self.journal)
                        if transaction_started and current_journal.get('id') == prepared['id']:
                            self.recover(explicit=True, force_undo=True)
                        elif prepared and committed and not current_journal and self.fs.obj(self.state_path).get('txn') == prepared['id']:
                            # open may fail after commit: undo those keys as well.
                            prepared['started'] = len(prepared['steps']) - 1
                            self.fs.write(self.journal, encoded(prepared))
                            self.recover(explicit=True, force_undo=True)
                    self.open_app(app['bundle_path'])
                except Exception:
                    self.note('! could not reopen Claude automatically; open it after checking desktop status')
            if isinstance(error, subprocess.SubprocessError):
                raise DesktopError('Claude quit/open command failed or timed out; check desktop status') from None
            raise


def add_parser(claude, fn):
    parser = claude.add_parser('desktop', help='pooled Claude desktop configuration (normal profile untouched)')
    commands = parser.add_subparsers(dest='desktop_cmd', required=True)
    summaries = {
        'status': 'show configured and running desktop modes',
        'pooled': 'configure the desktop app to use the Claude pool',
        'claudeai': 'switch the desktop app back to Claude.ai',
        'remove': 'undo the desktop configuration created by codexpool',
        'rollback': 'recover an interrupted desktop configuration change',
        'reveal': 'show the desktop configuration in Finder',
        'relaunch': 'quit and reopen the desktop app after confirmation',
    }
    for name, summary in summaries.items():
        cmd = commands.add_parser(name, help=summary)
        cmd.set_defaults(claude_fn=fn)
        if name == 'status':
            cmd.add_argument('--json', action='store_true')
        if name in ('pooled', 'claudeai', 'remove'):
            cmd.add_argument('--dry-run', action='store_true')
            cmd.add_argument('--relaunch', action='store_true')
        if name in ('pooled', 'claudeai', 'remove', 'relaunch'):
            cmd.add_argument('--yes', action='store_true', help='confirm removal or relaunch (after a GUI confirmation)')
        if name == 'pooled':
            cmd.add_argument('--models')
            cmd.add_argument('--effort', choices=('low', 'medium', 'high'))
            cmd.add_argument('--1m', dest='one_m', action='store_true', default=None)
            imports = cmd.add_mutually_exclusive_group()
            imports.add_argument('--import', dest='import_history', action='store_true', default=None)
            imports.add_argument('--no-import', dest='import_history', action='store_false')
            cmd.add_argument('--no-tool-search', dest='tool_search', action='store_false', default=None)
            cmd.add_argument('--reclaim', action='store_true')
        if name == 'remove':
            cmd.add_argument('--delete-edited', action='store_true', help='legacy flag; user keys are always preserved')


def doctor(cp, rep):
    rep.section('Desktop')
    fix = 'codexpool sienna desktop status; codexpool sienna desktop reveal'
    with Desktop(cp) as d:
        s = d.status()
        rep.check(s['cpa_compatible'], s['cpa_compatibility_reason'],
                  'use a CPA version that passes the desktop wire test, or build/cpa-native-desktop-3p.patch',
                  warn=s['configured_mode'] != 'pooled')
        for name in s['legacy_backups']:
            rep.check(False, 'legacy ' + name + ' may hold secrets; its contents were not read',
                      'Review ~/.codexpool/state/' + name + ' in Finder and delete that folder yourself when no longer needed.', warn=True)
        if any('another third-party configuration exists' in error for error in s['errors']):
            rep.check(False, d.manual_alternative(), warn=True)
            return
        relevant = s['txn_pending'] or s['ours'] or s['owned_drift'] or s['applied_name'] == 'Pool'
        with contextlib.suppress(DesktopError, OSError):
            relevant = relevant or d.fs.info(d.state_path) is not None or any(e['name'] == 'Pool' for e in d.fs.obj(d.meta_path).get('entries', []))
        if not relevant and not [e for e in s['errors'] if not e.startswith('Claude app not found')]:
            rep.check(True, 'desktop app not pooled (codexpool sienna desktop pooled sets it up)' +
                      ('; an older Claude-3p log folder from ' + s['old_3p_logs'] + ' exists' if s['old_3p_logs'] else ''))
            return
        for error in s['errors']:
            rep.check(False, error, fix)
        rep.check(bool(s['app_version']), 'Claude ' + (s['app_version'] or 'app not found'), 'install it from claude.ai/download')
        for name, seen in s['seen'].items():
            verified = VERIFIED[name]
            if seen and seen != verified:
                rep.check(False, name + ' differs from verified (' + seen + ' vs ' + verified + ')',
                          'run the desktop wire capture test; re-verify the app layout',
                          warn=name != 'app' or version_tuple(seen) >= version_tuple(verified))
        rep.check(not s['txn_pending'], 'an interrupted desktop change is pending' if s['txn_pending'] else 'no interrupted change',
                  'codexpool sienna desktop rollback')
        if s['txn_classes']:
            rep.check(True, ' · '.join(k + ': ' + v for k, v in s['txn_classes'].items()))
        try:
            d.ground()
            rep.check(True, 'Claude-3p and state are real owned folders; no redirect or managed profile')
        except (DesktopError, OSError) as e:
            rep.check(False, str(e), fix)
        rep.check(s['configured_mode'] not in ('unknown', 'other'), 'configured: ' + s['configured_mode'] +
                  ('; the applied configuration hides the Claude.ai sign-in' if s['chooser_disabled'] else ''),
                  'Developer → Configure Third-Party Inference… → apply another configuration', warn=s['configured_mode'] == 'other')
        rep.check(s['mode_explicit'], 'deploymentMode is explicit' if s['mode_explicit'] else
                  'a provider is applied with no deploymentMode: the app will open in 3p', 'codexpool sienna desktop claudeai')
        rep.check(s['app_bundle_ok'], 'running executable matches the validated app', fix)
        rep.check(s['running_mode'] != 'fallback', 'Claude not running' if not s['app_running'] else 'running: ' + str(s['running_mode']), fix)
        rep.check(not s['restart_required'], 'reopen Claude to switch' if s['restart_required'] else 'no restart pending',
                  'codexpool sienna desktop relaunch', warn=True)
        rep.check(s['current'] or s['configured_mode'] == 'none', 'Pool entry current' if s['current'] else 'Pool entry out of date or edited',
                  'codexpool sienna desktop pooled --reclaim (only if you want to replace edited owned fields)', warn=True)
        for key in s['conflicts']:
            rep.check(False, 'Pool carries ' + key + ': ' + REFUSED[key], 'remove the key in the app configuration window')
        if not s['conflicts']:
            rep.check(True, 'no conflicting keys in Pool')
        rep.check(s['placeholder_ok'], 'Pool carries only the placeholder key' if s['placeholder_ok'] else
                  'Pool carries unnecessary sensitive material in a plain-text file; the pool replaces it upstream',
                  'codexpool sienna desktop pooled --reclaim', warn=True)
        rep.check(s['models_ok'], 'model discovery off; full ids; maxEffort high on every model', 'codexpool sienna desktop pooled')
        if s['configured_mode'] == 'pooled':
            rep.check(s['port_ok'] and d.pool_ok(), 'the Claude pool answers and Pool points at it',
                      'codexpool sienna install, or codexpool sienna desktop claudeai')
        try:
            c = d.live_credits()
            rep.check(c['ok'], 'fresh credits policies acceptable' if c['ok'] else '; '.join(c['problems']),
                      'wait for a fresh guard poll or turn credits off at claude.ai', warn=s['configured_mode'] != 'pooled')
            for paid in c['paid']:
                rep.check(False, paid['label'] + ': capped last-resort paid use accepted ($' + str(paid['cap']) + ')', warn=True)
        except (cp.PoolDown, cp.ApiError, cp.KeyUnavailable, OSError):
            rep.check(False, 'no live eligible-account credits check', 'run codexpool sienna status', warn=s['configured_mode'] != 'pooled')
        started = cp.parse_time(s['pool_started_at'])
        unseen = s['running_mode'] == 'pooled' and not s['pool_seen_desktop_at'] and started and (cp.now_utc() - started).total_seconds() > 600
        rep.check(not unseen, 'pool saw desktop at ' + s['pool_seen_desktop_at'] if s['pool_seen_desktop_at'] else
                  'pool has not seen a desktop request since the pool started' if started else 'pool start time unknown; request evidence unavailable', warn=True)
        rep.check(not s['engine_identity_rejections_24h'], 'desktop engine identity rejections in 24 h: ' + str(s['engine_identity_rejections_24h']),
                  'the engine identity changed; update codexpool')
        rep.check(True, 'expected Electron rejections in 24 h: ' + str(s['electron_rejections_24h']))
        rep.check(True, 'desktop token-count rejections in 24 h: ' + str(s['token_count_rejections_24h']))
        rep.check(True, 'token counting from pooled desktop is estimated; wire fixtures are synthesised until E2 capture')
        rep.check(True, 'helper model from desktop: unknown until E2 step 4(e)')
        rep.check(True, 'key-level undo journal; no file backups')
        rep.check(True, 'Pool is the only managed third-party configuration; no policy inheritance')



def desktop_recorded():
    """Ownership remains relevant even after the pool launch agent is gone."""
    return ((cp.STATE / 'desktop.json').exists() or (cp.STATE / 'desktop-txn.json').exists() or
            any(cp.STATE.glob('desktop.json.removed-*')) or any(cp.STATE.glob('desktop-backup-*')))


def desktop_backend():
    return Desktop(cp)


def desktop_status(seats=None, guard=None, meta=None, live_version=None):
    with desktop_backend() as desktop:
        return desktop.status(seats, guard, meta, probe=False, live_version=live_version)


def cmd_desktop(args):
    with desktop_backend() as desktop:
        try:
            desktop.command(args)
        except (DesktopError, OSError, subprocess.SubprocessError, cp.PoolDown, cp.ApiError, cp.KeyUnavailable) as e:
            sys.exit('✗ ' + str(e))
        finally:
            for line in desktop.events:
                print(line)
