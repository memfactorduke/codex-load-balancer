"""Pool descriptors dispatch shared operations without depending on a built-in pool name.
All state and management records are synthetic and stay in the helper's throwaway home.
"""
from _helpers import HOME, cp, run

import copy
import datetime as dt
import json
import pathlib
import tempfile
import unittest
from unittest import mock


class Descriptors(unittest.TestCase):
    def test_unknown_explicit_ids_never_fall_back(self):
        for name in ('missing', '', 'Codex', ' codex', None, 7, []):
            for lookup in (cp.seat_pool, cp.pool_instance):
                with self.subTest(lookup=lookup.__name__, name=name), self.assertRaisesRegex(ValueError, 'unknown'):
                    lookup(name)
            with self.assertRaisesRegex(ValueError, 'unknown'):
                cp.seat_pool(cp.argparse.Namespace(pool=name))
        with self.assertRaisesRegex(ValueError, 'unknown'):
            cp.seat_pool(cp.argparse.Namespace(pool=None))
        self.assertEqual(cp.seat_pool(cp.argparse.Namespace()).name, 'codex')

    def test_addon_registry_resolves_its_descriptors(self):
        instance, seats = cp.pool_instance(), cp.seat_pool()
        instance.name = seats.name = 'fixture'
        def contributions(hook, **kwargs):
            return [(object(), [instance] if hook == 'pools' else {'fixture': seats})]
        with mock.patch.object(cp, 'addon_objects', side_effect=contributions):
            self.assertIs(cp.pool_instance('fixture'), instance)
            self.assertIs(cp.seat_pool('fixture'), seats)
            self.assertIs(cp.seat_pool(cp.types.SimpleNamespace(pool='fixture')), seats)
            for lookup in (cp.pool_instance, cp.seat_pool):
                with self.assertRaisesRegex(ValueError, 'unknown'):
                    lookup('missing')


    def test_enrichment_receives_unabridged_inputs_and_context_once(self):
        pool = cp.seat_pool()
        pool.name = pool.provider = 'fixture'
        claims = {'email': 'one@test', 'plan': 'fixture', 'private_extension': {'kept': True}}
        context = object()
        pool.claims_context = mock.Mock(return_value=context)
        pool.claims = mock.Mock(return_value=claims)
        pool.default_weight = mock.Mock(return_value=7.0)
        records = [{'name': 'one', 'provider': 'fixture', 'path': 'synthetic',
                    'model_quotas': {'fixture-model': {'observed_at': 'test'}}, 'unrecognized': [1, 2]},
                   {'name': 'two', 'provider': 'fixture'}]
        def enrich(row, facts, record):
            self.assertIs(facts, claims)
            self.assertTrue(any(record is r for r in records))
            row['extra'] = record.get('unrecognized')
        pool.enrich_row = mock.Mock(side_effect=enrich)
        with mock.patch.object(cp, 'read_meta', return_value={'one': {'weight': 2}}):
            rows = cp.load_seats(pool, listing={'files': records})
        pool.claims_context.assert_called_once_with()
        self.assertEqual(pool.claims.call_args_list,
                         [mock.call('synthetic', 'one', context), mock.call(None, 'two', context)])
        self.assertEqual(pool.enrich_row.call_count, 2)
        self.assertEqual([(r['weight'], r['extra']) for r in rows], [(7.0, [1, 2]), (7.0, None)])


    def test_custom_template_default_port_and_probe(self):
        with tempfile.TemporaryDirectory(dir=HOME) as work:
            pool = cp.pool_instance()
            pool.name = 'fixture'
            pool.config_template = pathlib.Path(work) / 'fixture.yaml'
            pool.config_template.write_text('port: 1234\nurl: http://127.0.0.1:1234\n  secret-key: ""\n')
            pool.config_default_port = 1234
            pool.port = 4567
            rendered = cp.render_config('synthetic-key', pool)
            self.assertIn('port: 4567', rendered)
            self.assertIn('http://127.0.0.1:4567', rendered)
            self.assertNotIn('1234', rendered)
            pool.probe_cases = mock.Mock(return_value=[('fixture', 200, 200)])
            self.assertEqual(cp.gate_probe_cases(pool), [('fixture', 200, 200)])
            pool.probe_cases.assert_called_once_with(pool)


if __name__ == '__main__':
    unittest.main()
