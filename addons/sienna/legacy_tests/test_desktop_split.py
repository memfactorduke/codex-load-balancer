"""Desktop extraction boundaries: real install payload, partial installs, parser and core-only build."""
from _helpers import addon, cp, HOME, REPO, run
import contextlib
import io
import pathlib
import shutil
import tempfile
import unittest
from unittest.mock import patch


class DesktopSplit(unittest.TestCase):
    def test_core_has_no_desktop_implementation(self):
        for name in ('desktop_module', 'desktop_backend', 'desktop_status', 'cmd_desktop',
                     'desktop_cpa_compatibility', 'desktop_wire_record_path', 'DESKTOP_P3'):
            self.assertFalse(hasattr(cp, name), name)
        self.assertFalse((REPO / 'bin/subpool_desktop.py').exists())
        self.assertTrue((REPO / 'tests/test_gate.py').is_file())

    def test_parser_routes_both_names_to_addon(self):
        for name in ('sienna', 'claude'):
            args = cp.build_parser().parse_args([name, 'desktop', 'status', '--json'])
            with patch.object(addon.desktop, 'desktop_backend') as backend:
                with contextlib.redirect_stdout(io.StringIO()):
                    args.fn(args)
                backend.return_value.__enter__.return_value.command.assert_called_once_with(args)

    def test_install_publishes_cli_and_complete_desktop_payload_or_rolls_back(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            dest = pathlib.Path(temp)
            (dest / 'bin').mkdir()
            (dest / 'bin/subpool').write_text('old CLI')
            old_addon = dest / 'addons/sienna'
            old_addon.mkdir(parents=True)
            (old_addon / 'old-marker').write_text('old add-on')
            replace = cp.os.replace
            def fail_cli(source, target):
                if pathlib.Path(target) == dest / 'bin/subpool' and 'payload' in pathlib.Path(source).parts:
                    raise OSError('interrupted CLI publication')
                return replace(source, target)
            with patch.multiple(cp, ROOT=dest, STATE=dest / 'state', ADDON_REGISTRY=dest / 'state/addons.json',
                                CODE_FILES=('bin/subpool',)):
                with patch.object(cp.os, 'replace', side_effect=fail_cli), self.assertRaises(OSError):
                    cp.copy_code()
                self.assertEqual((dest / 'bin/subpool').read_text(), 'old CLI')
                self.assertEqual((old_addon / 'old-marker').read_text(), 'old add-on')
                cp.copy_code()
                self.assertFalse((old_addon / 'old-marker').exists())
                for source, target in cp.code_files():
                    self.assertEqual(source.read_bytes(), target.read_bytes(), str(target))
                installed = cp.prepare_addon(old_addon, cp.read_addon_manifest(old_addon))
                self.assertEqual(installed.desktop.__file__, str(old_addon / 'desktop.py'))
                self.assertTrue(all(old_addon in path.parents for path in installed.desktop_build.desktop_wire_files()))

    def test_missing_desktop_or_fixture_refuses_partial_payload(self):
        for name in ('desktop.py', 'desktop_build.py', 'gate/desktop-wire/cli.json'):
            with self.subTest(name=name), tempfile.TemporaryDirectory(dir=HOME) as temp:
                dest = pathlib.Path(temp) / 'sienna'
                # Only the declared runtime payload is relevant; never historical logs.
                for source, relative in cp.addon_code_files(addon.path, addon.manifest):
                    target = pathlib.Path(temp) / relative.relative_to('addons')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)
                (dest / name).unlink()
                with self.assertRaisesRegex(ValueError, 'missing'):
                    cp.load_addon(dest, cp.read_addon_manifest(dest))

    def test_core_only_build_cache_needs_no_addon_evidence(self):
        with tempfile.TemporaryDirectory(dir=HOME) as temp, patch.object(cp, 'ADDONS', []):
            root = pathlib.Path(temp)
            with patch.multiple(cp, VERSIONS=root, GATE_SOURCE=REPO / 'build/codexpool_gate.go'):
                dest = root / ('v7.3.18-gate-' + cp.gate_id())
                dest.mkdir()
                (dest / 'cli-proxy-api').write_text('synthetic binary')
                with patch.object(cp, '_github_json', side_effect=AssertionError('unexpected download')):
                    self.assertEqual(cp.build('7.3.18'), dest)
