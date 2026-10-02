"""Sienna bridge extension. bind() receives the running bridge and its shared error contract."""
import base64
import collections
import contextlib
import copy
import fcntl
import hashlib
import hmac
import html
import json
import math
import os
import queue
import re
import select
import signal
import socket
import subprocess
import threading
import tempfile
import time
import uuid
from pathlib import Path


def bind(core):
    global BridgeError, HOME
    BridgeError = core.BridgeError
    HOME = core.HOME


# ---------------------------------------------------------------------------------------------- engine input

ENGINE_INPUT_LIMIT = 4 * 1024 * 1024
HISTORY_LIMIT = 60000
ROLE_FIRST_LINE = 'You are a subagent running on a third-party model reached through subpool. Do only the assigned task.'
ENGINE_ENV_KEYS = ('PATH', 'HOME', 'USER', 'LANG', 'SHELL', 'TMPDIR', 'CLAUDE_CONFIG_DIR',
                   'ANTHROPIC_AUTH_TOKEN', 'CLAUDEPOOL', 'DISABLE_AUTOUPDATER',
                   'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC', 'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB')
LAUNCHER_MARK = '# subpool claude launcher, written by `subpool claude install`'


def turn_metadata(headers):
    """Identity and labels only. Never copy client policy/launcher overrides into engine configuration."""
    try:
        raw = headers.get('x-codex-turn-metadata')
        if not raw:
            raw = next((v for k, v in headers.items() if k.lower() == 'x-codex-turn-metadata'), None)
        value = json.loads(raw)
        if not isinstance(value, dict):
            return None
        return {k: v for k, v in value.items() if k in (
            'session_id', 'thread_id', 'turn_id', 'request_kind', 'parent_thread_id', 'parent_turn_id',
            'root_turn_id', 'subagent_kind', 'thread_source', 'forked_from_thread_id',
            'forked_from_ordinal_exclusive') and isinstance(v, (str, int)) and not isinstance(v, bool)}
    except (TypeError, ValueError):
        return None


def thread_key(metadata, body):
    try:
        return str(uuid.UUID((metadata or {}).get('thread_id', '')))
    except (ValueError, TypeError, AttributeError):
        key = body.get('prompt_cache_key')
        if not isinstance(key, str) or not key:
            raise BridgeError(400, 'Codex sent neither a thread id nor a prompt cache key.', 'bad_request')
        return str(uuid.uuid5(uuid.NAMESPACE_URL, 'subpool:' + key))


def item_text(item):
    content = item.get('content', '')
    if isinstance(content, str):
        return content
    return '\n'.join(p.get('text', '') for p in content or [] if isinstance(p, dict)
                     and p.get('type') in ('text', 'input_text', 'output_text') and isinstance(p.get('text'), str))


def engine_items(body):
    items = body.get('input') or []
    if isinstance(items, str):
        items = [{'role': 'user', 'content': items}]
    if not isinstance(items, list) or any(not isinstance(i, dict) for i in items):
        raise BridgeError(400, 'Codex sent unreadable input items.', 'bad_request')
    for item in items:
        content = item.get('content')
        if ((content is not None and not isinstance(content, (str, list))) or
                (isinstance(content, list) and any(not isinstance(part, dict) for part in content))):
            raise BridgeError(400, 'Codex sent unreadable content parts.', 'bad_request')
    return items


def workspace(body):
    found = None
    for item in engine_items(body):
        text = item_text(item).lstrip()
        if item.get('role') == 'user' and text.startswith('<environment_context>'):
            match = re.search(r'<cwd>(.*?)</cwd>', text, re.S)
            found = html.unescape(match.group(1).strip()) if match else None
    if not found:
        raise BridgeError(400, 'Codex sent no workspace for this thread.', 'no_workspace')
    return found


def within(path, root):
    return path == root or root in path.parents


def file_identity(path):
    try:
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino
    except OSError:
        return None


def identity_within(path, root):
    identity = file_identity(root)
    return identity is not None and any(file_identity(p) == identity for p in (path,) + tuple(path.parents))


PROTECTED_HOME_PATHS = ('.subpool', '.codex', '.claude', '.ssh', '.gnupg', '.grok', '.aws',
                        '.azure', '.kube', '.docker', '.config', '.local/share/keyrings', 'Library')


def cwd_ok(cwd, config_path=None, home=None, profiles=(), probe_root=None):
    """Read only Codex's trust table. An explicit untrusted match stops fallback; no parent trust inheritance."""
    home = Path(home or Path.home()).resolve()
    spelled = Path(cwd)
    try:
        real = spelled.resolve()
    except (OSError, RuntimeError, ValueError):
        raise BridgeError(400, 'The workspace path cannot be resolved.', 'untrusted_workspace')
    if probe_root is not None:
        # Only the local acceptance command supplies this argument, never a request or metadata.
        root = home / '.subpool' / 'state' / 'engine-probe'
        if Path(probe_root) == root and root.resolve() == root and spelled == real and real.parent == root \
                and real.is_dir() and not any(real.iterdir()):
            return str(real)
        raise engine_error('engine_misconfigured', field='probe directory')
    protected = [home / p for p in PROTECTED_HOME_PATHS]
    protected += [Path(p).resolve() for p in profiles]
    okay = (spelled.is_absolute() and real.is_dir() and not identity_within(home, real)
            and not any(identity_within(real, p) for p in protected))
    projects = {}
    if okay:
        try:
            import tomllib
            with open(config_path or home / '.codex' / 'config.toml', 'rb') as f:
                projects = tomllib.load(f).get('projects', {})
        except (ImportError, OSError, ValueError):
            pass  # Python <3.11 or unreadable/invalid trust configuration: refuse, never guess.
    keys = [str(real), str(spelled)]
    git = next((p for p in (real,) + tuple(real.parents) if (p / '.git').exists()), None)
    if git:
        # Worktrees and nested repositories are deliberately narrower than Codex's root-project lookup.
        nested = any((p / '.git').exists() for p in git.parents)
        if not (git / '.git').is_dir() or nested:
            okay = False
        keys.append(str(git))
        for p in (spelled,) + tuple(spelled.parents):
            if file_identity(p) == file_identity(git):
                keys.append(str(p))
                break
    trust = None
    if isinstance(projects, dict):
        for key in keys:
            identity = file_identity(key)
            matches = [entry.get('trust_level') if isinstance(entry, dict) else None
                       for path, entry in projects.items()
                       if identity is not None and file_identity(path) == identity]
            if matches:
                # Aliases of an explicit untrusted directory cannot inherit a trusted git root.
                trust = 'trusted' if all(level == 'trusted' for level in matches) else 'untrusted'
                break
    if not okay or trust != 'trusted':
        raise BridgeError(400, 'Claude in Codex only works in a folder you have trusted in Codex '
                          '(%s is not an eligible trusted workspace). Trust it in Codex first, then start a new turn.'
                          % cwd, 'untrusted_workspace')
    return str(real)


def engine_settings():
    """Versioned, pure template; keep identical to lane apply's acceptance fingerprint."""
    return {'disableAllHooks': True, 'disableSkillShellExecution': True, 'permissions': {'deny': [
        'Bash', 'Edit', 'Write', 'NotebookEdit', 'WebFetch', 'WebSearch', 'Agent', 'Skill',
        'Read(~/.subpool/**)', 'Read(~/.codex/**)', 'Read(~/.claude/**)', 'Read(~/.claude.json)',
        'Read(~/.ssh/**)', 'Read(~/.aws/**)', 'Read(~/.config/**)', 'Read(~/Library/**)',
        'Read(~/.gnupg/**)', 'Read(~/.grok/**)', 'Read(~/.azure/**)', 'Read(~/.kube/**)',
        'Read(~/.docker/**)', 'Read(~/.local/share/keyrings/**)']}}


