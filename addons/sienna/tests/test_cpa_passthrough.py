"""CPA compatibility checks. All processes use a throwaway HOME and fabricated credentials.

Set CODEXPOOL_CPA_SOURCE (defaults to sibling scratchpad/cpa-src) for the Go check,
CODEXPOOL_GO if Go is not on PATH, and CODEXPOOL_CPA_BINARY plus CODEXPOOL_CPA_FIXTURES
for captured E4.2 requests. No captured fixture is claimed until E4 runs; absence skips.
"""
from _helpers import HOME, REPO, free_ports
import copy
import hashlib
import http.server
import json
import os
from pathlib import Path
import queue
import re
import shutil
import subprocess
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.parse
import urllib.request


from _helpers import addon
from importlib import import_module
_checker = import_module(addon.lane_engine.__package__ + ".cpa_check")
changed_paths, body_view, allowed_diff = _checker.changed_paths, _checker.body_view, _checker.allowed_diff
Capture = _checker.Capture

class AllowedDiff(unittest.TestCase):
    def fixture(self):
        return {'headers': {'Authorization': 'Bearer placeholder', 'anthropic-beta': 'native', 'x-app': 'cli'},
                'body': {'metadata': {'user_id': json.dumps(dict(account_uuid='old', device_id='old', session_id='same'))},
                         'system': [{'type': 'text', 'text': 'x-anthropic-billing-header: cc_entrypoint=sdk-cli; cch=00000;'}],
                         'messages': [{'role': 'user', 'content': 'ok'}], 'max_tokens': 100}}

    def test_only_credential_fields_and_signature_are_allowed(self):
        before = self.fixture()
        after = copy.deepcopy(before)
        after['body']['metadata']['user_id'] = json.dumps(dict(account_uuid='new', device_id='new', session_id='same'))
        after['body']['system'][0]['text'] = after['body']['system'][0]['text'].replace('00000', 'abcde')
        after['headers']['Authorization'] = 'Bearer synthetic-account'
        after['headers']['anthropic-beta'] += ',oauth-2025-04-20'
        self.assertEqual(allowed_diff(before, after), [])
        after['body']['max_tokens'] = 200
        after['body']['metadata']['user_id'] = json.dumps(dict(account_uuid='new', device_id='new', session_id='changed'))
        after['body']['system'][0]['text'] += ' injected'
        self.assertEqual(allowed_diff(before, after), ['$.max_tokens', '$.metadata.user_id.session_id', '$.system[0].text'])

    def test_deletion_new_tools_and_new_header_fail(self):
        before = self.fixture()
        after = copy.deepcopy(before)
        del after['body']['max_tokens']
        after['body']['tools'] = [{'name': 'Bash'}]
        after['headers']['unexpected'] = 'value'
        self.assertEqual(allowed_diff(before, after), ['$.max_tokens', '$.tools', '$.headers.unexpected'])


class FailedChunk(unittest.TestCase):
    def test_cpa_source_failed_chunk(self):
        source = Path(os.environ.get('CODEXPOOL_CPA_SOURCE', REPO.parents[1] / 'cpa-src'))
        implementation = source / 'sdk' / 'api' / 'handlers' / 'openai_responses_stream_error.go'
        go = os.environ.get('CODEXPOOL_GO') or shutil.which('go')
        if not implementation.is_file() or not go:
            self.skipTest('CPA source and Go required (CODEXPOOL_CPA_SOURCE, CODEXPOOL_GO)')
        with tempfile.TemporaryDirectory(dir=HOME) as temp:
            work = Path(temp)
            # Compile the actual standalone CPA source, unmodified, with no module downloads or source-tree edits.
            shutil.copyfile(implementation, work / implementation.name)
            shutil.copyfile(Path(__file__).parent / 'cpa/failed_chunk_test.go', work / 'failed_chunk_test.go')
            env = dict(PATH=os.environ['PATH'], HOME=str(work), TMPDIR=str(work), GO111MODULE='off',
                       GOPROXY='off', GOSUMDB='off', GOTOOLCHAIN='local', GOCACHE=str(work / 'cache'))
            result = subprocess.run([go, 'test', '-v', '.'], cwd=work, env=env, capture_output=True, text=True, timeout=120)
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)



if __name__ == '__main__':
    unittest.main()
