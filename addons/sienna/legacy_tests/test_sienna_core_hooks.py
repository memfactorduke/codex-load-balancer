"""What the core does for the sienna add-on: its subcommand family and the claude alias, the gui --pool id,
its settings keys, its login files under the guard's lock-down, and claude_balancing next to balancing."""
import argparse
import contextlib
import io
import json
import os
import sys
import unittest
from unittest import mock

from _helpers import HOME, ROOT, cp, run, addon, sienna_pool

sys.path.insert(0, str(cp.CODE_DIR / 'tests'))
import test_cli  # noqa: E402  (the core's Help expectations and helpers)


class Help(unittest.TestCase):
    EXPECTED = {('sienna',), ('sienna', 'install'), ('sienna', 'uninstall'), ('sienna', 'status'), ('sienna', 'route'),
                ('sienna', 'shim'), ('sienna', 'logs'), ('sienna', 'login'), ('sienna', 'enable'), ('sienna', 'disable'),
                ('sienna', 'label'), ('sienna', 'weight'), ('sienna', 'priority'), ('sienna', 'reserve'),
                ('sienna', 'remove'), ('sienna', 'order'), ('sienna', 'credits'), ('sienna', 'selftest'),
                ('sienna', 'resume'), ('sienna', 'accept-engine'), ('sienna', 'cpa-check'), ('sienna', 'desktop'), ('sienna', 'desktop', 'status'),
                ('sienna', 'desktop', 'pooled'), ('sienna', 'desktop', 'claudeai'),
                ('sienna', 'desktop', 'remove'), ('sienna', 'desktop', 'rollback'),
                ('sienna', 'desktop', 'reveal'), ('sienna', 'desktop', 'relaunch')}

    def test_every_sienna_subcommand_has_help(self):
        found = {p for p, _ in test_cli.subcommands(cp.build_parser()) if p[0] == 'sienna'}
        self.assertEqual(found, self.EXPECTED)
        for path in sorted(found):
            out = io.StringIO()
            with self.subTest(path=' '.join(path)), contextlib.redirect_stdout(out):
                with self.assertRaises(SystemExit) as ctx:
                    cp.build_parser().parse_args(list(path) + ['--help'])
                self.assertEqual(ctx.exception.code, 0)
                self.assertIn(f'usage: codexpool {" ".join(path)}', out.getvalue())

    def test_claude_is_an_alias_for_the_whole_sienna_family(self):
        parser = cp.build_parser()
        top = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
        self.assertIs(top.choices['claude'], top.choices['sienna'])
        self.assertEqual(sum(c.dest == 'sienna' for c in top._choices_actions), 1)
        self.assertFalse(any(c.dest == 'claude' for c in top._choices_actions))
        for path in sorted(self.EXPECTED):
            with self.subTest(path=path):
                outputs = []
                for name in ('sienna', 'claude'):
                    out = io.StringIO()
                    with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as ctx:
                        parser.parse_args([name, *path[1:], '--help'])
                    self.assertEqual(ctx.exception.code, 0)
                    outputs.append(out.getvalue())
                self.assertEqual(outputs[0].replace('codexpool sienna', 'codexpool claude'), outputs[1].replace('codexpool sienna', 'codexpool claude'))


class Gui(unittest.TestCase):
    def test_opens_on_the_claude_pool(self):
        self.assertIn('claude', cp.GUI_POOLS)
        script, python = test_cli.Gui.launchable(self)
        with mock.patch.object(cp.subprocess, 'Popen') as popen:
            code, _, _ = run(cp.cmd_gui, pane='seats', pool='claude')
        self.assertEqual(code, 0)
        self.assertEqual(popen.call_args[0][0], [str(python), str(script), '--pane', 'seats', '--pool', 'claude'])
        args = cp.build_parser().parse_args(['gui', 'overview', '--pool', 'claude'])
        self.assertEqual((args.pane, args.pool), ('overview', 'claude'))


class Settings(unittest.TestCase):
    def test_rejects_bad_claude_values(self):
        before = cp.SETTINGS_FILE.read_text()
        for key, value, text in (('claude_balancing', 'fair', 'claude_balancing is priority or reset, not "fair"'),
                                 ('claude_port', '9000', '"claude_port" is not one of the settings')):
            code, out, err = run(cp.cmd_set, key=key, value=value)
            self.assertEqual(code, 1, (key, value))
            self.assertIn(text, err)
            self.assertEqual(out, '')
        self.assertEqual(cp.SETTINGS_FILE.read_text(), before)

    def test_set_claude_balancing_leaves_the_codex_pool_alone(self):
        cp.STATUS_FILE.write_text(json.dumps(cp.status_down('connection refused', False)))
        before = cp.STATUS_FILE.read_text()
        code, out, _ = run(cp.cmd_set, key='claude_balancing', value='reset')
        self.assertEqual(code, 0)
        self.assertIn('claude_balancing: priority → reset. The guard applies it to the Claude pool on its next pass', out)
        raw = json.loads(cp.SETTINGS_FILE.read_text())
        self.assertEqual((raw['claude_balancing'], raw.get('balancing', 'priority')), ('reset', 'priority'))
        self.assertEqual((cp.SETTINGS['claude_balancing'], cp.SETTINGS['balancing']), ('reset', 'priority'))
        self.assertEqual(cp.STATUS_FILE.read_text(), before)  # status.json is the Codex pool's: untouched
        code, out, _ = run(cp.cmd_set, key='claude_balancing', value='reset')
        self.assertEqual(out.strip(), 'claude_balancing is already reset.')



class LoginFilesPrivate(unittest.TestCase):
    def setUp(self):
        for folder in (cp.AUTH, sienna_pool.CLAUDE_AUTH):
            folder.mkdir(parents=True, exist_ok=True)
        self.files = [cp.AUTH / 'codex-test-open@test.json', sienna_pool.CLAUDE_AUTH / 'claude-test-open@test.json']
        for f in self.files:
            f.write_text('{}')
            os.chmod(f, 0o644)
            self.addCleanup(lambda f=f: f.exists() and f.unlink())

    def test_the_guard_locks_down_both_pools_login_files(self):
        with mock.patch.object(cp, 'log_line') as logged:
            cp.lock_down_logins()
        for f in self.files:
            self.assertEqual(f.stat().st_mode & 0o777, 0o600)
        self.assertEqual(logged.call_count, len(self.files))


if __name__ == '__main__':
    unittest.main()
