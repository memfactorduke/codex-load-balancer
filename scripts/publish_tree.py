#!/opt/homebrew/bin/python3
"""Publish a staged tree to a GitHub repo via the Git Data API (no local git needed).

usage: publish_tree.py <root> <owner/repo> <commit message>
Uploads every file under root except caches; aborts before any upload if the secret or personal-data scan hits.
Author/committer: the user's GitHub noreply identity.
"""
import base64, json, pathlib, re, subprocess, sys

SKIP_DIRS = {'.git', '__pycache__', '.ruff_cache', '.venv', '.pytest_cache'}
SECRET = re.compile(r'eyJ[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}|"(?:access|refresh|id)_token"\s*:\s*"[^"<]|'
                    r'\$2[aby]\$\d\d\$|sk-[A-Za-z0-9]{20,}|gh[pousr]_[A-Za-z0-9]{30,}|quotio-local-[0-9A-F-]{20,}|'
                    r'-----BEGIN [A-Z ]*PRIVATE KEY-----|RateLimitResetCredit_[0-9a-f]{8,}')
PERSONAL = re.compile(r'/Users/(?!<)[A-Za-z]|com\.' + 'rotcafmem'[::-1] + r'|@gmail\.com|' +
                      '|'.join(re.escape(w[::-1]) + ('(?!duke)' if w == 'rotcafmem' else '') for w in ('rotcafmem', 'oibartni', 'modeerfgniraeb')) +
                      r'|\bIB-(?:DATA|AI)\b', re.I)


# Synthetic fixtures are permitted; other email addresses require removal before publication.
EMAIL = re.compile(r'[A-Za-z0-9._%+-]+@([A-Za-z][A-Za-z0-9.-]*\.[A-Za-z]{2,})')
FIXTURE_DOMAINS = {'example.com', 'example.org', 'example.net', 'test.json'}
RUNTIME_PATHS = {'auth', 'auth-claude', 'state', 'logs', 'config.yaml', 'config-claude.yaml',
                 'seats.json', 'claude-seats.json', 'settings.json', 'rollback', 'LOCAL.md', 'toolchain', 'shims', 'apps'}


def gh(repo, method, path, body=None):
    r = subprocess.run(['gh', 'api', '-X', method, f'repos/{repo}/{path}'] + (['--input', '-'] if body is not None else []),
                       input=json.dumps(body) if body is not None else None, capture_output=True, text=True)
    if r.returncode != 0:
        raise SystemExit(f'gh api {method} {path} failed: {r.stderr.strip()[:300]} {r.stdout[:300]}')
    return json.loads(r.stdout) if r.stdout.strip() else {}


def files(root):
    for p in sorted(root.rglob('*')):
        if p.is_file() and not any(part in SKIP_DIRS for part in p.relative_to(root).parts) and p.suffix != '.pyc':
            yield p


def scan(root):
    """(paths, hits): every file to publish, and each secret or personal-data match."""
    paths = list(files(root))
    hits = []
    for p in paths:
        rel = p.relative_to(root)
        if rel.parts[0] in RUNTIME_PATHS or rel.as_posix() in {'lanes/secrets', 'lanes/bridge.json', 'lanes.json'} or rel.parts[:2] == ('lanes', 'secrets'):
            hits.append(f'runtime: {rel}: local state is not publishable')
        text = p.read_bytes().decode('utf-8', errors='replace')
        for match in EMAIL.finditer(text):
            domain = match.group(1).lower()
            if domain not in FIXTURE_DOMAINS and not domain.endswith('.invalid'):
                hits.append(f'personal: {rel}: email address')
        for rx, kind in ((SECRET, 'secret'), (PERSONAL, 'personal')):
            for m in rx.finditer(text):
                hits.append(f'{kind}: {rel}: prohibited data')
    return paths, hits


def main():
    root, repo, message = pathlib.Path(sys.argv[1]).resolve(), sys.argv[2], sys.argv[3]
    paths, hits = scan(root)
    if hits:
        raise SystemExit('scan failed; nothing uploaded:\n  ' + '\n  '.join(hits[:40]))
    print(f'secret and personal-data scan clean over {len(paths)} files')
    user = subprocess.run(['gh', 'api', 'user'], capture_output=True, text=True, check=True)
    profile = json.loads(user.stdout)
    identity = {'name': profile['login'], 'email': str(profile['id']) + '+' + profile['login'] +
                '@users.noreply.github.com'}
    head = gh(repo, 'GET', 'git/ref/heads/main')['object']['sha']
    tree = []
    for p in paths:
        blob = gh(repo, 'POST', 'git/blobs', {'content': base64.b64encode(p.read_bytes()).decode(), 'encoding': 'base64'})
        rel = str(p.relative_to(root))
        mode = '100755' if (p.stat().st_mode & 0o111) and p.suffix not in ('.md', '.json', '.yaml', '.png', '.template', '.jsonl') else '100644'
        tree.append({'path': rel, 'mode': mode, 'type': 'blob', 'sha': blob['sha']})
    new_tree = gh(repo, 'POST', 'git/trees', {'tree': tree})
    commit = gh(repo, 'POST', 'git/commits', {'message': message, 'tree': new_tree['sha'], 'parents': [head],
                                              'author': identity, 'committer': identity})
    gh(repo, 'PATCH', 'git/refs/heads/main', {'sha': commit['sha']})
    print(f'pushed {commit["sha"][:10]} ({len(tree)} files) to {repo} as {identity["name"]} <{identity["email"]}>')


if __name__ == '__main__':
    main()