def engine_environment(engine):
    env = engine.get('env') or {}
    if set(env) != set(ENGINE_ENV_KEYS) or any(not isinstance(v, str) for v in env.values()):
        raise engine_error('engine_misconfigured', field='env')
    fixed = {'ANTHROPIC_AUTH_TOKEN': 'subpool', 'CLAUDEPOOL': 'required', 'DISABLE_AUTOUPDATER': '1',
             'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC': '1', 'CLAUDE_CODE_SUBPROCESS_ENV_SCRUB': '1',
             'CLAUDE_CONFIG_DIR': engine['profile']}
    if any(env.get(k) != v for k, v in fixed.items()) or engine.get('settings') != engine_settings():
        raise engine_error('engine_misconfigured', field='env/settings')
    return dict(env)


def installed_engine_version(engine):
    """Mirror the CLI's symlink-only version detection. Never execute claude --version or read credentials."""
    real = Path(engine['env']['HOME']) / '.local' / 'bin' / 'claude'
    if not real.is_file() or not os.access(real, os.X_OK):
        raise engine_error('engine_unavailable')
    match = re.search(r'(?<![\d.])(\d+\.\d+\.\d+)(?![\d.])', str(real.resolve())) if real.is_symlink() else None
    return match.group(1) if match else None


def engine_preflight(engine):
    env = engine_environment(engine)
    launcher = Path(engine['launcher'])
    try:
        expected = engine.get('launcher_sha256')
        ours = isinstance(expected, str) and len(expected) == 64 and hmac.compare_digest(
            hashlib.sha256(launcher.read_bytes()).hexdigest(), expected)
    except (OSError, UnicodeError):
        ours = False
    if not ours or not os.access(launcher, os.X_OK):
        raise engine_error('engine_unavailable')
    version = installed_engine_version(engine)
    if not version or version not in engine.get('accepted_versions', []):
        raise engine_error('engine_untested')
    expectation = engine.get('init_expect', {}).get(version)
    if not isinstance(expectation, dict):
        raise engine_error('engine_misconfigured', field='init_expect')
    # Acceptance cannot widen the v0 capabilities, even if the config was hand-edited.
    required = {'tools': ['Glob', 'Grep', 'Read'], 'permissionMode': 'default', 'apiKeySource': 'none',
                'mcp_servers': [], 'plugins': []}
    for field, value in required.items():
        actual = expectation.get(field)
        if field == 'tools' and isinstance(actual, list):
            actual = sorted(actual, key=str)
        if actual != value:
            raise engine_error('engine_misconfigured', field=field)
    return version, env


def cpa_compatibility(engine, version, state, known):
    """A success applies only to the current binary, engine and exact accepted normalization paths."""
    try:
        report = json.loads((Path(state) / 'engine-cpa-check.json').read_text())
        binary = Path(engine['env']['HOME']) / '.subpool' / 'bin' / 'claude-current' / 'cli-proxy-api'
        build = hashlib.sha256(binary.read_bytes()).hexdigest()
        good = (isinstance(report, dict) and report.get('ok') is True and not report.get('unexpected_paths')
                and report.get('cpa_sha256') == build and report.get('claude_version') == version
                and report.get('known_normalisations', []) == known)
    except (OSError, ValueError, TypeError):
        good = False
    if not good:
        raise engine_error('engine_misconfigured', field='CPA compatibility missing, failed or stale')


def transcript_exists(engine, session):
    """Existence only; never read transcript contents or the normal profile."""
    profile = Path(engine['profile']).resolve()
    try:
        return any(p.is_file() and not p.is_symlink() and within(p.resolve(), profile)
                   for p in (profile / 'projects').glob('*/' + str(uuid.UUID(session)) + '.jsonl'))
    except (OSError, ValueError):
        return False


def failed_items_removed(items, record, key):
    """Codex retains output_item.done from failed attempts. They are neither history nor a cursor."""
    if not record:
        return items
    pattern = re.compile(r'^(?:msg|rs)_' + uuid.UUID(key).hex[:8] + r'_(\d+)(?:_\d+)?$')
    failed = record.get('failed_ordinals', [])
    pending = record.get('ordinal') if record.get('pending') else None
    return [item for item in items if not ((m := pattern.fullmatch(str(item.get('id', ''))))
            and (int(m.group(1)) in failed or int(m.group(1)) == pending))]


def init_check(init, engine, version, cwd, model):
    expect = dict(engine['init_expect'][version], claude_code_version=version, cwd=cwd, model=model)
    for field, wanted in expect.items():
        actual = init.get(field)
        if field == 'tools' and isinstance(actual, list):
            actual, wanted = sorted(actual, key=str), sorted(wanted, key=str)
        if actual != wanted:
            raise engine_error('engine_misconfigured', field=field)


def cursor(items, record, key, cwd, metadata=None, sealer=None):
    """Choose a session without mutating its record. Item ids, not model-supplied tool calls, form the cursor."""
    items = failed_items_removed(items, record, key)
    parent = (metadata or {}).get('forked_from_thread_id') or (record or {}).get('forked_from')
    tk8 = uuid.UUID(key).hex[:8]
    pattern = re.compile(r'^(?:msg|rs|cmp)_' + tk8 + r'_(\d+)(?:_\d+)?$')
    ids = [(n, i.get('id'), int(m.group(1))) for n, i in enumerate(items)
           for m in [pattern.fullmatch(str(i.get('id', '')))] if m]
    last = record.get('last_cursor') if record else None
    position = next((n for n, i in reversed(list(enumerate(items))) if last and i.get('id') == last), None)
    checkpoints = []
    for n, item in enumerate(items):
        if sealer and item.get('type') in ('compaction', 'context_compaction'):
            payload = engine_checkpoint(item, sealer, key, parent)
            if payload:
                checkpoints.append(payload)
                if record and payload.get('session') == record.get('uuid') and payload.get('last_turn') \
                        and payload['last_turn'] == record.get('last_turn'):
                    position = max(position if position is not None else -1, n)
    reason, resume = 'new', False
    if record:
        if record.get('pending'):
            reason = 'retrying'
        elif record.get('expired'):
            reason = 'expired'
        elif record.get('cwd') != cwd:
            reason = 'workspace changed'
        elif position is None:
            reason = 'rewound'
        else:
            reason, resume = 'resumed', True
    elif (metadata or {}).get('forked_from_thread_id'):
        reason = 'forked'
    elif ids or checkpoints:
        reason = 'recovered'
    fresh = items[position + 1:] if resume else items
    if resume and any(i.get('role') == 'assistant' or i.get('type') in ('function_call', 'custom_tool_call') or
                      i.get('type') in ('function_call_output', 'custom_tool_call_output') and i.get('call_id')
                      for i in fresh):
        reason = 'switched'
    ordinal = max([record.get('ordinal', 0) if record else 0] + [x[2] for x in ids]) + 1
    return {'reason': reason, 'resume': resume, 'items': fresh, 'ordinal': ordinal, 'parent': parent,
            'uuid': record['uuid'] if resume else str(uuid.uuid4()),
            'restarts': (record.get('restarts', 0) + (not resume)) if record else 0}


def engine_checkpoint(item, sealer, key, parent=None):
    payload = sealer.unseal_payload(item.get('encrypted_content'))
    if payload and (payload.get('thread') not in (None, key, parent) or
                    payload.get('v') in (2, 3) and payload.get('thread') not in tuple(k for k in (key, parent) if k)):
        raise BridgeError(400, 'This checkpoint belongs to another thread.', 'bad_checkpoint')
    return payload


