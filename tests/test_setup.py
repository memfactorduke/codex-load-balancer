"""codexpool setup and the sign-in it shares with codexpool login (with a stand-in CLIProxyAPI and pool)."""
import contextlib
import io
import itertools
import unittest
from unittest import mock

from _helpers import ROOT, TEST_KEY, FakePool, cp, fake_cpa, fake_seat_file, preserved, run, seat_name

URL = 'https://auth.example.invalid/oauth/authorize?client_id=app_test&state=s1'
Prompter = cp.Prompter  # the real class, while tests patch cp.Prompter


_names = itertools.count(1)


def seat(priority, reserve=False, provider='codex', disabled=False, name=None):
    name = name or f'codex-seat{next(_names)}@test-plus.json'
    return {'name': name, 'id': name, 'provider': provider, 'priority': priority, 'reserve': reserve,
            'disabled': disabled}


def placed(seats, reserve=False):
    """Add a new seat where fill_order puts it, moving the reserves it says to move; returns the new seat."""
    p, moves = cp.fill_order(seats, reserve)
    for s, q in moves:
        s['priority'] = q
    new = seat(p, reserve)
    seats.append(new)
    return new


def scripted(*answers):
    """A Prompter factory that answers from a script (then Ctrl-D)."""
    return lambda: Prompter(io.StringIO(''.join(a + '\n' for a in answers)))


class LabelProblem(unittest.TestCase):
    def test_good_labels(self):
        for label in ('Work', 'Work A', 'Pro 20x', 'team-2', 'Équipe', 'a' * cp.SEAT_LABEL_MAX, 'x'):
            self.assertIsNone(cp.label_problem(label), label)

    def test_bad_labels(self):
        for label, text in (('', 'empty'), ('   ', 'empty'), (None, 'empty'), (' Work', 'start or end with a space'),
                            ('Work ', 'start or end with a space'), ('-x', 'start with -'),
                            ('--priority', 'start with -'),
                            ('a' * (cp.SEAT_LABEL_MAX + 1), 'at most'), ('two\nlines', 'one line'),
                            ('tab\there', 'one line'), ('nul\x00', 'one line'), ('sep x', 'one line')):
            problem = cp.label_problem(label)
            self.assertIsNotNone(problem, repr(label))
            self.assertIn(text, problem, repr(label))

    def test_login_refuses_a_bad_label_before_anything_runs(self):
        with mock.patch.object(cp, 'login_seat') as login:
            code, _, err = run(cp.cmd_login, label='-x', device=False, no_open=True, priority=None)
        self.assertEqual(code, 1)
        self.assertIn('codexpool login: a label cannot start with -', err)
        login.assert_not_called()


class FirstUrl(unittest.TestCase):
    def test_cliproxyapi_lines(self):
        self.assertIsNone(cp.first_url('Visit the following URL to continue authentication:\n'))
        self.assertEqual(cp.first_url(URL + '\n'), URL)
        self.assertEqual(cp.first_url(f'Open {URL}.'), URL)
        self.assertEqual(cp.first_url(f'<{URL}> and https://example.invalid/second'), URL)
        self.assertEqual(cp.first_url(f'"{URL}"'), URL)

    def test_not_urls(self):
        for text in ('', None, 'http://localhost:1455/auth/callback', 'Waiting for Codex authentication callback...',
                     'https:// nothing'):
            self.assertIsNone(cp.first_url(text), text)

    def test_query_strings_survive(self):
        url = ('https://auth.example.invalid/oauth/authorize?response_type=code&redirect_uri=http%3A%2F%2Flocalhost'
               '%3A1455%2Fauth%2Fcallback&scope=openid+email&state=abc123')
        self.assertEqual(cp.first_url(f'  {url}  \n'), url)


class OpenUrl(unittest.TestCase):
    def test_argv(self):
        self.assertEqual(cp.open_url_argv(URL), ['open', URL])
        self.assertEqual(cp.open_url_argv(URL, incognito=True),
                         ['open', '-na', 'Google Chrome', '--args', '--incognito', URL])


