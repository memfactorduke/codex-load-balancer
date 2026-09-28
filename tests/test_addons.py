"""Add-on interface tests. No live services, external programs, or network requests."""
from _helpers import cp, ROOT, REPO, preserved, run_script

import contextlib
import copy
import io
import json
import pathlib
import py_compile
import shutil
import tempfile
import sys
import unittest
import weakref
from types import SimpleNamespace
from unittest.mock import patch

FIXTURE = REPO / 'tests/fixtures/addon_stub'


def failing(*args):
    raise RuntimeError('fixture failure')


class Addons(unittest.TestCase):
    def setUp(self):
        self.work = pathlib.Path(tempfile.mkdtemp(prefix='codexpool-addon-test-'))
        self.addCleanup(shutil.rmtree, self.work, True)
        self.checkout, self.installed = self.work / 'checkout', self.work / 'installed'
        settings = preserved(cp.SETTINGS_FILE)
        settings.__enter__()
        self.addCleanup(settings.__exit__, None, None, None)
        cp.write_json(cp.SETTINGS_FILE, {k: v for k, v in cp.SETTINGS.items() if k in cp.CORE_SETTINGS_DEFAULTS})
        self.checkout.mkdir()
        self.installed.mkdir()
        names = ('ADDONS', 'ADDON_ERRORS', 'ADDON_MANIFESTS', 'ADDON_PATHS', 'ADDON_NAMESPACES',
                 'ADDON_FAILED_HOOKS', 'ADDON_READY', 'SETTINGS_DEFAULTS', 'SETTABLE', 'SETTINGS',
                 'LANE_PROVIDERS', 'GUI_POOLS', 'GATE_PROFILES')
        for name in names:
            value = getattr(cp, name)
            saved = list(value) if name == 'ADDONS' else dict(value) if name == 'LANE_PROVIDERS' else copy.deepcopy(value)
            self.addCleanup(setattr, cp, name, saved)
        cp.ADDONS, cp.ADDON_ERRORS = [], {}
        cp.ADDON_MANIFESTS, cp.ADDON_PATHS, cp.ADDON_NAMESPACES = {}, {}, {}
        cp.ADDON_FAILED_HOOKS, cp.ADDON_READY = set(), False
        cp.SETTINGS_DEFAULTS, cp.SETTABLE = dict(cp.CORE_SETTINGS_DEFAULTS), dict(cp.CORE_SETTABLE)
        cp.LANE_PROVIDERS = {k: v for k, v in cp.LANE_PROVIDERS.items()
                             if not isinstance(v, cp.LaneProvider) or v.addon is None}
        cp.GUI_POOLS, cp.GATE_PROFILES = cp.CORE_GUI_POOLS, cp.CORE_GATE_PROFILES
        self.patches = patch.multiple(cp, ADDON_DIRS=(self.checkout, self.installed),
                                      ADDON_REGISTRY=self.work / 'reservations.json')
        self.patches.start()
        self.addCleanup(self.patches.stop)
        loader_state = patch.multiple(cp, _LOADED_ADDONS={}, _ADDON_PARSERS=weakref.WeakSet())
        loader_state.start()
        self.addCleanup(loader_state.stop)

    def loader_fixture(self):
        dest = self.fixture(code_files=['addon.py', 'component.py'])
        (dest / 'component.py').write_text('def callback(*args): return "old"\n')
        (dest / 'addon.py').write_text('''from . import component
from pathlib import Path
from types import SimpleNamespace
def load(cp):
    pool = cp.SeatPool('stub', 'stub', 49321, cp.ROOT / 'meta', cp.ROOT / 'guard',
        cp.ROOT / 'auth', cp.ROOT / 'config', cp.ROOT / 'link', cp.ROOT,
        cp.ROOT / 'log', 'codexpool stub', 'stub_mode', 'seat', cp.ROOT / 'stub.lock')
    def add_parser(sub, core):
        sub.add_parser('stub').set_defaults(fn=component.callback)
    return SimpleNamespace(id='stub', version='1.0.0', core_min='1.3.0', component=component,
        add_parser=add_parser, guard_passes=lambda: [('stub', component.callback, pool)],
        lane_providers=lambda: {'fixture': {'kind': 'bridge', 'test': component.callback}},
        bridge_extension=Path(component.__file__), menubar_extension=Path(component.__file__),
        gate=lambda: SimpleNamespace(profiles={'stub': component.callback}, sources=[], hash_inputs=[]))
''')
        return dest

    def test_loading_twice_preserves_package_submodules_and_callbacks(self):
        dest = self.loader_fixture()
        addon = self.boot()
        package = sys.modules['codexpool_addon_stub']
        cp.register_addon_extensions()
        provider = cp.LANE_PROVIDERS['fixture']
        again = cp.load_addon(dest / '.', copy.deepcopy(addon.manifest))
        self.assertIs(again, addon)
        self.assertIs(sys.modules['codexpool_addon_stub'], package)
        self.assertIs(again.component, addon.component)
        self.assertIs(cp.LANE_PROVIDERS['fixture'], provider)
        self.assertIs(cp.build_parser().parse_args(['stub']).fn, addon.component.callback)

    def test_repeated_initialization_does_not_duplicate_registrations(self):
        self.loader_fixture()
        addon = self.boot()
        cp.initialize_addons()
        cp.register_addon_extensions()
        cp.register_addon_extensions()
        self.assertEqual(len(cp.ADDONS), 1)
        self.assertIs(cp.ADDONS[0], addon)
        self.assertIs(cp.LANE_PROVIDERS['fixture'].addon, addon)
        self.assertEqual(cp.GUI_POOLS.count('stub'), 1)
        self.assertEqual(cp.GATE_PROFILES.count('stub'), 1)
        self.assertFalse(cp.ADDON_ERRORS)

    def test_changed_code_replaces_all_registered_hooks_and_existing_parsers(self):
        dest = self.loader_fixture()
        py_compile.compile(str(dest / 'component.py'), doraise=True)
        old = self.boot()
        cp.register_addon_extensions()
        parser = cp.build_parser()
        component = dest / 'component.py'
        before = component.stat()
        component.write_text('def callback(*args): return "new"\n')
        # Equal size and timestamp must not hide a source change behind a .pyc.
        cp.os.utime(component, ns=(before.st_atime_ns, before.st_mtime_ns))
        new = cp.load_addon(dest, old.manifest)
        self.assertIsNot(new, old)
        self.assertIsNot(new.component, old.component)
        self.assertEqual(new.component.callback(), 'new')
        self.assertIs(sys.modules['codexpool_addon_stub.component'], new.component)
        self.assertIs(cp.ADDONS[0], new)
        for current in (parser, cp.build_parser()):
            self.assertIs(current.parse_args(['stub']).fn, new.component.callback)
        provider = cp.LANE_PROVIDERS['fixture']
        self.assertIs(provider.addon, new)
        self.assertIs(provider.provider['test'], new.component.callback)
        self.assertIs(new.guard_passes()[0][1], new.component.callback)
        with patch.object(new.component, 'callback') as callback, patch.object(cp, 'log_line'):
            cp.addon_guard_passes()
        callback.assert_called_once_with()
        with patch.object(old.implementation, 'bridge_extension', dest / 'addon.py'):
            config = {}
            cp.addon_bridge_extension([{'members': [{'provider': 'fixture'}]}], config)
        self.assertEqual(config['extensions']['stub'], str(cp.ROOT / 'addons/stub/component.py'))
        self.assertEqual(cp.GUI_POOLS.count('stub'), 1)
        self.assertEqual(cp.GATE_PROFILES.count('stub'), 1)
        self.assertIs(cp.addon_gate_contributions()[0][2]['stub'], new.component.callback)
        self.assertFalse(cp.ADDON_ERRORS)

    def test_manifest_path_and_explicit_reload_invalidate_identity(self):
        dest = self.loader_fixture()
        old = self.boot()
        manifest = copy.deepcopy(old.manifest)
        manifest['title'] = 'Changed title'
        new = cp.load_addon(dest, manifest)
        self.assertIsNot(new, old)
        manifest['title'] = 'Caller mutation'
        self.assertEqual(new.manifest['title'], 'Changed title')
        moved = self.installed / 'stub'
        shutil.copytree(dest, moved)
        relocated = cp.load_addon(moved, new.manifest)
        self.assertIsNot(relocated, new)
        self.assertEqual(pathlib.Path(relocated.component.__file__).parent, moved)
        forced = cp.load_addon(moved, relocated.manifest, reload=True)
        self.assertIsNot(forced, relocated)
        self.assertIs(cp.ADDONS[0], forced)
        self.assertIs(cp.load_addon(moved, forced.manifest), forced)

    def test_failed_reload_keeps_existing_modules_and_registries(self):
        dest = self.loader_fixture()
        old = self.boot()
        cp.register_addon_extensions()
        provider = cp.LANE_PROVIDERS['fixture']
        parser = cp.build_parser()
        (dest / 'addon.py').write_text('from . import component\nraise RuntimeError("bad reload")\n')
        with self.assertRaisesRegex(RuntimeError, 'bad reload'):
            cp.load_addon(dest, old.manifest)
        self.assertIs(cp.ADDONS[0], old)
        self.assertIs(cp.LANE_PROVIDERS['fixture'], provider)
        self.assertIs(sys.modules['codexpool_addon_stub.component'], old.component)
        self.assertIs(parser.parse_args(['stub']).fn, old.component.callback)

    def test_preparing_install_copy_does_not_replace_active_modules(self):
        dest = self.loader_fixture()
        old = self.boot()
        cp.register_addon_extensions()
        provider = cp.LANE_PROVIDERS['fixture']
        moved = self.installed / 'stub'
        shutil.copytree(dest, moved)
        prepared = cp.prepare_addon(moved, old.manifest)
        self.assertIsNot(prepared.component, old.component)
        self.assertIs(cp.ADDONS[0], old)
        self.assertIs(cp.LANE_PROVIDERS['fixture'], provider)
        self.assertIs(sys.modules['codexpool_addon_stub.component'], old.component)
        self.assertIs(cp.load_addon(dest, old.manifest), old)

    def test_asset_changes_and_missing_payload_are_not_cache_hits(self):
        dest = self.fixture()
        old = self.boot()
        (dest / 'payload.txt').write_text('changed asset')
        changed = cp.load_addon(dest, old.manifest)
        self.assertIsNot(changed, old)
        (dest / 'payload.txt').unlink()
        with self.assertRaisesRegex(ValueError, 'missing code file'):
            cp.load_addon(dest, old.manifest)
        self.assertIs(cp.ADDONS[0], changed)

    def test_reload_removes_retired_extensions_and_retries_failed_hooks(self):
        dest = self.loader_fixture()
        old = self.boot()
        cp.register_addon_extensions()
        parser = cp.build_parser()
        cp.ADDON_FAILED_HOOKS.add(('stub', 'add_parser'))
        cp.ADDON_ERRORS['stub'] = 'old parser failure'
        code = dest / 'addon.py'
        code.write_text(code.read_text().replace("'fixture': {'kind': 'bridge', 'test': component.callback}", '')
                        .replace('menubar_extension=Path(component.__file__)', 'menubar_extension=None')
                        .replace("profiles={'stub': component.callback}", 'profiles={}'))
        new = cp.load_addon(dest, old.manifest)
        self.assertNotIn('fixture', cp.LANE_PROVIDERS)
        self.assertNotIn('stub', cp.GUI_POOLS)
        self.assertNotIn('stub', cp.GATE_PROFILES)
        self.assertIs(parser.parse_args(['stub']).fn, new.component.callback)
        self.assertNotIn(('stub', 'add_parser'), cp.ADDON_FAILED_HOOKS)
        self.assertNotIn('stub', cp.ADDON_ERRORS)

    def fixture(self, base=None, **metadata):
        dest = (base or self.checkout) / 'stub'
        shutil.copytree(FIXTURE, dest)
        if metadata:
            manifest = json.loads((dest / 'addon.json').read_text())
            manifest.update(metadata)
            (dest / 'addon.json').write_text(json.dumps(manifest))
        return dest

    def boot(self):
        cp.discover_addons()
        cp.initialize_addons()
        return cp.ADDONS[0] if cp.ADDONS else None

    def output(self, fn, *args):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = fn(*args)
        return out.getvalue(), result

    def test_absent_is_noop_and_unknown_settings_still_fail(self):
        self.boot()
        self.assertEqual(cp.ADDONS, [])
        self.assertEqual(cp.ADDON_ERRORS, {})
        self.assertEqual(cp.SETTINGS_DEFAULTS, cp.CORE_SETTINGS_DEFAULTS)
        with self.assertRaises(cp.SettingsError):
            cp.parse_settings({'nonesuch_port': 49234})
        self.assertEqual(self.output(cp.cmd_version, None)[0], f'codexpool {cp.VERSION}\n')

    def test_manifest_is_data_only_and_runtime_is_late(self):
        dest = self.fixture()
        (dest / 'addon.py').write_text('raise SystemExit("executed")\n')
        cp.discover_addons()
        self.assertFalse(cp.ADDON_ERRORS)
        self.assertEqual(cp.SETTINGS_DEFAULTS['stub_mode'], 'off')
        cp.initialize_addons()
        self.assertIn('executed', cp.ADDON_ERRORS['stub'])
        self.assertEqual(cp.ADDONS, [])
        self.assertNotIn('stub_mode', cp.SETTABLE)
        self.assertNotIn('stub_mode', cp.parse_settings({'stub_mode': 'on'}))

    def test_complete_core_visible_during_load(self):
        self.fixture()
        addon = self.boot()
        self.assertEqual(addon.id, 'stub')
        self.assertEqual(cp.SETTINGS['stub_mode'], 'off')
        self.assertEqual(self.output(cp.cmd_version, None)[0], f'codexpool {cp.VERSION}\n+ stub 1.0.0\n')

    def test_checkout_wins_even_if_partial(self):
        self.fixture(self.installed)
        dest = self.fixture()
        (dest / 'addon.py').unlink()
        self.boot()
        self.assertFalse(cp.ADDONS)
        self.assertEqual(cp.ADDON_PATHS['stub'], dest)
        self.assertIn('load:', cp.ADDON_ERRORS['stub'])

    def test_incompatible_reserves_legacy_prefix_without_execution(self):
        dest = self.fixture(core_min='999.0.0')
        (dest / 'addon.py').write_text('raise AssertionError("must not execute")\n')
        self.boot()
        self.assertIn('needs codexpool', cp.ADDON_ERRORS['stub'])
        self.assertNotIn('must not execute', cp.ADDON_ERRORS['stub'])
        cp.parse_settings({'legacyfixture_old': 'retained'})

    def test_absent_code_reservation_survives(self):
        cp.ADDON_REGISTRY.write_text(json.dumps({'stub': ['stub_', 'legacyfixture_']}))
        self.boot()
        cp.parse_settings({'legacyfixture_old': 'retained', 'stub_mode': 'on'})
        self.assertIn('code absent', cp.ADDON_ERRORS['stub'])

    def test_corrupt_manifest_retains_persisted_namespace(self):
        dest = self.fixture()
        cp.ADDON_REGISTRY.write_text(json.dumps({'stub': ['legacyfixture_']}))
        (dest / 'addon.json').write_text('{broken')
        self.boot()
        cp.parse_settings({'legacyfixture_old': 1})
        self.assertFalse(cp.ADDONS)
        self.assertIn('manifest:', cp.ADDON_ERRORS['stub'])

    def test_namespace_collision_and_core_setting_collision(self):
        self.fixture(settings_prefixes=['pool_'], settings={'pool_label': 'bad'}, settable={})
        self.boot()
        self.assertFalse(cp.ADDONS)
        self.assertEqual(cp.SETTINGS_DEFAULTS['pool_label'], cp.CORE_SETTINGS_DEFAULTS['pool_label'])
        self.assertIn('collide', cp.ADDON_ERRORS['stub'])

    def test_namespace_collision_does_not_overwrite_defaults(self):
        self.fixture()
        cp.ADDON_REGISTRY.write_text(json.dumps({'other': ['stub_']}))
        self.boot()
        self.assertFalse(cp.ADDONS)
        self.assertIn('reserved by other', cp.ADDON_ERRORS['stub'])

    def test_later_namespace_collision_cannot_remove_the_first_addons_settings(self):
        self.fixture()
        second = self.checkout / 'zz'
        shutil.copytree(FIXTURE, second)
        manifest = json.loads((second / 'addon.json').read_text())
        manifest['id'] = 'zz'
        (second / 'addon.json').write_text(json.dumps(manifest))
        self.boot()
        self.assertEqual([a.id for a in cp.ADDONS], ['stub'])
        self.assertEqual(cp.SETTINGS_DEFAULTS['stub_mode'], 'off')
        self.assertEqual(cp.SETTABLE['stub_mode'], ['off', 'on'])
        with self.assertRaises(cp.SettingsError):
            cp.parse_settings({'stub_typo': 1})

    def test_settings_hook_derives_only_owned_values(self):
        self.fixture()
        addon = self.boot()
        addon.implementation.settings_problems = lambda s: s.update(stub_port=49325) or []
        settings = cp.parse_settings({})
        self.assertEqual(settings['stub_port'], 49325)
        addon.implementation.settings_problems = lambda s: s.update(port=1) or []
        settings = cp.parse_settings({})
        self.assertEqual(settings['port'], cp.CORE_SETTINGS_DEFAULTS['port'])
        self.assertIn('does not own', cp.ADDON_ERRORS['stub'])

    def test_settings_failure_does_not_stop_core(self):
        dest = self.fixture()
        text = (dest / 'addon.py').read_text().replace('return []', 'raise SystemExit("bad settings")', 1)
        (dest / 'addon.py').write_text(text)
        self.boot()
        self.assertFalse(cp.ADDONS)
        self.assertEqual(cp.SETTINGS['port'], cp.PORT)
        self.assertIn('bad settings', cp.ADDON_ERRORS['stub'])

    def test_addon_ports_and_labels_must_be_distinct(self):
        self.fixture(settings={'stub_port': cp.PORT, 'stub_label': cp.POOL_JOB}, settable={})
        self.boot()
        self.assertFalse(cp.ADDONS)
        self.assertIn('stub_port must differ', cp.ADDON_ERRORS['stub'])
        self.assertIn('stub_label must differ', cp.ADDON_ERRORS['stub'])

    def test_parser_failure_rolls_back_partial_commands(self):
        self.fixture()
        addon = self.boot()
        def partial(sub, core):
            sub.add_parser('half')
            raise SystemExit('parser failed')
        addon.implementation.add_parser = partial
        parser = cp.build_parser()
        self.assertEqual(parser.parse_args(['status']).cmd, 'status')
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            parser.parse_args(['half'])
        self.assertIn('parser failed', cp.ADDON_ERRORS['stub'])

    def test_parser_cannot_replace_core_command_or_alias(self):
        self.fixture()
        addon = self.boot()
        addon.implementation.add_parser = lambda sub, core: sub.add_parser('status')
        parser = cp.build_parser()
        self.assertIs(parser.parse_args(['status']).fn, cp.cmd_status)
        self.assertIn('collides', cp.ADDON_ERRORS['stub'])

    def test_guard_discovery_failure_runs_after_core(self):
        self.fixture()
        addon = self.boot()
        order = []
        addon.implementation.guard_passes = lambda: order.append('addon') or failing()
        with patch.object(cp, 'guard_pass', side_effect=lambda: order.append('core')), \
                patch.object(cp, 'lock_down_logins'), patch.object(cp, 'log_line') as log:
            cp.cmd_guard(None)
        self.assertEqual(order, ['core', 'addon'])
        self.assertTrue(log.called)

    def test_guard_callback_systemexit_isolated(self):
        self.fixture()
        addon = self.boot()
        pool = cp.SeatPool('stub', 'stub', 49321, self.work / 'meta', self.work / 'guard',
                           self.work / 'auth', self.work / 'config', self.work / 'link', self.work,
                           self.work / 'log', 'codexpool stub', 'stub_mode', 'seat', self.work / 'lock')
        def die():
            raise SystemExit(9)
        addon.implementation.guard_passes = lambda: [('fixture', die, pool)]
        with patch.object(cp, 'guard_pass') as core, \
                patch.object(cp, 'lock_down_logins'), patch.object(cp, 'log_line'):
            cp.cmd_guard(None)
        core.assert_called_once()
        self.assertIn('SystemExit', cp.ADDON_ERRORS['stub'])

    def test_lazy_hook_failure_is_caught(self):
        self.fixture()
        addon = self.boot()
        def lazy():
            yield ('partial', None, None)
            raise RuntimeError('lazy failure')
        addon.implementation.guard_passes = lazy
        with patch.object(cp, 'log_line'):
            cp.addon_guard_passes()
        self.assertIn('lazy failure', cp.ADDON_ERRORS['stub'])

    def test_status_failure_preserves_core_json(self):
        self.fixture()
        addon = self.boot()
        addon.implementation.status = failing
        with patch.object(cp, 'require_installed'), patch.object(cp, 'status_now', return_value={'pool': {}}):
            text, _ = self.output(cp.cmd_status, SimpleNamespace(live=True, json=True))
        self.assertEqual(json.loads(text), {'pool': {}})
        self.assertIn('status:', cp.ADDON_ERRORS['stub'])

    def test_status_collision_or_invalid_json_isolated(self):
        self.fixture()
        addon = self.boot()
        addon.implementation.status = lambda live: ('pool', {}, lambda: None)
        self.assertEqual(cp.addon_status(False, {'pool': {}}), [])
        self.assertIn('collides', cp.ADDON_ERRORS['stub'])

    def test_doctor_failure_keeps_core_and_reports_warning(self):
        self.fixture()
        addon = self.boot()
        def broken(rep):
            rep.section('Half section')
            raise SystemExit('doctor failed')
        addon.implementation.doctor = broken
        rep = cp.DoctorReport(echo=False)
        rep.section('Core')
        rep.check(True, 'core ready')
        cp.doctor_addons(rep)
        self.assertTrue(rep.as_json()['ok'])
        self.assertEqual([s['title'] for s in rep.sections], ['Core', 'Add-ons'])
        self.assertEqual(rep.warnings, 1)

    def test_successful_reporting_and_on_set(self):
        self.fixture()
        self.boot()
        rep = cp.DoctorReport(echo=False)
        cp.doctor_addons(rep)
        self.assertEqual([s['title'] for s in rep.sections], ['Fixture pool', 'Add-ons'])
        with preserved(cp.SETTINGS_FILE), patch.object(cp, 'show_settings_now') as show:
            text, _ = self.output(cp.cmd_set, SimpleNamespace(key='stub_mode', value='on'))
        show.assert_not_called()
        self.assertIn('Fixture setting saved.', text)

    def test_unknown_pool_never_falls_back_to_codex(self):
        self.boot()
        for lookup in (cp.pool_instance, cp.seat_pool):
            with self.assertRaisesRegex(ValueError, 'unknown'):
                lookup('missing')
        with self.assertRaisesRegex(ValueError, 'unknown'):
            cp.seat_pool(SimpleNamespace(pool='missing'))

    def test_code_files_are_declared_and_cannot_escape(self):
        dest = self.fixture()
        (dest / 'not-declared.txt').write_text('not copied')
        self.boot()
        files = cp.addon_code_files(dest, cp.ADDON_MANIFESTS['stub'])
        self.assertEqual({src.name for src, _ in files}, {'addon.json', 'addon.py', 'payload.txt'})
        (dest / 'payload.txt').unlink()
        (dest / 'payload.txt').symlink_to(self.work / 'outside')
        with self.assertRaises(ValueError):
            cp.addon_code_files(dest, cp.ADDON_MANIFESTS['stub'])

    def test_invalid_manifest_paths_and_versions(self):
        dest = self.fixture(code_files=['addon.py', '../outside'])
        with self.assertRaises(ValueError):
            cp.read_addon_manifest(dest)
        with self.assertRaises(ValueError):
            cp.parse_version('1.0')

    def test_install_remove_and_reservation_with_runtime_state_preserved(self):
        dest = self.fixture()
        with patch.object(cp, 'ROOT', self.installed):
            text, _ = self.output(cp.cmd_addon, SimpleNamespace(addon_cmd='install', path=str(dest)))
            installed = self.installed / 'addons/stub'
            self.assertEqual((installed / 'payload.txt').read_text(), (dest / 'payload.txt').read_text())
            self.assertIn('Installed add-on stub', text)
            state = self.installed / 'stub-state.json'
            state.write_text('retained')
            self.output(cp.cmd_addon, SimpleNamespace(addon_cmd='remove', id='stub'))
            self.assertFalse(installed.exists())
            self.assertEqual(state.read_text(), 'retained')
            self.assertIn('legacyfixture_', json.loads(cp.ADDON_REGISTRY.read_text())['stub'])

    def test_remove_refuses_installed_components_or_broken_code(self):
        dest = self.fixture()
        with patch.object(cp, 'ROOT', self.installed):
            self.output(cp.cmd_addon, SimpleNamespace(addon_cmd='install', path=str(dest)))
            code = self.installed / 'addons/stub/addon.py'
            code.write_text(code.read_text().replace('def uninstall_plan(self):\n            return []',
                                                    'def uninstall_plan(self):\n            return [("stop", lambda: None)]'))
            with self.assertRaisesRegex(SystemExit, 'installed components'):
                cp.cmd_addon(SimpleNamespace(addon_cmd='remove', id='stub'))
            code.write_text('raise SystemExit("broken")')
            with self.assertRaisesRegex(SystemExit, 'broken'):
                cp.cmd_addon(SimpleNamespace(addon_cmd='remove', id='stub'))
            self.assertTrue(code.exists())

    def test_install_publish_failure_rolls_back(self):
        dest = self.fixture()
        with patch.object(cp, 'ROOT', self.installed):
            self.output(cp.cmd_addon, SimpleNamespace(addon_cmd='install', path=str(dest)))
            installed = self.installed / 'addons/stub/payload.txt'
            before = installed.read_bytes()
            (dest / 'payload.txt').write_text('new code')
            replace = cp.os.replace
            def fail_publish(src, target):
                if pathlib.Path(src).name == 'payload':
                    raise OSError('fixture publish failed')
                return replace(src, target)
            with patch.object(cp.os, 'replace', side_effect=fail_publish), self.assertRaises(SystemExit):
                cp.cmd_addon(SimpleNamespace(addon_cmd='install', path=str(dest)))
            self.assertEqual(installed.read_bytes(), before)

    def test_missing_optional_hooks_are_noops(self):
        dest = self.fixture()
        (dest / 'addon.py').write_text('from types import SimpleNamespace\n'
                                      'def load(cp): return SimpleNamespace(id="stub", version="1.0.0", core_min="1.3.0")\n')
        self.boot()
        cp.build_parser()
        cp.addon_status(False, {})
        cp.doctor_addons(cp.DoctorReport(echo=False))
        cp.register_addon_extensions()
        self.assertFalse(cp.ADDON_ERRORS)

    def test_partial_payload_is_rejected_before_execution(self):
        dest = self.fixture()
        (dest / 'payload.txt').unlink()
        (dest / 'addon.py').write_text('raise AssertionError("must not run")')
        self.boot()
        self.assertFalse(cp.ADDONS)
        self.assertIn('missing code file payload.txt', cp.ADDON_ERRORS['stub'])
        self.assertNotIn('must not run', cp.ADDON_ERRORS['stub'])

    def test_relative_imports_and_metadata_identity(self):
        dest = self.fixture(code_files=['addon.py', 'component.py'])
        (dest / 'component.py').write_text('value = "stub"')
        (dest / 'addon.py').write_text('from .component import value\nfrom types import SimpleNamespace\n'
                                      'def load(cp): return SimpleNamespace(id=value, version="1.0.0", core_min="1.3.0")')
        self.assertEqual(self.boot().id, 'stub')

    def test_runtime_hook_stdout_cannot_corrupt_status_json(self):
        self.fixture()
        addon = self.boot()
        def noisy(live):
            print('add-on diagnostic')
            raise SystemExit('failed')
        addon.implementation.status = noisy
        with patch.object(cp, 'require_installed'), patch.object(cp, 'status_now', return_value={'pool': {}}), contextlib.redirect_stderr(io.StringIO()):
            text, _ = self.output(cp.cmd_status, SimpleNamespace(live=True, json=True))
        self.assertEqual(json.loads(text), {'pool': {}})

    def test_copy_code_stages_addon_and_cli_and_rolls_back(self):
        self.fixture()
        self.boot()
        source = self.work / 'core'
        (source / 'bin').mkdir(parents=True)
        (source / 'bin/codexpool').write_text('new CLI')
        (self.installed / 'bin').mkdir()
        (self.installed / 'bin/codexpool').write_text('old CLI')
        with patch.multiple(cp, ROOT=self.installed, CODE_DIR=source, CODE_FILES=('bin/codexpool',)):
            real_replace = cp.os.replace
            def fail_cli(src, dest):
                if 'payload/bin/codexpool' in str(src):
                    raise OSError('CLI publish failed')
                return real_replace(src, dest)
            with patch.object(cp.os, 'replace', side_effect=fail_cli), self.assertRaises(OSError):
                cp.copy_code()
            self.assertEqual((self.installed / 'bin/codexpool').read_text(), 'old CLI')
            self.assertFalse((self.installed / 'addons/stub').exists())
            cp.copy_code()
        self.assertEqual((self.installed / 'bin/codexpool').read_text(), 'new CLI')
        self.assertEqual((self.installed / 'addons/stub/payload.txt').read_text(), 'fixture code asset\n')

    def test_bridge_extension_only_for_its_own_provider(self):
        dest = self.fixture()
        addon = self.boot()
        addon.implementation.bridge_extension = dest / 'addon.py'
        addon.implementation.lane_providers = lambda: {'fixture': {'kind': 'bridge'}}
        config = {}
        cp.addon_bridge_extension([{'members': [{'provider': 'responses'}]}], config)
        self.assertEqual(config, {})
        cp.addon_bridge_extension([{'members': [{'provider': 'fixture'}]}], config)
        self.assertEqual(config['extensions'], {'stub': str(cp.ROOT / 'addons/stub/addon.py')})

    def test_failed_parser_diagnostics_leave_json_stdout_clean(self):
        self.fixture()
        addon = self.boot()
        def broken(sub, core):
            print('parser diagnostic')
            raise RuntimeError('parser failed')
        addon.implementation.add_parser = broken
        with contextlib.redirect_stderr(io.StringIO()):
            text, parser = self.output(cp.build_parser)
        self.assertEqual(text, '')
        self.assertIs(parser.parse_args(['status']).fn, cp.cmd_status)

    def test_gate_hash_covers_addon_sources_and_fixtures(self):
        dest = self.fixture()
        addon = self.boot()
        go, fixture = dest / 'gate.go', dest / 'case.json'
        go.write_text('package main')
        fixture.write_text('{}')
        gate = SimpleNamespace(profiles={'stub': lambda *args: []}, sources=[go], hash_inputs=[fixture])
        addon.implementation.gate = lambda: gate
        core = cp.gate_id(b'core gate', profile='codex')
        before = cp.gate_id(b'core gate')
        fixture.write_text('{"changed": true}')
        self.assertNotEqual(cp.gate_id(b'core gate'), before)
        self.assertEqual(cp.gate_id(b'core gate', profile='codex'), core)
        self.assertNotEqual(cp.gate_id(b'changed core', profile='codex'), core)
        after_fixture = cp.gate_id(b'core gate')
        go.write_text('package main // changed contribution')
        self.assertNotEqual(cp.gate_id(b'core gate'), after_fixture)
        self.assertEqual(cp.gate_id(b'core gate', profile='codex'), core)
        core_file = dest / 'core.go'
        core_file.write_bytes(b'core gate')
        with patch.object(cp, 'GATE_SOURCE', core_file):
            self.assertEqual(cp.gate_source_note('1.0+gate.' + core, cp.pool_instance('codex')), '')
            extra = SimpleNamespace(profile='stub', next_rebuild='addon rebuild')
            self.assertIn(cp.gate_id(profile='stub'), cp.gate_source_note('1.0+gate.' + before, extra))
        addon.implementation.gate = failing
        cp.gate_id(b'core gate')  # doctor can still evaluate the core gate
        self.assertIn('gate:', cp.ADDON_ERRORS['stub'])

    def test_failed_registries_do_not_replace_core(self):
        self.fixture()
        addon = self.boot()
        before = dict(cp.LANE_PROVIDERS)
        addon.implementation.lane_providers = lambda: {'xai': {'kind': 'other'}}
        addon.implementation.gate = failing
        cp.register_addon_extensions()
        self.assertEqual(cp.LANE_PROVIDERS, before)
        self.assertEqual(cp.GATE_PROFILES, ('codex',))
        self.assertIn('lane_providers:', cp.ADDON_ERRORS['stub'])
        self.assertIn('gate:', cp.ADDON_ERRORS['stub'])

    def test_full_import_and_help_with_and_without_addon(self):
        # Subprocess uses _helpers' throwaway HOME and stubs. No add-on is put in the checkout.
        with preserved(ROOT / 'addons/stub/addon.json', ROOT / 'addons/stub/addon.py'):
            target = ROOT / 'addons/stub'
            target.mkdir(parents=True, exist_ok=True)
            for name in ('addon.json', 'addon.py', 'payload.txt'):
                shutil.copy2(FIXTURE / name, target / name)
            try:
                result = run_script('version')
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn('+ stub 1.0.0', result.stdout)
            finally:
                shutil.rmtree(target)
        if sys.version_info >= (3, 11):  # only --help and version run on older Pythons; the rest need 3.11+
            self.assertEqual(run_script('addon', 'list').returncode, 0)


if __name__ == '__main__':
    unittest.main()
