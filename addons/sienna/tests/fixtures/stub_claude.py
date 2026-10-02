#!/usr/bin/env python3
"""Engine tests only: replay a synthetic stream, recording argv/cwd/env under the throwaway profile.

The harness copies this into fake HOME/.local/versions/<version> and adds an absolute Python shebang.
No real Claude executable, login, network or application is involved.
"""
# subpool claude launcher, written by `subpool claude install`
import json
import os
from pathlib import Path
import signal
import sys
import time

profile = Path(os.environ['CLAUDE_CONFIG_DIR'])
fixture = json.loads((profile / 'fixture.json').read_text())
record = dict(argv=sys.argv[1:], cwd=os.getcwd(), env=dict(os.environ), pid=os.getpid())
(profile / 'spawn.json').write_text(json.dumps(record))

def interrupted(sig, frame):
    (profile / 'signal.json').write_text(json.dumps({'signal': sig, 'at': time.time()}))
    sys.exit(0)

signal.signal(signal.SIGINT, interrupted)
if fixture.get('ignore_interrupt'):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
if fixture.get('ignore_term'):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
if fixture.get('no_read'):
    time.sleep(60)
else:
    record['stdin'] = sys.stdin.read()
    (profile / 'spawn.json').write_text(json.dumps(record))
for event in fixture.get('events', []):
    if 'sleep' in event:
        time.sleep(event['sleep'])
    elif 'stderr' in event:
        print(event['stderr'], file=sys.stderr, flush=True)
    elif 'raw' in event:
        print(event['raw'], flush=True)
    elif 'oversize' in event:
        print('x' * (8 * 1024 * 1024 + 1), flush=True)
    elif 'chatty' in event:
        for _ in range(event['chatty']):
            print(json.dumps({'type': 'system', 'subtype': 'status', 'padding': 'x' * 4096}), flush=True)
    else:
        if fixture.get('probe') and event.get('subtype') == 'init':
            event['cwd'] = os.getcwd()
        print(json.dumps(event), flush=True)
if not fixture.get('no_transcript') and ('--session-id' in sys.argv or '--resume' in sys.argv):
    flag = '--session-id' if '--session-id' in sys.argv else '--resume'
    session = sys.argv[sys.argv.index(flag) + 1]
    project = profile / 'projects' / 'synthetic-workspace'
    project.mkdir(parents=True, exist_ok=True)
    (project / (session + '.jsonl')).write_text('{}\n')
if fixture.get('mutate_profile'):
    (Path(os.environ['HOME']) / '.claude.json').write_text('{}\n')
if fixture.get('linger'):
    time.sleep(60)
sys.exit(fixture.get('exit', 0))