class FillOrder(unittest.TestCase):
    def assertReserveLast(self, seats):
        regular = [s['priority'] for s in seats if not s['reserve']]
        reserves = [s['priority'] for s in seats if s['reserve']]
        self.assertEqual(len({s['priority'] for s in seats}), len(seats), seats)  # unique
        if regular and reserves:
            self.assertGreater(min(regular), max(reserves), seats)

    def test_first_seat(self):
        self.assertEqual(cp.fill_order([]), (500, []))
        self.assertEqual(cp.fill_order([], reserve=True), (10, []))

    def test_fills_after_existing_seats(self):
        seats = [seat(400), seat(300)]
        self.assertEqual(cp.fill_order(seats), (200, []))
        self.assertEqual(cp.fill_order(seats, reserve=True), (290, []))

    def test_stays_above_the_reserve(self):
        self.assertEqual(cp.fill_order([seat(100), seat(10, reserve=True)]), (55, []))
        self.assertEqual(cp.fill_order([seat(10, reserve=True)]), (510, []))
        self.assertEqual(cp.fill_order([seat(400), seat(10, reserve=True)]), (300, []))
        self.assertEqual(cp.fill_order([seat(300), seat(200, reserve=True), seat(250)]), (225, []))

    def test_no_room_above_the_reserve_moves_it_down(self):
        reserve = seat(99, reserve=True)
        self.assertEqual(cp.fill_order([seat(100), reserve]), (0, [(reserve, -100)]))

    def test_reserves_move_in_their_order(self):
        seats = [seat(100), seat(99, reserve=True), seat(98, reserve=True)]
        new = placed(seats)
        self.assertEqual([s['priority'] for s in seats], [100, -100, -200, 0])
        self.assertEqual(new['priority'], 0)
        self.assertReserveLast(seats)

    def test_a_reserve_that_fills_before_a_regular_seat_moves_last(self):
        seats = [seat(300), seat(400, reserve=True), seat(250)]
        placed(seats)
        self.assertEqual([s['priority'] for s in seats], [300, 50, 250, 150])
        self.assertReserveLast(seats)

    def test_many_additions_keep_the_reserve_last(self):
        """The order setup builds: a seat, the reserve, then one regular seat after another."""
        seats = []
        order = [placed(seats)]
        placed(seats, reserve=True)
        for _ in range(12):
            order.append(placed(seats))
            self.assertReserveLast(seats)
        prios = [s['priority'] for s in order]
        self.assertEqual(prios, sorted(prios, reverse=True))  # regular seats fill in the order they were added

    def test_hand_set_priorities_keep_the_reserve_last(self):
        seats = [seat(400), seat(300), seat(200), seat(100), seat(10, reserve=True)]
        for _ in range(10):
            placed(seats)
            self.assertReserveLast(seats)

    def test_a_seat_that_is_off_counts(self):
        self.assertEqual(cp.fill_order([seat(200), seat(100, disabled=True)]), (0, []))

    def test_ignores_other_providers(self):
        self.assertEqual(cp.fill_order([seat(0, provider='xai')]), (500, []))

    def test_existing_reserve_already_last_stays(self):
        pro = seat(10, name='pro')
        self.assertEqual(cp.fill_order([seat(400), seat(300), pro], reserve=True, name='pro'), (10, []))

    def test_existing_seat_marked_reserve_moves_last(self):
        pro = seat(350, name='pro')
        self.assertEqual(cp.fill_order([seat(400), seat(300), pro, seat(290, reserve=True)], reserve=True,
                                       name='pro'), (280, []))
        self.assertEqual(cp.fill_order([seat(0), seat(0, name='x')], reserve=True, name='x'), (-10, []))

    def test_existing_seat_made_regular_moves_before_the_reserves(self):
        low = seat(280, reserve=True, name='low')
        self.assertEqual(cp.fill_order([seat(400), seat(300), seat(290, reserve=True), low], name='low'), (295, []))
        top = seat(500, name='top')
        self.assertEqual(cp.fill_order([seat(400), seat(290, reserve=True), top], name='top'), (500, []))


class PrompterAnswers(unittest.TestCase):
    def ask(self, answers, default):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            result = cp.Prompter(io.StringIO(answers)).yes('Go?', default)
        return result, out.getvalue()

    def test_defaults_and_answers(self):
        self.assertEqual(self.ask('\n', True)[0], True)
        self.assertEqual(self.ask('\n', False)[0], False)
        for text, want in (('y\n', True), ('YES\n', True), (' n \n', False), ('No\n', False)):
            self.assertEqual(self.ask(text, not want)[0], want, text)

    def test_asks_again(self):
        result, out = self.ask('maybe\ny\n', False)
        self.assertTrue(result)
        self.assertEqual(out.count('Go? [y/N] '), 2)
        self.assertIn('Please answer y or n.', out)

    def test_end_of_input(self):
        with contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaises(EOFError):
                cp.Prompter(io.StringIO('')).ask('Name: ')
            p = cp.Prompter(io.StringIO('x\n'))
            p.src = None
            with self.assertRaises(EOFError):
                p.ask('Name: ')


