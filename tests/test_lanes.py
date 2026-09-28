"""Lanes: lanes.json validation, the lane plan and what lane apply renders from it, config.yaml and AGENTS.md
splicing, status lines, and the checks that need a pool (against a stand-in pool). Member aliases (<lane>-<id>)
exist only while lane test runs: lane_plan(..., with_members=True)."""
import contextlib
import io
import json
import os
import random
import shutil
import socket
import threading
import time
import unittest
from unittest import mock

from _helpers import EXAMPLE_LANES, HOME, FakePool, cp, load_bridge, preserved, run

try:
    import tomllib
except ImportError:  # Python < 3.11
    tomllib = None

DEFS = {'grok-4.7-build-fast': 500000}
GOOD = {'provider': 'xai', 'model': 'grok-1'}
MULTI = {'lanes': {
    'bulk': EXAMPLE_LANES['lanes']['bulk'],
    'fast': {'role': 'Quick lookups.', 'effort': 'low', 'members': [{'provider': 'xai', 'model': 'grok-mini'}]},
    'cheap': {'role': 'Drafts', 'effort': 'medium', 'members': [{'id': 'zen', 'provider': 'opencode-zen',
                                                                 'model': 'm-zen'}]}}}
CHEAP = {'lanes': {'cheap': MULTI['lanes']['cheap']}}


def plan_of(raw=EXAMPLE_LANES, defs=DEFS, members=False):
    return cp.lane_plan(cp.validate_lanes(raw), defs, with_members=members)


def problems(raw):
    try:
        cp.validate_lanes(raw)
    except cp.LaneError as e:
        return e.problems
    return []


def lane(members, **spec):
    return {'lanes': {'b': dict({'role': 'r', 'members': members}, **spec)}}


def base_config():
    return cp.CONFIG.read_text()


class Validation(unittest.TestCase):
    CASES = [
        ([], ['must hold a JSON object']),
        ({'lanes': [], 'extra': 1}, ['unknown key "extra"', '"lanes" must be an object']),
        ({'lanes': {'Bulk': {'role': 'r', 'members': [GOOD]}}}, ['lane "Bulk": a lane name']),
        ({'lanes': {'default': {'role': 'r', 'members': [GOOD]}}}, ['not "default"']),
        (lane([GOOD], role=''), ['"role" must be']),
        (lane([GOOD], role='line1\nline2'), ['"role" must be']),
        (lane([GOOD], role='a <!-- b'), ['"role" must be']),
        (lane([GOOD], effort='max'), ['effort "max" is not one of']),
        (lane([]), ['"members" must be a non-empty list']),
        (lane([GOOD, {'provider': 'xai', 'model': 'grok-2'}]), ['at most one xai member']),
        (lane([{'provider': 'gemini', 'model': 'm'}]), ['provider "gemini" is not one of']),
        (lane([{'provider': 'xai', 'model': 'bad model!'}]), ['"model" must be']),
        (lane([dict(GOOD, id='a'), {'id': 'a', 'provider': 'opencode-go', 'model': 'x'}]), ['id "a" is used twice']),
        (lane([dict(GOOD, id='Bad_Id')]), ['"id" must be 1 to 16']),
        (lane([dict(GOOD, context=0)]), ['"context" must be a positive']),
        (lane([dict(GOOD, context=True)]), ['"context" must be a positive']),
        (lane([{'provider': 'responses', 'model': 'm'}]), ['needs "base_url"']),
        (lane([{'provider': 'responses', 'model': 'm', 'base_url': 'http://x/v1'}]), ['needs "base_url"']),
        (lane([{'provider': 'responses', 'model': 'm', 'base_url': 'https://x.io/v1', 'session_header': 'bad header'}]),
         ['"session_header" must be']),
        (lane([{'provider': 'opencode-go', 'model': 'm', 'base_url': 'https://x.io/v1'}]),
         ['"base_url" is only for responses']),
        (lane([dict(GOOD, colour='red')], priority=1), ['unknown key "colour"', 'unknown key "priority"']),
        (lane([dict(GOOD, name='')]), ['"name" must be']),
        ({'lanes': {'zz-codexpool-test-bulk-grok': {'role': 'r', 'members': [GOOD]}}},
         ['does not start with "zz-codexpool-test-"']),
        (lane([{'provider': [], 'model': 'm'}]), ['provider [] is not one of']),
        (lane([{'provider': {}, 'model': 'm'}]), ['provider {} is not one of']),
        (lane([GOOD], display=''), ['"display" must be a one-line string of 1 to 40 characters']),
        (lane([GOOD], display='   '), ['"display" must be']),
        (lane([GOOD], display='Bulk\nlane'), ['"display" must be']),
        (lane([GOOD], display='x' * 41), ['"display" must be']),
        (lane([GOOD], display=7), ['"display" must be']),
        (lane([GOOD], display=['Bulk']), ['"display" must be']),
        (lane([GOOD], display='Bulk\u2028lane'), ['"display" must be']),   # Unicode line separator
        (lane([GOOD], role='a\u2029b'), ['"role" must be']),              # Unicode paragraph separator
        (lane([dict(GOOD, name='Grok\u2028Fast')]), ['"name" must be']),
    ]

    def test_messages(self):
        for raw, texts in self.CASES:
            found = problems(raw)
            self.assertTrue(found, raw)
            for text in texts:
                self.assertTrue(any(text in p for p in found), (text, found))
            self.assertTrue(all(p.startswith('lanes.json: ') for p in found), found)

    def test_underscore_keys_ignored_and_empty_lanes(self):
        ok = cp.validate_lanes({'_c': 1, 'lanes': {'_off': 5, 'b': {'_x': 1, 'role': 'r',
                                                                    'members': [dict(GOOD, _y=2)]}}})
        self.assertEqual([x['name'] for x in ok], ['b'])
        self.assertEqual(cp.validate_lanes({'lanes': {}}), [])

    def test_responses_member_cannot_take_a_catalog_key(self):
        p = problems({'lanes': {'opencode': {'role': 'r', 'members': [
            {'id': 'go', 'provider': 'responses', 'model': 'm', 'base_url': 'https://api.other.example/v1'}]}}})
        self.assertTrue(any('"opencode-go" is the opencode-go provider\'s key' in x for x in p), p)
        p = problems({'lanes': {'opencode': {'role': 'r', 'members': [
            {'provider': 'responses', 'model': 'zen-1', 'base_url': 'https://api.other.example/v1'}]}}})
        self.assertTrue(any('"opencode-zen"' in x for x in p), p)
        self.assertEqual(problems({'lanes': {'opencode': {'role': 'r', 'members': [
            {'id': 'mine', 'provider': 'responses', 'model': 'm', 'base_url': 'https://api.other.example/v1'}]}}}), [])

    def test_aliases_unique_across_lanes(self):
        p = problems({'lanes': {'bulk': {'role': 'r', 'members': [{'provider': 'xai', 'model': 'grok-1'}]},
                                'bulk-grok': {'role': 'r', 'members': [{'provider': 'opencode-go',
                                                                        'model': 'kimi-2'}]}}})
        want = '"bulk-grok" would mean both lane "bulk" member grok and lane "bulk-grok"'
        self.assertTrue(any(want in x for x in p), p)
        p = problems({'lanes': {'bulk': {'role': 'r', 'members': [{'id': 'fast', 'provider': 'xai', 'model': 'm1'}]},
                                'bulk-fast': {'role': 'r', 'members': [{'provider': 'opencode-go', 'model': 'm2'}]}}})
        self.assertEqual(len([x for x in p if 'bulk-fast' in x]), 1, p)

    def test_junk_raises_only_lane_errors(self):
        rng = random.Random(7)
        junk = [None, True, 0, 1.5, -3, '', 'x', 'bulk', [], {}, [1], {'a': 1}, 'xai', 'responses', 'opencode-go',
                'grok-1', 'https://x.io/v1', 'bad header', '<!--']
        for _ in range(3000):
            member = {k: rng.choice(junk) for k in rng.sample(cp.MEMBER_KEYS + ('zzz',), rng.randrange(0, 6))}
            spec = {k: rng.choice(junk + [[member], [member, member]])
                    for k in rng.sample(cp.LANE_KEYS + ('qq',), rng.randrange(0, 4))}
            raw = rng.choice([{'lanes': {rng.choice(['b', 'Bad', '_x', 'default', 'a-b']): spec}},
                              {'lanes': rng.choice(junk)}, rng.choice(junk), {'lanes': {'b': spec, 'b-x': spec}}])
            with contextlib.suppress(cp.LaneError):
                cp.validate_lanes(raw)


