"""Run the core gate Go tests in a disposable source copy.

No listeners, upstream network, or real credentials. Missing source/Go skips cleanly;
missing offline module cache is reported as a skip, never downloaded by the test.
"""
from _helpers import HOME, REPO, cp
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch, call


class GateTests(unittest.TestCase):
    def unavailable(self, reason):
        if os.environ.get('CI') or os.environ.get('CODEXPOOL_CI'):
            self.fail(reason)
        self.skipTest(reason)

    def test_gate(self):
        source = Path(os.environ.get('CODEXPOOL_CPA_SOURCE', REPO.parents[1] / 'cpa-src'))
        go = os.environ.get('CODEXPOOL_GO') or shutil.which('go')
        if not (source / 'cmd/server/main.go').is_file() or not go:
            self.unavailable('CPA source and Go required (CODEXPOOL_CPA_SOURCE, CODEXPOOL_GO)')
        with tempfile.TemporaryDirectory(prefix='core-gate-', dir=HOME) as temp:
            work = Path(temp)
            tree = work / 'cpa'
            shutil.copytree(source, tree, ignore=shutil.ignore_patterns('.git', 'node_modules'))
            for stale in (tree / 'cmd/server').glob('codexpool*gate*.go'):
                stale.unlink()
            for name in ('codexpool_gate.go', 'codexpool_gate_test.go'):
                gate_source = (REPO / 'build' / name).read_text()
                if 'module github.com/router-for-me/CLIProxyAPI/v8' in (tree / 'go.mod').read_text():
                    gate_source = gate_source.replace('CLIProxyAPI/v7/', 'CLIProxyAPI/v8/')
                (tree / 'cmd/server' / name).write_text(gate_source)
            # Overlay modules already cached by the coordinator; never fetch dependencies.
            env = dict(os.environ, HOME=str(HOME), GOPROXY='off', GOSUMDB='off', GOTOOLCHAIN='local',
                       CGO_ENABLED='0', GOFLAGS='-mod=mod', GOCACHE=str(work / 'cache'))
            result = subprocess.run([go, 'test', '-count=1', '-run', '^TestCodexpoolGate',
                                     './cmd/server/'], cwd=tree, env=env,
                                    capture_output=True, text=True, timeout=240)
            output = result.stdout + result.stderr
            if result.returncode and ('module lookup disabled by GOPROXY=off' in output or
                                      'requires go >=' in output):
                self.unavailable('offline Go toolchain/module cache unavailable: coordinator must run the gate test')
            self.assertEqual(result.returncode, 0, output[-14000:])


class GateBuildIdentity(unittest.TestCase):
    def test_codex_hash_cache_and_selftest_never_load_contributions(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp, \
                patch.object(cp, 'GATE_SOURCE', REPO / 'build/codexpool_gate.go'), \
                patch.object(cp, 'VERSIONS', Path(temp)), \
                patch.object(cp, 'addon_gate_contributions', side_effect=AssertionError('add-on consulted')), \
                patch.object(cp, 'ADDON_FAILED_HOOKS', {('broken', 'gate')}):
            gid = cp.gate_id(profile='codex')
            self.assertEqual(cp.gate_version('1.2.3'), '1.2.3+gate.' + gid)
            dest = cp.VERSIONS / ('v1.2.3-gate-' + gid)
            dest.mkdir()
            (dest / 'cli-proxy-api').touch()
            self.assertEqual(cp.build('1.2.3', profile='codex'), dest)
            self.assertEqual(cp.gate_source_note('1.2.3+gate.' + gid, cp.pool_instance('codex')), '')
            with patch.object(cp, '_gate_selftest_profile', return_value=(True, 'default passed')) as run:
                self.assertEqual(cp.gate_selftest('binary', profile='codex'), (True, 'default passed'))
                run.assert_called_once_with('binary', 'codex')

    def test_selftest_runs_default_and_contributed_profiles(self):
        contribution = (None, None, {'extra': lambda *args: []}, [], [])
        with patch.object(cp, 'addon_gate_contributions', return_value=[contribution]), \
                patch.object(cp, 'ADDON_FAILED_HOOKS', set()), \
                patch.object(cp, '_gate_selftest_profile', return_value=(True, 'passed')) as run:
            self.assertTrue(cp.gate_selftest('binary')[0])
            self.assertEqual(run.call_args_list, [call('binary', 'codex'), call('binary', 'extra')])
            run.reset_mock()
            self.assertFalse(cp.gate_selftest('binary', profile='missing')[0])
            run.assert_not_called()

    def test_broken_contribution_cannot_pass_combined_selftest(self):
        with patch.object(cp, 'addon_gate_contributions', return_value=[]), \
                patch.object(cp, 'ADDON_FAILED_HOOKS', {('broken', 'gate')}), \
                patch.object(cp, '_gate_selftest_profile') as run:
            self.assertFalse(cp.gate_selftest('binary')[0])
            run.assert_not_called()
