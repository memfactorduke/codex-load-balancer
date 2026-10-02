"""Sienna desktop wire validation and its advisory CPA compatibility record."""
from . import cp, pool
import pathlib
import re
import shutil
import subprocess



def desktop_wire_files():
    """Installed build assets; tests may use the checkout without an install."""
    base = pathlib.Path(__file__).parent / 'gate'
    return [base / 'codexpool_desktop_wire_test.go'] + sorted((base / 'desktop-wire').glob('*.json'))


def desktop_wire_test(tree, go, env):
    files = desktop_wire_files()
    if len(files) != 5:
        return {'result': 'FAIL', 'failing_paths': [], 'reason': 'desktop wire fixtures missing'}
    executor = tree / 'internal/runtime/executor'
    source = files[0].read_text()
    # CPA v8 changed its Go module path; the test exercises the same interface.
    if 'module github.com/router-for-me/CLIProxyAPI/v8' in (tree / 'go.mod').read_text():
        source = source.replace('CLIProxyAPI/v7/', 'CLIProxyAPI/v8/')
    (executor / files[0].name).write_text(source)
    fixtures = executor / 'testdata/subpool-desktop'
    fixtures.mkdir(parents=True, exist_ok=True)
    for stale in fixtures.glob('*.json'):
        stale.unlink()
    for path in files[1:]:
        shutil.copy2(path, fixtures / path.name)
    result = subprocess.run([str(go), 'test', '-count=1', '-run', '^TestCodexpoolDesktopWire',
                             './internal/runtime/executor/'], cwd=tree, env=env, capture_output=True, text=True)
    failure_lines = '\n'.join(line for line in (result.stdout + result.stderr).splitlines()
                              if 'BODY_DIFF ' not in line)
    paths = sorted(set(re.findall(r'\$\.[A-Za-z0-9_.\[\]-]+', failure_lines)))
    return {'result': 'FAIL' if result.returncode else 'PASS', 'failing_paths': paths,
            'reason': ('desktop wire test failed' if paths else 'desktop wire test could not pass (test or compiler error)')
                      if result.returncode else 'desktop wire test passed'}


def desktop_wire_record_path(build_id):
    return cp.STATE / 'cpa-desktop-wire' / (build_id + '.json')


def desktop_cpa_compatibility(live_version=None):
    """Only evidence for the process answering on the Claude port authorizes desktop use.
    None probes that port; an empty string means no live evidence and must never trigger a retry."""
    live = cp.running_version(pool.CLAUDE_PORT) if live_version is None else live_version
    linked = cp.cpa_version(pool.CLAUDE_CURRENT)
    identity = (live or linked or '').lstrip('v').replace('+gate.', '-gate-')
    valid = re.fullmatch(r'[0-9]+\.[0-9]+\.[0-9]+-gate-[a-f0-9]+', identity)
    build_id = 'v' + identity if valid else None
    version = identity.split('-gate-')[0] if valid else 'unknown'
    record = cp.read_json(desktop_wire_record_path(build_id), {}) if build_id else {}
    if not isinstance(record, dict):
        record = {}
    matched = record.get('build_id') == build_id and record.get('version') == version and build_id is not None
    verdict = record.get('result') if matched and record.get('result') in ('PASS', 'FAIL') else 'UNKNOWN'
    paths = record.get('failing_paths', []) if matched else []
    paths = [p for p in paths if isinstance(p, str) and re.fullmatch(r'\$\.[A-Za-z0-9_.\[\]-]+', p)] if isinstance(paths, list) else []
    compatible = bool(live and verdict == 'PASS')
    if compatible:
        reason = f'CLIProxyAPI v{version}: desktop wire test PASS for {build_id}'
    else:
        problem = ('changes the desktop app\'s identity headers' if verdict == 'FAIL' and any(p.startswith('$.headers.') for p in paths)
                   else 'failed the desktop wire compatibility test' if verdict == 'FAIL'
                   else 'has no recorded desktop wire PASS' if verdict != 'PASS'
                   else 'is not confirmed running')
        reason = (f'CLIProxyAPI v{version} {problem}; pooled desktop mode is off. ' +
                  ('Failing paths: ' + ', '.join(paths) + '. ' if paths else '') +
                  'Use a CPA version that passes, or rebuild with the upstream patch build/cpa-native-desktop-3p.patch '
                  'and pass the desktop wire test. Rebuild/install to record a missing result.')
    return {'cpa_compatible': compatible, 'cpa_compatibility_reason': reason, 'cpa_build_id': build_id,
            'cpa_version': version, 'cpa_wire_result': verdict, 'cpa_failing_paths': paths}


class DesktopBuild:
    """Build contribution; a failed wire check disables desktop use, not the pool."""
    @property
    def hash_inputs(self):
        return desktop_wire_files()

    def cache_valid(self, dest):
        return desktop_wire_record_path(dest.name).exists()

    def pre_build(self, tree, go, env):
        print('running the desktop and CLI wire test (real binary captures)')
        try:
            wire = desktop_wire_test(tree, go, env)
        except (OSError, subprocess.SubprocessError):
            wire = {'result': 'FAIL', 'failing_paths': [], 'reason': 'desktop wire test could not run'}
        print('desktop wire test: ' + wire['result'] + ' (' + wire['reason'] + ')' +
              (': ' + ', '.join(wire['failing_paths']) if wire['failing_paths'] else '') +
              ('; pooled desktop mode unavailable; continuing the pool build' if wire['result'] != 'PASS' else ''))
        return wire

    def post_build(self, dest, version, stamp, result):
        cp.write_json(desktop_wire_record_path(dest.name), dict(result, build_id=dest.name, version=version,
                                                               checked_at=stamp))
