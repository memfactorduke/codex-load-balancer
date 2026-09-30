import _helpers as h
import json
import pathlib
import runpy
import tempfile
import unittest


class CatalogPatchTests(unittest.TestCase):
    def test_patches_both_registry_loaders_and_client_loader(self):
        patch = runpy.run_path(str(h.REPO / 'build/catalog/apply.py'))['apply']
        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp)
            registry = tree / 'internal/registry'
            (registry / 'models').mkdir(parents=True)
            anchor = 'if err := json.Unmarshal(data, &parsed); err != nil {'
            updater = registry / 'model_updater.go'
            updater.write_text(anchor + '\n' + anchor)
            client = registry / 'codex_client_models.go'
            client.write_text('func loadCodexClientModelsFromBytes(data []byte, source string) (bool, error) {\n')
            projection = tree / 'internal/client/codex/models/models.go'
            projection.parent.mkdir(parents=True)
            projection.write_text('\t\tif template, ok := templates[metadataID]; ok {\n'
                                  '\t\t\tapplyCodexClientDescription(entry, model)')
            patch(tree)
            self.assertEqual(updater.read_text().count('codexpoolCatalogSupplement(data, false)'), 2)
            self.assertIn('codexpoolCatalogSupplement(data, true)', client.read_text())
            self.assertTrue((registry / 'models/codexpool_catalog.json').exists())
            self.assertIn('template, ok := templates[id]', projection.read_text())
            self.assertIn('template, ok = templates[metadataID]', projection.read_text())
            self.assertTrue((projection.parent / 'codexpool_catalog_alias_test.go').exists())
            before = updater.read_text()
            with self.assertRaises(RuntimeError):
                patch(tree)
            self.assertEqual(updater.read_text(), before)

    def test_unknown_upstream_layout_changes_nothing(self):
        patch = runpy.run_path(str(h.REPO / 'build/catalog/apply.py'))['apply']
        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp)
            registry = tree / 'internal/registry'
            registry.mkdir(parents=True)
            updater = registry / 'model_updater.go'
            updater.write_text('different upstream code')
            with self.assertRaises(RuntimeError):
                patch(tree)
            self.assertEqual(updater.read_text(), 'different upstream code')

    def test_unknown_projection_changes_no_loader(self):
        patch = runpy.run_path(str(h.REPO / 'build/catalog/apply.py'))['apply']
        with tempfile.TemporaryDirectory() as tmp:
            tree = pathlib.Path(tmp)
            registry = tree / 'internal/registry'
            registry.mkdir(parents=True)
            updater = registry / 'model_updater.go'
            text = 'if err := json.Unmarshal(data, &parsed); err != nil {\n' * 2
            updater.write_text(text)
            client = registry / 'codex_client_models.go'
            client_text = 'func loadCodexClientModelsFromBytes(data []byte, source string) (bool, error) {\n'
            client.write_text(client_text)
            projection = tree / 'internal/client/codex/models/models.go'
            projection.parent.mkdir(parents=True)
            projection.write_text('changed upstream projection')
            with self.assertRaises(RuntimeError):
                patch(tree)
            self.assertEqual(updater.read_text(), text)
            self.assertEqual(client.read_text(), client_text)

    def test_speed_alias_is_client_metadata_only(self):
        data = json.loads((h.REPO / 'build/catalog/supplement.json').read_text())
        alias = 'astra-max-speed'
        self.assertFalse(any(m['id'] == alias for group in data['registry'].values() for m in group))
        models = [m for m in data['client']['models'] if m['slug'] == alias]
        self.assertEqual(len(models), 1)
        model = models[0]
        self.assertEqual(model['display_name'], 'Astra Max Speed')
        self.assertEqual(model['default_service_tier'], 'ultrafast')
        self.assertEqual([tier['id'] for tier in model['service_tiers']], ['ultrafast'])
        self.assertEqual(model['default_reasoning_level'], 'medium')
        self.assertIn('separate', model['description'])
