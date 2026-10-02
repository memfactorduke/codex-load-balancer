"""The guard's heal step and sign-ins that OpenAI ended: guard_pass() with the pool's seat list, its log and its refresh
answers made up here. An ended sign-in (refresh_token_invalidated, "Your session has ended", refresh_token_reused,
invalid_grant) asks for a new sign-in on the first pass that sees it; the seat stays ready ("sign in again soon")
while the pool still serves it and is blocked once the pool stops. Any other 401 is healed as before, three refreshes
15 minutes apart, then a notification."""
import argparse
import contextlib
import datetime as dt
import json
import unittest
from unittest import mock

from _helpers import TEST_KEY, cp, fake_seat_file, preserved, seat_name, write_private

SEAT = seat_name('work@test')
# What the pool's refresh endpoint answers (HTTP 500) when OpenAI refuses the refresh: its error, with OpenAI's body
BODY_ENDED = ('{"error":"token refresh failed after 3 attempts: token refresh failed with status 401: {\\n  '
              '\\"error\\": {\\n    \\"message\\": \\"Your session has ended. Please sign in again.\\",\\n    '
              '\\"type\\": \\"invalid_request_error\\",\\n    \\"param\\": null,\\n    '
              '\\"code\\": \\"refresh_token_invalidated\\"\\n  }\\n}"}')
BODY_REUSED = ('{"error":"token refresh failed with status 401: {\\"error\\": {\\"message\\": \\"Your refresh '
               'token has already been used to generate a new access token.\\", \\"code\\": '
               '\\"refresh_token_reused\\"}}"}')
BODY_TRANSIENT = ('{"error":"token refresh failed with status 401: {\\"error\\": {\\"message\\": \\"Provided '
                  'authentication token is expired.\\", \\"code\\": \\"token_expired\\"}}"}')
SOON = 'OpenAI ended this sign-in: sign in again soon'   # the row's detail while the pool still serves the seat
ENDED_NOTE = ('Codex seat needs a re-login', 'Work: OpenAI ended this sign-in. Sign in again: subpool login Work '
              '--priority 300 (or click it in the menu bar).')


def stamp(t):
    """A pool-log time stamp (local time, to the second)."""
    return t.astimezone().strftime('%Y-%m-%d %H:%M:%S')


def refresh_line(t, seat=SEAT, source='conductor_refresh.go:585',
                 why='token refresh failed with status 401: { "error": { "message": "Your session has ended. Please '
                     'sign in again.", "type": "invalid_request_error", "param": null, "code": '
                     '"refresh_token_invalidated" } }'):
    return (f'[{stamp(t)}] [--------] [warn ] [{source}] credential refresh failed for codex ({seat}): {why}; '
            'retaining active credential as access token is unexpired\n')


