"""lanes/bridge.py: how it adapts Codex requests for a lane provider and adapts the answers back (pure parts)."""
import hashlib
import json
import random
import string
import unittest

from _helpers import load_bridge

b = load_bridge()
ROUTE = {'upstream': 'x', 'upstream_model': 'muse-up', 'summary_max_output_tokens': 8192}


def codex_request():
    return {
        'model': 'bulk', 'stream': True, 'service_tier': 'priority', 'prompt_cache_key': 'thr-1', 'store': False,
        'tools': [
            {'type': 'function', 'name': 'exec_command', 'parameters': {
                'type': 'object', 'properties': {'n': {'$ref': '#/$defs/N'}}, '$defs': {'N': {'type': 'number'}}}},
            {'type': 'namespace', 'name': 'collaboration', 'description': 'd', 'tools': [
                {'type': 'function', 'name': 'wait_agent', 'parameters': {'type': 'object', 'properties': {}}}]},
            {'type': 'custom', 'name': 'apply_patch', 'format': {'type': 'grammar', 'definition': 'G'}},
            {'type': 'web_search'}],
        'tool_choice': {'type': 'function', 'namespace': 'collaboration', 'name': 'wait_agent'},
        'input': [
            {'type': 'message', 'role': 'developer', 'content': [{'type': 'input_text', 'text': 'dev'}]},
            {'type': 'agent_message', 'author': 'root', 'content': [{'type': 'input_text', 'text': 'do the task'}]},
            {'type': 'reasoning', 'summary': []},
            {'type': 'reasoning', 'id': 'rs_1', 'encrypted_content': 'Q-PaDgXYZ', 'summary': []},
            {'type': 'function_call', 'id': 'fc_1', 'namespace': 'collaboration', 'name': 'wait_agent', 'call_id': 'c1',
             'arguments': '{}'},
            {'type': 'function_call_output', 'call_id': 'c1', 'output': 'done'},
            {'type': 'custom_tool_call', 'call_id': 'c2', 'name': 'apply_patch', 'input': '*** Begin Patch'},
            {'type': 'custom_tool_call_output', 'call_id': 'c2', 'output': 'ok'},
            {'type': 'web_search_call', 'id': 'ws_1'},
            {'type': 'compaction', 'encrypted_content': 'FOREIGNxai'}]}


class Prepare(unittest.TestCase):
    def setUp(self):
        self.sealer = b.Sealer(b'k' * 32)
        self.wire, self.names, self.compact, self.stats = b.prepare(codex_request(), ROUTE, self.sealer)

    def test_model_and_fields(self):
        self.assertEqual(self.wire['model'], 'muse-up')
        self.assertNotIn('service_tier', self.wire)
        self.assertIs(self.wire['store'], False)
        self.assertFalse(self.compact)
        self.assertEqual(self.stats, {'dropped_items': 1, 'foreign_compaction': 1})

    def test_tools(self):
        self.assertEqual([t['name'] for t in self.wire['tools']], ['exec_command', 'collaboration__wait_agent',
                                                                   'apply_patch'])
        self.assertEqual(self.wire['tools'][0]['parameters'],
                         {'type': 'object', 'properties': {'n': {'type': 'number'}}})
        self.assertEqual(self.wire['tool_choice'], {'type': 'function', 'name': 'collaboration__wait_agent'})

    def test_input_items(self):
        items = self.wire['input']
        self.assertEqual([(i.get('type'), i.get('role'), i.get('name')) for i in items],
                         [('message', 'developer', None), ('message', 'user', None), ('reasoning', None, None),
                          ('function_call', None, 'collaboration__wait_agent'), ('function_call_output', None, None),
                          ('function_call', None, 'apply_patch'), ('function_call_output', None, None)])
        self.assertEqual(items[1]['content'], [{'type': 'input_text', 'text': 'do the task'}])
        self.assertNotIn('id', items[3])
        self.assertEqual(json.loads(items[5]['arguments']), {'input': '*** Begin Patch'})


