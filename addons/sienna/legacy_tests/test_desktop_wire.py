"""Run the real CPA executor wire test in a disposable source copy.

No listeners, upstream network, or real credentials. Missing source/Go skips cleanly;
missing offline module cache is reported as a skip, never downloaded by the test.
"""
from _helpers import HOME, REPO, addon
GATE = addon.path / "gate"
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch


class DesktopWire(unittest.TestCase):
    def unavailable(self, reason):
        if os.environ.get('CI') or os.environ.get('CODEXPOOL_CI'):
            self.fail(reason)
        self.skipTest(reason)

    def test_cpa_executor_and_desktop_gate(self):
        source = Path(os.environ.get('CODEXPOOL_CPA_SOURCE', REPO.parents[1] / 'cpa-src'))
        go = os.environ.get('CODEXPOOL_GO') or shutil.which('go')
        if not (source / 'internal/runtime/executor/claude_executor_execute.go').is_file() or not go:
            self.unavailable('CPA source and Go required (CODEXPOOL_CPA_SOURCE, CODEXPOOL_GO)')
        with tempfile.TemporaryDirectory(prefix='desktop-wire-', dir=HOME) as temp:
            work = Path(temp)
            tree = work / 'cpa'
            shutil.copytree(source, tree, ignore=shutil.ignore_patterns('.git', 'node_modules'))
            for stale in (tree / 'cmd/server').glob('codexpool*gate*.go'):
                stale.unlink()
            executor = tree / 'internal/runtime/executor'
            source_text = (GATE / 'codexpool_desktop_wire_test.go').read_text()
            if 'module github.com/router-for-me/CLIProxyAPI/v8' in (tree / 'go.mod').read_text():
                source_text = source_text.replace('CLIProxyAPI/v7/', 'CLIProxyAPI/v8/')
            (executor / 'codexpool_desktop_wire_test.go').write_text(source_text)
            fixtures = executor / 'testdata/codexpool-desktop'
            if fixtures.exists():
                shutil.rmtree(fixtures)
            shutil.copytree(GATE / 'desktop-wire', fixtures)
            for asset in (REPO / 'build/codexpool_gate.go', GATE / 'codexpool_gate_sienna.go',
                          GATE / 'codexpool_gate_sienna_test.go', GATE / 'codexpool_desktop_gate_test.go'):
                source_text = asset.read_text()
                if 'module github.com/router-for-me/CLIProxyAPI/v8' in (tree / 'go.mod').read_text():
                    source_text = source_text.replace('CLIProxyAPI/v7/', 'CLIProxyAPI/v8/')
                (tree / 'cmd/server' / asset.name).write_text(source_text)
            # Overlay modules already cached by the coordinator; never fetch dependencies.
            env = dict(os.environ, HOME=str(HOME), GOPROXY='off', GOSUMDB='off', GOTOOLCHAIN='local',
                       CGO_ENABLED='0', GOFLAGS='-mod=mod', GOCACHE=str(work / 'cache'))
            result = subprocess.run([go, 'test', '-count=1', '-run', '^TestCodexpool(DesktopWire|Gate)',
                                     './internal/runtime/executor/', './cmd/server/'], cwd=tree, env=env,
                                    capture_output=True, text=True, timeout=240)
            output = result.stdout + result.stderr
            if result.returncode and ('module lookup disabled by GOPROXY=off' in output or
                                      'requires go >=' in output):
                self.unavailable('offline Go toolchain/module cache unavailable: coordinator must run the wire test')
            self.assertEqual(result.returncode, 0, output[-14000:])

    def test_fixtures_are_real_captures(self):
        base = GATE / 'desktop-wire'
        self.assertEqual({p.name for p in base.glob('*.json')},
                         {'desktop-initial.json', 'desktop-retry.json', 'cli.json', 'allowed-differences.json'})
        for name, version in [('desktop-initial.json', '2.1.281'), ('desktop-retry.json', '2.1.281'), ('cli.json', '2.1.283')]:
            fixture = json.loads((base / name).read_text())
            self.assertFalse(fixture['synthesised'])
            self.assertEqual(fixture['capture']['kind'], 'real-binary-loopback')
            self.assertEqual(fixture['capture']['binary_version'], version)
            self.assertEqual(fixture['headers']['Authorization'], '<present>')
            self.assertEqual(fixture['capture']['response_status'], 400)
            self.assertTrue(fixture['body']['stream'])
            identity = json.loads(fixture['body']['metadata']['user_id'])
            self.assertEqual(identity['session_id'], fixture['headers']['X-Claude-Code-Session-Id'])
            self.assertEqual(identity['account_uuid'], '')
            self.assertNotIn('temperature', fixture['body'])
            self.assertNotIn('top_k', fixture['body'])
        initial = json.loads((base / 'desktop-initial.json').read_text())
        retry = json.loads((base / 'desktop-retry.json').read_text())
        self.assertEqual(initial['body'], retry['body'])
        self.assertEqual(initial['headers']['anthropic-beta'],
                         retry['headers']['anthropic-beta'] + ',fallback-credit-2026-06-01')
        # Corrected coordinator environment: entrypoint and TTL are separate variables.
        self.assertEqual(initial['headers']['User-Agent'], 'claude-cli/2.1.281 (external, claude-desktop-3p)')
        cli = json.loads((base / 'cli.json').read_text())
        self.assertIn('extended-cache-ttl-2025-04-11', cli['headers']['anthropic-beta'])
        self.assertNotIn('extended-cache-ttl-2025-04-11', initial['headers']['anthropic-beta'])
        for fixture in (initial, retry, cli):
            self.assertEqual(fixture['headers']['Accept-Encoding'], 'gzip, deflate, br, zstd')
            self.assertFalse(any(k.lower() == 'x-client-request-id' for k in fixture['headers']))
            self.assertNotIn('cch=', fixture['body']['system'][0]['text'])

    def test_ci_missing_prerequisite_is_failure(self):
        with patch.dict(os.environ, {'CI': 'true'}):
            with self.assertRaises(AssertionError):
                self.unavailable('missing fixture/toolchain')