class Plan(unittest.TestCase):
    def test_ids_priorities_contexts(self):
        raw = {'lanes': {'x': {'role': 'r', 'members': [
            {'provider': 'opencode-go', 'model': 'muse-a'}, {'provider': 'opencode-zen', 'model': 'muse-b'},
            {'provider': 'xai', 'model': 'grok-1'},
            {'provider': 'responses', 'model': 'org/muse.c', 'base_url': 'https://api.example.com/v1'}]}}}
        (x,) = plan_of(raw, {'grok-1': 100000})
        ms = x['members']
        self.assertEqual([m['id'] for m in ms], ['muse', 'muse2', 'grok', 'orgmuse'])
        self.assertEqual([m.get('priority') for m in ms], [20, 10, None, -10])
        self.assertEqual((x['context'], ms[0]['context']), (100000, 272000))
        self.assertEqual((ms[3]['key_name'], ms[3]['upstream_url']), ('x-orgmuse', 'https://api.example.com/v1'))
        bridge = json.loads(cp.render_bridge_config([x]))
        self.assertEqual(set(bridge['upstreams']), {'opencode-go', 'opencode-zen', 'responses-x-orgmuse'})
        self.assertIsNone(bridge['upstreams']['responses-x-orgmuse']['session_header'])
        self.assertEqual(bridge['port'], cp.BRIDGE_PORT)

    def test_without_xai_priorities_count_down(self):
        (y,) = plan_of({'lanes': {'y': {'role': 'r', 'members': [
            {'provider': 'opencode-go', 'model': 'a-1'}, {'provider': 'opencode-go', 'model': 'b-1'},
            {'provider': 'opencode-go', 'model': 'c-1'}]}}})
        self.assertEqual([m['priority'] for m in y['members']], [30, 20, 10])
        self.assertEqual(cp.lane_description(y, y['members']),
                         'Y lane: a-1 at xhigh; then b-1, then c-1 (xhigh) when the earlier ones are unavailable or '
                         'used up. r.')

    def test_context_unknown_without_the_pool(self):
        (bulk,) = plan_of(defs=None)
        grok = bulk['members'][0]
        self.assertEqual((grok['context'], grok['context_known']), (cp.LANE_CONTEXT, False))
        self.assertEqual(bulk['context'], cp.LANE_CONTEXT)

    def test_no_bridge_members_no_bridge_config(self):
        self.assertIsNone(cp.render_bridge_config(plan_of({'lanes': {'f': MULTI['lanes']['fast']}})))