class EndedSignIn(unittest.TestCase):
    def setUp(self):
        self.now = cp.now_utc()
        self.path = fake_seat_file('work@test', priority=300)
        self.addCleanup(self.path.unlink)
        self.signed_in(self.now - dt.timedelta(days=2))
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(preserved(cp.GUARD_FILE, cp.STATUS_FILE, cp.HISTORY_FILE, cp.MAIN_LOG, cp.GUARD_LOG,
                                      cp.SEATS_META))
        for f in (cp.GUARD_FILE, cp.MAIN_LOG):
            if f.exists():
                f.unlink()
        cp.update_meta(SEAT, label='Work')
        self.pool = {'status': 'active', 'unavailable': False, 'status_message': '', 'cooldowns': []}
        self.refresh_answer = None   # the error body of the pool's refresh answer (None: the refresh worked)
        self.refreshes, self.notes, self.all_notes = [], [], []
        self.others = []             # more seats, healthy ones, after Work in the fill order (see spare())
        for name, kwargs in (('load_seats', {'side_effect': self.seats}), ('api', {'side_effect': self.api}),
                             ('notify', {'side_effect': self.notify}),
                             ('poll_usage', {'side_effect': cp.ApiError(500, 'not here')}),
                             ('poll_resets', {'side_effect': cp.ApiError(500, 'not here')})):
            stack.enter_context(mock.patch.object(cp, name, **kwargs))

    def notify(self, title, body):
        self.all_notes.append((title, body))   # with the pool's other news (another seat, all out)
        if title == 'Codex seat needs a re-login':
            self.notes.append((title, body))

    def signed_in(self, when):
        """The seat file as a sign-in (or a refresh that worked) leaves it: last_refresh is when."""
        data = json.loads(self.path.read_text())
        data['last_refresh'] = when.astimezone().isoformat(timespec='seconds')
        write_private(self.path, json.dumps(data))

    def seats(self):
        healthy = {'status': 'active', 'unavailable': False, 'status_message': '', 'cooldowns': []}
        return [self.seat_row(self.path, 'Work', 300, self.pool)] + \
            [self.seat_row(path, label, priority, healthy) for path, label, priority in self.others]

    @staticmethod
    def seat_row(path, label, priority, pool):
        claims = cp.file_claims(path)
        return {'name': path.name, 'id': path.name, 'path': str(path), 'provider': 'codex', 'auth_index': '1',
                'label': label, 'weight': 1.0, 'reserve': False, 'email': claims['email'], 'plan': claims['plan'],
                'account_id': claims['account_id'], 'priority': priority, 'disabled': False,
                'unavailable': pool['unavailable'], 'status': pool['status'], 'status_message': pool['status_message'],
                'next_retry_after': None, 'cooldowns': pool['cooldowns'], 'quota': {}, 'success': 10, 'failed': 2}

    def spare(self):
        """A second seat, Spare, healthy, after Work in the fill order."""
        path = fake_seat_file('spare@test', priority=200)
        self.addCleanup(path.unlink)
        cp.update_meta(path.name, label='Spare')
        self.others.append((path, 'Spare', 200))

    def api(self, method, path, body=None, **kwargs):
        if path == '/v0/management/auth-files/refresh':
            self.refreshes.append(body['name'])
            if self.refresh_answer is not None:
                raise cp.ApiError(500, self.refresh_answer)
            return {'ok': True}
        raise AssertionError(f'unexpected {method} {path}')

    def blocked_by_the_pool(self):
        """CLIProxyAPI's view after a refresh failed with the access token run out: unavailable, "unauthorized"."""
        self.pool.update(status='error', unavailable=True, status_message='unauthorized')

    def guard_pass(self):
        cp.guard_pass()
        guard = json.loads(cp.GUARD_FILE.read_text())
        rows = json.loads(cp.STATUS_FILE.read_text())['seats']
        self.assertEqual(len(rows), 1 + len(self.others))
        return guard['seats'][SEAT], next(r for r in rows if r['name'] == SEAT)

    def warm_up(self):
        """Two passes: the first notes the seat's tokens, the second marks where the log is; lines after count."""
        self.guard_pass()
        self.guard_pass()

    def log(self, *lines):
        with cp.MAIN_LOG.open('a') as f:
            f.writelines(lines)

    def test_a_heal_refresh_that_says_the_session_ended(self):
        self.blocked_by_the_pool()
        self.refresh_answer = BODY_ENDED
        g, row = self.guard_pass()
        self.assertEqual(self.refreshes, [SEAT])
        self.assertEqual(self.notes, [ENDED_NOTE])   # the first pass that sees it, not the third heal attempt
        self.assertEqual((row['state'], row['detail'], row['until']), ('blocked', 'OpenAI ended this sign-in', None))
        self.assertEqual(g['sign_in_ended']['seen'], 'a heal refresh')
        self.assertFalse(set(cp.HEAL_KEYS) & set(g))
        _, row = self.guard_pass()   # no more refreshes, no second notification
        self.assertEqual(self.refreshes, [SEAT])
        self.assertEqual(self.notes, [ENDED_NOTE])
        self.assertEqual(row['state'], 'blocked')

    def test_a_reused_refresh_token_is_final_too(self):
        self.blocked_by_the_pool()
        self.refresh_answer = BODY_REUSED
        g, _ = self.guard_pass()
        self.assertEqual(self.notes, [ENDED_NOTE])
        self.assertIn('sign_in_ended', g)

    def test_other_401s_heal_as_before(self):
        """Three refreshes 15 minutes apart, then the old notification; the seat stays on heal probation."""
        self.blocked_by_the_pool()
        self.refresh_answer = BODY_TRANSIENT
        for attempt in (1, 2, 3):
            g, row = self.guard_pass()
            self.assertEqual((g['heal_failures'], len(self.refreshes)), (attempt, attempt))
            self.assertNotIn('sign_in_ended', g)
            self.assertEqual((row['state'], row['detail']), ('blocked', 'unauthorized'))
            self.assertEqual(self.notes, [] if attempt < 3 else [
                ('Codex seat needs a re-login', 'Work: unauthorized. Run: subpool login Work --priority 300')])
            guard = json.loads(cp.GUARD_FILE.read_text())
            guard['seats'][SEAT]['last_heal'] = (self.now - dt.timedelta(minutes=16)).isoformat()
            cp.write_json(cp.GUARD_FILE, guard)
        self.assertTrue(g['relogin_notified'])
        self.guard_pass()   # after the notification it waits four times as long
        self.assertEqual(len(self.refreshes), 3)

    def test_a_refresh_that_works_heals(self):
        self.blocked_by_the_pool()
        g, _ = self.guard_pass()
        self.assertEqual((g['heal_failures'], self.notes), (1, []))
        self.assertNotIn('sign_in_ended', g)

    def test_the_pool_log(self):
        """A seat still served on its access token: the pool logs the refused refresh, the guard asks for a new
        sign-in at once and never refreshes it; the seat serves on, "sign in again soon". A new sign-in clears it."""
        self.warm_up()   # passes that note which tokens the seat has and where the log then ends
        self.log('[2026-01-01 00:00:00] [--------] [info ] [main.go:1] an unrelated line\n',
                 refresh_line(self.now - dt.timedelta(minutes=1)))
        g, row = self.guard_pass()
        self.assertEqual(self.notes, [ENDED_NOTE])
        self.assertEqual(g['sign_in_ended']['seen'], 'the pool log')
        self.assertEqual((row['state'], row['detail'], row['sign_in_ended']), ('active', SOON, True))
        self.assertEqual(self.refreshes, [])
        self.signed_in(self.now)   # subpool login: new tokens
        g, row = self.guard_pass()
        self.assertNotIn('sign_in_ended', g)
        self.assertEqual((row['state'], row['detail'], row['sign_in_ended']), ('active', '', False))
        self.assertEqual(self.notes, [ENDED_NOTE])
        self.log(refresh_line(self.now - dt.timedelta(seconds=30)))   # written late, about the old tokens
        _, row = self.guard_pass()
        self.assertEqual((row['state'], self.notes), ('active', [ENDED_NOTE]))

    def test_old_lines_after_a_time_zone_change_do_not_count(self):
        """The pool logs naive local times. After the Mac changes time zone, a line written before a new sign-in can
        read as later than it: lines already in the log when the guard starts reading it never count."""
        self.log(refresh_line(self.now + dt.timedelta(hours=1)))   # an old line, misread an hour late
        _, row = self.guard_pass()
        self.assertEqual((row['state'], self.notes), ('active', []))
        _, row = self.guard_pass()
        self.assertEqual((row['state'], self.notes), ('active', []))

    def test_lines_around_a_new_sign_in_do_not_count(self):
        """Lines logged while the guard sees new tokens, and in the pass after, may be about the old tokens: only
        lines after the seat's token mark count, whatever their time stamps say."""
        self.warm_up()
        self.signed_in(self.now)
        self.log(refresh_line(self.now + dt.timedelta(hours=1)))   # time stamps can't be trusted: never used
        _, row = self.guard_pass()                                  # sees the new tokens
        self.assertEqual((row['state'], self.notes), ('active', []))
        self.log(refresh_line(self.now + dt.timedelta(hours=1)))
        _, row = self.guard_pass()                                  # marks the log for them
        self.assertEqual((row['state'], self.notes), ('active', []))
        self.log(refresh_line(self.now - dt.timedelta(hours=5)))   # after the mark: counts, even "earlier" stamped
        _, row = self.guard_pass()
        self.assertEqual((row['detail'], self.notes), (SOON, [ENDED_NOTE]))

    def test_a_backlog_after_new_tokens_does_not_count(self):
        """More than two passes' worth of old lines (the reader takes 4 MiB a pass): a line already in the log when
        the mark is taken never counts, even when it is read passes later; a line after the mark does."""
        self.warm_up()
        self.log(*(['[2026-01-01 00:00:00] [--------] [info ] [main.go:1] ' + 'x' * 1000 + '\n'] * (9 * 1024)))
        self.log(refresh_line(self.now))                           # about the old tokens, 9 MiB into the backlog
        self.signed_in(self.now)
        for _ in range(4):                                          # sees the new tokens, marks, reads the rest
            _, row = self.guard_pass()
            self.assertEqual((row['state'], self.notes), ('active', []))
        self.log(refresh_line(self.now))                           # after the mark: about these tokens
        _, row = self.guard_pass()
        self.assertEqual((row['detail'], self.notes), (SOON, [ENDED_NOTE]))

    def test_a_log_cut_short_in_place_is_a_new_generation(self):
        """The same file truncated: read again from its start, and its lines come after the old mark."""
        self.log('x' * 5000 + '\n')
        self.warm_up()
        cp.MAIN_LOG.write_text('')                                  # same inode, shorter
        self.log(refresh_line(self.now))
        _, row = self.guard_pass()
        self.assertEqual((row['detail'], self.notes), (SOON, [ENDED_NOTE]))

    def test_an_unreadable_log_takes_no_mark(self):
        """If the log can't be read on the pass that would mark it, the mark waits: old lines read later never count."""
        self.log('x' * 100 + '\n')
        self.guard_pass()                                           # notes the tokens
        self.log(refresh_line(self.now))                           # an old-token line, not read yet
        unreadable = cp.MAIN_LOG.with_name('unreadable.log')
        unreadable.mkdir()                                          # opening a directory fails, and not as "missing"
        self.addCleanup(unreadable.rmdir)
        with mock.patch.object(cp, 'MAIN_LOG', unreadable):
            self.guard_pass()                                       # there but unreadable: no mark
        g = json.loads(cp.GUARD_FILE.read_text())['seats'][SEAT]
        self.assertNotIn('tokens_from', g)
        _, row = self.guard_pass()                                  # marks now (after the old line), reads it: ignored
        self.assertEqual((row['state'], self.notes), ('active', []))

    def test_log_lines_older_than_the_tokens_do_not_count(self):
        self.log(refresh_line(self.now - dt.timedelta(days=3)))
        _, row = self.guard_pass()
        self.assertEqual((row['state'], self.notes), ('active', []))

    def test_log_lines_that_do_not_count(self):
        self.log(refresh_line(self.now, source='gin_logger.go:92'),   # an access-log line: any client writes those
                 refresh_line(self.now, seat=seat_name('other@test')),
                 refresh_line(self.now, why='token refresh request failed: dial tcp: i/o timeout'))
        _, row = self.guard_pass()
        self.assertEqual((row['state'], self.notes), ('active', []))

    def test_the_log_is_read_once(self):
        self.log('[2026-01-01 00:00:00] [--------] [info ] [main.go:1] an unrelated line\n')
        self.warm_up()   # reads nothing back, remembers where the log ends, marks the seat's tokens
        guard = json.loads(cp.GUARD_FILE.read_text())
        self.assertEqual(guard['pool_log']['offset'], cp.MAIN_LOG.stat().st_size)
        self.log(refresh_line(self.now)[:-1])   # half a line: read once it is whole
        _, row = self.guard_pass()
        self.assertEqual((row['state'], self.notes), ('active', []))
        self.log('\n')
        _, row = self.guard_pass()
        self.assertEqual((row['detail'], self.notes), (SOON, [ENDED_NOTE]))
        self.assertEqual(json.loads(cp.GUARD_FILE.read_text())['pool_log']['offset'], cp.MAIN_LOG.stat().st_size)

    def test_a_rotated_log_is_read_from_its_start(self):
        self.log('x' * 500 + '\n')
        self.warm_up()
        cp.MAIN_LOG.unlink()
        self.log(refresh_line(self.now))
        _, row = self.guard_pass()
        self.assertEqual((row['detail'], self.notes), (SOON, [ENDED_NOTE]))

    def test_an_invalid_grant_block(self):
        """The pool's seat list says only what blocked the seat (invalid_grant here); the guard's refresh on that
        pass says whether the sign-in ended."""
        self.pool.update(status='error', unavailable=True, status_message='unauthorized', cooldowns=[
            {'scope': 'credential', 'reason': 'invalid_grant',
             'retry_at': (self.now + dt.timedelta(minutes=30)).isoformat()}])
        self.refresh_answer = ('{"error":"token refresh failed with status 400: {\\"error\\": \\"invalid_grant\\", '
                               '\\"error_description\\": \\"Invalid refresh token\\"}"}')
        g, row = self.guard_pass()
        self.assertEqual((g['sign_in_ended']['seen'], self.notes, self.refreshes),
                         ('a heal refresh', [ENDED_NOTE], [SEAT]))
        self.assertEqual((row['state'], row['detail'], row['until']), ('blocked', 'OpenAI ended this sign-in', None))

    def test_a_sign_in_after_the_pool_blocked_the_seat(self):
        """The pool still lists the old block after a new sign-in: the refresh works, nothing is marked."""
        self.pool.update(status='error', unavailable=True, status_message='unauthorized', cooldowns=[
            {'scope': 'credential', 'reason': 'invalid_grant',
             'retry_at': (self.now + dt.timedelta(minutes=30)).isoformat()}])
        g, _ = self.guard_pass()
        self.assertNotIn('sign_in_ended', g)
        self.assertEqual((g['heal_failures'], self.notes), (1, []))

    def test_a_transient_status_message_is_healed(self):
        self.pool.update(status='error', unavailable=True, status_message='token expired')
        g, _ = self.guard_pass()
        self.assertNotIn('sign_in_ended', g)
        self.assertEqual((g['heal_failures'], self.notes), (1, []))

    def doctor(self):
        rep = cp.DoctorReport(echo=False)
        with mock.patch.object(cp, 'api', side_effect=cp.PoolDown('not in this test')), \
                mock.patch.object(cp, 'mgmt_key', return_value=TEST_KEY):
            cp.doctor_checks(rep)
        (seats,) = [sec['checks'] for sec in rep.sections if sec['title'] == 'Seats']
        return seats

    def test_a_seat_the_pool_still_serves(self):
        """Marked on the pool log's word while the pool still serves the seat on its access token (for up to a day):
        the guard asks for a sign-in and shows the seat serving, "sign in again soon" (doctor warns), and says
        nothing of the pool moving on or running dry until the pool itself stops serving the seat. Then the seat
        is blocked."""
        self.warm_up()
        self.log(refresh_line(self.now - dt.timedelta(minutes=1)))
        _, row = self.guard_pass()
        self.assertEqual((row['state'], row['detail']), ('active', SOON))
        st = json.loads(cp.STATUS_FILE.read_text())
        self.assertEqual((st['active'], st['pool']['available']), ('Work', 1))
        self.assertEqual(self.all_notes, [ENDED_NOTE])
        checks = self.doctor()
        (lands,) = [c for c in checks if c['text'].startswith('new threads land on')]
        self.assertEqual((lands['status'], lands['text']), ('ok', 'new threads land on: Work'))
        (work,) = [c for c in checks if c['text'].startswith('Work: ')]
        self.assertEqual(work, {'status': 'warn', 'text': f'Work: active ({SOON})',
                                'fix': 'subpool login Work --priority 300'})
        self.guard_pass()
        self.assertEqual(self.all_notes, [ENDED_NOTE])
        self.blocked_by_the_pool()   # its access token ran out
        _, row = self.guard_pass()
        self.assertEqual((row['state'], row['detail']), ('blocked', 'OpenAI ended this sign-in'))
        self.assertIsNone(json.loads(cp.STATUS_FILE.read_text())['active'])
        self.assertEqual(self.all_notes, [ENDED_NOTE, ('All Codex seats are exhausted', 'No reset time known.')])
        self.assertEqual(self.refreshes, [])

    def test_a_seat_the_pool_still_serves_before_another(self):
        """The same with a second seat after it: no "Codex now on Spare" while the pool still sends Work the
        traffic, and the move is announced when the pool makes it."""
        self.spare()
        self.warm_up()
        self.log(refresh_line(self.now - dt.timedelta(minutes=1)))
        self.guard_pass()
        self.assertEqual(self.all_notes, [ENDED_NOTE])
        self.assertEqual(json.loads(cp.GUARD_FILE.read_text())['announced'], SEAT)
        st = json.loads(cp.STATUS_FILE.read_text())
        self.assertEqual((st['active'], [r['state'] for r in st['seats']]), ('Work', ['active', 'ready']))
        args = argparse.Namespace(from_seat='Work', to_seat='Spare')   # selftest still takes it for blocked
        with mock.patch.object(cp, 'codex_bin', return_value='codex'), self.assertRaises(SystemExit) as out:
            cp.cmd_selftest(args)
        self.assertEqual(str(out.exception.code), 'Work is blocked; pick seats that are ready')
        self.blocked_by_the_pool()
        self.guard_pass()
        self.assertEqual(self.all_notes, [ENDED_NOTE, ('Codex now on Spare', 'Work: OpenAI ended this sign-in')])
        self.assertEqual(json.loads(cp.STATUS_FILE.read_text())['active'], 'Spare')
        self.signed_in(self.now)   # subpool login: new tokens, and the pool serves Work again
        self.pool.update(status='active', unavailable=False, status_message='')
        _, row = self.guard_pass()
        self.assertEqual(row['state'], 'active')
        self.assertEqual(self.all_notes[2:], [('Codex now on Work', 'Spare: ready')])

    def test_doctor_asks_for_a_sign_in_only(self):
        self.blocked_by_the_pool()
        self.refresh_answer = BODY_ENDED
        self.guard_pass()
        (work,) = [c for c in self.doctor() if c['text'].startswith('Work: ')]
        self.assertEqual(work, {'status': 'fail', 'text': 'Work: blocked (OpenAI ended this sign-in)',
                                'fix': 'subpool login Work --priority 300'})