class LoginSeat(unittest.TestCase):
    def test_link_and_saved_file(self):
        urls = []
        with fake_cpa(), contextlib.redirect_stdout(io.StringIO()) as out:
            saved, lines = cp.login_seat(no_open=True, echo=False, on_url=urls.append)
        self.assertEqual(urls, [URL])
        self.assertEqual(saved, ROOT / 'auth' / seat_name('one@test'))
        self.assertIn('Codex authentication successful!', lines)
        self.assertEqual(out.getvalue(), '')

    def test_echo_streams_the_output(self):
        with fake_cpa(), contextlib.redirect_stdout(io.StringIO()) as out:
            saved, _ = cp.login_seat(no_open=True)
        self.assertIsNotNone(saved)
        self.assertIn(URL, out.getvalue())
        self.assertIn('Authentication saved to', out.getvalue())

    def test_paste_prompt_on_the_same_line(self):
        """After 15 s CLIProxyAPI prints its paste prompt without a line break; the saved line still counts."""
        with fake_cpa(FAKE_CPA_MODE='prompt'):
            saved, lines = cp.login_seat(no_open=True, echo=False)
        self.assertIsNotNone(saved)
        self.assertTrue(any(line.startswith(cp.CPA_PASTE_PROMPT + 'Authentication saved to') for line in lines))

    def test_failed_sign_in(self):
        mark = cp.log_mark()
        with fake_cpa(FAKE_CPA_MODE='fail'):
            saved, lines = cp.login_seat(no_open=True, echo=False)
        self.assertIsNone(saved)
        self.assertIn('Waiting for Codex authentication callback...', lines)
        self.assertEqual(cp.sign_in_errors(mark), ['Authentication failed. Please try again.'])


class SignInFailure(unittest.TestCase):
    OUTPUT = ['To authenticate from a remote machine, an SSH tunnel may be required.', '=' * 80,
              '  Run one of the following commands on your local machine (NOT the server):', '',
              '  # Standard SSH command (assumes SSH port 22):', '  ssh -L 1455:127.0.0.1:1455 root@192.0.2.1 -p 22',
              '', '  # If using an SSH key (assumes SSH port 22):',
              '  ssh -i <path_to_your_key> -L 1455:127.0.0.1:1455 root@192.0.2.1 -p 22', '',
              "  NOTE: If your server's SSH port is not 22, please modify the '-p 22' part accordingly.", '=' * 80,
              'Visit the following URL to continue authentication:', URL,
              'Waiting for Codex authentication callback...', cp.CPA_PASTE_PROMPT]

    def setUp(self):
        ctx = preserved(cp.MAIN_LOG)
        ctx.__enter__()
        self.addCleanup(ctx.__exit__, None, None, None)
        cp.MAIN_LOG.write_text('[2026-01-02 03:04:05] [--------] [info ] [main.go:1] an older line\n')
        self.mark = cp.log_mark()

    def log(self, *lines):
        with cp.MAIN_LOG.open('a') as f:
            f.writelines(line + '\n' for line in lines)

    def test_instructions_and_link_are_not_reasons(self):
        self.assertEqual(cp.sign_in_failure(self.OUTPUT, self.mark), [])

    def test_the_logged_error_is(self):
        self.log('[2026-01-02 03:09:05] [--------] [warn ] [manager.go:10] a pool line from the same minutes',
                 '[2026-01-02 03:09:06] [--------] [error] [openai_login.go:58] Authentication timed out. Please try '
                 'again.')
        self.assertEqual(cp.sign_in_failure(self.OUTPUT, self.mark), ['Authentication timed out. Please try again.'])
        self.assertEqual(cp.sign_in_errors(0), ['Authentication timed out. Please try again.'])

    def test_older_log_lines_do_not_count(self):
        self.log('[2026-01-02 03:09:06] [--------] [error] [openai_login.go:58] Authentication failed. Please try '
                 'again.')
        mark = cp.log_mark()
        self.assertEqual(cp.sign_in_failure(self.OUTPUT, mark), [])

    def test_printed_errors_count(self):
        out = self.OUTPUT + ['Codex authentication failed: authentication was cancelled or denied']
        self.assertEqual(cp.sign_in_failure(out, self.mark),
                         ['Codex authentication failed: authentication was cancelled or denied'])

    def test_log_lines_on_the_output(self):
        """With logging-to-file off, CLIProxyAPI's log lines come on its output: only the sign-in's own count."""
        out = ['[2026-01-02 03:04:05] [--------] [info ] [main.go:640] CLIProxyAPI Version: 7.0.0'] + self.OUTPUT + [
            '[2026-01-02 03:09:06] [--------] [error] [openai_login.go:58] Authentication timed out. Please try again.']
        self.assertEqual(cp.sign_in_failure(out, self.mark), ['Authentication timed out. Please try again.'])

    def test_a_rotated_log_is_read_from_the_start(self):
        big = self.mark + 10_000
        self.log('[2026-01-02 03:09:06] [--------] [error] [openai_device_login.go:46] Authentication failed. '
                 'Please try again.')
        self.assertEqual(cp.sign_in_errors(big), ['Authentication failed. Please try again.'])

    def test_missing_log(self):
        cp.MAIN_LOG.unlink()
        self.assertEqual(cp.log_mark(), 0)
        self.assertEqual(cp.sign_in_errors(0), [])