class MemberAliases(unittest.TestCase):
    """Normal apply offers only the lane aliases; lane test adds the member aliases for its run."""

    def test_normal_plan_offers_only_lanes(self):
        plan = plan_of()
        self.assertEqual(cp.lane_aliases(plan), ['bulk'])
        self.assertEqual(cp.member_aliases(plan[0]), [])
        block = cp.render_lanes_block(plan, 'K')
        self.assertNotIn('"bulk-grok"', block)
        self.assertNotIn('"bulk-muse"', block)
        self.assertNotIn(' only"', block)

    def test_lane_test_plan_adds_members(self):
        plan = plan_of(members=True)
        self.assertEqual(cp.lane_aliases(plan), ['bulk', 'bulk-grok', 'bulk-muse'])
        block = cp.render_lanes_block(plan, 'K')
        self.assertIn('    - { name: "grok-4.7-build-fast", alias: "bulk-grok", fork: false, display-name: '
                      '"Bulk lane: Grok 4.7 Fast only" }', block)
        self.assertIn('      - { name: "lane-bulk-muse", alias: "bulk-muse", display-name: '
                      '"Bulk lane: Muse Spark 1.3 contributor only", max-context-length: 1048576 }', block)
        self.assertIn('{ name: "bulk", protocol: "codex" }, { name: "bulk-grok", protocol: "codex" } ]', block)
        self.assertIn('{ name: "bulk", protocol: "meta" }, { name: "bulk-muse", protocol: "meta" } ]', block)

    def test_xai_alias_has_the_meta_display_name(self):
        for members in (False, True):
            plan = plan_of(members=members)
            block = cp.render_lanes_block(plan, 'K')
            display = plan[0]['display']
            self.assertEqual(display, 'Bulk')
            self.assertIn(f'    - {{ name: "grok-4.7-build-fast", alias: "bulk", fork: false, display-name: '
                          f'"{display}" }}', block)
            self.assertIn(f'      - {{ name: "lane-bulk-muse", alias: "bulk", display-name: "{display}", '
                          f'max-context-length: 500000 }}', block)

    def test_display_names(self):
        for m in plan_of(members=True)[0]['members']:
            self.assertEqual(m['display'], f'Bulk lane: {m["name"]} only')

    def test_default_display_is_the_lane_name(self):
        """The picker shows the lane by name ("Bulk" for bulk); its members stay under the hood."""
        (bulk,) = plan_of()
        self.assertEqual(bulk['display'], 'Bulk')
        members = [{'provider': 'opencode-go', 'model': f'm-{i}', 'name': f'Model {i}'} for i in range(1, 5)]
        for n in range(1, 5):
            (quick,) = plan_of({'lanes': {'quick': {'role': 'r', 'members': members[:n]}}})
            self.assertEqual(quick['display'], 'Quick', n)
        for name, want in (('code-review', 'Code-review'), ('x2', 'X2'), ('a' + 'b' * 30, 'A' + 'b' * 30)):
            (lane,) = plan_of({'lanes': {name: {'role': 'r', 'members': members[:2]}}})
            self.assertEqual(lane['display'], want)
            self.assertLessEqual(len(lane['display']), cp.LANE_DISPLAY_MAX)  # the longest lane name fits too

    def test_display_is_trimmed(self):
        raw = {'lanes': {'bulk': dict(EXAMPLE_LANES['lanes']['bulk'], display='  Bulk: Grok  ')}}
        self.assertEqual(cp.validate_lanes(raw)[0]['display'], 'Bulk: Grok')
        self.assertIn('display-name: "Bulk: Grok" }', cp.render_lanes_block(plan_of(raw), 'K'))
        raw = {'lanes': {'bulk': dict(EXAMPLE_LANES['lanes']['bulk'], display=' ' + 'x' * 40 + ' ')}}
        self.assertEqual(problems(raw), [])   # 40 characters once trimmed

    def test_display_from_lanes_json(self):
        raw = {'lanes': {'bulk': dict(EXAMPLE_LANES['lanes']['bulk'], display='Bulk (Grok, Muse)')}}
        for members in (False, True):
            plan = plan_of(raw, members=members)
            self.assertEqual(plan[0]['display'], 'Bulk (Grok, Muse)')
            block = cp.render_lanes_block(plan, 'K')
            self.assertIn('alias: "bulk", fork: false, display-name: "Bulk (Grok, Muse)" }', block)
            self.assertIn('alias: "bulk", display-name: "Bulk (Grok, Muse)", max-context-length: 500000 }', block)
            self.assertEqual(block.count('display-name: "Bulk (Grok, Muse)"'), 2)
            if members:  # member aliases keep their own names
                self.assertIn('display-name: "Bulk lane: Grok 4.7 Fast only"', block)
        self.assertEqual(cp.validate_lanes(raw)[0]['display'], 'Bulk (Grok, Muse)')
        self.assertIsNone(cp.validate_lanes(EXAMPLE_LANES)[0]['display'])
        self.assertEqual(plan_of({'lanes': {'bulk': dict(EXAMPLE_LANES['lanes']['bulk'], display=None)}})[0]['display'],
                         'Bulk')  # null: the default
        self.assertEqual(problems({'lanes': {'bulk': dict(EXAMPLE_LANES['lanes']['bulk'], display='x' * 40)}}), [])

    def test_role_file_and_agents_md_name_members_in_full(self):
        raw = {'lanes': {'bulk': dict(EXAMPLE_LANES['lanes']['bulk'], display='Bulk: Grok, then Muse')}}
        plain, named = plan_of()[0], plan_of(raw)[0]
        self.assertEqual(cp.render_role(named), cp.render_role(plain))
        self.assertEqual(cp.render_agents_block([named]), cp.render_agents_block([plain]))
        self.assertIn('Muse Spark 1.3 contributor', cp.render_role(named))

    def test_config_toggles_only_the_member_aliases(self):
        base = base_config()
        normal = cp.lanes_config_text(base, plan_of(), 'K', None)
        testing = cp.lanes_config_text(normal, plan_of(members=True), 'K', None)
        self.assertEqual(cp.block_aliases(normal), {'bulk'})
        self.assertEqual(cp.block_aliases(testing), {'bulk', 'bulk-grok', 'bulk-muse'})
        self.assertEqual(cp.lanes_config_text(testing, plan_of(), 'K', None), normal)
        removed = [line for line in testing.splitlines() if line not in normal.splitlines()]
        self.assertTrue(removed and all('bulk-grok' in line or 'bulk-muse' in line for line in removed), removed)


