"""Source extension and subscription cache tests; all OAuth calls are synthetic."""
from _helpers import addon, HOME
import hashlib
import importlib
import json
import os
import tempfile
import textwrap
import types
import unittest
from pathlib import Path
from unittest import mock

patch = importlib.import_module(addon.cswap.__package__ + '.cswap_patch')


class PlanDetection(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=HOME)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.clock = mock.Mock(return_value=10000)
        self.oauth = types.SimpleNamespace(extract_access_token=mock.Mock(return_value='synthetic'),
            fetch_oauth_profile=mock.Mock(return_value={'uuid': 'example-id', 'subscription': {
                'organizationType': 'claude_max', 'rateLimitTier': 'default_claude_max_20x',
                'hasMax': True, 'private': 'do-not-persist'}}))
        ns = dict(json=json, os=os, time=types.SimpleNamespace(time=self.clock), oauth=self.oauth)
        exec(textwrap.dedent(patch.SUBSCRIPTION_METHOD), ns)
        self.owner = types.SimpleNamespace(backup_dir=self.root,
            account_identity=mock.Mock(return_value={'uuid': 'example-id', 'email': 'work@example.com', 'organizationUuid': ''}),
            _resolved_matches_slot_identity=mock.Mock(return_value=True))
        self.detect = lambda: ns['_subpool_subscription'](self.owner, '1', 'synthetic')

    def test_cache_contains_only_public_metadata_and_rechecks_on_expiry(self):
        self.assertEqual(self.detect()['rateLimitTier'], 'default_claude_max_20x')
        self.detect()
        self.oauth.fetch_oauth_profile.assert_called_once()
        cache = next((self.root / 'cache').glob('*.json')).read_text()
        self.assertNotIn('synthetic', cache)
        self.assertNotIn('do-not-persist', cache)
        self.clock.return_value += 21601
        self.oauth.fetch_oauth_profile.return_value['subscription']['rateLimitTier'] = 'default_claude_max_5x'
        self.assertEqual(self.detect()['rateLimitTier'], 'default_claude_max_5x')

    def test_unresolved_or_foreign_identity_stays_unknown_and_backs_off(self):
        self.owner._resolved_matches_slot_identity.return_value = False
        self.assertIsNone(self.detect())
        self.assertIsNone(self.detect())
        self.oauth.fetch_oauth_profile.assert_called_once()
        self.clock.return_value += 901
        self.owner._resolved_matches_slot_identity.return_value = True
        self.assertIsNotNone(self.detect())

    def test_changed_identity_cannot_inherit_plan(self):
        self.detect()
        self.owner.account_identity.return_value['uuid'] = 'another-example-id'
        self.oauth.fetch_oauth_profile.return_value = None
        self.assertIsNone(self.detect())
        self.assertEqual(self.oauth.fetch_oauth_profile.call_count, 2)

    def test_patcher_checks_all_sources_and_is_idempotent(self):
        sources = {
            'oauth.py': 'def profile():\n    org_uuid = None\n    organization = {}\n    account = {}\n    return {\n' + patch.PROFILE_ANCHOR,
            'switcher.py': 'class Switcher:\n' + patch.LIST_ANCHOR +
                '        self, accounts_info, entries\n    ):\n        accounts = []\n        for num, creds in accounts_info:\n'
                '            accounts.append({})\n' + patch.ROW_ANCHOR + '        }\n        return payload\n',
        }
        for name, value in sources.items():
            (self.root / name).write_text(value)
        binary = self.root / 'cswap'
        binary.write_text('#!/fake/python\n')
        hashes = {name: hashlib.sha256(value.encode()).hexdigest() for name, value in sources.items()}
        lookup = types.SimpleNamespace(stdout=str(self.root))
        with mock.patch.dict(patch.HASHES, hashes, clear=True), mock.patch.object(patch.subprocess, 'run', return_value=lookup):
            patch.install(str(binary))
            rendered = {name: (self.root / name).read_text() for name in sources}
            patch.install(str(binary))
            self.assertEqual(rendered, {name: (self.root / name).read_text() for name in sources})
            (self.root / 'switcher.py').write_text('# unexpected change\n')
            with self.assertRaises(ValueError):
                patch.install(str(binary))
            self.assertEqual((self.root / 'oauth.py').read_text(), rendered['oauth.py'])
