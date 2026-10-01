"""Build-time desktop compatibility is advisory; use only synthetic source and a fake Go."""
from _helpers import addon
sienna_desktop, desktop_build = addon.desktop, addon.desktop_build

from _helpers import cp, HOME, REPO
import contextlib
import io
import pathlib
import tempfile
import unittest
from unittest.mock import Mock, patch


class DesktopBuildTests(unittest.TestCase):
    def test_wire_failure_does_not_abort_build_or_build_selection(self):
        with tempfile.TemporaryDirectory(dir=HOME) as tmp, contextlib.ExitStack() as stack:
            root = pathlib.Path(tmp)
            for name in ('build', 'versions', 'toolchain/current/bin', 'state'):
                (root / name).mkdir(parents=True)
            (root / 'toolchain/current/bin/go').touch()
            for name, value in {'BUILD': root / 'build', 'VERSIONS': root / 'versions',
                                'TOOLCHAIN': root / 'toolchain', 'STATE': root / 'state',
                                'GATE_SOURCE': REPO / 'build/codexpool_gate.go'}.items():
                stack.enter_context(patch.object(cp, name, value))
            stack.enter_context(patch.object(cp, '_github_json', return_value={'sha': '12345678'}))
            stack.enter_context(patch.object(cp, '_download'))
            archive = Mock()
            archive.__enter__ = Mock(return_value=archive)
            archive.__exit__ = Mock(return_value=False)
            archive.getnames.return_value = ['cpa/']

            def extract(dest, **kwargs):
                tree = dest / 'cpa'
                (tree / 'cmd/server').mkdir(parents=True)
                (tree / 'cmd/server/main.go').write_text(cp.GATE_ANCHOR)
                (tree / 'go.mod').write_text('module github.com/router-for-me/CLIProxyAPI/v7\n')
                helpers = tree / 'internal/runtime/executor/helps'
                helpers.mkdir(parents=True)
                (helpers / 'detector.go').write_text('func DetectClaudeCodeRequest() {}')
                for name in ('config.example.yaml', 'LICENSE'):
                    (tree / name).write_text('synthetic')

            archive.extractall.side_effect = extract
            stack.enter_context(patch.object(cp.tarfile, 'open', return_value=archive))
            paths = ['$.headers.User-Agent', '$.headers.Anthropic-Beta',
                     '$.headers.Accept-Encoding', '$.headers.X-Client-Request-Id']

            def go(args, **kwargs):
                if args[1] == 'test':
                    return Mock(returncode=1, stdout='\n'.join(paths) + '\n' + paths[0], stderr='')
                if args[1] == 'build':
                    pathlib.Path(args[args.index('-o') + 1]).write_text('synthetic binary')
                return Mock(returncode=0, stdout='go version synthetic', stderr='')

            runner = stack.enter_context(patch.object(cp.subprocess, 'run', side_effect=go))
            gate = stack.enter_context(patch.object(cp, 'gate_selftest', return_value=(True, 'both profiles pass')))
            output = stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            pool = Mock(link=root / 'claude-current', profile='claude')
            dest = cp.build_for(pool, '7.3.18')
            self.assertEqual(pool.link.resolve(), dest)
            self.assertTrue((dest / 'cli-proxy-api').exists())
            gate.assert_called_once()
            record = cp.read_json(desktop_build.desktop_wire_record_path(dest.name), {})
            self.assertEqual(record['result'], 'FAIL')
            self.assertEqual(record['build_id'], dest.name)
            self.assertEqual(record['version'], '7.3.18')
            self.assertEqual(record['failing_paths'], sorted(paths))
            self.assertIn('continuing the pool build', output.getvalue())
            runner.reset_mock()
            self.assertEqual(cp.build('7.3.18', profile='claude'), dest)
            runner.assert_not_called()  # a recorded FAIL is a usable pool build
            desktop_build.desktop_wire_record_path(dest.name).unlink()
            self.assertEqual(cp.build('7.3.18', profile='claude'), dest)  # legacy cache acquires evidence
            self.assertTrue(desktop_build.desktop_wire_record_path(dest.name).exists())
            # A broken extension hook must never select a build, unlike an advisory wire FAIL.
            for hook in ('cache_valid', 'pre_build', 'post_build'):
                with self.subTest(hook=hook):
                    record = desktop_build.desktop_wire_record_path(dest.name)
                    if record.exists():
                        record.unlink()
                    with patch.object(desktop_build.DesktopBuild, hook, side_effect=RuntimeError('hook failed')), \
                            patch.object(cp, 'ADDON_ERRORS', {}), patch.object(cp, 'ADDON_FAILED_HOOKS', set()), \
                            patch.object(cp, '_point_current') as select:
                        with self.assertRaisesRegex(RuntimeError, 'add-on gate failed'):
                            cp.build_for(pool, '7.3.18')
                        select.assert_not_called()



if __name__ == '__main__':
    unittest.main()