class RenderBlock(unittest.TestCase):
    def test_sections_appear_once(self):
        for members in (False, True):
            block = cp.render_lanes_block(plan_of(MULTI, dict(DEFS, **{'grok-mini': 200000}), members), 'K')
            for section in ('oauth-model-alias:', 'meta-api-key:', 'payload:', '  override:', '  filter:'):
                self.assertEqual(block.count('\n' + section + '\n'), 1, (section, members))

    def test_several_lanes(self):
        defs = dict(DEFS, **{'grok-mini': 200000})
        block = cp.render_lanes_block(plan_of(MULTI, defs), 'K')
        self.assertIn('alias: "fast", fork: false', block)
        self.assertNotIn('fast-grok', block)
        self.assertIn('    - models: [ { name: "fast", protocol: "codex" } ]\n'
                      '      params: { "reasoning.effort": "low" }', block)
        self.assertIn('    - models: [ { name: "cheap", protocol: "meta" } ]\n'
                      '      params: { "reasoning.effort": "medium" }', block)
        self.assertIn('priority: 10\n', block)
        self.assertNotIn('protocol: "codex" }, { name: "cheap', block)
        rules = [line for line in block.splitlines() if line.startswith('    - models:')]
        self.assertEqual(rules[-1], '    - models: [ { name: "bulk", protocol: "codex" }, { name: "fast", protocol: '
                                    '"codex" } ]')  # the filter and the float fix cover every xai lane

    def test_several_lanes_during_lane_test(self):
        defs = dict(DEFS, **{'grok-mini': 200000})
        block = cp.render_lanes_block(plan_of(MULTI, defs, members=True), 'K')
        self.assertIn('alias: "fast-grok"', block)
        self.assertIn('{ name: "fast", protocol: "codex" }, { name: "fast-grok", protocol: "codex" } ]\n'
                      '      params: { "reasoning.effort": "low" }', block)
        self.assertIn('{ name: "cheap", protocol: "meta" }, { name: "cheap-zen", protocol: "meta" } ]\n'
                      '      params: { "reasoning.effort": "medium" }', block)
        rules = [line for line in block.splitlines() if line.startswith('    - models:')]
        self.assertEqual(rules[-1], '    - models: [ { name: "bulk", protocol: "codex" }, { name: "bulk-grok", '
                                    'protocol: "codex" }, { name: "fast", protocol: "codex" }, { name: "fast-grok", '
                                    'protocol: "codex" } ]')

    def test_bridge_only_lane(self):
        block = cp.render_lanes_block(plan_of(CHEAP), 'K')
        for absent in ('oauth-model-alias', 'filter', 'namespace'):
            self.assertNotIn(absent, block)

    def test_float_fields(self):
        block = cp.render_lanes_block(plan_of(), 'K')
        for field in cp.FLOAT_FIELDS:
            self.assertEqual(block.count(f'parameters.properties.{field}.type=="number"'), 2, field)

    def test_no_lanes(self):
        self.assertEqual(cp.render_lanes_block([], 'K'), '')


