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
        {'number': 1, 'alias': 'Work', 'email': 'work@example.com', 'active': False, 'usageStatus': 'ok',
         'usage': {'sevenDay': {'pct': 80}, 'fiveHour': {'pct': 100, 'resetsAt': '2026-10-01T23:00:00Z'}}},
        {'number': 2, 'alias': 'Personal', 'email': 'personal@example.com', 'active': True, 'usageStatus': 'ok',
         'usage': {'sevenDay': {'pct': 25}, 'fiveHour': {'pct': 15}}}]}


class Cswap(unittest.TestCase):
    def setUp(self):
        self.temp = mock.patch.object(backend, 'OPTIONS', HOME / 'cswap-test-options.json')
        self.temp.start()
        self.addCleanup(self.temp.stop)
        backend.OPTIONS.unlink(missing_ok=True)
        self.addCleanup(lambda: backend.OPTIONS.unlink(missing_ok=True))

    def test_retirement_can_update_status_after_proxy_unregisters(self):
        legacy = addon.pool
        path = HOME / 'retired-claude-status.json'
        cp.write_json(path, {'pool': {'installed': True}})
        with mock.patch.object(legacy, 'CLAUDE_STATUS_FILE', path), mock.patch.object(cp, 'seat_pool', side_effect=AssertionError('proxy is no longer registered')):
            legacy.patch_claude_status(installed=False)
        self.assertFalse(cp.read_json(path, {})['pool']['installed'])

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

    def reserve_options(self, auto=True, excluded=False):
        cp.write_json(backend.OPTIONS, {'enabled': True, 'auto_switch': auto,
            'reserves': {'personal@example.com': {'excluded': excluded, 'held': False}}})

    def test_reserve_is_held_while_regular_has_quota(self):
        self.reserve_options()
        data = payload()
        data['accounts'][0]['usage']['fiveHour']['pct'] = 20
        with mock.patch.object(backend, 'call') as call:
            backend.sync_reserves(backend.project(data))
        call.assert_called_once_with(['disable', '2'])
        data['accounts'][1]['disabled'] = True
        account = backend.project(data)['seats'][1]
        self.assertTrue(account['reserve'])
        self.assertTrue(account['reserve_held'])
        self.assertEqual(account['state'], 'active')

    def test_reserve_is_released_only_at_regular_threshold(self):
        self.reserve_options()
        cp.write_json(backend.OPTIONS, {**backend.options(), 'reserves': {
            'personal@example.com': {'excluded': False, 'held': True}}})
        data = payload()
        data['accounts'][1]['disabled'] = True
        with mock.patch.object(backend, 'call') as call:
            backend.sync_reserves(backend.project(data, {'autoswitch.threshold': 90}))
        call.assert_called_once_with(['enable', '2'])
        self.assertFalse(backend.reserves()['personal@example.com']['held'])

    def test_reserve_is_held_with_auto_off_even_if_regular_exhausted(self):
        self.reserve_options(auto=False)
        with mock.patch.object(backend, 'call') as call:
            backend.sync_reserves(backend.project(payload()))
        call.assert_called_once_with(['disable', '2'])

    def test_unknown_regular_usage_does_not_release_reserve(self):
        self.reserve_options()
        data = payload()
        data['accounts'][0]['usageStatus'] = 'unavailable'
        with mock.patch.object(backend, 'call') as call:
            backend.sync_reserves(backend.project(data))
        call.assert_called_once_with(['disable', '2'])

    def test_one_healthy_regular_keeps_reserve_held(self):
        self.reserve_options()
        data = payload()
        extra = copy.deepcopy(data['accounts'][0])
        extra.update(number=3, email='other@example.com', usage={'sevenDay': {'pct': 5}, 'fiveHour': {'pct': 10}})
        data['accounts'].append(extra)
        with mock.patch.object(backend, 'call') as call:
            backend.sync_reserves(backend.project(data))
        call.assert_called_once_with(['disable', '2'])

    def test_excluded_reserve_is_never_released(self):
        self.reserve_options(excluded=True)
        data = payload()
        data['accounts'][1]['disabled'] = True
        with mock.patch.object(backend, 'call') as call:
            backend.sync_reserves(backend.project(data))
        call.assert_not_called()
        self.assertEqual(backend.project(data)['seats'][1]['state'], 'disabled')

    def test_excluded_regular_does_not_block_release(self):
        self.reserve_options()
        data = payload()
        data['accounts'][0].update(disabled=True, usageStatus='unavailable')
        with mock.patch.object(backend, 'call') as call:
            backend.sync_reserves(backend.project(data))
        call.assert_not_called()  # Reserve already enabled; no regular account is eligible.

    def test_reserve_follows_identity_after_slot_changes(self):
        self.reserve_options(auto=False)
        data = payload()
        data['accounts'][1]['number'] = 3
        data['activeAccountNumber'] = 3
        with mock.patch.object(backend, 'call') as call:
            backend.sync_reserves(backend.project(data))
        call.assert_called_once_with(['disable', '3'])
        data['accounts'][1]['email'] = 'different@example.com'
        self.assertFalse(backend.project(data)['seats'][1]['reserve'])

    def test_unreserve_restores_only_adapter_held_account(self):
        for excluded in (False, True):
            self.reserve_options(excluded=excluded)
            records = backend.reserves()
            records['personal@example.com']['held'] = True
            cp.write_json(backend.OPTIONS, {**backend.options(), 'reserves': records})
            with mock.patch.object(backend, 'refresh', return_value=backend.project(payload())), mock.patch.object(backend, 'call') as call:
                backend.set_reserve('2', 'off')
            self.assertEqual(call.call_count, 0 if excluded else 1)
            self.assertEqual(backend.reserves(), {})

    def test_reserve_command_does_not_enable_auto_or_switch(self):
        view = backend.project(payload())
        with mock.patch.object(backend, 'refresh', return_value=view), mock.patch.object(backend, 'call') as call:
            backend.command(cp.build_parser().parse_args(['claude', 'reserve', '2']))
        call.assert_called_once_with(['disable', '2'])
        self.assertIsNot(backend.options().get('auto_switch'), True)

    def test_reserve_guard_gates_before_delegating_choice(self):
        self.reserve_options()
        data = payload()
        data['accounts'][0]['usage']['fiveHour']['pct'] = 20
        with mock.patch.object(backend, 'executable', return_value='/fake/cswap'), mock.patch.object(backend, 'refresh', return_value=backend.project(data)), mock.patch.object(backend, 'call') as call:
            backend.guard()
        self.assertEqual(call.call_args_list, [mock.call(['disable', '2']), mock.call(['auto', '--once', '--json'], ok=(0, 2, 3))])

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