class Responses(unittest.TestCase):
    def setUp(self):
        _, names, _, _ = b.prepare(codex_request(), ROUTE, b.Sealer(b'k' * 32))
        self.adapter = b.EventAdapter(names)

    def test_namespace_restored_and_floats_fixed(self):
        item = self.adapter.item({'type': 'function_call', 'id': 'fc_9', 'name': 'collaboration__wait_agent',
                                  'call_id': 'c9', 'arguments': '{"timeout_ms":60000.0,"x":1.5}'})
        self.assertEqual((item['namespace'], item['name'], item['arguments']),
                         ('collaboration', 'wait_agent', '{"timeout_ms":60000,"x":1.5}'))

    def test_custom_tool_restored(self):
        item = self.adapter.item({'type': 'function_call', 'id': 'fc_p', 'name': 'apply_patch', 'call_id': 'cp',
                                  'arguments': '{"input":"PATCH"}'})
        self.assertEqual((item['type'], item['input']), ('custom_tool_call', 'PATCH'))
        self.assertNotIn('arguments', item)
        events = self.adapter.events({'type': 'response.function_call_arguments.done', 'item_id': 'fc_p',
                                      'arguments': '{"input":"PATCH"}'})
        self.assertEqual([e['type'] for e in events], ['response.custom_tool_call_input.delta',
                                                       'response.custom_tool_call_input.done'])
        self.assertEqual(self.adapter.events({'type': 'response.function_call_arguments.delta', 'item_id': 'fc_p',
                                              'delta': 'x'}), [])

    def test_event_floats(self):
        self.adapter.item({'type': 'function_call', 'id': 'fc_9', 'name': 'collaboration__wait_agent',
                           'call_id': 'c9', 'arguments': '{}'})
        done = self.adapter.events({'type': 'response.function_call_arguments.done', 'item_id': 'fc_9',
                                    'arguments': '{"a":30000.0}'})
        self.assertEqual(done[0]['arguments'], '{"a":30000}')
        completed = self.adapter.events({'type': 'response.completed', 'response': {'output': [
            {'type': 'function_call', 'name': 'exec_command', 'arguments': '{"y":5.0}'}]}})
        self.assertEqual(completed[0]['response']['output'][0]['arguments'], '{"y":5}')
        self.assertEqual(b.normalize_arguments('{"e":1e5,"s":"3.0"}'), '{"e":100000.0,"s":"3.0"}')


class Compaction(unittest.TestCase):
    def setUp(self):
        self.sealer = b.Sealer(b'k' * 32)

    def test_seal_round_trip_and_tamper(self):
        sealed = self.sealer.seal('muse-up', 'SUMMARY TEXT')
        self.assertEqual(self.sealer.unseal(sealed), 'SUMMARY TEXT')
        with self.assertRaises(b.BridgeError) as ctx:
            self.sealer.unseal(sealed[:-3] + 'AAA')
        self.assertEqual(ctx.exception.code, 'bad_checkpoint')

    def test_random_texts_round_trip(self):
        rng = random.Random(11)
        for _ in range(300):
            text = ''.join(rng.choice(string.ascii_letters + ' é') for _ in range(rng.randint(0, 4000)))
            self.assertEqual(self.sealer.unseal(self.sealer.seal('m', text)), text)

    def test_checkpoint_restored_and_summary_request(self):
        sealed = self.sealer.seal('muse-up', 'SUMMARY TEXT')
        body = {'model': 'bulk', 'input': [{'type': 'compaction', 'encrypted_content': sealed},
                                           {'role': 'user', 'content': 'next'}, {'type': 'compaction_trigger'}]}
        wire, _, compact, _ = b.prepare(body, ROUTE, self.sealer)
        self.assertTrue(compact)
        self.assertTrue(wire['input'][0]['content'][0]['text'].endswith('SUMMARY TEXT'))
        summary = b.summary_request(wire, body, ROUTE)
        self.assertNotIn('tools', summary)
        self.assertEqual(summary['max_output_tokens'], 8192)
        self.assertIn('SUMMARY TEXT', summary['input'][0]['content'][0]['text'])

    def test_refusals(self):
        with self.assertRaises(b.BridgeError) as ctx:
            b.prepare({'model': 'bulk', 'input': [{'type': 'compaction_trigger'}, {'role': 'user', 'content': 'x'}]},
                      ROUTE, self.sealer)
        self.assertEqual(ctx.exception.code, 'bad_compaction_trigger')
        with self.assertRaises(b.BridgeError) as ctx:
            b.prepare({'model': 'bulk', 'input': [{'type': 'agent_message', 'content': [
                {'type': 'encrypted_content', 'encrypted_content': 'gAAAA' + 'x' * 100}]}]}, ROUTE, self.sealer)
        self.assertEqual(ctx.exception.code, 'unreadable_handoff')