class ConfigSplice(unittest.TestCase):
    HIDDEN = ['grok-3', 'grok-4']

    def raises(self, fn, text):
        with self.assertRaises(cp.LaneError) as ctx:
            fn()
        self.assertIn(text, str(ctx.exception))
        return str(ctx.exception)

    def test_append_idempotent_remove(self):
        base, plan = base_config(), plan_of()
        t1 = cp.lanes_config_text(base, plan, 'KEY', self.HIDDEN)
        self.assertTrue(t1.startswith(base.rstrip('\n')))
        self.assertEqual(t1.count(cp.LANES_END), 1)
        self.assertIn('oauth-excluded-models:\n  # Only the lane models are offered from xAI.\n  xai:\n    - "grok-3"\n'
                      '    - "grok-4"\n', t1)
        self.assertEqual(cp.lanes_config_text(t1, plan, 'KEY', self.HIDDEN), t1)
        self.assertEqual(cp.lanes_config_text(t1, [], 'KEY', None), base)
        self.assertEqual(cp.existing_xai_hidden(t1), self.HIDDEN)

    def test_sub_block_under_the_users_own_key(self):
        base, plan = base_config(), plan_of()
        own = base + 'oauth-excluded-models:\n  codex:\n    - "old"\n'
        t2 = cp.lanes_config_text(own, plan, 'KEY', self.HIDDEN)
        self.assertIn('oauth-excluded-models:\n  # >>> codexpool lanes (xai): only the lane models are offered >>>\n'
                      '  xai:\n    - "grok-3"\n    - "grok-4"\n  # <<< codexpool lanes (xai) <<<\n  codex:\n', t2)
        self.assertNotIn('# Only the lane models', t2)
        self.assertEqual(cp.existing_xai_hidden(t2), self.HIDDEN)
        t2b = cp.lanes_config_text(t2, plan, 'KEY', ['grok-9'])
        self.assertEqual(t2b.count('grok-9'), 1)
        self.assertNotIn('grok-3', t2b)
        t3 = cp.lanes_config_text(t2, plan_of(CHEAP), 'KEY', None)
        self.assertNotIn('codexpool lanes (xai)', t3)
        self.assertIn('oauth-excluded-models:\n  codex:', t3)
        self.assertEqual(cp.lanes_config_text(t2, [], 'K', None), own)
        four = base + 'oauth-excluded-models:\n    codex:\n        - "old"\n'
        t4 = cp.lanes_config_text(four, plan, 'K', self.HIDDEN)
        self.assertIn('\n    # >>> codexpool lanes (xai)', t4)
        self.assertIn('\n    xai:\n      - "grok-3"', t4)

    def test_quoted_excluded_key(self):
        q = cp.lanes_config_text(base_config() + '"oauth-excluded-models":\n  codex:\n    - "old"\n', plan_of(), 'K',
                                 ['grok-3'])
        self.assertEqual(q.count('\n"oauth-excluded-models":'), 1)
        self.assertNotIn('\noauth-excluded-models:', q)
        self.assertIn('"oauth-excluded-models":\n  # >>> codexpool lanes (xai)', q)

    def test_refusals(self):
        base, plan = base_config(), plan_of()
        self.raises(lambda: cp.lanes_config_text(base + 'payload:\n  override: []\n', plan, 'K', self.HIDDEN),
                    'payload')
        self.raises(lambda: cp.lanes_config_text(base + 'meta-api-key: []\noauth-model-alias:\n  codex: []\n', plan,
                                                 'K', self.HIDDEN), 'oauth-model-alias, meta-api-key')
        self.raises(lambda: cp.lanes_config_text(base + 'oauth-excluded-models:\n  xai:\n    - "x"\n', plan, 'K',
                                                 self.HIDDEN), 'xai: list of its own')
        self.raises(lambda: cp.lanes_config_text(base + 'oauth-excluded-models:\n  "xai":\n    - "x"\n', plan, 'K',
                                                 self.HIDDEN), 'xai: list of its own')
        self.raises(lambda: cp.lanes_config_text(base + 'oauth-excluded-models: {codex: [a]}\n', plan, 'K',
                                                 self.HIDDEN), 'on one line')
        self.raises(lambda: cp.lanes_config_text(base + cp.LANES_BEGIN + '\n', plan, 'K', self.HIDDEN),
                    'markers are broken')
        for text in ('"payload":\n  override: []\n', "'meta-api-key': []\n", '"oauth-model-alias" :\n  codex: []\n'):
            message = self.raises(lambda: cp.lanes_config_text(base + text, plan, 'K', ['grok-3']), 'lanes.json cannot '
                                  'hold them')
            self.assertIn('Remove or comment out your own', message)

    def test_no_clash_when_the_block_does_not_need_the_key(self):
        text = cp.lanes_config_text(base_config() + 'oauth-model-alias:\n  codex: []\n', plan_of(CHEAP), 'K', None)
        self.assertEqual(text.count('oauth-model-alias'), 1)

    def test_block_aliases(self):
        hand = (base_config() + '# >>> codexpool lanes: bulk = grok, then muse >>>\noauth-model-alias:\n  xai:\n'
                "    - { name: g, alias: bulk, fork: false }\n    - name: g\n      alias: 'bulk-grok'\n"
                '# <<< codexpool lanes <<<\n')
        self.assertEqual(cp.block_aliases(hand), {'bulk', 'bulk-grok'})
        self.assertEqual(cp.block_aliases(base_config()), set())

    def test_masking(self):
        self.assertEqual(cp.masked('  - api-key: "abc"\n  secret-key: "x" # c\napi-keys: []\n'),
                         '  - api-key: "<hidden>"\n  secret-key: "<hidden>"\napi-keys: []\n')
        m = cp.masked('meta-api-key:\n  - { api-key: "FLOW1", base-url: "http://x" }\n    headers:\n'
                      '      Authorization: "Bearer H1"\nremote-management: {secret-key: S1}\napi-keys:\n  - "K1"\n'
                      '"api-key": \'Q1\'\n')
        for secret in ('FLOW1', 'H1', 'S1', 'K1', 'Q1'):
            self.assertNotIn(secret, m)
        self.assertIn('base-url: "http://x"', m)
        block = cp.lanes_config_text(base_config(), plan_of(), 'THE-BRIDGE-KEY', None)
        self.assertIn('THE-BRIDGE-KEY', block)
        self.assertNotIn('THE-BRIDGE-KEY', cp.masked(block))