class LoginCommand(unittest.TestCase):
    def test_label_and_priority(self):
        with fake_cpa(FAKE_CPA_EMAIL='work@test'), FakePool() as pool, \
                mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY):
            code, out, _ = run(cp.cmd_login, label='Work', device=False, no_open=True, priority=300)
            meta = cp.read_meta()
        self.assertEqual(code, 0)
        name = seat_name('work@test')
        self.assertIn(f'seat {name}: work@test plan=plus account=acct-work label=Work priority=300', out)
        self.assertEqual(meta[name]['label'], 'Work')
        self.assertEqual(pool.priorities, {name: 300})

    def test_failure_changes_nothing(self):
        with fake_cpa(FAKE_CPA_MODE='fail'):
            code, out, err = run(cp.cmd_login, label='Work', device=False, no_open=True, priority=None)
            self.assertEqual(cp.read_meta(), {})
        self.assertEqual(code, 1)
        self.assertIn('login did not complete; nothing changed (the pool said: Authentication failed. Please try '
                      'again.)', err.splitlines()[-1])

    def test_new_seat_fills_after_the_others(self):
        work = fake_seat_file('work@test', priority=400)
        reserve = fake_seat_file('spare@test', 'pro', priority=399)
        self.addCleanup(work.unlink)
        self.addCleanup(reserve.unlink)
        with preserved(cp.SEATS_META):
            cp.update_meta(reserve.name, reserve=True)
            with fake_cpa(FAKE_CPA_EMAIL='team@test'), FakePool() as pool, \
                    mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY):
                code, out, _ = run(cp.cmd_login, label='Team', device=False, no_open=True, priority=None)
        self.assertEqual(code, 0, out)
        name = seat_name('team@test')
        self.assertEqual(pool.priorities, {name: 300, reserve.name: 200})  # no room above the reserve: it moves
        self.assertIn(f'seat {name}: team@test plan=plus account=acct-team label=Team priority=300', out)

    def test_signing_in_again_keeps_the_place(self):
        existing = fake_seat_file('one@test', priority=400)
        self.addCleanup(existing.unlink)
        with preserved(cp.SEATS_META):
            cp.update_meta(existing.name, label='One', weight=3)
            with fake_cpa(), FakePool() as pool, mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY):
                code, out, _ = run(cp.cmd_login, label=None, device=False, no_open=True, priority=None)
                meta = cp.read_meta()
        self.assertEqual(code, 0)
        self.assertEqual(pool.priorities, {})
        self.assertEqual(meta[existing.name], {'label': 'One', 'weight': 3})
        self.assertNotIn('priority=', out)

    def test_a_new_seat_leaves_old_meta_behind(self):
        """seats.json can still hold a size or reserve flag for a file name that is gone (a removed seat)."""
        name = seat_name('work@test')
        with preserved(cp.SEATS_META):
            cp.update_meta(name, label='Old', weight=3, reserve=True)
            with fake_cpa(FAKE_CPA_EMAIL='work@test'), FakePool(), \
                    mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY):
                code, _, _ = run(cp.cmd_login, label='Work', device=False, no_open=True, priority=None)
                meta = cp.read_meta()
        self.assertEqual(code, 0)
        self.assertEqual(meta[name], {'label': 'Work'})


