"""When Codex is pointed at the pool: install leaves openai_base_url alone while the pool has no seats (so Codex,
and a Codex agent running the install, keeps working), and the first seat sign-in makes the switch. Doctor, setup,
uninstall and install's closing text follow that state. All in the fake home, with a stand-in CLIProxyAPI and pool."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import threading
import unittest
from unittest import mock

from _helpers import HOME, TEST_KEY, FakePool, cp, fake_cpa, fake_seat_file, preserved, run, seat_name

OURS = f'{cp.BASE}/v1'
MINE = 'model = "gpt-test"\n\n[features]\nx = true\n'  # a Codex config of the user's own, no openai_base_url
Prompter = cp.Prompter


def base_url():
    """openai_base_url in the Codex config, None when it is not set."""
    value = cp.toml_top_level(cp.CODEX_CONFIG).get('openai_base_url')
    return None if value is None else cp.unquote(value)


def record():
    return json.loads(cp.INSTALL_FILE.read_text()) if cp.INSTALL_FILE.exists() else None


class CodexSwitch(unittest.TestCase):
    def setUp(self):
        plist = cp.LAUNCH_AGENTS / f'{cp.POOL_JOB}.plist'
        for keep in (preserved(cp.INSTALL_FILE, plist, cp.SEATS_META),
                     mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY)):
            keep.__enter__()
            self.addCleanup(keep.__exit__, None, None, None)
        self.addCleanup(shutil.rmtree, str(HOME / '.codex'), True)
        before = set(cp.STATE.iterdir())
        self.addCleanup(lambda: [p.unlink() for p in set(cp.STATE.iterdir()) - before if p.is_file()])
        seats = set(cp.AUTH.glob('*.json'))
        self.addCleanup(lambda: [p.unlink() for p in set(cp.AUTH.glob('*.json')) - seats])
        cp.CODEX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
        cp.CODEX_CONFIG.write_text(MINE)
        cp.LAUNCH_AGENTS.mkdir(parents=True, exist_ok=True)
        plist.write_text('<plist/>\n')  # what an install leaves, and uninstall deletes

    def install(self, dry=False):
        """install's Codex config step, for real (or a dry run). Returns its output."""
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            cp.install_codex_config(cp.Steps(dry), False)
        return out.getvalue()

    def login(self, email='work@test', label='Work'):
        with fake_cpa(FAKE_CPA_EMAIL=email), FakePool():
            code, out, err = run(cp.cmd_login, label=label, device=False, no_open=True, no_copy=False, priority=None)
        self.assertEqual(code, 0, err)
        return out

    def seat(self, email='work@test'):
        """A seat that stays in the pool for the test (fake_cpa removes the ones it made)."""
        return fake_seat_file(email)

    # -- install -----------------------------------------------------------------------------------------------

    def test_install_without_seats_leaves_codex_alone(self):
        out = self.install()
        self.assertIsNone(base_url())
        self.assertIn('leave openai_base_url unset for now: the pool has no seats yet, so Codex keeps its own login '
                      'until you add the first one, and that sign-in points Codex at the pool', out)
        rec = record()
        self.assertIs(rec['codex_switch_pending'], True)
        self.assertIn('codex_switch_from', rec)
        self.assertIsNone(rec['codex_switch_from'])  # what the first sign-in replaces: nothing, the line is unset
        self.assertEqual(rec['original'], {'openai_base_url': None, 'model_provider': None, 'model_catalog_json': None})
        self.assertTrue(pathlib_exists(rec['backup']))  # the original settings are still backed up and recorded
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)
        again = self.install()
        self.assertIn('openai_base_url waits for the first seat', again)
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)
        self.assertIs(record()['codex_switch_pending'], True)

    def test_install_without_seats_keeps_a_url_of_your_own(self):
        cp.CODEX_CONFIG.write_text('openai_base_url = "https://proxy.example/v1"\n' + MINE)
        out = self.install()
        self.assertIn('leave openai_base_url as it is ("https://proxy.example/v1") for now', out)
        self.assertEqual(base_url(), 'https://proxy.example/v1')

    def test_dry_run_writes_nothing(self):
        out = self.install(dry=True)
        self.assertIn('[dry-run] leave openai_base_url unset for now', out)
        self.assertIsNone(record())
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)

    def test_install_without_seats_takes_back_a_pointer_to_the_pool(self):
        """An install before 1.1.0 pointed Codex at the pool before it had a seat, so every request failed."""
        cp.CODEX_CONFIG.write_text(f'openai_base_url = "{OURS}"\n' + MINE)
        out = self.install()
        self.assertIn(f'+ remove openai_base_url in {cp.tilde(cp.CODEX_CONFIG)}: the pool has no seats yet', out)
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)
        self.assertIs(record()['codex_switch_pending'], True)
        self.assertTrue(self.login().rstrip().endswith('Codex now uses the pool; quit and reopen the Codex app.'))
        self.assertEqual(base_url(), OURS)

    def test_install_without_seats_puts_back_the_recorded_url(self):
        cp.CODEX_CONFIG.write_text('openai_base_url = "https://proxy.example/v1"\n' + MINE)
        seat = self.seat()
        self.install()  # with a seat: switches at once, and records the URL
        self.assertEqual(base_url(), OURS)
        seat.unlink()   # the seat went some other way than subpool remove
        out = self.install()
        self.assertIn('+ set openai_base_url = "https://proxy.example/v1" (as before install)', out)
        self.assertEqual(base_url(), 'https://proxy.example/v1')
        self.assertEqual(record()['codex_switch_from'], '"https://proxy.example/v1"')

    def test_dry_run_with_a_pointer_to_an_empty_pool_writes_nothing(self):
        cp.CODEX_CONFIG.write_text(f'openai_base_url = "{OURS}"\n' + MINE)
        out = self.install(dry=True)
        self.assertIn('[dry-run] remove openai_base_url', out)
        self.assertIsNone(record())
        self.assertEqual(base_url(), OURS)

    def test_install_with_seats_switches(self):
        self.seat()
        out = self.install()
        self.assertIn(f'+ set openai_base_url = "{OURS}"', out)
        self.assertEqual(base_url(), OURS)
        self.assertNotIn('codex_switch_pending', record())
        self.assertIn(f'openai_base_url = "{OURS}"', self.install())  # and says so from then on

    def test_install_rerun_with_seats_makes_the_pending_switch(self):
        self.install()
        self.seat()
        self.install()
        self.assertEqual(base_url(), OURS)
        self.assertNotIn('codex_switch_pending', record())

    # -- the first sign-in ---------------------------------------------------------------------------------------

    def test_first_login_switches(self):
        self.install()
        out = self.login()
        self.assertIn(f'seat {seat_name("work@test")}: work@test', out)
        self.assertTrue(out.rstrip().endswith('Codex now uses the pool; quit and reopen the Codex app.'), out)
        self.assertEqual(base_url(), OURS)
        self.assertNotIn('codex_switch_pending', record())
        self.assertEqual(cp.CODEX_CONFIG.read_text(), f'openai_base_url = "{OURS}"\n' + MINE)
        self.assertEqual(cp.CODEX_CONFIG.stat().st_mode & 0o777, 0o600)
        self.seat()  # the first seat stays; a second sign-in has nothing to switch
        self.assertNotIn('Codex now uses the pool', self.login('team@test', 'Team'))

    def test_a_value_changed_by_hand_before_the_first_sign_in_stays(self):
        """install (no seats) leaves openai_base_url for the first sign-in; one you change meanwhile is yours."""
        self.install()
        cp.set_codex_key('openai_base_url', '"https://my-proxy.example/v1"')
        out = self.login()
        self.assertIn('openai_base_url was changed by hand since install ("https://my-proxy.example/v1"), so it is left '
                      'as it is and Codex does not use the pool; subpool install points it at the pool.', out)
        self.assertNotIn('Codex now uses the pool', out)
        self.assertEqual(base_url(), 'https://my-proxy.example/v1')
        self.assertNotIn('codex_switch_pending', record())
        self.assertNotIn('codex_switch_from', record())
        code, _, _ = run(cp.cmd_uninstall, yes=True)
        self.assertEqual(code, 0)
        self.assertEqual(base_url(), 'https://my-proxy.example/v1')  # not subpool's: uninstall leaves it

    def test_a_line_taken_out_before_the_first_sign_in_stays_out(self):
        cp.CODEX_CONFIG.write_text('openai_base_url = "https://proxy.example/v1"\n' + MINE)
        self.install()
        cp.set_codex_key('openai_base_url', None)
        self.assertIn('openai_base_url was taken out by hand since install, so it is left as it is', self.login())
        self.assertIsNone(base_url())

    def test_install_again_after_a_hand_edit_makes_the_switch_wait_again(self):
        self.install()
        cp.set_codex_key('openai_base_url', '"https://my-proxy.example/v1"')
        out = self.install()
        self.assertIn('+ leave openai_base_url as it is ("https://my-proxy.example/v1") for now', out)
        self.assertEqual(record()['codex_switch_from'], '"https://my-proxy.example/v1"')
        self.assertTrue(self.login().rstrip().endswith('Codex now uses the pool; quit and reopen the Codex app.'))
        self.assertEqual(base_url(), OURS)

    def test_a_note_without_the_value_it_replaces(self):
        """A note from before 1.1.0 has no codex_switch_from: the value recorded before install counts."""
        self.install()
        rec = record()
        del rec['codex_switch_from']
        cp.INSTALL_FILE.write_text(json.dumps(rec))
        self.assertIn('Codex now uses the pool', self.login())
        self.assertEqual(base_url(), OURS)

    def test_uninstall_after_the_switch_restores_the_original(self):
        self.install()
        self.login()
        code, _, _ = run(cp.cmd_uninstall, yes=True)
        self.assertEqual(code, 0)
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)

    def test_login_when_already_ours_is_a_no_op(self):
        self.install()
        cp.CODEX_CONFIG.write_text(f'openai_base_url = "{OURS}"\n' + MINE)
        before = cp.CODEX_CONFIG.read_bytes()
        out = self.login()
        self.assertNotIn('Codex now uses the pool', out)
        self.assertEqual(cp.CODEX_CONFIG.read_bytes(), before)
        self.assertNotIn('codex_switch_pending', record())

    def test_login_without_a_pending_switch_leaves_codex_alone(self):
        """No note from install (a 1.0.0 install, or you took the line out yourself): login never adds it."""
        self.seat()
        self.install()
        cp.set_codex_key('openai_base_url', None)
        self.assertNotIn('Codex now uses the pool', self.login('team@test', 'Team'))
        self.assertIsNone(base_url())

    def test_uninstalled_record_is_respected(self):
        self.install()
        code, out, _ = run(cp.cmd_uninstall, yes=True)
        self.assertEqual(code, 0)
        self.assertIn('nothing of subpool\'s left in it', out)  # the switch never happened: nothing to remove
        self.assertIsNone(record())
        self.assertTrue(list(cp.STATE.glob('install.json.uninstalled-*')))
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)
        self.assertNotIn('Codex now uses the pool', self.login())
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)

    def test_no_switch_without_the_pool_agent(self):
        self.install()
        (cp.LAUNCH_AGENTS / f'{cp.POOL_JOB}.plist').unlink()
        self.assertNotIn('Codex now uses the pool', self.login())
        self.assertIsNone(base_url())
        self.assertIs(record()['codex_switch_pending'], True)  # install puts the agent back and then switches

    def test_a_failed_write_is_reported(self):
        self.install()
        with mock.patch.object(cp, 'set_codex_key', side_effect=PermissionError(13, 'Permission denied')):
            out = self.login()
        self.assertIn('warning: could not point Codex at the pool (Permission denied); run subpool install', out)
        self.assertIs(record()['codex_switch_pending'], True)

    def test_a_sign_in_during_uninstall_waits_for_it(self):
        """A sign-in that finishes while uninstall runs must not point Codex at the pool uninstall takes down."""
        self.install()
        self.seat()
        threads, switched, waited = [], [], []
        real = subprocess.run

        def bootout(argv, *args, **kwargs):
            if list(argv[:2]) == ['launchctl', 'bootout'] and not threads:
                t = threading.Thread(target=lambda: switched.append(cp.codex_switch_after_sign_in()))
                threads.append(t)
                t.start()
                t.join(0.5)   # without the lock, the switch happens right here
                waited.append(t.is_alive())
            return real(argv, *args, **kwargs)
        with mock.patch.object(subprocess, 'run', side_effect=bootout):
            code, _, err = run(cp.cmd_uninstall, yes=True)
        threads[0].join(5)
        self.assertEqual(code, 0, err)
        self.assertEqual(waited, [True])
        self.assertEqual(switched, [None])  # it ran after uninstall: no install left to switch
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)

    # -- the last seat -------------------------------------------------------------------------------------------

    def remove(self, seat):
        with FakePool():
            code, out, err = run(cp.cmd_remove, seat=seat, yes=True)
        self.assertEqual(code, 0, err)
        return out

    def test_removing_the_last_seat_gives_codex_its_own_login_back(self):
        self.install()
        self.login()
        self.seat()  # fake_cpa took its copy away; the pool keeps one
        self.assertEqual(base_url(), OURS)
        out = self.remove('work@test')
        self.assertIn('That was the last ChatGPT account, so Codex goes back to its own login, as before install: quit '
                      'and reopen the Codex app. The next account you add points it at the pool again.', out)
        self.assertEqual(cp.CODEX_CONFIG.read_text(), MINE)
        self.assertIs(record()['codex_switch_pending'], True)
        self.assertTrue(self.login().rstrip().endswith('Codex now uses the pool; quit and reopen the Codex app.'))

    def test_removing_the_last_seat_puts_back_a_url_of_your_own(self):
        cp.CODEX_CONFIG.write_text('openai_base_url = "https://proxy.example/v1"\n' + MINE)
        self.seat()
        self.install()
        self.assertIn('Codex goes back to openai_base_url = "https://proxy.example/v1", as before install',
                      self.remove('work@test'))
        self.assertEqual(base_url(), 'https://proxy.example/v1')

    def test_removing_one_of_two_seats_leaves_codex_alone(self):
        self.seat()
        self.seat('team@test')
        self.install()
        self.assertNotIn('last ChatGPT account', self.remove('team@test'))
        self.assertEqual(base_url(), OURS)

    def test_removing_the_last_seat_without_an_install_record_warns(self):
        self.seat()
        cp.CODEX_CONFIG.write_text(f'openai_base_url = "{OURS}"\n' + MINE)
        self.assertIn('That was the last ChatGPT account, and Codex still points at the pool: every Codex request '
                      'fails until you add one', self.remove('work@test'))
        self.assertEqual(base_url(), OURS)

    # -- doctor --------------------------------------------------------------------------------------------------

    def codex_check(self):
        code, out, _ = run(cp.cmd_doctor, json=True)
        section = next(s for s in json.loads(out)['sections'] if s['title'] == 'Codex app')
        return section['checks'][0]

    def test_doctor_warns_while_the_switch_waits(self):
        self.install()
        c = self.codex_check()
        self.assertEqual(c['status'], 'warn')
        self.assertEqual(c['text'], 'openai_base_url = (unset): Codex still talks to OpenAI directly until you add '
                                    'the first seat')
        # The Setup assistant shows the fix on its own Welcome step, so it names no tool.
        self.assertEqual(c['fix'], 'add your first ChatGPT account; its sign-in points Codex at the pool')

    def test_doctor_while_the_switch_waits_with_a_url_of_your_own(self):
        cp.CODEX_CONFIG.write_text('openai_base_url = "https://proxy.example/v1"\n' + MINE)
        self.install()
        c = self.codex_check()
        self.assertEqual((c['status'], c['text']), ('warn', 'openai_base_url = https://proxy.example/v1: Codex keeps '
                                                            'using it until you add the first seat'))

    def test_doctor_problem_with_seats_and_codex_not_pointed(self):
        self.install()
        self.seat()  # a seat, and still no switch (say it came in some other way)
        self.assertEqual(self.codex_check(), {'status': 'fail', 'text': 'openai_base_url = (unset)',
                                              'fix': 'subpool install'})

    def test_doctor_problem_without_the_note(self):
        self.assertEqual(self.codex_check()['status'], 'fail')  # no install record: not a first-seat wait

    def test_doctor_ok_once_switched(self):
        self.install()
        self.login()
        self.assertEqual(self.codex_check(), {'status': 'ok', 'text': f'openai_base_url = {OURS}', 'fix': None})

    # -- setup ---------------------------------------------------------------------------------------------------

    def setup(self, *answers):
        with mock.patch.object(cp, 'Prompter', lambda: Prompter(io.StringIO(''.join(a + '\n' for a in answers)))), \
                mock.patch.object(cp, 'chrome_app', return_value=None), \
                mock.patch.object(cp, 'reopen_codex_app', return_value=True) as reopen, \
                mock.patch.object(cp, 'open_url'):
            code, out, _ = run(cp.cmd_setup)
        return code, out, reopen

    def test_setup_without_a_seat_has_nothing_to_reopen(self):
        self.install()
        with FakePool():
            code, out, reopen = self.setup('n')
        self.assertEqual(code, 0, out)
        self.assertIn('No ChatGPT accounts in the pool yet: Codex keeps its own login until you add one, and that '
                      'sign-in points it at the pool.', out)
        self.assertIn('Codex keeps its own login until the pool has a seat, so there is nothing to reopen yet.', out)
        self.assertNotIn('Quit and reopen the Codex app now', out)
        reopen.assert_not_called()

    def test_setup_first_seat_switches_then_offers_the_reopen(self):
        self.install()
        with fake_cpa(FAKE_CPA_EMAIL='work@test'), FakePool():
            code, out, reopen = self.setup('', 'Work', 'n', '', 'n', 'y')
        self.assertEqual(code, 0, out)
        self.assertIn('  ✓ Codex now uses the pool; quit and reopen the Codex app.', out)
        self.assertLess(out.index('Codex now uses the pool'), out.index('Quit and reopen the Codex app now'))
        reopen.assert_called_once_with()
        self.assertEqual(base_url(), OURS)