class AgentsMd(unittest.TestCase):
    def test_splice(self):
        block = cp.render_agents_block(plan_of())
        self.assertEqual(cp.splice_agents_md('', block), block)
        mine = '# My rules\n\nBe nice.\n'
        a1 = cp.splice_agents_md(mine, block)
        self.assertEqual(a1, mine + '\n' + block)
        self.assertEqual(cp.splice_agents_md(a1, block), a1)
        self.assertEqual(cp.splice_agents_md(a1, ''), mine)
        mid = mine + '\n' + block + '\n## After\n'
        self.assertEqual(cp.splice_agents_md(mid, block), mid)
        self.assertEqual(cp.splice_agents_md(mid, ''), mine + '\n## After\n')
        with self.assertRaises(cp.LaneError) as ctx:
            cp.splice_agents_md(cp.AGENTS_END + '\n', block)
        self.assertIn('markers are broken', str(ctx.exception))

    def test_no_lanes_no_block(self):
        self.assertEqual(cp.render_agents_block([]), '')


class DumpLanes(unittest.TestCase):
    def test_round_trip(self):
        text = cp.dump_lanes(EXAMPLE_LANES)
        self.assertEqual(json.loads(text), EXAMPLE_LANES)
        self.assertEqual(text, (cp.CODE_DIR / 'examples' / 'lanes.json').read_text())
        self.assertEqual(json.loads(cp.dump_lanes({'_note': 'x', 'lanes': {}})), {'_note': 'x', 'lanes': {}})


class RoleFiles(unittest.TestCase):
    @unittest.skipIf(tomllib is None, 'tomllib needs Python 3.11')
    def test_role_parses_as_toml(self):
        role = tomllib.loads(cp.render_role(plan_of()[0]))
        self.assertIs(role['features']['apps'], False)
        self.assertEqual((role['name'], role['model'], role['model_reasoning_effort']), ('bulk', 'bulk', 'xhigh'))
        self.assertTrue(role['developer_instructions'].startswith('You are'))
        member = plan_of(members=True)[0]['members'][0]
        pinned = tomllib.loads(cp.render_role(plan_of()[0], 'zz-codexpool-test-bulk-grok', member['alias'], [member]))
        self.assertEqual((pinned['name'], pinned['model']), ('zz-codexpool-test-bulk-grok', 'bulk-grok'))

    def test_leftovers_are_generated_roles_only(self):
        agents = cp.CODEX_AGENTS_DIR
        self.addCleanup(shutil.rmtree, str(HOME / '.codex'), True)
        agents.mkdir(parents=True, exist_ok=True)
        plan = plan_of(members=True)
        (agents / 'bulk.toml').write_text(cp.render_role(plan[0]))
        (agents / 'mine.toml').write_text('name = "mine"\n')
        (agents / 'zz-codexpool-test-bulk-grok.toml').write_text(
            cp.render_role(plan[0], 'zz-codexpool-test-bulk-grok', 'bulk-grok', [plan[0]['members'][0]]))
        self.assertEqual([p.name for p in cp.lane_leftovers()], ['bulk.toml', 'zz-codexpool-test-bulk-grok.toml'])
        self.assertTrue(cp.role_is_ours(agents / 'bulk.toml'))
        self.assertFalse(cp.role_is_ours(agents / 'mine.toml'))


class StatusLines(unittest.TestCase):
    def test_lines(self):
        st = {'pool': {'running': True}, 'seats': [{'provider': 'xai', 'state': 'cooldown'},
                                                   {'provider': 'codex', 'state': 'active'}]}
        self.assertEqual(cp.lane_status_lines(st), ['lane bulk: grok ◌ cooldown → muse ✕ bridge down'])
        self.assertEqual(cp.lane_status_lines({'seats': []}), ['lane bulk: grok ✕ missing → muse ✕ bridge down'])

    def test_invalid_lanes_file(self):
        with preserved(cp.LANES_FILE):
            cp.LANES_FILE.write_text('{"lanes": []}')
            self.assertEqual(cp.lane_status_lines({'seats': []}),
                             ['lanes: lanes.json is invalid (codexpool lane list says why)'])


class RefreshStatusFile(unittest.TestCase):
    SEAT = {'name': 'codex-a.json', 'id': 'codex-a.json', 'provider': 'codex', 'auth_index': 0, 'label': 'A',
            'weight': 1.0, 'reserve': False, 'email': None, 'plan': 'plus', 'account_id': None, 'priority': 1,
            'disabled': True, 'unavailable': False, 'status': 'disabled', 'status_message': '',
            'next_retry_after': None, 'cooldowns': [], 'quota': {}, 'success': 0, 'failed': 0}

    def test_writes_status_the_guard_way(self):
        with preserved(cp.STATUS_FILE), mock.patch.object(cp, 'load_seats', return_value=[dict(self.SEAT)]):
            self.assertTrue(cp.refresh_status_file())
            st = json.loads(cp.STATUS_FILE.read_text())
        self.assertEqual((st['seats'][0]['state'], st['pool']['seats']), ('disabled', 0))

    def test_pool_down_is_not_an_error(self):
        with preserved(cp.STATUS_FILE), mock.patch.object(cp, 'load_seats', side_effect=cp.PoolDown('refused')):
            self.assertFalse(cp.refresh_status_file())


