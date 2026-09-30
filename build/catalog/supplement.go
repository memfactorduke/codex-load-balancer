package registry

import (
    _ "embed"
    "encoding/json"
)

// This supplements catalog data only. It never handles inference requests.
// Existing upstream entries always win, including after a remote refresh.
//go:embed models/codexpool_catalog.json
var codexpoolCatalogJSON []byte

func codexpoolCatalogSupplement(data []byte, client bool) []byte {
    var catalog map[string]json.RawMessage
    var extra map[string]map[string][]json.RawMessage
    if json.Unmarshal(data, &catalog) != nil || catalog == nil {
        return data // The upstream validator reports malformed input.
    }
    if err := json.Unmarshal(codexpoolCatalogJSON, &extra); err != nil {
        panic("invalid embedded catalog supplement: " + err.Error())
    }
    section, key := "registry", "id"
    if client { section, key = "client", "slug" }
    changed := false
    for name, additions := range extra[section] {
        var models []json.RawMessage
        if raw, exists := catalog[name]; exists {
            if json.Unmarshal(raw, &models) != nil { return data }
        }
        seen := map[string]bool{}
        for _, model := range models {
            var fields map[string]json.RawMessage
            var id string
            if json.Unmarshal(model, &fields) == nil {
                _ = json.Unmarshal(fields[key], &id)
                seen[id] = true
            }
        }
        for _, model := range additions {
            var fields map[string]json.RawMessage
            var id string
            if json.Unmarshal(model, &fields) != nil || json.Unmarshal(fields[key], &id) != nil || id == "" {
                panic("invalid embedded catalog model")
            }
            if !seen[id] {
                models = append(models, model)
                seen[id] = true
                changed = true
            }
        }
        catalog[name], _ = json.Marshal(models)
    }
    if !changed { return data }
    merged, err := json.Marshal(catalog)
    if err != nil { panic(err) }
    return merged
}