class SetupCommand(unittest.TestCase):
    def setUp(self):
        for patch in (mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY),
                      mock.patch.object(cp, 'chrome_app', return_value=None)):
            patch.start()
            self.addCleanup(patch.stop)
        self.open_url = self.patch('open_url')
        self.reopen = self.patch('reopen_codex_app', return_value=True)
        self.kick = self.patch('kick_pool')

    def patch(self, name, **kwargs):
        p = mock.patch.object(cp, name, **kwargs)
        self.addCleanup(p.stop)
        return p.start()

    def setup(self, *answers):
        with mock.patch.object(cp, 'Prompter', scripted(*answers)):
            return run(cp.cmd_setup)

    def test_adds_a_seat(self):
        with fake_cpa(FAKE_CPA_EMAIL='work@test'), FakePool() as pool:
            code, out, err = self.setup('', 'Work', 'n', '', 'n', '')
            meta = cp.read_meta()
        self.assertEqual((code, err), (0, ''), out)
        name = seat_name('work@test')
        self.assertEqual(meta[name], {'label': 'Work'})
        self.assertEqual(pool.priorities, {name: 500})
        for text in ('[1/4] The pool', 'No ChatGPT accounts in the pool yet', '[2/4] ChatGPT accounts',
                     'private (incognito) window', URL, 'Open it in your default browser? [Y/n]',
                     'Added Work: work@test, plan plus, size 1×', 'Use it as the reserve (last resort)? [y/N]',
                     'Add another ChatGPT account? [Y/n]', '[3/4] Subagent lanes (optional)', 'LANES.md',
                     '[4/4] Check everything (codexpool doctor)', 'Pool process',
                     'Setup done. Added 1 seat this time: Work (plus, 1×).'):
            self.assertIn(text, out)
        self.open_url.assert_not_called()
        self.reopen.assert_not_called()

    def test_labels_reserve_and_reopen(self):
        work = fake_seat_file('work@test', priority=400)
        self.addCleanup(work.unlink)
        with preserved(cp.SEATS_META):
            cp.update_meta(work.name, label='Work')
            with fake_cpa(FAKE_CPA_EMAIL='team@test', FAKE_CPA_PLAN='pro'), FakePool() as pool:
                code, out, _ = self.setup('', 'work', '-x', 'Team', 'y', 'y', 'n', 'y')
                meta = cp.read_meta()
        self.assertEqual(code, 0, out)
        self.assertIn('1 ChatGPT account in the pool, in fill order:\n    Work  (plus, size 1×)', out)
        self.assertIn('That name will not do: a seat is already called work', out)
        self.assertIn('That name will not do: a label cannot start with -', out)
        name = seat_name('team@test', 'pro')
        self.assertEqual(meta[name], {'label': 'Team', 'reserve': True})
        self.assertEqual(pool.priorities, {name: 390})  # below Work (400): the reserve is the last resort
        self.open_url.assert_called_once_with(URL)
        self.reopen.assert_called_once_with()
        self.assertIn('the Codex app reopened', out)
        self.assertIn('Added 1 seat this time: Team (pro, 20×, reserve).', out)

    def test_same_account_again_adds_nothing(self):
        existing = fake_seat_file('one@test')
        self.addCleanup(existing.unlink)
        with preserved(cp.SEATS_META):
            with fake_cpa(), FakePool() as pool:
                code, out, _ = self.setup('', 'Other', 'n', 'n', '')
                meta = cp.read_meta()
        self.assertEqual(code, 0)
        self.assertIn('once more (the same ChatGPT account and workspace)', out)
        self.assertEqual(meta, {})  # not relabelled
        self.assertEqual(pool.priorities, {})
        self.assertIn('No seats were added this time.', out)

    def failed(self, mode, *answers):
        """setup with a sign-in that fails the way FAKE_CPA_MODE mode does: (exit code, what setup printed after the
        sign-in link)."""
        with fake_cpa(FAKE_CPA_MODE=mode), FakePool() as pool:
            code, out, _ = self.setup('', '', 'n', *(answers or ('n', '')))
            self.assertEqual(cp.read_meta(), {})
        self.assertEqual(pool.priorities, {})
        return code, out.split('Waiting for the sign-in to finish', 1)[1]

    def assertNoInstructions(self, tail):
        for text in (URL, 'ssh -L', 'SSH tunnel', '=====', 'Visit the following URL', 'Waiting for Codex',
                     cp.CPA_PASTE_PROMPT.strip()):
            self.assertNotIn(text, tail)

    def test_failed_sign_in_says_why(self):
        code, tail = self.failed('fail')
        self.assertEqual(code, 0)
        self.assertIn('The sign-in did not finish; nothing changed. The pool said:\n'
                      '    Authentication failed. Please try again.\n', tail)
        self.assertNoInstructions(tail)
        self.assertIn('Try again, with a new sign-in link? [Y/n]', tail)
        self.assertNotIn('Add another', tail)
        self.assertIn('No seats were added this time.', tail)

    def test_expired_link(self):
        code, tail = self.failed('expire')
        self.assertEqual(code, 0)
        self.assertIn('The pool said:\n    Authentication timed out. Please try again.\n', tail)
        self.assertNoInstructions(tail)

    def test_printed_error(self):
        _, tail = self.failed('denied')
        self.assertIn('The pool said:\n    Codex authentication failed: authentication was cancelled or denied\n',
                      tail)
        self.assertNoInstructions(tail)

    def test_failure_without_a_word(self):
        _, tail = self.failed('silent')
        self.assertIn('The sign-in did not finish; nothing changed. The link may have expired (links last about 5\n'
                      '  minutes), or the sign-in was stopped. ~/.codexpool/logs/main.log may say more.', tail)
        self.assertNotIn('The pool said', tail)
        self.assertNoInstructions(tail)

    def test_try_again_after_a_failure(self):
        with fake_cpa(FAKE_CPA_MODE='fail'), FakePool():
            code, out, _ = self.setup('', '', 'n', 'y', '', 'n', 'n', '')
        self.assertEqual(code, 0)
        self.assertEqual(out.count('Try again, with a new sign-in link? [Y/n]'), 2)
        self.assertEqual(out.count('The sign-in did not finish'), 2)

    def test_no_to_reserve_clears_an_old_flag(self):
        """A removed seat's account added again: a reserve flag left in seats.json does not come back."""
        name = seat_name('work@test')
        with preserved(cp.SEATS_META):
            cp.update_meta(name, reserve=True, weight=4)
            with fake_cpa(FAKE_CPA_EMAIL='work@test'), FakePool() as pool:
                code, out, _ = self.setup('', 'Work', 'n', 'n', 'n', '')
                meta = cp.read_meta()
        self.assertEqual(code, 0, out)
        self.assertEqual(meta[name], {'label': 'Work'})
        self.assertEqual(pool.priorities, {name: 500})

    def test_new_seat_stays_before_the_reserve(self):
        work = fake_seat_file('work@test', priority=100)
        spare = fake_seat_file('spare@test', 'pro', priority=99)
        self.addCleanup(work.unlink)
        self.addCleanup(spare.unlink)
        with preserved(cp.SEATS_META):
            cp.update_meta(spare.name, label='Spare', reserve=True)
            with fake_cpa(FAKE_CPA_EMAIL='team@test'), FakePool() as pool:
                code, out, _ = self.setup('', 'Team', 'n', 'n', 'n', '')
        self.assertEqual(code, 0, out)
        self.assertEqual(pool.priorities, {seat_name('team@test'): 0, spare.name: -100})

    def test_end_of_input_stops_with_a_summary(self):
        with fake_cpa(FAKE_CPA_EMAIL='work@test'), FakePool():
            code, out, _ = self.setup('', 'Work')
        self.assertEqual(code, 130)
        self.assertIn('Setup stopped. No seats were added this time.', out)

    def test_ctrl_c_after_a_seat_lists_it(self):
        with fake_cpa(FAKE_CPA_EMAIL='work@test'), FakePool(), \
                mock.patch.object(cp, 'set_priority_when_loaded', side_effect=KeyboardInterrupt):
            code, out, _ = self.setup('', 'Work', 'n', '')
        self.assertEqual(code, 130)
        self.assertIn('Setup stopped. Added 1 seat this time: Work (plus, 1×).', out)

    def test_pool_down(self):
        code, out, err = self.setup('n')
        self.assertEqual(code, 1)
        self.assertIn(f'the pool is not answering on 127.0.0.1:{cp.PORT}', out)
        self.assertIn('Restart it now? [Y/n]', out)
        self.assertIn('Setup cannot add seats while the pool is down', err)
        self.kick.assert_not_called()

    def test_not_installed(self):
        with mock.patch.object(cp, 'missing_install', return_value=['~/.codexpool/config.yaml']):
            code, out, err = self.setup()
        self.assertEqual((code, out), (1, ''))
        self.assertIn('codexpool is not installed yet', err)

    def test_needs_a_terminal(self):
        def no_terminal():
            p = Prompter(io.StringIO())
            p.src = None
            return p
        with mock.patch.object(cp, 'Prompter', no_terminal):
            code, _, err = run(cp.cmd_setup)
        self.assertEqual(code, 1)
        self.assertIn('needs a terminal', err)


