"""The samples in docs/LANES.md are what subpool renders for examples/lanes.json (with the default bridge port):
the lanes.json file itself, the config.yaml block of a normal lane apply (lane aliases only; member aliases exist
only while lane test runs), the role file and the ~/.codex/AGENTS.md block."""
import json
import re
import unittest
from unittest import mock

from _helpers import EXAMPLE_LANES, REPO, cp

DOC = REPO / 'docs' / 'LANES.md'
FLOAT_PAIRS = re.compile(r'(max_output_tokens|session_id|timeout_ms)')  # the sample shows one pair of four


class LanesDocSamples(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.blocks = re.findall(r'```(\w+)\n(.*?)```', DOC.read_text(), re.S)
        with mock.patch.object(cp, 'BRIDGE_PORT', 8320):
            cls.plan = cp.lane_plan(cp.validate_lanes(EXAMPLE_LANES), {'grok-4.7-build-fast': 500000})

    def sample(self, kind):
        found = [body for k, body in self.blocks if k == kind]
        self.assertTrue(found, f'docs/LANES.md has no ```{kind} sample')
        return found[0]

    def test_lanes_json(self):
        self.assertEqual(json.loads(self.sample('json')), EXAMPLE_LANES)

    def test_agents_md_block(self):
        self.assertEqual(self.sample('markdown').splitlines(), cp.render_agents_block(self.plan).splitlines()[1:-1])

    def test_role_file(self):
        self.assertEqual(self.sample('toml'), cp.render_role(self.plan[0]))

    def test_config_block(self):
        rendered = cp.render_lanes_block(self.plan, '<bridge key>')
        want = [line for line in rendered.splitlines() if not FLOAT_PAIRS.search(line)]
        got = [line for line in self.sample('yaml').splitlines() if '# ... the same pair' not in line]
        self.assertEqual(got, want, 'the ```yaml sample in docs/LANES.md should read (plus a "# ... the same pair for '
                                    'max_output_tokens, session_id and timeout_ms" line after the first pair):\n'
                         + '\n'.join(want))


if __name__ == '__main__':
    unittest.main()