@contextlib.contextmanager
def junk_bridge():
    """Something on the bridge port that is not HTTP (an SSH banner)."""
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(('127.0.0.1', cp.BRIDGE_PORT))
    srv.listen(5)

    def serve():
        while True:
            try:
                conn, _ = srv.accept()
            except OSError:
                return
            with conn:
                with contextlib.suppress(OSError):
                    conn.sendall(b'SSH-2.0-OpenSSH_9.9\r\n')
    threading.Thread(target=serve, daemon=True).start()
    try:
        yield
    finally:
        srv.close()


class WithPool(unittest.TestCase):
    """Checks that ask the pool (/v1/models) and the bridge, against stand-ins."""
    FACTS = {'seats': [], 'defs': DEFS, 'error': None}

    def setUp(self):
        keep = preserved(cp.CONFIG)
        keep.__enter__()
        self.addCleanup(keep.__exit__, None, None, None)
        self.addCleanup(shutil.rmtree, str(HOME / '.codex'), True)

    def test_non_http_bridge_is_down(self):
        with junk_bridge():
            self.assertIsNone(cp.local_json(f'http://127.0.0.1:{cp.BRIDGE_PORT}/healthz', 2))
            self.assertIsNone(cp.bridge_health(2))
            code, out, _ = run(cp.cmd_lane_list, json=False)
        self.assertEqual(code, 0)
        self.assertIn('bridge down', out)

    def test_served_model_names_are_refused(self):
        lanes = cp.validate_lanes(EXAMPLE_LANES)
        with FakePool(models=['gpt-test', 'grok-4.7-build-fast']):
            self.assertTrue(cp.lane_prepare(lanes, self.FACTS)['plan'])
            seat_named = cp.validate_lanes({'lanes': {'gpt-test': {'role': 'r', 'members': [
                {'provider': 'opencode-go', 'model': 'm'}]}}})
            with self.assertRaises(cp.LaneError) as ctx:
                cp.lane_prepare(seat_named, self.FACTS)
        self.assertIn('the pool already serves a model named "gpt-test"', str(ctx.exception))

    def test_names_our_block_added_are_not_collisions(self):
        lanes = cp.validate_lanes(EXAMPLE_LANES)
        cp.CONFIG.write_text(cp.lanes_config_text(base_config(), plan_of(members=True), 'K', None))
        with FakePool(models=['gpt-test', 'bulk', 'bulk-grok', 'bulk-muse']):
            self.assertTrue(cp.lane_prepare(lanes, self.FACTS)['plan'])
            self.assertTrue(cp.lane_prepare(lanes, self.FACTS, members=True)['plan'][0]['member_aliases'])

    def test_member_alias_collision_only_while_offered(self):
        lanes = cp.validate_lanes(EXAMPLE_LANES)
        with FakePool(models=['bulk-grok']):
            self.assertTrue(cp.lane_prepare(lanes, self.FACTS)['plan'])  # normal apply does not offer bulk-grok
            with self.assertRaises(cp.LaneError):
                cp.lane_prepare(lanes, self.FACTS, members=True)

    def test_doctor_keeps_live_checks_when_prepare_stops(self):
        cp.CODEX_AGENTS_DIR.mkdir(parents=True, exist_ok=True)
        (cp.CODEX_AGENTS_DIR / 'bulk.toml').write_text('name = "bulk"\n')  # not ours: lane apply would stop
        lines = []
        with FakePool(models=['gpt-test']), junk_bridge():
            cp.doctor_lanes(lambda good, text, fix=None, warn=False: lines.append(text),
                            seats=[{'provider': 'codex', 'name': 'codex-a.json'}])
        texts = ' | '.join(lines)
        for text in ('lane apply would stop', f'launchd job {cp.BRIDGE_JOB} not loaded',
                     f'bridge on 127.0.0.1:{cp.BRIDGE_PORT}: not answering', 'key opencode-go: present',
                     'xAI credential: missing', '/v1/models does not list bulk'):
            self.assertIn(text, texts)

    def test_doctor_compares_the_display_name(self):
        """A lane's picker name is part of the config.yaml block doctor compares with lanes.json."""
        key = cp.BRIDGE_KEY.read_text().strip()
        cp.CONFIG.write_text(cp.lanes_config_text(base_config(), plan_of(CHEAP), key, None))

        def block_check(raw):
            found = []
            cp.LANES_FILE.write_text(json.dumps(raw))
            with FakePool(models=['gpt-test', 'cheap']):
                cp.doctor_lanes(lambda good, text, fix=None, warn=False: found.append((good, text, fix)),
                                seats=[{'provider': 'codex', 'name': 'codex-a.json'}])
            return next(f for f in found if f[1] == 'config.yaml lanes block matches lanes.json')
        with preserved(cp.LANES_FILE):
            self.assertTrue(block_check(CHEAP)[0])
            renamed = {'lanes': {'cheap': dict(CHEAP['lanes']['cheap'], display='Drafts')}}
            self.assertEqual(block_check(renamed), (False, 'config.yaml lanes block matches lanes.json',
                                                    'codexpool lane apply'))
            cp.CONFIG.write_text(cp.lanes_config_text(cp.CONFIG.read_text(), plan_of(renamed), key, None))
            self.assertTrue(block_check(renamed)[0])
            self.assertIn('display-name: "Drafts"', cp.CONFIG.read_text())

    def test_doctor_warns_about_leftover_member_aliases(self):
        found = []
        with FakePool(models=['bulk', 'bulk-grok']):
            cp.doctor_lanes(lambda good, text, fix=None, warn=False: found.append((good, warn, text)), seats=[])
        self.assertIn((True, False, 'every lane is offered by the pool (/v1/models)'), found)
        self.assertIn((False, True, '/v1/models still lists the member alias(es) bulk-grok from a lane test'), found)

    def test_set_member_aliases_round_trip(self):
        lanes = cp.validate_lanes(EXAMPLE_LANES)
        with FakePool(models=lambda: sorted(cp.block_aliases(cp.CONFIG.read_text()))):
            with contextlib.redirect_stdout(io.StringIO()):
                cp.set_member_aliases(lanes, True, {'bulk-grok', 'bulk-muse'})
                self.assertEqual(cp.block_aliases(cp.CONFIG.read_text()), {'bulk', 'bulk-grok', 'bulk-muse'})
                cp.set_member_aliases(lanes, False)
        self.assertEqual(cp.block_aliases(cp.CONFIG.read_text()), {'bulk'})
        self.assertEqual(cp.CONFIG.stat().st_mode & 0o777, 0o600)

    def test_set_member_aliases_waits_for_the_pool_to_reload(self):
        lanes = cp.validate_lanes(EXAMPLE_LANES)
        seen = {'text': cp.CONFIG.read_text(), 'since': 0.0, 'listed': set()}

        def lagging():  # like the real pool: a changed config.yaml shows in /v1/models about a second later
            text = cp.CONFIG.read_text()
            if text != seen['text']:
                seen['text'], seen['since'] = text, time.time()
            if time.time() - seen['since'] >= 1.0:
                seen['listed'] = cp.block_aliases(text)
            return sorted(seen['listed'])
        with FakePool(models=lagging), contextlib.redirect_stdout(io.StringIO()) as out:
            cp.set_member_aliases(lanes, True, {'bulk-grok', 'bulk-muse'})
            self.assertLessEqual({'bulk-grok', 'bulk-muse'}, cp.pool_model_ids(3))
            cp.set_member_aliases(lanes, False)
            self.assertEqual(cp.pool_model_ids(3), {'bulk'})  # withdrawn from the pool, not only from the file
        self.assertEqual(out.getvalue(), '')

    def test_withdrawing_with_the_pool_down_does_not_wait(self):
        lanes = cp.validate_lanes(EXAMPLE_LANES)
        cp.CONFIG.write_text(cp.lanes_config_text(cp.CONFIG.read_text(), plan_of(members=True), 'K', None))
        start = time.time()
        with contextlib.redirect_stdout(io.StringIO()):
            cp.set_member_aliases(lanes, False)
        self.assertLess(time.time() - start, 10)
        self.assertEqual(cp.block_aliases(cp.CONFIG.read_text()), {'bulk'})

    def test_dry_run_prints_roles_and_writes_nothing(self):
        before = cp.CONFIG.read_bytes()
        with FakePool(models=['gpt-test']):
            code, out, err = run(cp.cmd_lane_apply, dry_run=True)
        self.assertIn(code, (0, 1), err)
        self.assertIn('+name = "bulk"', out)
        self.assertIn('+apps = false', out)
        self.assertIn('alias: "bulk"', out)
        self.assertNotIn('"bulk-grok"', out)  # normal apply offers the lane alias only
        self.assertNotIn('"bulk-muse"', out)
        self.assertIn('bulk/grok: the pool did not give the context window', out)
        self.assertNotIn('the pool already serves', out)
        self.assertFalse((cp.CODEX_AGENTS_DIR / 'bulk.toml').exists())
        self.assertEqual(cp.CONFIG.read_bytes(), before)