class InstallClosingText(unittest.TestCase):
    """The end of a first install (no seats yet), with the switch waiting for the first seat or not."""
    STEPS = ('install_preflight', 'install_toolchain', 'install_build', 'install_key_and_config', 'install_agents',
             'install_codex_config', 'install_command')

    def install(self, pending, **env):
        with contextlib.ExitStack() as stack:
            for name in self.STEPS:
                stack.enter_context(mock.patch.object(cp, name))
            stack.enter_context(mock.patch.object(cp, 'headline_upgrade_note', return_value=None))
            stack.enter_context(mock.patch.object(cp, 'codex_switch_pending', return_value=pending))
            stack.enter_context(mock.patch.dict(os.environ, env))
            return run(cp.cmd_install, dry_run=False, fix_config=False)

    def test_pending(self):
        for env in ({}, {'CODEXPOOL_VIA_INSTALLER': '1'}):
            code, out, _ = self.install(True, **env)
            self.assertEqual(code, 0)
            self.assertIn('Codex keeps its own login and works as before until you add the first seat; that\n'
                          'sign-in points it at the pool.', out)
            self.assertNotIn('Codex requests\nfail', out)
        self.assertIn('The installer shows the next steps below.', out)

    def test_already_pointed(self):
        code, out, _ = self.install(False)
        self.assertEqual(code, 0)
        self.assertIn('Codex requests\nfail until then', out)
        self.assertIn('subpool setup walks you through the next steps', out)
        self.assertIn('so it goes through the pool', out)


def pathlib_exists(path):
    return path is not None and os.path.exists(path)


if __name__ == '__main__':
    unittest.main()