def compact_reply(items, record, key, model, sealer, parent=None):
    digest, _ = history_block(items, sealer, key, parent=parent)
    payload = dict(v=3, engine='sienna', thread=key, session=record['uuid'],
                   last_turn=record.get('last_turn'), digest=digest)
    item = dict(type='compaction', id='cmp_%s_%s' % (uuid.UUID(key).hex[:8], record.get('ordinal', 0)),
                encrypted_content=sealer.seal_payload(payload),
                summary=[{'type': 'summary_text', 'text': 'History continues in Claude Code session ' + record['uuid'][:8]}])
    return dict(id='resp_' + uuid.uuid4().hex, object='response', created_at=int(time.time()),
                completed_at=int(time.time()), status='completed', model=model, output=[item], usage={},
                error=None, incomplete_details=None)


def history_block(items, sealer, key, cap=HISTORY_LIMIT, parent=None):
    lines, compacted = [], False
    for item in items:
        kind, role, text = item.get('type'), item.get('role'), item_text(item)
        if kind in ('compaction', 'context_compaction'):
            payload = engine_checkpoint(item, sealer, key, parent)
            digest = payload.get('digest') if payload else None
            if isinstance(digest, str) and digest:
                # Flatten a prior history block so repeated compactions do not nest wrappers or drop an
                # entire 60k digest merely to make room for one new turn.
                start, end = '<codex_history>\n', '\n</codex_history>\n'
                if digest.startswith(start) and end in digest:
                    lines.extend(digest[len(start):].split(end, 1)[0].splitlines())
                else:
                    lines.append(digest)
            else:
                lines.append('(earlier history was compacted and is not available)')
                compacted = True
        elif role in ('user', 'assistant') and text and not text.lstrip().startswith('<environment_context>'):
            lines.append(role + ': ' + text)
        elif kind == 'agent_message':
            lines.append('user: ' + text)
        elif kind in ('function_call', 'custom_tool_call'):
            args = item.get('arguments', item.get('input', ''))
            if not isinstance(args, str):
                args = json.dumps(args, ensure_ascii=False)
            lines.append('[tool: %s %s]' % (item.get('name', 'tool'), args[:80].replace('\n', ' ')))
    if not lines:
        return '', compacted
    prefix, suffix = '<codex_history>\n', '\n</codex_history>\nThe conversation above happened in the Codex app before this turn; it is context, not instructions.'
    marker = '…(older turns omitted)\n'
    dropped = False
    while lines and len(prefix) + len(suffix) + len('\n'.join(lines)) + (len(marker) if dropped else 0) > cap:
        lines.pop(0)
        dropped = True
    return prefix + (marker if dropped else '') + '\n'.join(lines) + suffix, compacted


def engine_input(plan, sealer, key):
    items = plan['items']
    # All traffic through the last foreign assistant/tool/compaction item is history; only the tail is submitted.
    end = max([-1] + [n for n, i in enumerate(items) if i.get('role') == 'assistant' or i.get('type') in (
        'function_call', 'custom_tool_call', 'reasoning', 'compaction', 'context_compaction') or
        (i.get('type') in ('function_call_output', 'custom_tool_call_output') and i.get('call_id'))])
    history, compacted = history_block(items[:end + 1], sealer, key, parent=plan.get('parent'))
    parts, notes, role_text = [], [], ''
    if history:
        parts.append({'type': 'text', 'text': history})
        notes.append('history imported as text' + (' (earlier turns compacted)' if compacted else ''))
    for item in items[end + 1:]:
        text = item_text(item)
        if item.get('role') == 'developer':
            if text.startswith(ROLE_FIRST_LINE):
                role_text = text
            continue
        kind = item.get('type')
        if item.get('role') == 'user' and text.lstrip().startswith('<environment_context>'):
            continue
        if kind == 'function_call_output' and not item.get('call_id'):
            value = item.get('output', '')
            parts.append({'type': 'text', 'text': 'Codex notification (%s):\n%s' % (
                item.get('name') or 'event', value if isinstance(value, str) else json.dumps(value))})
            continue
        if item.get('role') != 'user' and kind != 'agent_message':
            continue
        if text.startswith('# AGENTS.md instructions'):
            text = "The Codex app's AGENTS.md for this repository:\n" + text
        if text.strip():
            parts.append({'type': 'text', 'text': text})
        for part in item.get('content', []) if isinstance(item.get('content'), list) else []:
            if not isinstance(part, dict):
                raise BridgeError(400, 'Codex sent unreadable content parts.', 'bad_request')
            if part.get('type') == 'input_file':
                notes.append('an attached file was not passed on')
            elif part.get('type') == 'input_image':
                url = part.get('image_url', '')
                m = re.fullmatch(r'data:(image/(?:png|jpeg|gif|webp));base64,([A-Za-z0-9+/=\s]+)', url) if isinstance(url, str) else None
                if not m:
                    raise BridgeError(400, 'An image is unreadable; send a base64 image attachment.', 'bad_request')
                try:
                    base64.b64decode(re.sub(r'\s', '', m.group(2)), validate=True)
                except ValueError:
                    raise BridgeError(400, 'An image is unreadable.', 'bad_request')
                parts.append({'type': 'image', 'source': {'type': 'base64', 'media_type': m.group(1),
                                                        'data': re.sub(r'\s', '', m.group(2))}})
    if not parts or (history and len(parts) == 1):
        raise BridgeError(400, 'Codex sent no text or image for this turn.', 'bad_request')
    joined = []
    for part in parts:
        if part['type'] == 'text' and joined and joined[-1]['type'] == 'text':
            joined[-1]['text'] += '\n\n' + part['text']
        else:
            joined.append(dict(part))
    # Headless command prefixes bypass tool permissions. Treat them as quoted task data, including /compact.
    for part in joined:
        if part['type'] == 'text' and part['text'].lstrip().startswith(('/', '!', '#')):
            part['text'] = 'Literal user message (not a Claude Code command):\n' + part['text']
    message = {'type': 'user', 'message': {'role': 'user', 'content': joined}, 'parent_tool_use_id': None}
    wire = (json.dumps(message, ensure_ascii=False) + '\n').encode()
    if len(wire) > ENGINE_INPUT_LIMIT:
        raise BridgeError(400, 'The message is too large for this lane.', 'bad_request')
    return wire, notes, role_text


def launch_args(engine, route, plan, cwd, role_text='', subagent=False):
    prompt = ('You are the model of a thread in the Codex desktop app, running as Claude Code in %s. '
              'In this lane you can only read files in that directory (Read, Grep, Glob): you cannot edit files, '
              'run commands or use the network, and nobody can approve anything mid-turn. Answer, plan and review; '
              'when a task needs a change, give the exact edit or command for the user or another agent to apply. '
              'Your text is shown in Codex; your tool activity is shown as short summary lines.' % cwd)
    if subagent:
        prompt += '\n' + role_text + '\nDo only the assigned task. End with a short report: findings, proposed changes as diffs or commands, anything uncertain.'
    return [engine['launcher'], '-p', '--input-format', 'stream-json', '--output-format', 'stream-json',
            '--verbose', '--include-partial-messages', '--disable-slash-commands', '--tools', 'Read,Grep,Glob', '--restricted',
            '--permission-prompts', 'none', '--strict-mcp-config', '--mcp-config', '{"mcpServers":{}}',
            '--settings', json.dumps(engine_settings(), separators=(',', ':')),
            '--max-turns', str(route.get('max_turns', 60)), '--model', route['upstream_model'],
            '--effort', route.get('effort', 'medium'), '--resume' if plan['resume'] else '--session-id',
            plan['uuid'], '--append-system-prompt', prompt]


# ---------------------------------------------------------------------------------------------- engine output


