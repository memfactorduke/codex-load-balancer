"""settings.json: validation (load_settings / parse_settings) and `codexpool set`."""
import json
import os
import threading
import unittest
from unittest import mock

from _helpers import HOME, REPO, ROOT, cp, preserved, run, run_script, settings_restored


def parse(raw):
    return cp.parse_settings(raw, where='settings.json')


class ParseSettings(unittest.TestCase):
    def assertRefused(self, raw, *texts):
        with self.assertRaises(cp.SettingsError) as ctx:
            parse(raw)
        for text in texts:
            self.assertIn(text, str(ctx.exception))
        return str(ctx.exception)

    def test_defaults(self):
        s = parse({})
        self.assertEqual(s, cp.SETTINGS_DEFAULTS)
        self.assertEqual((s['port'], s['bridge_port'], s['display'], s['headline']), (8319, 8320, 'left', 'all'))

    def test_example_file_is_valid(self):
        s = parse(json.loads((REPO / 'examples' / 'settings.json').read_text()))
        self.assertEqual(s, cp.SETTINGS_DEFAULTS)

    def test_bridge_port_follows_a_pool_moved_to_8320(self):
        self.assertEqual((parse({'port': 8320})['port'], parse({'port': 8320})['bridge_port']), (8320, 8321))

    def test_explicit_ports(self):
        s = parse({'port': 9000, 'bridge_port': 9001})
        self.assertEqual((s['port'], s['bridge_port']), (9000, 9001))
        self.assertEqual(parse({'port': 9000})['bridge_port'], 8320)

    def test_bridge_port_must_differ_from_port(self):
        self.assertRefused({'port': 8320, 'bridge_port': 8320}, 'bridge_port (must differ from port)')
        self.assertRefused({'bridge_port': 8319}, 'bridge_port (must differ from port)')

    def test_bad_ports(self):
        for bad in (0, 65536, -1, '8319', 8319.0, True, None):
            self.assertRefused({'port': bad}, 'invalid port')
            self.assertRefused({'bridge_port': bad}, 'invalid bridge_port')

    def test_bad_labels(self):
        for key in ('pool_label', 'guard_label', 'menubar_label', 'bridge_label'):
            for bad in ('', 'has space', '-lead', None, 5, 'a/b'):
                self.assertRefused({key: bad}, f'invalid {key}')
        self.assertEqual(parse({'pool_label': 'com.example.pool_2-x'})['pool_label'], 'com.example.pool_2-x')

    def test_interpreters(self):
        self.assertEqual(parse({'python': '~/py/bin/python'})['python'], '~/py/bin/python')
        for key in ('python', 'menubar_python', 'codex_bin'):
            self.assertRefused({key: ''}, f'invalid {key}')
            self.assertRefused({key: '  '}, f'invalid {key}')
            self.assertRefused({key: 3}, f'invalid {key}')
            self.assertIsNone(parse({key: None})[key])

    def test_choices(self):
        self.assertEqual(parse({'display': 'used', 'headline': 'regular'})['display'], 'used')
        self.assertRefused({'display': 'Left'}, 'display (one of: left, used)')
        self.assertRefused({'headline': 'reserve'}, 'headline (one of: all, regular)')

    def test_unknown_keys_and_notes(self):
        message = self.assertRefused({'colour': 'red', 'port': 9000}, 'unknown key(s) colour', 'keys starting with _')
        self.assertIn('known: pool_label', message)
        self.assertEqual(parse({'_note': 'anything', '_port': 1})['port'], 8319)

    def test_not_an_object(self):
        for raw in ([], 'x', 3, None):
            self.assertRefused(raw, 'must hold a JSON object')

    def test_every_problem_is_listed(self):
        message = self.assertRefused({'port': 0, 'display': 'x', 'pool_label': ''}, 'port', 'display', 'pool_label')
        self.assertTrue(message.startswith('invalid '))


