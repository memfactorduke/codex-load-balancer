"""Sienna's gate sources, profile self-tests and upstream compatibility requirement."""
from . import cp, desktop_build
import pathlib
import re
import sys

CLAUDE_CODE_HEADERS = {'User-Agent': 'claude-cli/2.1.283 (external, cli)', 'X-App': 'cli'}


def cases(code, ws_code, auth, evil, rebind):
    cc = CLAUDE_CODE_HEADERS
    cases = [
        ('Claude Code GET /v1/models', code('/v1/models', cc), 200),
        ('Claude Code background session', code('/v1/models', {**cc, 'X-App': 'cli-bg'}), 403),
        ('unrecognised token counter', code('/v1/messages/count_tokens', cc, 'POST'), 403),
        ('background token counter', code('/v1/messages/count_tokens', {**cc, 'X-App': 'cli-bg'}, 'POST'), 403),
        ('background session without Claude Code User-Agent', code('/v1/models', {'X-App': 'cli-bg'}), 403),
        ('background session with browser Origin', code('/v1/models', {**cc, 'X-App': 'cli-bg', **evil}), 403),
        ('other client (no Claude Code User-Agent)', code('/v1/models'), 403),
        ('Claude Code User-Agent without x-app: cli', code('/v1/models', {'User-Agent': cc['User-Agent']}), 403),
        ('other client outside /v1 (/v1beta)', code('/v1beta/models'), 403),
        ('path climbing out of management', code('/v0/management/../v1/models'), 403),
        ('path climbing into management (/v1beta/models/../../v0/management/…)',
         code('/v1beta/models/../../v0/management/claude-opus-5-5:generateContent', method='POST'), 403),
        ('browser Origin', code('/v1/models', {**cc, **evil}), 403),
        ('no-cors cross-site', code('/v1/models', {**cc, 'Sec-Fetch-Site': 'cross-site'}), 403),
        ('DNS rebinding Host', code('/v1/models', {**cc, **rebind}), 403),
        ('Codex renderer app://-', code('/v1/models', {**cc, 'Origin': 'app://-'}), 403),
        ('management with key', code('/v0/management/auth-files', auth), 200),
        ('WebSocket upgrade from another client', ws_code(), 403),
    ]
    for _ in range(6):
        code('/v0/management/auth-files', evil)
    cases.append(('management after 6 browser attempts', code('/v0/management/auth-files', auth), 200))
    return cases


def check_gate_detector(tree, version):
    """The token-count gate deliberately uses CPA's own detector; older source cannot build this gate."""
    helpers = tree / 'internal/runtime/executor/helps'
    if not any(re.search(r'(?m)^func DetectClaudeCodeRequest\(', p.read_text())
               for p in helpers.glob('*.go') if not p.name.endswith('_test.go')):
        sys.exit(f'codexpool: CLIProxyAPI v{version} lacks helps.DetectClaudeCodeRequest, required by the '
                 'Claude token-count gate. Choose a compatible CLIProxyAPI release or update codexpool. '
                 'No running build was changed.')



class GateContribution(desktop_build.DesktopBuild):
    profiles = {'claude': cases}

    @property
    def sources(self):
        return [pathlib.Path(__file__).parent / 'gate/codexpool_gate_sienna.go']

    def pre_build(self, tree, go, env):
        check_gate_detector(tree, 'source')
        return super().pre_build(tree, go, env)