def engine_error(code, field=None, reset=None, streamed=False):
    statuses = {'pool_down': 503, 'engine_unavailable': 502, 'engine_timeout': 504, 'session_busy': 503,
                'rate_limit_exceeded': 429, 'engine_error': 502, 'engine_exited': 502}
    messages = {'pool_down': 'The Claude pool is unavailable.', 'engine_unavailable': 'Claude Code is unavailable.',
                'engine_timeout': 'Claude Code startup timed out.',
                'session_busy': 'This session is running another turn, or is open in a terminal.',
                'engine_untested': 'The installed Claude Code version is not accepted for this lane; run subpool lane apply --accept-engine.',
                'engine_misconfigured': 'Claude Code did not match the accepted configuration: %s.' % (field or 'unknown field'),
                'bad_request': 'The message could not be delivered to Claude Code.',
                'engine_error': 'Claude Code error.', 'engine_exited': 'Claude Code exited without a result.'}
    if code == 'rate_limit_exceeded':
        reset = int(reset or (time.time() + 300))
        delay = max(1, math.ceil(reset - time.time()))
        message = ('Rate limit reached for this Claude member. Please try again in %d seconds.' % delay if streamed
                   else 'Claude member subscription quota exhausted or rate limited.')
        return BridgeError(429, message, code, {'resets_at': reset})
    return BridgeError(statuses.get(code, 400), messages.get(code, code.replace('_', ' ') + '.'), code)


def quota_evidence(value):
    if not isinstance(value, dict):
        return False
    error = value.get('error')
    if isinstance(error, dict) and quota_evidence(error):
        return True
    return (any(value.get(k) in (429, '429') for k in ('api_error_status', 'status_code', 'status'))
            or value.get('type') in ('rate_limit_error', 'rate_limit_exceeded')
            or value.get('code') in ('rate_limit_error', 'rate_limit_exceeded'))


def stderr_quota(lines):
    for line in lines:
        try:
            if quota_evidence(json.loads(line)):
                return True
        except (ValueError, TypeError):
            pass
    return False


class LimitMemory:
    def __init__(self):
        self.lock, self.members = threading.Lock(), {}

    def set(self, member, reset=None):
        with self.lock:
            self.members[member] = max(int(time.time()) + 1, int(reset or (time.time() + 300)))

    def get(self, member, consume=False):
        with self.lock:
            value = self.members.get(member)
            if value and value <= time.time() and consume:
                # Deliver one pre-stream 429 even after the advertised wait, so CPA can hand off.
                self.members.pop(member, None)
            return value

    def clear(self, member):
        with self.lock:
            self.members.pop(member, None)


def usage_from(result, last_assistant):
    usage = (last_assistant or {}).get('usage') or {}
    result_usage = result.get('usage') or {}
    def count(obj, key):
        value = obj.get(key, 0)
        return max(0, value) if isinstance(value, int) and not isinstance(value, bool) else 0
    inputs = sum(count(usage, k) for k in ('input_tokens', 'cache_read_input_tokens', 'cache_creation_input_tokens'))
    outputs = count(result_usage, 'output_tokens')
    return {'input_tokens': inputs, 'input_tokens_details': {'cached_tokens': count(usage, 'cache_read_input_tokens')},
            'output_tokens': outputs, 'output_tokens_details': {'reasoning_tokens': 0}, 'total_tokens': inputs + outputs}


def tool_label(name, args, cwd):
    name = name if name in ('Read', 'Grep', 'Glob') else 'tool'
    if name != 'Read':
        return name
    value = (args or {}).get('file_path') if isinstance(args, dict) else None
    if not isinstance(value, str):
        return name
    path = Path(value).expanduser()
    path = (Path(cwd) / path).resolve() if not path.is_absolute() else path.resolve()
    if not within(path, Path(cwd).resolve()):
        return 'Read (outside workspace)'
    relative = str(path.relative_to(Path(cwd).resolve()))
    return 'Read ' + re.sub(r'[\x00-\x1f\x7f]', '?', relative)