class SeatCommands(unittest.TestCase):
    """reserve, label and remove, against the stand-in pool."""

    def setUp(self):
        p = mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY)
        p.start()
        self.addCleanup(p.stop)
        meta = preserved(cp.SEATS_META)
        meta.__enter__()
        self.addCleanup(meta.__exit__, None, None, None)
        self.files = {}
        for label, email, plan, priority in (('Work', 'work@test', 'plus', 400), ('Team', 'team@test', 'plus', 300),
                                             ('Pro', 'pro@test', 'pro', 350)):
            f = fake_seat_file(email, plan, priority=priority)
            self.addCleanup(self.unlink, f)
            cp.update_meta(f.name, label=label)
            self.files[label] = f.name

    @staticmethod
    def unlink(path):
        with contextlib.suppress(FileNotFoundError):
            path.unlink()

    def test_reserve_moves_the_seat_last(self):
        with FakePool() as pool:
            code, out, _ = run(cp.cmd_reserve, seat='Pro', off=False)
        self.assertEqual(code, 0)
        self.assertEqual(pool.priorities, {self.files['Pro']: 290})
        self.assertEqual(cp.read_meta()[self.files['Pro']], {'label': 'Pro', 'reserve': True})
        self.assertIn('Pro is a reserve seat. It now fills after every regular seat (priority 350 → 290).', out)

    def test_reserve_already_last_stays(self):
        with FakePool() as pool:
            run(cp.cmd_reserve, seat='Team', off=False)
            self.assertEqual(pool.priorities, {})
            code, out, _ = run(cp.cmd_reserve, seat='Team', off=True)
        self.assertEqual(code, 0)
        self.assertEqual(pool.priorities, {})
        self.assertNotIn('It now fills', out)
        self.assertEqual(cp.read_meta()[self.files['Team']], {'label': 'Team'})

    def test_reserve_off_moves_the_seat_before_the_reserves(self):
        cp.update_meta(self.files['Pro'], reserve=True)
        cp.update_meta(self.files['Team'], reserve=True)  # Team (300) fills after the reserve Pro (350)
        with FakePool() as pool:
            code, out, _ = run(cp.cmd_reserve, seat='Team', off=True)
        self.assertEqual(code, 0)
        self.assertEqual(pool.priorities, {self.files['Team']: 375})  # between Work (400) and the reserve Pro (350)
        self.assertIn('It now fills before the reserve seats (priority 300 → 375).', out)

    def test_label_refuses_what_login_refuses(self):
        with mock.patch.object(cp, 'load_seats') as load:
            for bad, text in (('two\nlines', 'one line'), ('-x', 'start with -'), ('', 'empty'), (' Work', 'space')):
                code, _, err = run(cp.cmd_label, seat='Work', label=bad)
                self.assertEqual(code, 1, repr(bad))
                self.assertIn('codexpool label: a label', err)
                self.assertIn(text, err)
            load.assert_not_called()
        self.assertEqual(cp.read_meta()[self.files['Work']], {'label': 'Work'})

    def test_label(self):
        with FakePool():
            code, out, _ = run(cp.cmd_label, seat='Work', label='Work A')
        self.assertEqual(code, 0)
        self.assertEqual(cp.read_meta()[self.files['Work']], {'label': 'Work A'})

    def test_remove_forgets_the_reserve_flag(self):
        cp.update_meta(self.files['Pro'], weight=20, reserve=True)
        with FakePool() as pool:
            code, _, _ = run(cp.cmd_remove, seat='Pro', yes=True)
        self.assertEqual(code, 0)
        self.assertEqual(pool.deleted, [self.files['Pro']])
        self.assertNotIn(self.files['Pro'], cp.read_meta())


if __name__ == '__main__':
    unittest.main()