class LaneNamesUpgradeNote(unittest.TestCase):
    """install's note for lanes applied by an older codexpool, whose lanes block gives a lane another picker name."""

    def test_note_only_while_the_block_has_old_names(self):
        with preserved(cp.CONFIG, cp.LANES_FILE):
            cp.LANES_FILE.write_text(json.dumps(EXAMPLE_LANES))
            base = base_config()
            self.assertIsNone(cp.lane_names_note())  # lanes.json but no lanes block yet: lane apply never ran
            fresh = cp.lanes_config_text(base, plan_of(), 'K', None)
            cp.CONFIG.write_text(fresh)
            self.assertIsNone(cp.lane_names_note())
            old = 'display-name: "Bulk lane (Grok 4.7 Fast, then Muse Spark 1.3 contributor)"'  # what 1.0.0 wrote
            cp.CONFIG.write_text(fresh.replace('display-name: "Bulk"', old))
            self.assertIn('Run codexpool lane apply', cp.lane_names_note())
            cp.LANES_FILE.write_text('{"lanes": {"Bad Name": {}}}\n')
            self.assertIsNone(cp.lane_names_note())  # a broken lanes.json: doctor reports that instead
            cp.LANES_FILE.unlink()
            self.assertIsNone(cp.lane_names_note())


if __name__ == '__main__':
    unittest.main()