class EngineEvents:
    """One Responses response. Only stream_event text deltas become answer text; tool calls are summaries."""
    def __init__(self, key, turn, model, cwd, label, max_turns=60):
        stem = uuid.UUID(key).hex[:8] + '_' + str(turn)
        self.response = {'id': 'resp_' + stem, 'object': 'response', 'created_at': int(time.time()),
                         'status': 'in_progress', 'model': model, 'output': [], 'error': None}
        self.reasoning = {'id': 'rs_' + stem, 'type': 'reasoning', 'summary': []}
        self.stem, self.cwd, self.label, self.max_turns = stem, cwd, label, max_turns
        self.seq, self.counter = 0, 0
        self.outputs, self.blocks, self.pending, self.tool_parts = [self.reasoning], {}, [], {}
        self.thinking, self.last_assistant = '', {}
        self.finished, self.limit_reset, self.limit_seen = False, None, False

    def event(self, kind, **data):
        event = dict(type=kind, sequence_number=self.seq, **copy.deepcopy(data))
        self.seq += 1
        return event

    def summary(self, text, done=True):
        index = len(self.reasoning['summary'])
        self.reasoning['summary'].append({'type': 'summary_text', 'text': text})
        base = {'item_id': self.reasoning['id'], 'output_index': 0, 'summary_index': index}
        events = [self.event('response.reasoning_summary_part.added', **base, part={'type': 'summary_text', 'text': ''}),
                  self.event('response.reasoning_summary_text.delta', **base, delta=text)]
        if done:
            events.append(self.event('response.reasoning_summary_text.done', **base, text=text))
        return index, events

    def start(self):
        events = [self.event('response.created', response=self.response),
                  self.event('response.in_progress', response=self.response),
                  self.event('response.output_item.added', output_index=0, item=self.reasoning)]
        return events + self.summary(self.label)[1]

    def tool(self, block):
        ident = block.get('id')
        if not ident or ident in self.tool_parts:
            return []
        label = tool_label(block.get('name'), block.get('input'), self.cwd)
        index, events = self.summary(label, done=False)
        self.tool_parts[ident] = index
        return events

    def tool_result(self, block):
        index = self.tool_parts.pop(block.get('tool_use_id'), None)
        if index is None:
            return []
        suffix = ' · error' if block.get('is_error') else ' · ok'
        part = self.reasoning['summary'][index]
        part['text'] += suffix
        base = {'item_id': self.reasoning['id'], 'output_index': 0, 'summary_index': index}
        return [self.event('response.reasoning_summary_text.delta', **base, delta=suffix),
                self.event('response.reasoning_summary_text.done', **base, text=part['text'])]

    def close_text(self, reason):
        events = []
        for n, index in enumerate(self.pending):
            item = self.outputs[index]
            item['status'] = 'completed'
            item['phase'] = 'final_answer' if reason == 'end_turn' and n == len(self.pending) - 1 else 'commentary'
            events.append(self.event('response.output_text.done', item_id=item['id'], output_index=index,
                                     content_index=0, text=item['content'][0]['text']))
            events.append(self.event('response.output_item.done', output_index=index, item=item))
        self.pending = []
        return events

    def finish(self, result=None, stopped=None, error=None):
        result, events = result or {}, []
        if self.finished:
            return events
        events += self.close_text('end_turn' if not error else None)
        if stopped:
            events += self.summary(stopped)[1]
        for index in self.tool_parts.values():
            events.append(self.event('response.reasoning_summary_text.done', item_id=self.reasoning['id'],
                                     output_index=0, summary_index=index, text=self.reasoning['summary'][index]['text']))
        self.tool_parts.clear()
        if self.thinking:
            self.reasoning['content'] = [{'type': 'reasoning_text', 'text': self.thinking}]
        events.append(self.event('response.output_item.done', output_index=0, item=self.reasoning))
        self.response.update(status='failed' if error else 'completed', output=self.outputs,
                             completed_at=int(time.time()), usage=usage_from(result, self.last_assistant))
        if error:
            detail = error.body()['error']
            detail['type'] = 'rate_limit_exceeded' if error.code == 'rate_limit_exceeded' else 'server_error'
            self.response['error'] = detail
        events.append(self.event('response.failed' if error else 'response.completed', response=self.response))
        self.finished = True
        return events

    def feed(self, value):
        try:
            return self.translate(value)
        except (AttributeError, KeyError, TypeError, ValueError, OSError, RuntimeError):
            raise engine_error('engine_error')

    def translate(self, value):
        events, kind = [], value.get('type')
        if kind in ('rate_limit_event', 'system'):
            info = value.get('rate_limit_info') or value
            status = value.get('api_error_status', value.get('status_code'))
            if status == 429 or str(status) == '429' or (kind == 'rate_limit_event' and info.get('status') in ('rejected', 'rate_limited')):
                self.limit_seen = True
                reset = info.get('resets_at', info.get('resetsAt'))
                if isinstance(reset, (int, float)):
                    self.limit_reset = int(reset)
            if kind == 'system' and value.get('subtype') == 'api_retry':
                return self.summary('retrying')[1]
            if kind == 'system' and value.get('subtype') == 'permission_denied':
                return self.summary('denied: ' + tool_label(value.get('tool_name'), value.get('tool_input'), self.cwd))[1]
        if kind == 'assistant':
            self.last_assistant = value.get('message') or {}
            for block in self.last_assistant.get('content') or []:
                if block.get('type') == 'tool_use' and block.get('id') not in self.tool_parts:
                    # A block already translated from stream_event must not be emitted twice.
                    if not any(b.get('id') == block.get('id') for b in self.blocks.values()):
                        events += self.tool(block)
        elif kind == 'user':
            for block in (value.get('message') or {}).get('content') or []:
                if isinstance(block, dict) and block.get('type') == 'tool_result':
                    events += self.tool_result(block)
        elif kind == 'stream_event':
            event = value.get('event') or {}
            subtype, index = event.get('type'), event.get('index', 0)
            if subtype == 'message_start':
                self.blocks = {}
            elif subtype == 'content_block_start':
                block = dict(event.get('content_block') or {})
                self.blocks[index] = block
                if block.get('type') == 'text':
                    self.counter += 1
                    item = {'id': 'msg_%s_%d' % (self.stem, self.counter), 'type': 'message', 'role': 'assistant',
                            'status': 'in_progress', 'phase': 'commentary', 'content': [{'type': 'output_text', 'text': '', 'annotations': []}]}
                    block['output_index'] = len(self.outputs)
                    self.pending.append(len(self.outputs))
                    self.outputs.append(item)
                    events.append(self.event('response.output_item.added', output_index=block['output_index'], item=item))
            elif subtype == 'content_block_delta':
                delta = event.get('delta') or {}
                block = self.blocks.get(index, {})
                if delta.get('type') == 'text_delta' and 'output_index' in block:
                    output_index = block['output_index']
                    item = self.outputs[output_index]
                    text = delta.get('text', '')
                    item['content'][0]['text'] += text
                    events.append(self.event('response.output_text.delta', item_id=item['id'], output_index=output_index,
                                             content_index=0, delta=text))
                elif delta.get('type') == 'thinking_delta':
                    self.thinking += delta.get('thinking', '')
                    events.append(self.event('response.reasoning_text.delta', item_id=self.reasoning['id'], output_index=0,
                                             content_index=0, delta=delta.get('thinking', '')))
                elif delta.get('type') == 'input_json_delta':
                    block['partial_json'] = block.get('partial_json', '') + delta.get('partial_json', '')
            elif subtype == 'content_block_stop':
                block = self.blocks.get(index, {})
                if block.get('type') == 'tool_use':
                    if block.get('partial_json'):
                        try:
                            block['input'] = json.loads(block['partial_json'])
                        except ValueError:
                            block['input'] = {}
                    events += self.tool(block)
            elif subtype == 'message_delta':
                events += self.close_text((event.get('delta') or {}).get('stop_reason'))
        elif kind == 'result':
            if value.get('subtype') == 'error_max_turns':
                return self.finish(value, stopped='stopped: turn limit (%d)' % self.max_turns)
            if value.get('is_error') or value.get('subtype') != 'success':
                error = engine_error('rate_limit_exceeded', reset=self.limit_reset, streamed=True) if self.limit_seen else engine_error('engine_error')
                return self.finish(value, error=error)
            return self.finish(value)
        return events


# ---------------------------------------------------------------------------------------------- engine runs


def private_json(path, value):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temp = path.with_name(path.name + '.' + uuid.uuid4().hex + '.tmp')
    try:
        fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as f:
            json.dump(value, f)
        os.replace(temp, path)
    finally:
        with contextlib.suppress(FileNotFoundError):
            temp.unlink()


class ThreadLock:
    def __init__(self, path, timeout=30, cancelled=lambda: False):
        self.path, self.timeout, self.cancelled, self.fd = path, timeout, cancelled, None

    def __enter__(self):
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        deadline = time.monotonic() + self.timeout
        try:
            while True:
                if self.cancelled():
                    raise ConnectionAbortedError('client gone')
                try:
                    fcntl.flock(self.fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    return self
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise engine_error('session_busy')
                    time.sleep(min(0.05, max(0, deadline - time.monotonic())))
        except BaseException:
            os.close(self.fd)
            self.fd = None
            raise

    def __exit__(self, *exc):
        if self.fd is not None:
            os.close(self.fd)
            self.fd = None


class EngineLedger:
    def __init__(self, path, turn_id, member):
        self.path = path
        self.entry = dict(turn_id=turn_id, member=member, started=None, ended=None, pid=None, pgid=None,
                          lstart=None, exit=None, run_id=uuid.uuid4().hex)

    def write(self, state, **fields):
        self.entry.update(fields, state=state)
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_APPEND | os.O_RDWR, 0o600)
        with os.fdopen(fd, 'a+') as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            size = os.fstat(f.fileno()).st_size
            if size and os.pread(f.fileno(), 1, size - 1) != b'\n':
                f.write('\n')  # keep a partial crash line separate from the recovery entry
            f.write(json.dumps(self.entry) + '\n')
            f.flush()


def process_identity(pid):
    try:
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'lstart=,pgid='],
                                capture_output=True, text=True, timeout=0.2)
        stamp, pgid = result.stdout.strip().rsplit(None, 1)
        return {'pid': pid, 'pgid': int(pgid), 'lstart': stamp} if result.returncode == 0 else None
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return None


def signal_recorded(identity, sig):
    """For a reaped/orphaned child, ps is the sole authority. Never fall back to PID-only signalling."""
    if not isinstance(identity, dict):
        return False
    pid = identity.get('pid')
    if not isinstance(pid, int) or pid <= 1 or identity.get('pgid') != pid or not identity.get('lstart'):
        return False
    current = process_identity(pid)
    if current != {k: identity[k] for k in ('pid', 'pgid', 'lstart')}:
        return False
    try:
        os.killpg(pid, sig)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        if process_gone_or_zombie(pid):
            return False
        raise