class NamesAndSchemas(unittest.TestCase):
    def test_name_cap(self):
        self.assertLessEqual(len(b.Names().flatten('mcp__' + 'a' * 60, 'tool_name')), 64)

    def test_long_names_are_hashed(self):
        long_ns = 'n' * 70
        wire, _, _, _ = b.prepare({'model': 'x', 'tools': [{'type': 'namespace', 'name': long_ns, 'tools': [
            {'type': 'function', 'name': 'f', 'parameters': {}}]}], 'input': []}, ROUTE, b.Sealer(b'k' * 32))
        flat = f'{long_ns}__f'
        self.assertEqual(wire['tools'][0]['name'], flat[:55] + '_' + hashlib.sha256(flat.encode()).hexdigest()[:8])

    def test_properties_named_definitions_stay(self):
        schema = {'type': 'object', 'required': ['definitions', 'x'], 'properties': {
            'definitions': {'type': 'string'}, '$defs': {'type': 'integer'}, 'x': {'$ref': '#/definitions/X'}},
            'definitions': {'X': {'type': 'object', 'properties': {'definitions': {'type': 'boolean'}},
                                  '$defs': {'Z': {}}}}}
        self.assertEqual(b.expand_schema(schema), {
            'type': 'object', 'required': ['definitions', 'x'], 'properties': {
                'definitions': {'type': 'string'}, '$defs': {'type': 'integer'},
                'x': {'type': 'object', 'properties': {'definitions': {'type': 'boolean'}}}}})

    def test_flat_names_never_take_a_top_level_name(self):
        empty = {'type': 'object', 'properties': {}}
        body = {'model': 'bulk', 'tools': [
            {'type': 'function', 'name': 'a__b', 'parameters': empty},
            {'type': 'namespace', 'name': 'a', 'tools': [{'type': 'function', 'name': 'b', 'parameters': empty}]},
            {'type': 'namespace', 'name': 'c', 'tools': [{'type': 'function', 'name': 'd', 'parameters': empty}]}],
            'input': [{'type': 'function_call', 'namespace': 'a', 'name': 'b', 'call_id': 'k1', 'arguments': '{}'},
                      {'type': 'function_call_output', 'call_id': 'k1', 'output': 'ok'},
                      {'type': 'function_call', 'name': 'a__b', 'call_id': 'k2', 'arguments': '{}'},
                      {'type': 'function_call_output', 'call_id': 'k2', 'output': 'ok'}]}
        wire, names, _, _ = b.prepare(body, ROUTE, b.Sealer(b'k' * 32))
        flat = [t['name'] for t in wire['tools']]
        self.assertEqual(len(set(flat)), 3)
        self.assertEqual((flat[0], flat[2]), ('a__b', 'c__d'))
        self.assertTrue(flat[1].startswith('a__b_'))
        self.assertEqual([i['name'] for i in wire['input'] if i.get('type') == 'function_call'], [flat[1], 'a__b'])
        top = {'type': 'function_call', 'name': 'a__b', 'arguments': '{}'}
        names.restore(top)
        self.assertEqual((top.get('namespace'), top['name']), (None, 'a__b'))
        nested = {'type': 'function_call', 'name': flat[1], 'arguments': '{}'}
        names.restore(nested)
        self.assertEqual((nested['namespace'], nested['name']), ('a', 'b'))


class UpstreamErrors(unittest.TestCase):
    def test_quota_is_a_429_with_a_reset(self):
        e = b.upstream_error(429, {'Retry-After': '120'}, b'{}')
        body = e.body()['error']
        self.assertEqual((e.status, body['code']), (429, 'rate_limit_exceeded'))
        self.assertGreater(body['resets_at'], 0)
        self.assertIn('subscription quota', body['message'].lower())

    def test_cdn_block_is_a_502(self):
        self.assertEqual(b.upstream_error(403, {'Content-Type': 'text/html'}, b'<html>').status, 502)


if __name__ == '__main__':
    unittest.main()
