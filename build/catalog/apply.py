"""Apply the catalog-only supplement; fail closed if upstream loader anchors change."""
import pathlib
import shutil


def apply(tree):
    assets = pathlib.Path(__file__).resolve().parent
    module = (pathlib.Path(tree) / "go.mod").read_text().splitlines()[0].strip().split()
    supported = ("github.com/router-for-me/CLIProxyAPI/v7", "github.com/router-for-me/CLIProxyAPI/v8")
    if len(module) != 2 or module[0] != "module" or module[1] not in supported:
        raise RuntimeError("unsupported upstream module; catalog supplement was not applied")
    registry = pathlib.Path(tree) / 'internal/registry'
    updater = registry / 'model_updater.go'
    client = registry / 'codex_client_models.go'
    text = updater.read_text()
    anchor = 'if err := json.Unmarshal(data, &parsed); err != nil {'
    if text.count(anchor) != 2 or 'codexpoolCatalogSupplement' in text:
        raise RuntimeError('catalog supplement: upstream registry loaders changed')
    client_text = client.read_text()
    client_anchor = 'func loadCodexClientModelsFromBytes(data []byte, source string) (bool, error) {\n'
    if client_text.count(client_anchor) != 1:
        raise RuntimeError('catalog supplement: upstream client loader changed')
    projection = pathlib.Path(tree) / 'internal/client/codex/models/models.go'
    projection_text = projection.read_text()
    projection_anchor = '\t\tif template, ok := templates[metadataID]; ok {'
    description_anchor = '\t\t\tapplyCodexClientDescription(entry, model)'
    if (projection_text.count(projection_anchor) != 1 or
            projection_text.count(description_anchor) != 1 or
            'codexpool exact catalog template' in projection_text):
        raise RuntimeError('catalog supplement: upstream client projection changed')
    # Keep metadataID intact for provider capability checks. Only the catalog
    # template lookup prefers an explicitly supplied public alias template.
    projection_replacement = '''\t\t// codexpool exact catalog template: presentation only; routing is unchanged.
\t\ttemplate, ok := templates[id]
\t\texactAliasTemplate := ok && id != metadataID
\t\tif !ok {
\t\t\ttemplate, ok = templates[metadataID]
\t\t}
\t\tif ok {'''
    updater.write_text(text.replace(anchor,
        'data = codexpoolCatalogSupplement(data, false)\n\t' + anchor))
    client.write_text(client_text.replace(client_anchor, client_anchor +
        '\tdata = codexpoolCatalogSupplement(data, true)\n'))
    projection.write_text(projection_text.replace(projection_anchor, projection_replacement).replace(
        description_anchor, '\t\t\tif !exactAliasTemplate {\n' + description_anchor + '\n\t\t\t}'))
    shutil.copy2(assets / 'supplement.go', registry / 'codexpool_catalog.go')
    shutil.copy2(assets / 'supplement_test.go', registry / 'codexpool_catalog_test.go')
    shutil.copy2(assets / 'supplement.json', registry / 'models/codexpool_catalog.json')
    (projection.parent / 'codexpool_catalog_alias_test.go').write_text(
        (assets / 'alias_test.go').read_text().replace(supported[0], module[1]))