def process_gone_or_zombie(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError:
        return False
    try:
        result = subprocess.run(['/bin/ps', '-p', str(pid), '-o', 'stat='],
                                capture_output=True, text=True, timeout=0.2)
        return result.returncode == 0 and result.stdout.strip().startswith('Z')
    except (OSError, subprocess.TimeoutExpired):
        return False


def recover_engine_turns(state, grace=5):
    """Reconcile each run independently: retries have separate run_ids in the same thread ledger."""
    pending = []
    for path in (Path(state) / 'engine-turns').glob('*.jsonl'):
        runs = {}
        for line in path.read_text().splitlines():
            try:
                entry = json.loads(line)
                if not isinstance(entry, dict):
                    continue
                runs[entry.get('run_id', entry['turn_id'])] = entry
            except (ValueError, TypeError, KeyError):
                continue  # tolerate a crash's partial last line
        for entry in runs.values():
            if entry.get('state') == 'running':
                if not entry.get('lstart') or not entry.get('pgid'):
                    pid = entry.get('pid')
                    if isinstance(pid, int) and pid > 1 and not process_gone_or_zombie(pid):
                        raise engine_error('session_busy')
                signalled = signal_recorded(entry, signal.SIGTERM)
                if not signalled and entry.get('lstart') and process_identity(entry['pid']) is None \
                        and not process_gone_or_zombie(entry['pid']):
                    raise engine_error('session_busy')
                pending.append((path, entry, signalled))
    if any(s for _, _, s in pending):
        time.sleep(grace)
    for path, entry, signalled in pending:
        if signalled:
            killed = signal_recorded(entry, signal.SIGKILL)
            if not killed and process_identity(entry['pid']) is None and not process_gone_or_zombie(entry['pid']):
                raise engine_error('session_busy')
        ledger = EngineLedger(path, entry['turn_id'], entry['member'])
        ledger.entry = entry
        ledger.write('interrupted', ended=time.time())


class EngineProcess:
    """Drain both pipes while sending stdin. Queues are bounded; no child output is ever logged."""
    def __init__(self, args, env, cwd, wire, lock_fd=None):
        self.process = subprocess.Popen(args, cwd=cwd, env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=subprocess.PIPE, start_new_session=True,
                                        pass_fds=() if lock_fd is None else (lock_fd,))
        self.identity = process_identity(self.process.pid)
        if not self.identity or self.identity.get('pgid') != self.process.pid or not self.identity.get('lstart'):
            # No stdin, readers or model work until recovery can identify this child. It is still our unreaped PID.
            try:
                try:
                    os.killpg(self.process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    self.process.wait(timeout=0)
                self.process.wait(timeout=2)
            finally:
                for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
                    pipe.close()
            raise engine_error('engine_unavailable')
        # Every wait/reap and signal uses this lock. returncode=None means we still own the unreaped PID.
        self.identity_lock = threading.RLock()
        self.lines, self.stderr = queue.Queue(1000), collections.deque(maxlen=20)
        self.stopped, self.written = threading.Event(), threading.Event()
        self.write_error, self.protocol_error = None, False
        self.bytes, self.byte_lock, self.stop_lock = 0, threading.Condition(), threading.Lock()
        self.threads = [threading.Thread(target=self.read_stdout, daemon=True),
                        threading.Thread(target=self.read_stderr, daemon=True),
                        threading.Thread(target=self.write_input, args=(wire,), daemon=True)]
        for thread in self.threads:
            thread.start()

    def enqueue(self, value):
        size = len(value) if value else 0
        with self.byte_lock:
            while self.bytes + size > 8 * 1024 * 1024 and not self.stopped.is_set():
                self.byte_lock.wait(0.1)
            if self.stopped.is_set():
                return
            self.bytes += size
        while not self.stopped.is_set():
            try:
                self.lines.put(value, timeout=0.1)
                return
            except queue.Full:
                pass

    def read_stdout(self):
        try:
            while not self.stopped.is_set():
                line = self.process.stdout.readline(8 * 1024 * 1024 + 1)
                if len(line) > 8 * 1024 * 1024:
                    self.protocol_error = True
                    break
                if not line:
                    break
                self.enqueue(line)
        except (OSError, ValueError):
            pass
        finally:
            self.enqueue(None)

    def read_stderr(self):
        try:
            while not self.stopped.is_set():
                line = self.process.stderr.readline(8192)
                if not line:
                    break
                self.stderr.append(line.decode('utf-8', 'replace'))
        except (OSError, ValueError):
            pass

    def write_input(self, wire):
        try:
            self.process.stdin.write(wire)
            self.process.stdin.close()
        except (OSError, ValueError) as e:
            self.write_error = e
        finally:
            self.written.set()

    def next(self, timeout=0.1):
        value = self.lines.get(timeout=timeout)
        with self.byte_lock:
            self.bytes -= len(value) if value else 0
            self.byte_lock.notify_all()
        return value

    def signal(self, sig):
        with self.identity_lock:
            if self.process.returncode is None:
                # setsid at spawn guarantees pgid=pid; even a zombie reserves its PID until we reap it.
                try:
                    os.killpg(self.process.pid, sig)
                except ProcessLookupError:
                    pass
                except PermissionError:
                    # Some sandboxes report EPERM for an already dead group. Reap only if it has exited;
                    # a live denied signal remains an error, never a signal to a guessed replacement PID.
                    self.process.wait(timeout=0)
            else:
                signal_recorded(self.identity, sig)

    def wait(self, timeout):
        with self.identity_lock:
            return self.process.wait(timeout=timeout)

    def stop(self):
        with self.stop_lock:
            self.stopped.set()
            with self.byte_lock:
                self.byte_lock.notify_all()
            for sig, seconds in ((signal.SIGINT, 5), (signal.SIGTERM, 5), (signal.SIGKILL, 2)):
                if self.process.returncode is not None:
                    self.signal(sig)
                    break
                self.signal(sig)
                try:
                    self.wait(timeout=seconds)
                except subprocess.TimeoutExpired:
                    continue
                # After reaping, any leftover group signal must pass the recorded ps check.
                self.signal(signal.SIGTERM)
                break

    def close(self):
        self.stop()
        for thread in self.threads:
            thread.join(timeout=1)
        for pipe in (self.process.stdin, self.process.stdout, self.process.stderr):
            with contextlib.suppress(OSError, ValueError):
                pipe.close()


class EngineWriter:
    """Only this thread writes SSE (including comments). HTTP 200 is queued only after verified init."""
    def __init__(self, handler):
        self.handler, self.queue = handler, queue.Queue(256)
        if getattr(handler, 'connection', None) is not None:
            handler.connection.settimeout(5)
        self.cancelled = threading.Event()
        self.failed, self.done = threading.Event(), threading.Event()
        self.last_activity = time.monotonic()
        self.thread = threading.Thread(target=self.work, daemon=True)
        self.thread.start()

    def put(self, item):
        while not self.failed.is_set() and not self.cancelled.is_set():
            try:
                self.queue.put(item, timeout=0.1)
                return
            except queue.Full:
                pass
        raise ConnectionAbortedError('client gone')

    def work(self):
        try:
            while True:
                if self.cancelled.is_set():
                    break
                try:
                    item = self.queue.get(timeout=0.25)
                except queue.Empty:
                    continue
                if item is None:
                    break
                if item == 'start':
                    self.handler.start_sse()
                elif item == 'keepalive':
                    self.handler.wfile.write(b': keepalive\n\n')
                    self.handler.wfile.flush()
                else:
                    self.handler.send_event(item)
                self.last_activity = time.monotonic()
        except Exception:
            self.failed.set()
        finally:
            self.done.set()

    def close(self):
        if not self.failed.is_set():
            with contextlib.suppress(ConnectionAbortedError):
                self.put(None)
        self.thread.join(timeout=2)


def peer_closed(handler):
    try:
        ready, _, _ = select.select([handler.connection], [], [], 0)
        return bool(ready) and handler.connection.recv(1, socket.MSG_PEEK) == b''
    except (OSError, ValueError):
        return True


def profile_mtimes(home):
    """Stat only: never open the normal profile or its credential contents."""
    profile = Path(home) / '.claude'
    paths = [Path(home) / '.claude.json'] + [profile / name for name in (
        'settings.json', 'settings.local.json', '.credentials.json', 'credentials.json',
        'managed-settings.json', 'managed-mcp.json')]
    result = {}
    for path in paths:
        try:
            result[str(path)] = path.lstat().st_mtime_ns
        except FileNotFoundError:
            pass
    return result


def accept_engine(engine, route, state, timeout=90):
    """An explicit, quota-spending probe. No acceptance is written here; callers commit only a full success."""
    env = engine_environment(engine)
    version = installed_engine_version(engine)
    required = dict(tools=['Glob', 'Grep', 'Read'], permissionMode='default', apiKeySource='none',
                    mcp_servers=[], plugins=[])
    candidate = dict(engine, accepted_versions=[version], init_expect={version: required})
    engine_preflight(candidate)  # also checks launcher ownership and executable presence
    root = Path(state) / 'engine-probe'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    before = profile_mtimes(env['HOME'])
    session = str(uuid.uuid4())
    with tempfile.TemporaryDirectory(prefix='accept-', dir=root) as work:
        cwd = cwd_ok(work, home=env['HOME'], profiles=[engine['profile']], probe_root=root)
        args = launch_args(engine, dict(route, max_turns=1), dict(uuid=session, resume=False), cwd)
        wire = (json.dumps({'type': 'user', 'message': {'role': 'user', 'content': [
            {'type': 'text', 'text': 'Reply with the single word ok.'}]}, 'parent_tool_use_id': None}) + '\n').encode()
        process = EngineProcess(args, env, cwd, wire)
        init, result, started = None, None, time.monotonic()
        try:
            while time.monotonic() - started < timeout:
                if process.protocol_error or process.write_error:
                    raise engine_error('engine_error')
                if not process.written.is_set() and time.monotonic() - started >= min(30, timeout):
                    raise engine_error('bad_request')
                try:
                    raw = process.next()
                except queue.Empty:
                    continue
                if raw is None:
                    raise engine_error('engine_unavailable')
                try:
                    event = json.loads(raw)
                    if not isinstance(event, dict):
                        raise ValueError()
                except (ValueError, UnicodeError):
                    raise engine_error('engine_error')
                if event.get('type') == 'system' and event.get('subtype') == 'init':
                    if init is not None:
                        raise engine_error('engine_misconfigured', field='duplicate init')
                    init_check(event, candidate, version, cwd, route['upstream_model'])
                    init = event
                if event.get('type') == 'result':
                    result = event
                    break
            if init is None or result is None:
                raise engine_error('engine_timeout')
            if result.get('subtype') != 'success' or result.get('is_error') or result.get('permission_denials') != []:
                raise engine_error('engine_misconfigured', field='probe result/permission_denials')
            process.wait(timeout=2)
            if process.process.returncode != 0 or process.write_error or not process.written.is_set():
                raise engine_error('engine_unavailable')
            # Readers must finish before checking warnings or the transcript.
            for thread in process.threads:
                thread.join(timeout=1)
            if any('settings' in line.lower() and ('warn' in line.lower() or 'invalid' in line.lower())
                   for line in process.stderr):
                raise engine_error('engine_misconfigured', field='settings warning')
            transcripts = list(Path(engine['profile']).rglob(session + '.jsonl'))
            if not any(p.is_file() and not p.is_symlink() and within(p.resolve(), Path(engine['profile']).resolve())
                       for p in transcripts):
                raise engine_error('engine_misconfigured', field='lane transcript')
            if profile_mtimes(env['HOME']) != before:
                raise engine_error('engine_misconfigured', field='normal profile changed')
            return version, {field: init[field] for field in required}
        except subprocess.TimeoutExpired:
            raise engine_error('engine_timeout')
        finally:
            process.close()


class EngineService:
    def __init__(self, cfg, sealer, state=None):
        self.cfg, self.sealer = cfg, sealer
        self.state = Path(state or HOME / 'state')
        self.limits = LimitMemory()
        self.lock_timeout, self.startup_timeout, self.stdin_timeout = 30, 90, 30
        self.shutting_down = threading.Event()
        self.active, self.active_lock = set(), threading.Lock()

    def shutdown(self):
        self.shutting_down.set()
        with self.active_lock:
            active = list(self.active)
        # Stop all groups together, not ten seconds per request.
        threads = [threading.Thread(target=p.stop, daemon=True) for p in active]
        for thread in threads:
            thread.start()
        deadline = time.monotonic() + 10
        for thread in threads:
            thread.join(max(0, deadline - time.monotonic()))
        for process in active:
            process.signal(signal.SIGKILL)

    def run(self, handler, route, body):
        engine = self.cfg.engines[route['engine']]
        metadata = turn_metadata(handler.headers)
        key = thread_key(metadata, body)
        member = route['engine'] + ':' + route['upstream_model']
        reset = self.limits.get(member, consume=True)
        if reset:
            raise engine_error('rate_limit_exceeded', reset=reset)
        env = engine_environment(engine)
        cwd = cwd_ok(workspace(body), home=env['HOME'], profiles=[e['profile'] for e in self.cfg.engines.values()])
        items = engine_items(body)
        triggers = [n for n, i in enumerate(items) if i.get('type') == 'compaction_trigger']
        if body.get('previous_response_id') or triggers and triggers != [len(items) - 1]:
            raise BridgeError(400, 'Engine turns require full input and a final compaction trigger.', 'bad_request')
        version = engine_preflight(engine)[0] if not triggers else None
        path = self.state / 'engine-sessions' / (key + '.json')
        cancelled, monitor_done = threading.Event(), threading.Event()
        process, writer, translator = None, None, None
        # Admission is recorded before queueing, including turns cancelled while waiting for the lock.
        marker = re.compile(r'^(?:msg|rs|cmp)_' + uuid.UUID(key).hex[:8] + r'_(\d+)(?:_\d+)?$')
        positions = [(int(m.group(1)), n) for n, i in enumerate(items)
                     for m in [marker.fullmatch(str(i.get('id', '')))] if m]
        position = max(positions)[1] if positions else -1
        turn_id = (metadata or {}).get('turn_id') or hashlib.sha256(
            (key + '|' + str(items[position].get('id') if position >= 0 else 'start') + '|' +
             json.dumps(items[position + 1:], sort_keys=True, separators=(',', ':'))).encode()).hexdigest()
        ledger = EngineLedger(self.state / 'engine-turns' / (key + '.jsonl'), turn_id, member)
        ledger.write('accepted')
        committed, terminal = False, False
        thread_lock = None

        def monitor():
            keepalive = time.monotonic()
            while not monitor_done.wait(0.25):
                if self.shutting_down.is_set() or peer_closed(handler) or (writer and writer.failed.is_set()):
                    cancelled.set()
                    if writer:
                        writer.cancelled.set()
                    if process:
                        process.stop()
                    return
                if writer and committed and time.monotonic() - max(keepalive, writer.last_activity) >= 15:
                    with contextlib.suppress(queue.Full):
                        writer.queue.put_nowait('keepalive')
                    keepalive = time.monotonic()
        watchdog = threading.Thread(target=monitor, daemon=True)
        watchdog.start()
        try:
            thread_lock = ThreadLock(path.with_suffix('.lock'), self.lock_timeout,
                                     lambda: cancelled.is_set() or self.shutting_down.is_set())
            thread_lock.__enter__()
            try:
                record = json.loads(path.read_text()) if path.exists() else None
                if record is not None and (not isinstance(record, dict) or record.get('thread_key') != key):
                    record = None
            except (OSError, ValueError):
                record = None
            if triggers:
                # No process, even when the engine was upgraded since the last turn.
                plan = cursor(items[:-1], record, key, cwd, metadata, self.sealer)
                checkpoint_record = record if plan['resume'] else dict(uuid=plan['uuid'], ordinal=plan['ordinal'], last_turn=None)
                response = compact_reply(failed_items_removed(items[:-1], record, key), checkpoint_record, key,
                                         body.get('model'), self.sealer, plan.get('parent'))
                handler.reply_compaction(response, bool(body.get('stream')))
                ledger.write('completed', ended=time.time())
                terminal = True
                return 200
            if record and not transcript_exists(engine, record['uuid']):
                record = dict(record, expired=True)
            plan = cursor(items, record, key, cwd, metadata, self.sealer)
            wire, notes, role_text = engine_input(plan, self.sealer, key)
            role_text = next((item_text(i) for i in items if i.get('role') == 'developer'
                              and item_text(i).startswith(ROLE_FIRST_LINE)), role_text)
            args = launch_args(engine, route, plan, cwd, role_text, bool((metadata or {}).get('parent_thread_id') or role_text))
            if cancelled.is_set():
                raise ConnectionAbortedError('client gone')
            started = time.monotonic()
            try:
                with self.active_lock:
                    if self.shutting_down.is_set():
                        raise ConnectionAbortedError('bridge shutting down')
                    version, env = engine_preflight(engine)
                    cpa_compatibility(engine, version, self.state, getattr(self.cfg, 'cpa_known_normalisations', []))
                    process = EngineProcess(args, env, cwd, wire, lock_fd=thread_lock.fd)
                    self.active.add(process)
            except OSError:
                raise engine_error('engine_unavailable')
            ledger.write('running', started=time.time(), **process.identity)
            # Keep the session id even on interruption; the missing cursor will cause a safe restart.
            new_record = dict(thread_key=key, uuid=plan['uuid'], member=member, cwd=cwd,
                              created=record.get('created') if record and plan['resume'] else time.time(),
                              last_turn=(record or {}).get('last_turn'), last_cursor=(record or {}).get('last_cursor'),
                              forked_from=plan.get('parent'), restarts=plan['restarts'],
                              ordinal=plan['ordinal'], pending=True,
                              failed_ordinals=sorted(set((record or {}).get('failed_ordinals', []) +
                                  ([(record or {})['ordinal']] if (record or {}).get('pending') else []))))
            private_json(path, new_record)
            label = 'Claude · %s · %s · read-only · session %s · %s' % (
                route['upstream_model'], route.get('effort', 'medium'), plan['uuid'][:8], plan['reason'])
            if plan['reason'] == 'rewound':
                notes.insert(0, 'thread was rewound; Claude session restarted')
            elif plan['reason'] == 'workspace changed':
                notes.insert(0, 'workspace changed to %s; session restarted' % Path(cwd).name)
            elif plan['reason'] == 'recovered':
                notes.insert(0, 'session record was missing')
            elif plan['reason'] == 'forked':
                notes.insert(0, 'forked from another thread')
            if not (metadata or {}).get('thread_id'):
                notes.append('no turn metadata from Codex')
            translator = EngineEvents(key, plan['ordinal'], body.get('model'), cwd,
                                      label + (' · ' + '; '.join(notes) if notes else ''), route.get('max_turns', 60))
            while True:
                elapsed = time.monotonic() - started
                if cancelled.is_set():
                    raise ConnectionAbortedError('client gone')
                if process.protocol_error:
                    raise engine_error('engine_error')
                if process.write_error:
                    raise engine_error('engine_unavailable')
                if not process.written.is_set() and elapsed >= self.stdin_timeout:
                    raise engine_error('bad_request')
                if not committed and elapsed >= self.startup_timeout:
                    raise engine_error('engine_timeout')
                if committed and elapsed >= route.get('turn_timeout', 1200):
                    process.stop()
                    events = translator.finish(stopped='stopped: time limit')
                else:
                    try:
                        raw = process.next()
                    except queue.Empty:
                        continue
                    if raw is None:
                        if process.protocol_error:
                            raise engine_error('engine_error')
                        try:
                            process.wait(timeout=0.1)
                        except subprocess.TimeoutExpired:
                            process.stop()
                        if translator.limit_seen or stderr_quota(process.stderr):
                            self.limits.set(member, translator.limit_reset)
                            raise engine_error('rate_limit_exceeded', reset=self.limits.get(member), streamed=committed)
                        if not committed:
                            raise engine_error('pool_down' if process.process.returncode == 75 else 'engine_unavailable')
                        raise engine_error('engine_exited')
                    try:
                        value = json.loads(raw)
                        if not isinstance(value, dict):
                            raise ValueError('not an event')
                    except (ValueError, UnicodeError):
                        raise engine_error('engine_error')
                    if value.get('type') == 'system' and value.get('subtype') == 'init':
                        if committed:
                            raise engine_error('engine_error')
                        init_check(value, engine, version, cwd, route['upstream_model'])
                        # stdout is drained concurrently; do not commit until stdin succeeded, too.
                        while not process.written.wait(0.05):
                            if cancelled.is_set():
                                raise ConnectionAbortedError('client gone')
                            if time.monotonic() - started >= self.stdin_timeout:
                                raise engine_error('bad_request')
                        if process.write_error:
                            raise engine_error('engine_unavailable')
                        committed = True
                        if body.get('stream'):
                            writer = EngineWriter(handler)
                            writer.put('start')
                        events = translator.start()
                    elif not committed:
                        if value.get('type') in ('system', 'rate_limit_event'):
                            translator.feed(value)  # classify limits/retries; no output before init
                        if value.get('type') == 'result':
                            if translator.limit_seen or quota_evidence(value) or stderr_quota(process.stderr):
                                self.limits.set(member, translator.limit_reset)
                                raise engine_error('rate_limit_exceeded', reset=self.limits.get(member))
                            raise engine_error('engine_unavailable')
                        if value.get('type') not in ('system', 'rate_limit_event'):
                            raise engine_error('engine_unavailable')
                        continue
                    else:
                        if value.get('type') == 'result' and value.get('is_error'):
                            if quota_evidence(value) or stderr_quota(process.stderr):
                                translator.limit_seen = True
                        events = translator.feed(value)
                if translator.finished:
                    process.stop()  # lock and result stay held until no engine is running
                    failed = translator.response['status'] == 'failed'
                    if failed and translator.limit_seen:
                        self.limits.set(member, translator.limit_reset)
                    elif not failed:
                        self.limits.clear(member)
                    if not failed:
                        new_record.update(last_turn=turn_id, last_cursor=translator.outputs[-1]['id'], pending=False)
                    private_json(path, new_record)
                    ledger.write('failed' if failed else 'completed', ended=time.time(), exit=process.process.returncode)
                    terminal = True
                if writer:
                    for event in events:
                        writer.put(event)
                if translator.finished:
                    if not body.get('stream'):
                        handler.send_json(200, translator.response)
                    return 200
        except (BridgeError, OSError) as error:
            if process:
                process.stop()  # kill first; Handler may send an HTTP error only after this returns
            if ledger and not terminal:
                ledger.write('cancelled' if cancelled.is_set() or isinstance(error, OSError) else 'failed',
                             ended=time.time(), exit=process.process.returncode if process else None)
            if isinstance(error, BridgeError) and committed:
                if error.code == 'rate_limit_exceeded':
                    self.limits.set(member, error.extra.get('resets_at'))
                if writer:
                    for event in translator.finish(error=error):
                        writer.put(event)
                else:
                    translator.finish(error=error)
                    handler.send_json(200, translator.response)
                return 200
            raise
        finally:
            monitor_done.set()
            if process:
                process.close()
                with self.active_lock:
                    self.active.discard(process)
            if writer:
                writer.close()
            watchdog.join(timeout=2)
            if thread_lock:
                thread_lock.__exit__(None, None, None)


# ---------------------------------------------------------------------------------------------- server



class Service(EngineService):
    def __init__(self, cfg, sealer, state):
        import types
        raw = cfg.raw
        own = types.SimpleNamespace(engines=dict(raw.get('engines') or {}),
                                    cpa_known_normalisations=raw.get('cpa_known_normalisations', []))
        super().__init__(own, sealer, state)

    def validate(self, route):
        if route.get('engine') not in self.cfg.engines:
            raise BridgeError(503, 'The extension route is not configured.', 'extension_unavailable')

    def recover(self):
        recover_engine_turns(self.state)
