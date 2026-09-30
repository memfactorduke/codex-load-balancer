package registry

import (
    "bytes"
    "encoding/json"
    "testing"
)

func TestCodexpoolCatalogSupplement(t *testing.T) {
    for _, client := range []bool{false, true} {
        input := embeddedModelsJSON
        if client { input = embeddedCodexClientModelsJSON }
        merged := codexpoolCatalogSupplement(input, client)
        if bytes.Equal(input, merged) { t.Fatal("missing supplement") }
        if !bytes.Equal(merged, codexpoolCatalogSupplement(merged, client)) {
            t.Fatal("duplicate supplement or upstream entries overwritten")
        }
        if client {
            if err := ValidateCodexClientModelsJSON(merged); err != nil { t.Fatal(err) }
        } else {
            var parsed staticModelsJSON
            if err := json.Unmarshal(merged, &parsed); err != nil { t.Fatal(err) }
            if err := validateModelsCatalog(&parsed); err != nil { t.Fatal(err) }
        }
        invalid := []byte("invalid JSON")
        if !bytes.Equal(invalid, codexpoolCatalogSupplement(invalid, client)) {
            t.Fatal("malformed upstream catalog was concealed")
        }
    }
}

func TestCodexpoolCatalogUpstreamWins(t *testing.T) {
    var extra map[string]map[string][]map[string]any
    if err := json.Unmarshal(codexpoolCatalogJSON, &extra); err != nil { t.Fatal(err) }
    for section, groups := range extra {
        input := map[string]any{}
        for name, models := range groups {
            for _, model := range models { model["description"] = "new upstream metadata" }
            input[name] = models
        }
        raw, _ := json.Marshal(input)
        if !bytes.Equal(raw, codexpoolCatalogSupplement(raw, section == "client")) {
            t.Fatal("upstream metadata must take precedence")
        }
    }
}
