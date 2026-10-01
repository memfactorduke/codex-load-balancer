"""The active Claude product delegates to cswap; it never operates credentials itself."""
from _helpers import cp, addon, HOME
import argparse
import copy
import json
import subprocess
import unittest
from unittest import mock

backend = addon.cswap


def payload():
    return {'schemaVersion': 1, 'activeAccountNumber': 2, 'accounts': [
        {'number': 1, 'alias': 'Work', 'active': False, 'usageStatus': 'ok',
         'usage': {'sevenDay': {'pct': 80}, 'fiveHour': {'pct': 100, 'resetsAt': '2026-10-01T23:00:00Z'}}},
        {'number': 2, 'alias': 'Personal', 'active': True, 'usageStatus': 'ok',
         'usage': {'sevenDay': {'pct': 25}, 'fiveHour': {'pct': 15}}}]}


class Cswap(unittest.TestCase):
    def setUp(self):
        self.temp = mock.patch.object(backend, 'OPTIONS', HOME / 'cswap-test-options.json')
        self.temp.start()
        self.addCleanup(self.temp.stop)
        backend.OPTIONS.unlink(missing_ok=True)
        self.addCleanup(lambda: backend.OPTIONS.unlink(missing_ok=True))

    def test_selected_account_is_not_a_pool_average(self):
        view = backend.project(payload())
        self.assertEqual(view['pool']['used_pct'], 25)
        self.assertEqual(view['seats'][0]['state'], 'exhausted')
        self.assertEqual(view['seats'][1]['week']['used'], 25)
        self.assertEqual(view['active'], 'Personal')
        self.assertEqual(view['seats'][0]['until'], '2026-10-01T23:00:00+00:00')

    def test_unknown_usage_stays_unknown(self):
        data = payload()
        data['accounts'][1]['usageStatus'] = 'unavailable'
        view = backend.project(data)
        self.assertIsNone(view['pool']['used_pct'])
        self.assertFalse(view['pool']['selected_usage_known'])

    def test_schema_and_identity_disagreement_refuse(self):
        for edit in (lambda p: p.update(schemaVersion=2),
                     lambda p: p.update(activeAccountNumber=7),
                     lambda p: p['accounts'].append(copy.deepcopy(p['accounts'][0]))):
            data = payload()
            edit(data)
            with self.assertRaises(ValueError):
                backend.project(data)

    def test_unknown_fields_never_enter_saved_view(self):
        data = payload()
        data['privateField'] = 'do-not-persist'
        data['accounts'][0]['credentials'] = 'do-not-persist'
        self.assertNotIn('do-not-persist', json.dumps(backend.project(data)))

    def test_auto_is_opt_in(self):
        with mock.patch.object(backend, 'executable', return_value='/fake/cswap'), mock.patch.object(backend, 'call') as call:
            backend.guard()
        call.assert_not_called()

    def test_auto_delegates_once_and_accepts_no_change(self):
        cp.write_json(backend.OPTIONS, {'enabled': True, 'auto_switch': True})
        with mock.patch.object(backend, 'executable', return_value='/fake/cswap'), mock.patch.object(backend, 'call') as call, mock.patch.object(backend, 'refresh') as refresh:
            backend.guard()
        call.assert_called_once_with(['auto', '--once', '--json'], ok=(0, 2, 3))
        refresh.assert_called_once()

    def test_switch_requires_exact_target_confirmation(self):
        args = argparse.Namespace(cswap_action='switch', account='2')
        with mock.patch.object(backend, 'call', return_value={'to': {'number': 1}, 'reason': 'switched'}), mock.patch.object(backend, 'refresh') as refresh:
            with self.assertRaises(SystemExit):
                backend.command(args)
        refresh.assert_not_called()

    def test_public_cli_has_no_proxy_or_desktop_or_lane(self):
        parser = cp.build_parser()
        args = parser.parse_args(['claude', 'switch', '2'])
        self.assertIs(args.fn, backend.command)
        self.assertEqual(args.account, '2')
        self.assertFalse(addon.lane_providers())
        self.assertFalse(addon.pools())

    def test_install_dry_run_never_launches_anything(self):
        with mock.patch.object(backend.subprocess, 'run') as run:
            backend.install(argparse.Namespace(dry_run=True))
        run.assert_not_called()

    def test_subprocess_is_argv_and_errors_do_not_echo_output(self):
        with mock.patch.object(backend, 'executable', return_value='/fake/cswap'), mock.patch.object(backend.subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, 'secret-value', 'secret-value')) as run:
            with self.assertRaises(ValueError) as error:
                backend.call(['list', '--json'], structured=True)
        self.assertNotIn('secret-value', str(error.exception))
        self.assertEqual(run.call_args.args[0], ['/fake/cswap', 'list', '--json'])

    def test_guard_lock_is_not_codex_or_legacy_lock(self):
        lock = backend.guard_descriptor(addon.pool.claude_seat_pool()).lock
        self.assertNotEqual(lock, cp.LOCK_FILE)
        self.assertNotEqual(lock, addon.pool.CLAUDE_LOCK_FILE)

    def test_refresh_does_not_replace_last_good_file_on_bad_response(self):
        path = HOME / 'cswap-last-good.json'
        path.write_text('last-good')
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        with mock.patch.object(backend, 'STATUS', path), mock.patch.object(backend, 'call', return_value={'schemaVersion': 1, 'accounts': 'invalid', 'settings': []}):
            with self.assertRaises(ValueError):
                backend.refresh()
        self.assertEqual(path.read_text(), 'last-good')

    def test_refresh_reads_public_json_and_projects_it(self):
        path = HOME / 'cswap-new-view.json'
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        with mock.patch.object(backend, 'STATUS', path), mock.patch.object(backend, 'call', side_effect=[payload(), {'schemaVersion': 1, 'settings': [{'key': 'autoswitch.strategy', 'value': 'consume-first'}]}]) as call:
            backend.refresh()
        self.assertEqual(json.loads(path.read_text())['pool']['strategy'], 'consume-first')
        self.assertEqual(call.call_args_list[0].args[0], ['list', '--json'])

    def test_invalid_threshold_does_not_call_cswap(self):
        with mock.patch.object(backend, 'call') as call:
            with self.assertRaises(SystemExit):
                backend.command(argparse.Namespace(cswap_action='threshold', value=float('nan')))
        call.assert_not_called()

    def test_remove_requires_explicit_confirmation(self):
        with mock.patch.object(backend, 'call') as call:
            with self.assertRaises(SystemExit):
                backend.command(argparse.Namespace(cswap_action='remove', account='2', yes=False))
        call.assert_not_called()

    def test_install_refuses_legacy_proxy_before_any_change(self):
        with mock.patch.object(addon.pool, 'claude_installed', return_value=True), mock.patch.object(backend.subprocess, 'run') as run:
            with self.assertRaises(ValueError):
                backend.install(argparse.Namespace(dry_run=False))
        run.assert_not_called()

    def test_guard_and_status_are_absent_until_enabled(self):
        self.assertEqual(addon.guard_passes(), [])
        self.assertIsNone(addon.status(False))

    def test_existing_proxy_keeps_protection_until_explicit_retirement(self):
        with mock.patch.object(addon.pool, 'claude_installed', return_value=True):
            passes = addon.guard_passes()
            self.assertEqual(passes[0][1], addon.guard.claude_guard_pass)
            self.assertIn('claude', addon.seat_pools())
        self.assertNotEqual(passes[0][1], backend.guard)


if __name__ == '__main__':
    unittest.main()