class PoolView(unittest.TestCase):
    """The guard's mark for an ended sign-in over the pool's own state (pool_only: without it, as selftest sees)."""

    def test_pool_only(self):
        s = {'name': SEAT, 'disabled': False, 'cooldowns': [], 'status_message': '', 'unavailable': False,
             'status': 'active', 'next_retry_after': None}
        guard = {'seats': {SEAT: {'sign_in_ended': {'tokens': None}}}}
        now = cp.now_utc()
        self.assertEqual(cp.seat_state(s, guard, now), ('ready', SOON, None))   # the pool still serves it
        self.assertEqual(cp.seat_state(s, guard, now, pool_only=True), ('ready', '', None))
        out = dict(s, status='error', unavailable=True, status_message='unauthorized')   # the pool stopped
        self.assertEqual(cp.seat_state(out, guard, now), ('blocked', 'OpenAI ended this sign-in', None))
        self.assertEqual(cp.seat_state(out, guard, now, pool_only=True)[:2], ('blocked', 'unauthorized'))
        self.assertEqual(cp.seat_state(dict(s, disabled=True), guard, now)[0], 'disabled')  # off stays off


class SignInEnded(unittest.TestCase):
    def test_codes(self):
        for text in ('refresh_token_invalidated', 'Your session has ended. Please sign in again.',
                     'refresh_token_reused', 'invalid_grant', 'refresh_token_revoked', 'refresh_token_expired'):
            self.assertTrue(cp.SIGN_IN_ENDED.search(text), text)
        for text in ('unauthorized', 'token expired', 'Provided authentication token is expired.', 'token_expired',
                     'HTTP 401', 'dial tcp: i/o timeout'):
            self.assertFalse(cp.SIGN_IN_ENDED.search(text), text)


if __name__ == '__main__':
    unittest.main()