class LoadSettings(unittest.TestCase):
    """A broken settings.json stops every command before it does anything."""

    def write(self, text):
        path = HOME / 'settings-under-test.json'
        path.write_text(text)
        self.addCleanup(path.unlink)
        return {'CODEXPOOL_SETTINGS': str(path)}

    def test_unknown_key_stops_the_command(self):
        r = run_script('version', env=self.write('{"colour": "red"}'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('unknown key(s) colour', r.stderr)
        self.assertEqual(r.stdout, '')

    def test_clash_stops_the_command(self):
        r = run_script('version', env=self.write('{"port": 8320, "bridge_port": 8320}'))
        self.assertEqual(r.returncode, 1)
        self.assertIn('bridge_port (must differ from port)', r.stderr)

    def test_unreadable_json(self):
        r = run_script('version', env=self.write('{"port": '))
        self.assertEqual(r.returncode, 1)
        self.assertIn('cannot read', r.stderr)

    def test_missing_file_means_defaults(self):
        r = run_script('version', env={'CODEXPOOL_SETTINGS': str(HOME / 'no-such-settings.json')})
        self.assertEqual((r.returncode, r.stdout), (0, f'codexpool {cp.VERSION}\n'))


class SetCommand(unittest.TestCase):
    NOTED = ('{\n  "_display": "how numbers read",\n  "display": "left",\n  "port": %d,\n  "bridge_port": %d,\n'
             '  "pool_label": "com.codexpool-test.pool",\n  "guard_label": "com.codexpool-test.guard",\n'
             '  "menubar_label": "com.codexpool-test.menubar",\n  "bridge_label": "com.codexpool-test.bridge"\n}\n')

    def setUp(self):
        ctx = settings_restored()
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        status = preserved(cp.STATUS_FILE)
        status.__enter__()
        self.addCleanup(status.__exit__, None, None, None)

    def settings_text(self):
        return cp.SETTINGS_FILE.read_text()

    def test_no_args_prints_both(self):
        code, out, _ = run(cp.cmd_set, key=None, value=None)
        self.assertEqual(code, 0)
        self.assertEqual([line.split()[:2] for line in out.splitlines()], [['display', 'left'], ['headline', 'all']])

    def test_rejects_unknown_key_and_bad_values(self):
        before = self.settings_text()
        for key, value, text in (('colour', 'red', '"colour" is not one of the settings'),
                                 ('port', '9000', '"port" is not one of the settings'),
                                 ('display', 'sideways', 'display is left or used, not "sideways"'),
                                 ('display', 'LEFT', 'display is left or used'),
                                 ('headline', None, 'headline is all or regular'),
                                 ('headline', 'left', 'headline is all or regular')):
            code, out, err = run(cp.cmd_set, key=key, value=value)
            self.assertEqual(code, 1, (key, value))
            self.assertIn(text, err)
            self.assertEqual(out, '')
        self.assertEqual(self.settings_text(), before)
        self.assertEqual(cp.SETTINGS['display'], 'left')

    def test_keeps_keys_order_and_notes(self):
        cp.SETTINGS_FILE.write_text(self.NOTED % (cp.PORT, cp.BRIDGE_PORT))
        cp.SETTINGS_FILE.chmod(0o640)
        code, out, _ = run(cp.cmd_set, key='display', value='used')
        self.assertEqual(code, 0)
        self.assertIn('display: left → used.', out)
        raw = json.loads(self.settings_text())
        self.assertEqual(list(raw), ['_display', 'display', 'port', 'bridge_port', 'pool_label', 'guard_label',
                                     'menubar_label', 'bridge_label'])
        self.assertEqual((raw['_display'], raw['display'], raw['port']), ('how numbers read', 'used', cp.PORT))
        self.assertEqual(cp.SETTINGS_FILE.stat().st_mode & 0o777, 0o640)
        self.assertEqual(cp.SETTINGS['display'], 'used')
        self.assertEqual(cp.parse_settings(raw)['display'], 'used')
        self.assertEqual([p.name for p in ROOT.iterdir() if p.name.endswith('.tmp')], [])

    def test_write_is_atomic(self):
        before = self.settings_text()
        with mock.patch.object(cp.os, 'replace', side_effect=OSError(28, 'No space left on device')):
            code, _, err = run(cp.cmd_set, key='display', value='used')
        self.assertEqual(code, 1)
        self.assertIn('cannot write', err)
        self.assertEqual(self.settings_text(), before)
        self.assertEqual(cp.SETTINGS['display'], 'left')
        self.assertEqual([p.name for p in ROOT.iterdir() if p.name.endswith('.tmp')], [])

    def test_two_writers_at_once_keep_both_changes(self):
        """write_setting reads settings.json under the lock, so a change another codexpool set makes meanwhile is
        kept: here the test holds the lock and writes display itself while a thread sets headline."""
        done = threading.Event()
        result = {}

        def writer():
            result['new'], _ = cp.write_setting('headline', 'regular')
            done.set()
        with cp.settings_lock():
            thread = threading.Thread(target=writer, daemon=True)
            thread.start()
            self.assertFalse(done.wait(0.3), 'write_setting did not wait for the lock')
            raw = json.loads(self.settings_text())
            raw['display'] = 'used'
            cp.SETTINGS_FILE.write_text(json.dumps(raw))
        self.assertTrue(done.wait(10))
        thread.join(10)
        raw = json.loads(self.settings_text())
        self.assertEqual((raw['display'], raw['headline']), ('used', 'regular'))
        self.assertEqual((result['new']['display'], result['new']['headline']), ('used', 'regular'))

    def test_status_file_gets_both_changes(self):
        """The later of two set runs rewrites status.json with the other's change too."""
        cp.STATUS_FILE.write_text(json.dumps(cp.status_down('connection refused', False)))
        raw = json.loads(self.settings_text())
        raw['headline'] = 'regular'  # as if another codexpool set wrote it after this process loaded its settings
        cp.SETTINGS_FILE.write_text(json.dumps(raw))
        run(cp.cmd_set, key='display', value='used')
        pool = json.loads(cp.STATUS_FILE.read_text())['pool']
        self.assertEqual((pool['display'], pool['headline']), ('used', 'regular'))

    def test_new_key_goes_last(self):
        code, _, _ = run(cp.cmd_set, key='headline', value='regular')
        self.assertEqual(code, 0)
        raw = json.loads(self.settings_text())
        self.assertEqual(list(raw)[-1], 'headline')
        self.assertEqual(raw['headline'], 'regular')
        self.assertEqual(raw['port'], cp.PORT)

    def test_creates_a_missing_file(self):
        os.unlink(cp.SETTINGS_FILE)
        cp.write_setting('display', 'used')
        self.assertEqual(json.loads(self.settings_text()), {'display': 'used'})
        self.assertEqual(cp.SETTINGS_FILE.stat().st_mode & 0o777, 0o644)

    def test_unchanged_value_does_not_rewrite(self):
        cp.SETTINGS_FILE.write_text(self.NOTED % (cp.PORT, cp.BRIDGE_PORT))
        before = cp.SETTINGS_FILE.stat().st_mtime_ns
        _, changed = cp.write_setting('display', 'left')
        self.assertFalse(changed)
        self.assertEqual(cp.SETTINGS_FILE.stat().st_mtime_ns, before)
        code, out, _ = run(cp.cmd_set, key='display', value='left')
        self.assertEqual(code, 0)
        self.assertIn('display is already left.', out)

    def test_refuses_to_touch_a_broken_file(self):
        cp.SETTINGS_FILE.write_text('{"colour": "red"}\n')
        with self.assertRaises(cp.SettingsError):
            cp.write_setting('display', 'used')
        self.assertEqual(self.settings_text(), '{"colour": "red"}\n')
        code, _, err = run(cp.cmd_set, key='display', value='used')
        self.assertEqual(code, 1)
        self.assertIn('unknown key(s) colour', err)

    def test_status_file_follows_while_the_pool_is_down(self):
        demo = json.loads((REPO / 'docs' / 'images' / 'demo' / 'status-regular.json').read_text())
        cp.STATUS_FILE.write_text(json.dumps(demo))
        code, out, _ = run(cp.cmd_set, key='headline', value='regular')
        self.assertEqual(code, 0)
        self.assertIn('within a few seconds', out)
        pool = json.loads(cp.STATUS_FILE.read_text())['pool']
        self.assertEqual((pool['headline'], pool['display']), ('regular', 'left'))
        self.assertEqual(pool['used_pct'], pool['used_pct_regular'])
        self.assertNotEqual(pool['used_pct'], pool['used_pct_all'])
        run(cp.cmd_set, key='display', value='used')
        st = json.loads(cp.STATUS_FILE.read_text())
        self.assertEqual(st['pool']['display'], 'used')
        self.assertEqual(st['generated_at'], demo['generated_at'])  # still reads as old as it is

    def test_down_status_file_gets_the_fields_too(self):
        cp.STATUS_FILE.write_text(json.dumps(cp.status_down('connection refused', False)))
        run(cp.cmd_set, key='display', value='used')
        self.assertEqual(json.loads(cp.STATUS_FILE.read_text())['pool']['display'], 'used')

    def test_without_a_status_file_it_says_when(self):
        with preserved(cp.STATUS_FILE):
            if cp.STATUS_FILE.exists():
                cp.STATUS_FILE.unlink()
            code, out, _ = run(cp.cmd_set, key='display', value='used')
        self.assertEqual(code, 0)
        self.assertIn("after the guard's next pass", out)


if __name__ == '__main__':
    unittest.main()
